"""
Integrated Autonomous Orchestrator -- S2 lifecycle + S3 FreeLLMAPI execution.

Replaces simulated provider with real FreeLLMAPI inference while preserving
S2's task state machine, skill selection, dependency ordering, and Git checkpoints.
"""

import os
import sys
import json
import time
import shutil
import hashlib
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Literal
from datetime import datetime, timezone
from enum import Enum

# Import S2 components
sys.path.insert(0, str(Path(__file__).parent))
from task_state import TaskStore, TaskState, TaskRecord, make_task
from skill_router import SkillRouter, SkillCategory

# Import S3 worker functions
TOOLS_DIR = Path(__file__).parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))
from opencode_worker import (
    run_packet,
    freellmapi_repair_loop,
    freellmapi_apply_files,
    freellmapi_run_tests,
    snapshot,
    scoped_paths,
    linux_path,
    FRELLMAPI_ROUTES,
)

# Optional Docker sandbox backend
try:
    from tools.docker_sandbox import run_tests_in_sandbox as _docker_run_tests
    _DOCKER_AVAILABLE = True
except Exception:
    _DOCKER_AVAILABLE = False


class IntegrationError(Exception):
    pass


@dataclass
class TaskReceipt:
    """Receipt for one completed task (may include multiple repair attempts)."""
    task_id: str
    status: Literal["completed", "failed", "blocked"]
    attempts: list[dict] = field(default_factory=list)
    model_used: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    test_results: Optional[dict] = None
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    elapsed_ms: int = 0
    error: Optional[str] = None
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None


class IntegratedOrchestrator:
    """
    Autonomous orchestrator with real FreeLLMAPI inference.

    Lifecycle:
    APPROVED TASK -> CLAIM -> SKILL SELECT -> MODEL SELECT -> REAL INFERENCE
    -> APPLY FILES -> TEST -> BOUNDED REPAIR -> RETEST -> REVIEW -> GIT COMMIT -> DONE
    """

    def __init__(self, workspace: Path, output_base: Path):
        self.workspace = Path(workspace).resolve()
        self.output_base = Path(output_base).resolve()
        self.store = TaskStore(self.workspace / "task_store")
        self.skill_router = SkillRouter()

        # Ensure workspace structure
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "src").mkdir(exist_ok=True)
        (self.workspace / "tests").mkdir(exist_ok=True)
        self.output_base.mkdir(parents=True, exist_ok=True)

        # Initialize git
        self._init_git()

        # Specialist -> S3 category mapping
        self.specialist_to_category = {
            "backend": "Coding / Refactoring",
            "testing": "Testing / Debugging",
            "database": "Database / PostgreSQL",
            "security": "Security / Auditing",
            "frontend": "Frontend / UI / UX",
            "productivity": "Automation / Integrations",
            "communication": "Research / Documentation",
            "devops": "Automation / Integrations",
            "data": "Data / ML",
            "architecture": "Architecture / System Design",
        }

    def _init_git(self):
        """Initialize git repo if needed."""
        if not (self.workspace / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=self.workspace, capture_output=True)
            subprocess.run(["git", "config", "user.email", "lain@local"], cwd=self.workspace, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Lain"], cwd=self.workspace, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=self.workspace, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial: autonomous integration workspace", "--allow-empty"],
                          cwd=self.workspace, capture_output=True)
        else:
            self._reconcile_git_state()

    def _reconcile_git_state(self):
        """Reconcile task store with actual Git commits on restart."""
        for task in self.store.all():
            if task.state == TaskState.DONE and not task.commit_id:
                try:
                    result = subprocess.run(
                        ["git", "log", "--oneline", "--grep", f"[{task.task_id}]"],
                        cwd=self.workspace, capture_output=True, text=True, timeout=10
                    )
                    if result.stdout.strip():
                        commit_hash = result.stdout.strip().split()[0]
                        task.commit_id = commit_hash
                        self.store.save(task)
                        print(f"  Reconciled {task.task_id}: found existing commit {commit_hash[:8]}")
                except Exception:
                    pass

    def add_task(self, task_id: str, title: str, description: str,
                 specialist: str, dependencies: list[str] = None):
        task = make_task(task_id, title, description, specialist=specialist,
                        dependencies=dependencies or [])
        self.store.save(task)
        return task

    def _get_category(self, specialist: str) -> str:
        return self.specialist_to_category.get(specialist, "Coding / Refactoring")

    def _collect_source_context(self, reads: list[str]) -> dict[str, str]:
        context = {}
        for rel_path in reads:
            full_path = self.workspace / rel_path
            if full_path.is_file():
                content = full_path.read_text(encoding="utf-8")
                limit = 8000 if "test_" in rel_path or rel_path.endswith(".py") else 5000
                context[rel_path] = content[:limit]
        return context

    def _build_task_prompt(self, task: TaskRecord, skill_brief: str,
                          source_context: dict[str, str]) -> str:
        files_list = "\n".join(
            f"--- {k} ({len(v)} chars) ---\n{v}\n--- END {k} ---"
            for k, v in source_context.items()
        )
        deps = f"\nDEPENDENCIES: {', '.join(task.dependencies)}" if task.dependencies else ""

        return f"""You are a senior Python engineer. Complete the task below.

TASK: {task.title}
DESCRIPTION: {task.description}{deps}

SKILL GUIDANCE:
{skill_brief}

EXISTING FILES IN WORKSPACE (READ-ONLY for tests, writeable for src/):
{files_list or "(none)"}

CRITICAL: The test files define the EXACT API you must implement.
Match every class name, method signature, parameter name, and return type.
Do NOT invent different APIs. Do NOT use Decimal when tests expect int cents.
Do NOT use function-based APIs when tests expect class-based APIs.

REQUIREMENTS:
1. Write clean, tested Python code
2. Follow EXACT API signatures from test files
3. Do NOT modify any test files -- only implementation files
4. Output ONLY the changed/added files as markdown code blocks
5. Each code block MUST start with a comment line: # path/to/file.py
6. No explanations, no markdown outside code blocks

EXAMPLE FORMAT:
```python
# src/string_processor.py
class StringProcessor:
    def reverse(self, text: str) -> str:
        return text[::-1]
```"""

    def _determine_reads_writes(self, task: TaskRecord) -> tuple[list[str], list[str]]:
        """Determine read/write file sets for the task.

        Includes:
        - All existing source files (read/write)
        - Expected implementation files (read/write, may not exist yet)
        - All test files (READ-ONLY)
        - Common config files (read)
        - API contract(s) (read, if present)
        """
        reads = []
        writes = []

        for src_file in (self.workspace / "src").rglob("*.py"):
            rel = src_file.relative_to(self.workspace).as_posix()
            reads.append(rel)
            writes.append(rel)

        expected_impl = ["src/inventory/models.py", "src/inventory/database.py",
                        "src/inventory/service.py", "src/inventory/api.py"]
        for impl in expected_impl:
            if impl not in writes:
                writes.append(impl)
            if impl not in reads:
                reads.append(impl)

        for test_file in (self.workspace / "tests").rglob("test_*.py"):
            rel = test_file.relative_to(self.workspace).as_posix()
            reads.append(rel)

        for config in ["pyproject.toml", "README.md"]:
            p = self.workspace / config
            if p.exists():
                rel = p.relative_to(self.workspace).as_posix()
                reads.append(rel)

        contract_paths = [
            "API_CONTRACT.md",
            "api_contract.md",
            "contracts/api_contract.md",
        ]
        for contract in contract_paths:
            if (self.workspace / contract).exists():
                if contract not in reads:
                    reads.append(contract)

        return reads, writes

    def _load_contracts(self) -> dict[str, str]:
        """Load all available contract files from workspace.

        Returns dict mapping contract path to content.
        """
        contracts = {}
        contract_globs = [
            "API_CONTRACT.md",
            "api_contract.md",
            "contracts/*.md",
        ]
        for pattern in contract_globs:
            for contract_file in self.workspace.glob(pattern):
                if contract_file.is_file():
                    rel = contract_file.relative_to(self.workspace).as_posix()
                    contracts[rel] = contract_file.read_text(encoding="utf-8")
        return contracts

    def execute_task(self, task_id: str) -> TaskReceipt:
        """Execute a single task with real FreeLLMAPI inference and repair."""
        task = self.store.get(task_id)
        if not task:
            raise IntegrationError(f"Task {task_id} not found")

        task = self.store.transition(task_id, TaskState.CLAIMED)
        task = self.store.transition(task_id, TaskState.RUNNING)

        skill_brief = self._build_skill_brief(task)

        category = self._get_category(task.specialist)
        reads, writes = self._determine_reads_writes(task)
        source_context = self._collect_source_context(reads)

        prompt = self._build_task_prompt(task, skill_brief, source_context)

        task_output = self.output_base / task_id
        task_output.mkdir(parents=True, exist_ok=True)

        import tempfile
        import shutil
        packet_root = Path(tempfile.mkdtemp(prefix=f"packet_{task_id}_"))
        try:
            for rel_path in reads:
                src = self.workspace / rel_path
                if src.is_file():
                    dst = packet_root / rel_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

            for rel_path in writes:
                src = self.workspace / rel_path
                if src.is_file() and rel_path not in reads:
                    dst = packet_root / rel_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

            acceptance_tests = self._get_acceptance_tests(task_id)

            print(f"  [{task_id}] Starting FreeLLMAPI execution (category: {category})...")
            start_time = time.time()

            model_receipts = freellmapi_repair_loop(
                category=category,
                workspace=str(packet_root),
                task=prompt,
                reads=reads,
                writes=writes,
                output_base=str(task_output),
                max_attempts=3,
                required_context=32768,
            )

            for rel_path in writes:
                src = packet_root / rel_path
                if src.is_file():
                    dst = self.workspace / rel_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

            max_acceptance_repairs = 2
            all_receipts = list(model_receipts)
            test_results = None

            for acceptance_attempt in range(max_acceptance_repairs + 1):
                if _DOCKER_AVAILABLE:
                    verify_result = self._run_acceptance_tests_in_sandbox(
                        task_id, acceptance_tests
                    )
                    # Map sandbox result dict to subprocess-like interface
                    class _SandboxResult:
                        def __init__(self, d):
                            self.returncode = d.get("exit_code", 125)
                            self.stdout = d.get("stdout", "")
                            self.stderr = d.get("stderr", "")
                    verify_result = _SandboxResult(verify_result)
                    if verify_result.returncode == 125 and "Docker sandbox not available" in verify_result.stderr:
                        # Docker was expected but not available - fail closed
                        test_results = {
                            "exit_code": 125,
                            "stdout": "",
                            "stderr": "Docker sandbox unavailable; execution blocked",
                            "acceptance_tests": acceptance_tests,
                            "sandbox_blocked": True,
                        }
                        print(f"  [{task_id}] BLOCKED: Docker sandbox unavailable")
                        break
                else:
                    verify_result = subprocess.run(
                        [sys.executable, "-m", "pytest", *acceptance_tests, "-q", "--tb=short"],
                        cwd=str(self.workspace),
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )

                test_results = {
                    "exit_code": verify_result.returncode,
                    "stdout": verify_result.stdout,
                    "stderr": verify_result.stderr,
                    "acceptance_tests": acceptance_tests,
                }

                if verify_result.returncode == 0:
                    print(f"  [{task_id}] Acceptance tests PASSED")
                    break

                if acceptance_attempt >= max_acceptance_repairs:
                    print(f"  [{task_id}] Acceptance tests FAILED after {max_acceptance_repairs} repair attempts")
                    break

                print(f"  [{task_id}] Acceptance tests FAILED (repair attempt {acceptance_attempt + 1}/{max_acceptance_repairs})")

                acceptance_prompt = (
                    prompt + "\n\n"
                    "PREVIOUS ATTEMPT FAILED ACCEPTANCE TESTS. Here is the failure output:\n"
                    f"STDOUT: {verify_result.stdout}\n"
                    f"STDERR: {verify_result.stderr}\n"
                    "The implementation must match the EXACT API signatures required by the acceptance tests. "
                    "If tests expect a class with __init__, add it. "
                    "If tests expect instance methods, use them. "
                    "Match every class name, method signature, parameter name, and return type exactly. "
                    "Output the COMPLETE corrected file(s)."
                )

                acc_output = task_output / f"acceptance-repair-{acceptance_attempt}"
                acc_output.mkdir(parents=True, exist_ok=True)

                for rel_path in reads + writes:
                    src = self.workspace / rel_path
                    if src.is_file():
                        dst = packet_root / rel_path
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)

                repair_receipt = run_packet(
                    category=category,
                    workspace=str(packet_root),
                    task=acceptance_prompt,
                    reads=reads,
                    writes=writes,
                    output=str(acc_output),
                    required_context=32768,
                    freellmapi_only=True,
                )
                all_receipts.append(repair_receipt)

                for rel_path in writes:
                    src = packet_root / rel_path
                    if src.is_file():
                        dst = self.workspace / rel_path
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)

            elapsed_ms = int((time.time() - start_time) * 1000)
            receipts = all_receipts
            final_receipt = receipts[-1]
            status = final_receipt.get("status", "blocked_unavailable")

        finally:
            shutil.rmtree(packet_root, ignore_errors=True)

        final_receipt = receipts[-1]
        status = final_receipt.get("status", "blocked_unavailable")

        total_tokens = 0
        total_cost = 0.0
        model_used = None

        for r in receipts:
            model_used = r.get("model") or model_used
            cost = r.get("reported_cost", 0)
            if cost:
                total_cost += cost

        files_changed = final_receipt.get("changed", [])

        task_receipt = TaskReceipt(
            task_id=task_id,
            status="completed" if (status == "completed" and test_results and test_results.get("exit_code") == 0) else "failed",
            attempts=[{"attempt": i, **r} for i, r in enumerate(receipts)],
            model_used=model_used,
            files_changed=files_changed,
            test_results=test_results,
            total_cost_usd=total_cost,
            elapsed_ms=elapsed_ms,
            error=None if (status == "completed" and test_results and test_results.get("exit_code") == 0) else final_receipt.get("error", "Acceptance tests failed"),
        )

        if task_receipt.status == "completed":
            task = self.store.transition(task_id, TaskState.VERIFYING)
            task = self.store.transition(task_id, TaskState.REVIEWING)

            branch = f"task/{task_id}"
            commit_msg = f"[{task_id}] {task.title} -- FreeLLMAPI autonomous completion"
            commit_hash = self._git_commit(commit_msg, branch=branch)
            task.commit_id = commit_hash
            task_receipt.git_commit = commit_hash
            task_receipt.git_branch = branch

            task = self.store.transition(task_id, TaskState.COMMITTED)
            task = self.store.transition(task_id, TaskState.DONE)
            task.finished_at = datetime.now(timezone.utc).isoformat()
            self.store.save(task)
        else:
            task = self.store.transition(task_id, TaskState.FAILED)
            task_receipt.status = "failed"

        return task_receipt

    def _build_skill_brief(self, task: TaskRecord) -> str:
        skills = self.skill_router.select_for_task(task.title, task.description)
        briefs = []
        for skill in skills[:3]:
            briefs.append(f"## {skill.name}\n{skill.description[:500]}")
        return "\n\n".join(briefs) if briefs else "No specific skills selected."

    def _run_verification_tests(self) -> dict:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except subprocess.TimeoutExpired as e:
            return {"exit_code": 124, "stdout": e.stdout or "", "stderr": e.stderr or ""}

    def _run_acceptance_tests_in_sandbox(self, task_id: str, acceptance_tests: list[str]) -> dict:
        """Run acceptance tests in the Docker sandbox.

        Creates a disposable packet from the current workspace and executes
        pytest inside the restricted container. Returns the same dict shape
        as subprocess.run-based execution.
        """
        if not _DOCKER_AVAILABLE:
            return {
                "exit_code": 125,
                "stdout": "",
                "stderr": "Docker sandbox not available; fail-closed",
                "acceptance_tests": acceptance_tests,
                "sandbox_blocked": True,
            }

        import tempfile
        packet_root = Path(tempfile.mkdtemp(prefix=f"sandbox_packet_{task_id}_"))
        try:
            # Copy current workspace state to packet
            for f in self.workspace.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(self.workspace).as_posix()
                    dst = packet_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)

            # Run tests in sandbox
            result = _docker_run_tests(
                packet_dir=packet_root,
                test_files=acceptance_tests,
                timeout=30,
            )

            return {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "acceptance_tests": acceptance_tests,
                "sandbox_runtime": result.runtime,
                "sandbox_security": result.security,
            }
        except Exception as e:
            return {
                "exit_code": 125,
                "stdout": "",
                "stderr": f"Sandbox execution failed: {e}",
                "acceptance_tests": acceptance_tests,
                "sandbox_blocked": True,
            }
        finally:
            if packet_root.exists():
                shutil.rmtree(packet_root, ignore_errors=True)

    def _run_targeted_verification(self, task: TaskRecord, files_changed: list[str]) -> dict:
        acceptance_tests = self._get_acceptance_tests(task.task_id)
        if not acceptance_tests:
            acceptance_tests = ["tests/"]
        if _DOCKER_AVAILABLE:
            return self._run_acceptance_tests_in_sandbox(task.task_id, acceptance_tests)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", *acceptance_tests, "-q", "--tb=short"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "acceptance_tests": acceptance_tests}
        except subprocess.TimeoutExpired as e:
            return {"exit_code": 124, "stdout": e.stdout or "", "stderr": e.stderr or "", "acceptance_tests": acceptance_tests}

    def _get_acceptance_tests(self, task_id: str) -> list[str]:
        manifest_path = self.workspace / "acceptance_tests.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            return manifest.get(task_id, [])
        return ["tests/test_*.py"]

    def _git_commit(self, message: str, branch: str) -> str:
        subprocess.run(["git", "checkout", "-b", branch], cwd=self.workspace, capture_output=True, text=True)
        subprocess.run(["git", "add", "-A"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=self.workspace, capture_output=True, text=True)
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.workspace, capture_output=True, text=True)
        commit_hash = result.stdout.strip()
        subprocess.run(["git", "checkout", "main"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "merge", "--no-ff", branch, "-m", f"Merge {branch}"], cwd=self.workspace, capture_output=True)
        return commit_hash

    def run_all(self) -> list[TaskReceipt]:
        receipts = []
        while True:
            next_task = self._next_available_task()
            if not next_task:
                break
            print(f"\n>>> Executing {next_task.task_id}: {next_task.title}")
            receipt = self.execute_task(next_task.task_id)
            receipts.append(receipt)
            if receipt.status == "failed":
                print(f"  [{next_task.task_id}] FAILED: {receipt.error}")
                break
            else:
                print(f"  [{next_task.task_id}] COMPLETED (model: {receipt.model_used}, cost: ${receipt.total_cost_usd:.6f})")
        return receipts

    def _next_available_task(self) -> Optional[TaskRecord]:
        for task in self.store.all():
            if task.state != TaskState.PENDING:
                continue
            deps_satisfied = all(
                self.store.get(dep_id) and self.store.get(dep_id).state == TaskState.DONE
                for dep_id in task.dependencies
            )
            if deps_satisfied:
                return task
        return None

    def save_evidence(self) -> Path:
        evidence = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workspace": str(self.workspace),
            "output_base": str(self.output_base),
            "tasks": [],
            "receipts": [],
        }
        for task_dir in self.output_base.iterdir():
            if not task_dir.is_dir() or task_dir.name.startswith("recovery"):
                continue
            for attempt_dir in task_dir.iterdir():
                if attempt_dir.is_dir():
                    receipt_path = attempt_dir / "receipt.json"
                    if receipt_path.exists():
                        try:
                            with open(receipt_path) as fp:
                                receipt = json.load(fp)
                            receipt["task_id"] = task_dir.name
                            receipt["attempt_dir"] = attempt_dir.name
                            evidence["receipts"].append(receipt)
                        except Exception:
                            pass
        for task in self.store.all():
            task_info = {
                "id": task.task_id,
                "title": task.title,
                "state": task.state.value,
                "specialist": task.specialist,
                "dependencies": task.dependencies,
                "commit_id": task.commit_id,
            }
            if task.commit_id:
                try:
                    result = subprocess.run(
                        ["git", "log", "--pretty=format:%P", "-1", task.commit_id],
                        cwd=self.workspace, capture_output=True, text=True, timeout=10
                    )
                    parents = result.stdout.strip().split() if result.stdout.strip() else []
                except Exception:
                    parents = []
                try:
                    result = subprocess.run(
                        ["git", "diff", "--name-only", *parents, task.commit_id] if parents else ["git", "show", "--name-only", "--format=", task.commit_id],
                        cwd=self.workspace, capture_output=True, text=True, timeout=10
                    )
                    changed_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
                except Exception:
                    changed_files = []
                task_info["commit_details"] = {
                    "hash": task.commit_id,
                    "branch": task.commit_hash if hasattr(task, 'commit_hash') else f"task/{task.task_id}",
                    "parents": parents,
                    "changed_files": changed_files,
                }
            evidence["tasks"].append(task_info)
        evidence_path = self.output_base / f"evidence_{int(time.time())}.json"
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        return evidence_path

    def demonstrate_interruption_recovery(self) -> bool:
        new_orch = IntegratedOrchestrator(self.workspace, self.output_base / "recovery")
        recovered = 0
        for task in self.store.all():
            if task.state == TaskState.DONE:
                new_task = new_orch.store.get(task.task_id)
                if new_task and new_task.state == TaskState.DONE:
                    recovered += 1
        return recovered > 0


class Orchestrator:
    """Compatibility wrapper for existing S2 code."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.integration = IntegratedOrchestrator(workspace, workspace / "output")

    def add_task(self, task_id: str, title: str, description: str,
                 specialist: str = "backend", dependencies: list[str] = None):
        return self.integration.add_task(task_id, title, description, specialist, dependencies)

    def run_task(self, task_id: str):
        return self.integration.execute_task(task_id)

    def run_all(self):
        return self.integration.run_all()

    def save_evidence(self):
        return self.integration.save_evidence()


__all__ = ["IntegratedOrchestrator", "Orchestrator", "TaskReceipt", "IntegrationError"]
