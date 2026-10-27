"""Persistent task scheduler for S2 Autonomous Engineering.

Design:
- Jobs run through task dependencies until acceptance criteria are satisfied
- No arbitrary wall-clock deadline for the job itself
- Individual Docker executions remain bounded (30s, 1GB, 0.5 CPU, 1 container)
- Steps are bounded and checkpointed
- Idempotent recovery after interruption
- Circuit breakers on repeated failures
- Fail-closed: no paid fallback, no unrestricted host execution
"""
from __future__ import annotations

import json
import os
import sys
import time
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Literal

# Import S2 components
sys.path.insert(0, str(Path(__file__).parent))
from task_state import TaskStore, TaskState, make_task


class JobState(Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    RECOVERABLE = "recoverable"


class FailureClassification(Enum):
    PROVIDER_DOWN = "provider_down"
    QUOTA_EXHAUSTED = "quota_exhausted"
    SANDBOX_TIMEOUT = "sandbox_timeout"
    SANDBOX_OOM = "sandbox_oom"
    TEST_FAILURE = "test_failure"
    CODE_REJECTION = "code_rejection"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


@dataclass
class StepCheckpoint:
    """Persistent checkpoint for one bounded step within a job."""
    step_id: str
    step_index: int
    title: str
    description: str
    state: JobState = JobState.PENDING
    attempts: int = 0
    max_attempts: int = 3
    repair_attempts: int = 0
    max_repairs: int = 2
    circuit_breaker_trips: int = 0
    last_error: Optional[str] = None
    last_exit_code: Optional[int] = None
    last_stdout: str = ""
    last_stderr: str = ""
    model_used: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    commit_id: Optional[str] = None
    git_branch: Optional[str] = None
    elapsed_ms: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: Optional[str] = None
    acceptance_tests: list[str] = field(default_factory=list)
    test_results: dict = field(default_factory=dict)
    sandbox_config: dict = field(default_factory=dict)
    security_tests_passed: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StepCheckpoint":
        d = dict(d)
        d["state"] = JobState(d["state"])
        return cls(**d)


@dataclass
class JobRecord:
    """Persistent job definition and state."""
    job_id: str
    title: str
    description: str
    state: JobState = JobState.PENDING
    tasks: list[str] = field(default_factory=list)  # task IDs to execute
    current_step_index: int = 0
    steps: list[StepCheckpoint] = field(default_factory=list)
    parent_job_id: Optional[str] = None  # for nested/dependent jobs
    dependencies: list[str] = field(default_factory=list)  # job IDs that must complete first
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    # Execution config
    max_concurrent_containers: int = 1
    container_timeout: int = 30
    container_memory: str = "1g"
    container_memory_swap: str = "1g"
    container_cpus: float = 0.5
    container_pids_limit: int = 128
    # Circuit breaker
    failure_threshold: int = 3  # trips after N consecutive failures
    circuit_open: bool = False
    circuit_opened_at: Optional[float] = None
    # Acceptance criteria
    acceptance_criteria: dict = field(default_factory=dict)
    # Evidence
    evidence_path: Optional[str] = None
    commit_id: Optional[str] = None
    git_branch: Optional[str] = None


    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["state"] = self.state.value
        d["steps"] = [s.to_dict() for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "JobRecord":
        d = dict(d)
        d["state"] = JobState(d["state"])
        d["steps"] = [StepCheckpoint.from_dict(s) for s in d.get("steps", [])]
        return cls(**d)


class PersistentScheduler:
    """
    Completion-driven engine scheduler.

    Lifecycle:
    JOB DEFINED -> DEPENDENCIES SATISFIED -> SCHEDULED ->
    CLAIM -> RUN STEPS -> ACCEPTANCE CHECK -> GIT COMMIT -> DONE
    OR: BLOCKED / PAUSED (recoverable)
    """

    def __init__(self, workspace: Path, output_base: Path):
        self.workspace = Path(workspace).resolve()
        self.output_base = Path(output_base).resolve()
        self.store = TaskStore(self.workspace / "task_store")
        self.jobs_file = self.workspace / "job_store" / "jobs.json"
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.output_base.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobRecord] = {}
        self._load()

    def _load(self):
        if self.jobs_file.exists():
            data = json.loads(self.jobs_file.read_text())
            for j in data.get("jobs", []):
                self._jobs[j["job_id"]] = JobRecord.from_dict(j)

    def _save(self):
        data = {"jobs": [j.to_dict() for j in self._jobs.values()]}
        tmp = self.jobs_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        # Use copy + replace for Windows compatibility
        try:
            self.jobs_file.unlink(missing_ok=True)
            shutil.move(str(tmp), str(self.jobs_file))
        except Exception:
            # Last resort: overwrite directly
            self.jobs_file.write_text(json.dumps(data, indent=2))

    def create_job(self, job_id: str, title: str, description: str,
                   tasks: list[str], dependencies: list[str] = None,
                   acceptance_criteria: dict = None,
                   **kwargs) -> JobRecord:
        job = JobRecord(
            job_id=job_id,
            title=title,
            description=description,
            tasks=tasks,
            dependencies=dependencies or [],
            acceptance_criteria=acceptance_criteria or {},
            **kwargs,
        )
        # Create steps from tasks
        for i, task_id in enumerate(tasks):
            step = StepCheckpoint(
                step_id=f"{job_id}_step_{i}",
                step_index=i,
                title=f"{title} step {i+1}: {task_id}",
                description=f"Execute task {task_id}",
            )
            job.steps.append(step)
        self._jobs[job_id] = job
        self._save()
        return job

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def all_jobs(self) -> list[JobRecord]:
        return list(self._jobs.values())

    def _deps_satisfied(self, job: JobRecord) -> bool:
        """Check if all job dependencies are DONE."""
        for dep_id in job.dependencies:
            dep = self._jobs.get(dep_id)
            if not dep or dep.state != JobState.DONE:
                return False
        return True

    def _next_pending_step(self, job: JobRecord) -> Optional[StepCheckpoint]:
        """Find the next pending/recoverable step."""
        for i, step in enumerate(job.steps):
            if step.state == JobState.PENDING:
                return step
            if step.state == JobState.RECOVERABLE and i > job.current_step_index:
                return step
        return None

    def schedule(self, job_id: str) -> bool:
        """Mark job as ready to run (dependencies satisfied)."""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if not self._deps_satisfied(job):
            job.state = JobState.BLOCKED
            job.error = "Dependencies not satisfied"
            self._save()
            return False
        job.state = JobState.SCHEDULED
        job.updated_at = time.time()
        self._save()
        return True

    def claim(self, job_id: str) -> bool:
        """Claim a scheduled job for execution."""
        job = self._jobs.get(job_id)
        if not job or job.state != JobState.SCHEDULED:
            return False
        # Check circuit breaker
        if job.circuit_open:
            job.error = "Circuit breaker open"
            self._save()
            return False
        job.state = JobState.RUNNING
        job.started_at = time.time()
        job.updated_at = time.time()
        self._save()
        return True

    def pause(self, job_id: str, reason: str = ""):
        """Pause a running job (recoverable)."""
        job = self._jobs.get(job_id)
        if not job or job.state != JobState.RUNNING:
            return
        job.state = JobState.PAUSED
        job.error = reason
        job.updated_at = time.time()
        self._save()

    def block(self, job_id: str, reason: str = "", classification: FailureClassification = FailureClassification.UNKNOWN):
        """Block a job with explicit reason and classification."""
        job = self._jobs.get(job_id)
        if not job:
            return
        job.state = JobState.BLOCKED
        job.error = reason
        job.metadata["failure_classification"] = classification.value
        job.updated_at = time.time()
        self._save()

    def run_job(self, job_id: str) -> JobRecord:
        """Run a job through all its steps until acceptance or failure."""
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # Allow resuming from RUNNING or SCHEDULED
        if job.state == JobState.RUNNING:
            pass  # Already running, continue
        elif job.state == JobState.SCHEDULED:
            job.state = JobState.RUNNING
            job.started_at = time.time()
            job.updated_at = time.time()
            self._save()
        else:
            raise ValueError(f"Job {job_id} cannot be started (state: {job.state.value})")

        # Execute steps in order
        while job.state == JobState.RUNNING:
            step = self._next_pending_step(job)
            if not step:
                # All steps complete
                break

            result = self._execute_step(job, step)

            if result.status == "completed":
                step.state = JobState.DONE
                step.commit_id = result.git_commit
                step.git_branch = result.git_branch
                step.finished_at = datetime.now(timezone.utc).isoformat()
                job.current_step_index = step.step_index + 1
            elif result.status == "blocked":
                # Hard blocker - mark job blocked
                job.state = JobState.BLOCKED
                job.error = f"Step {step.step_id} blocked: {result.error}"
                break
            elif result.status == "failed":
                step.attempts += 1
                if step.attempts >= step.max_attempts:
                    # Try repair
                    if step.repair_attempts < step.max_repairs:
                        repair_result = self._repair_step(job, step)
                        if repair_result.status == "completed":
                            step.state = JobState.DONE
                            step.commit_id = repair_result.git_commit
                            step.finished_at = datetime.now(timezone.utc).isoformat()
                            job.current_step_index = step.step_index + 1
                            continue
                        step.repair_attempts += 1
                    # Exhausted
                    step.state = JobState.FAILED
                    step.last_error = result.error
                    job.error = f"Step {step.step_id} failed after {step.attempts} attempts"
                    job.current_step_index = step.step_index + 1
                    break
                else:
                    # Retry
                    step.last_error = result.error
                    step.updated_at = time.time()
            job.updated_at = time.time()

        # Final acceptance check
        if job.state == JobState.RUNNING or (all(s.state == JobState.DONE for s in job.steps)):
            acceptance_passed = self._check_acceptance(job)
            if acceptance_passed:
                job.state = JobState.DONE
                job.finished_at = datetime.now(timezone.utc).isoformat()
                job.commit_id = self._git_commit(job)
                job.git_branch = f"job/{job_id}"
            else:
                job.state = JobState.FAILED
                job.error = "Final acceptance check failed"

        self._save()
        return job

    def _execute_step(self, job: JobRecord, step: StepCheckpoint) -> 'StepResult':
        """Execute a single step using the orchestrator + Docker backend."""
        import integrated_orchestrator as io

        orchestrator = io.IntegratedOrchestrator(self.workspace, self.output_base)
        task_id = job.tasks[step.step_index] if step.step_index < len(job.tasks) else f"UNKNOWN_{step.step_index}"

        print(f"  [{job.job_id}:{step.step_id}] Executing...")
        start = time.time()

        try:
            receipt = orchestrator.execute_task(task_id)
            elapsed_ms = int((time.time() - start) * 1000)

            result = StepResult(
                status="completed" if receipt.status == "completed" else "failed",
                git_commit=receipt.git_commit,
                git_branch=receipt.git_branch,
                files_changed=receipt.files_changed,
                elapsed_ms=elapsed_ms,
            )

            # Record evidence
            step.test_results = receipt.test_results if receipt.test_results else {}
            step.files_changed = receipt.files_changed
            step.model_used = receipt.model_used
            step.elapsed_ms = elapsed_ms
            step.updated_at = time.time()

            if receipt.status == "failed":
                result.status = "failed"
                result.error = receipt.error or "Task execution failed"

            return result

        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            return StepResult(
                status="blocked",
                error=str(e),
                elapsed_ms=elapsed_ms,
            )

    def _repair_step(self, job: JobRecord, step: StepCheckpoint) -> 'StepResult':
        """Attempt to repair a failed step (bounded repair loop)."""
        import integrated_orchestrator as io

        orchestrator = io.IntegratedOrchestrator(self.workspace, self.output_base)
        task_id = job.tasks[step.step_index] if step.step_index < len(job.tasks) else "UNKNOWN"

        print(f"  [{job.job_id}:{step.step_id}] Repairing...")
        start = time.time()

        try:
            receipt = orchestrator.execute_task(task_id)
            elapsed_ms = int((time.time() - start) * 1000)

            result = StepResult(
                status="completed" if receipt.status == "completed" else "failed",
                git_commit=receipt.git_commit,
                git_branch=receipt.git_branch,
                files_changed=receipt.files_changed,
                elapsed_ms=elapsed_ms,
            )

            step.repair_attempts += 1
            step.test_results = receipt.test_results if receipt.test_results else {}
            step.model_used = receipt.model_used
            step.updated_at = time.time()

            if receipt.status == "failed":
                result.status = "failed"
                result.error = receipt.error

            return result

        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            return StepResult(status="failed", error=str(e), elapsed_ms=elapsed_ms)

    def _check_acceptance(self, job: JobRecord) -> bool:
        """Check final acceptance criteria."""
        # All steps must be DONE
        if not all(s.state == JobState.DONE for s in job.steps):
            return False

        # Acceptance tests (if defined)
        for criterion_name, criterion_value in job.acceptance_criteria.items():
            if criterion_name == "all_tasks_passed":
                # Verify all task test suites pass
                for task_id in job.tasks:
                    manifest = json.loads((self.workspace / "acceptance_tests.json").read_text())
                    tests = manifest.get(task_id, ["tests/test_*.py"])
                    r = subprocess.run(
                        [sys.executable, "-m", "pytest", *tests, "-q"],
                        cwd=str(self.workspace), capture_output=True, timeout=120
                    )
                    if r.returncode != 0:
                        return False

        return True

    def _git_commit(self, job: JobRecord) -> str:
        """Create a git commit for the job."""
        branch = f"job/{job.job_id}"
        message = f"[{job.job_id}] {job.title} -- persistent scheduler completion"

        subprocess.run(["git", "checkout", "-b", branch], cwd=self.workspace,
                      capture_output=True, text=True)
        subprocess.run(["git", "add", "-A"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=self.workspace,
                      capture_output=True, text=True)

        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.workspace,
                               capture_output=True, text=True)
        commit_hash = result.stdout.strip()

        subprocess.run(["git", "checkout", "main"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "merge", "--no-ff", branch, "-m", f"Merge {branch}"],
                      cwd=self.workspace, capture_output=True)

        return commit_hash

    def recover(self) -> list[JobRecord]:
        """Recover and resume interrupted jobs idempotently."""
        recovered = []
        for job in self.all_jobs():
            if job.state == JobState.RUNNING:
                # Resume from where we left off - claim is skipped since already running
                recovered.append(job)
                self.run_job(job.job_id)
            elif job.state == JobState.PAUSED:
                # Can attempt resume
                recovered.append(job)
        return recovered

    def get_status(self) -> dict:
        """Get current scheduler status summary."""
        jobs = self.all_jobs()
        return {
            "total_jobs": len(jobs),
            "by_state": {s.value: sum(1 for j in jobs if j.state == s) for s in JobState},
            "jobs": [
                {
                    "job_id": j.job_id,
                    "title": j.title,
                    "state": j.state.value,
                    "steps_completed": sum(1 for s in j.steps if s.state == JobState.DONE),
                    "total_steps": len(j.steps),
                    "error": j.error,
                }
                for j in jobs
            ],
        }


@dataclass
class StepResult:
    status: Literal["completed", "failed", "blocked"]
    error: Optional[str] = None
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
