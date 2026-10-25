"""
Integrated Autonomous Orchestrator — S2 lifecycle + S3 FreeLLMAPI execution.

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
    APPROVED TASK → CLAIM → SKILL SELECT → MODEL SELECT → REAL INFERENCE 
    → APPLY FILES → TEST → BOUNDED REPAIR → RETEST → REVIEW → GIT COMMIT → DONE
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
        
        # Specialist → S3 category mapping
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
            # Initial commit
            subprocess.run(["git", "add", "-A"], cwd=self.workspace, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial: autonomous integration workspace", "--allow-empty"], 
                          cwd=self.workspace, capture_output=True)
    
    def add_task(self, task_id: str, title: str, description: str, 
                 specialist: str, dependencies: list[str] = None):
        """Add a task to the orchestrator."""
        task = make_task(task_id, title, description, specialist=specialist, 
                        dependencies=dependencies or [])
        self.store.save(task)
        return task
    
    def _get_category(self, specialist: str) -> str:
        """Map specialist to S3 category."""
        return self.specialist_to_category.get(specialist, "Coding / Refactoring")
    
    def _collect_source_context(self, reads: list[str]) -> dict[str, str]:
        """Read source files for context."""
        context = {}
        for rel_path in reads:
            full_path = self.workspace / rel_path
            if full_path.is_file():
                content = full_path.read_text(encoding="utf-8")
                context[rel_path] = content[:5000]  # Limit context size
        return context
    
    def _build_task_prompt(self, task: TaskRecord, skill_brief: str, 
                          source_context: dict[str, str]) -> str:
        """Build the prompt for the model."""
        files_list = "\n".join(f"- {k} ({len(v)} chars)" for k, v in source_context.items())
        deps = f"\nDEPENDENCIES: {', '.join(task.dependencies)}" if task.dependencies else ""
        
        return f"""You are a senior Python engineer. Complete the task below.

TASK: {task.title}
DESCRIPTION: {task.description}{deps}

SKILL GUIDANCE:
{skill_brief}

EXISTING FILES IN WORKSPACE:
{files_list or "(none)"}

REQUIREMENTS:
1. Write clean, tested Python code
2. Follow existing patterns in the workspace
3. Do NOT modify any test files — only implementation files
4. Output ONLY the changed/added files as markdown code blocks
5. Each code block MUST start with a comment line: # path/to/file.py
6. No explanations, no markdown outside code blocks

EXAMPLE FORMAT:
```python
# src/string_processor.py
class StringProcessor:
    def reverse(self, text: str) -> str:
        return text[::-1]
```
"""
    
    def _determine_reads_writes(self, task: TaskRecord) -> tuple[list[str], list[str]]:
        """Determine read/write file sets for the task."""
        # Default: allow reading all src/, writing src/ and tests/
        reads = []
        writes = []
        
        # Scan existing files
        for src_file in (self.workspace / "src").rglob("*.py"):
            rel = src_file.relative_to(self.workspace).as_posix()
            reads.append(rel)
            writes.append(rel)
        
        for test_file in (self.workspace / "tests").rglob("test_*.py"):
            rel = test_file.relative_to(self.workspace).as_posix()
            reads.append(rel)
            # Include test files in writes so they get tested during repair loop
            # Model is instructed not to modify them
            writes.append(rel)
        
        # Add common config files
        for config in ["pyproject.toml", "README.md"]:
            p = self.workspace / config
            if p.exists():
                rel = p.relative_to(self.workspace).as_posix()
                reads.append(rel)
        
        return reads, writes
    
    def execute_task(self, task_id: str) -> TaskReceipt:
        """Execute a single task with real FreeLLMAPI inference and repair."""
        task = self.store.get(task_id)
        if not task:
            raise IntegrationError(f"Task {task_id} not found")
        
        # Claim task
        task = self.store.transition(task_id, TaskState.CLAIMED)
        task = self.store.transition(task_id, TaskState.RUNNING)
        
        # Select skills
        skill_brief = self._build_skill_brief(task)
        
        # Determine category and file sets
        category = self._get_category(task.specialist)
        reads, writes = self._determine_reads_writes(task)
        source_context = self._collect_source_context(reads)
        
        # Build prompt
        prompt = self._build_task_prompt(task, skill_brief, source_context)
        
        # Create output directory for this task
        task_output = self.output_base / task_id
        task_output.mkdir(parents=True, exist_ok=True)
        
        # Create isolated packet workspace (no .git) for S3 worker
        import tempfile
        import shutil
        packet_root = Path(tempfile.mkdtemp(prefix=f"packet_{task_id}_"))
        try:
            # Mirror source files to packet
            for rel_path in reads:
                src = self.workspace / rel_path
                if src.is_file():
                    dst = packet_root / rel_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            
            # Also copy write-target files if they exist
            for rel_path in writes:
                src = self.workspace / rel_path
                if src.is_file() and rel_path not in reads:
                    dst = packet_root / rel_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            
            # Execute with FreeLLMAPI repair loop on isolated packet
            print(f"  [{task_id}] Starting FreeLLMAPI execution (category: {category})...")
            start_time = time.time()
            
            receipts = freellmapi_repair_loop(
                category=category,
                workspace=str(packet_root),
                task=prompt,
                reads=reads,
                writes=writes,
                output_base=str(task_output),
                max_attempts=3,
                required_context=32768,
            )
            
            # Copy results back to main workspace
            for rel_path in writes:
                src = packet_root / rel_path
                if src.is_file():
                    dst = self.workspace / rel_path
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            
        finally:
            # Cleanup packet
            shutil.rmtree(packet_root, ignore_errors=True)
        
        # Process final receipt
        final_receipt = receipts[-1]
        status = final_receipt.get("status", "blocked_unavailable")
        
        # Aggregate tokens and cost
        total_tokens = 0
        total_cost = 0.0
        model_used = None
        
        for r in receipts:
            model_used = r.get("model") or model_used
            cost = r.get("reported_cost", 0)
            if cost:
                total_cost += cost
        
        files_changed = final_receipt.get("changed", [])
        
        # Extract test results
        test_results = None
        if "test_exit" in final_receipt:
            test_results = {
                "exit_code": final_receipt.get("test_exit"),
                "stdout": final_receipt.get("test_stdout", ""),
                "stderr": final_receipt.get("test_stderr", ""),
            }
        
        # Create task receipt
        task_receipt = TaskReceipt(
            task_id=task_id,
            status="completed" if status == "completed" else "failed",
            attempts=[{"attempt": i, **r} for i, r in enumerate(receipts)],
            model_used=model_used,
            files_changed=files_changed,
            test_results=test_results,
            total_cost_usd=total_cost,
            elapsed_ms=elapsed_ms,
            error=None if status == "completed" else final_receipt.get("error", status),
        )
        
        # Transition state
        if status == "completed":
            task = self.store.transition(task_id, TaskState.VERIFYING)
            
            # Run verification tests ONLY for the module this task modified
            # The repair loop already ran tests on written files and passed
            # This is a targeted sanity check
            verify_result = self._run_targeted_verification(task, files_changed)
            if verify_result["exit_code"] != 0:
                task = self.store.transition(task_id, TaskState.FAILED)
                task_receipt.status = "failed"
                task_receipt.error = "Targeted verification tests failed"
            else:
                task = self.store.transition(task_id, TaskState.REVIEWING)
                
                # Git commit
                branch = f"task/{task_id}"
                commit_msg = f"[{task_id}] {task.title} — FreeLLMAPI autonomous completion"
                commit_hash = self._git_commit(commit_msg, branch=branch)
                task.commit_hash = commit_hash
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
        """Build skill guidance for the task."""
        skills = self.skill_router.select_for_task(task.title, task.description)
        
        briefs = []
        for skill in skills[:3]:
            briefs.append(f"## {skill.name}\n{skill.description[:500]}")
        
        return "\n\n".join(briefs) if briefs else "No specific skills selected."
    
    def _run_verification_tests(self) -> dict:
        """Run the full test suite for verification."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired as e:
            return {"exit_code": 124, "stdout": e.stdout or "", "stderr": e.stderr or ""}

    def _run_targeted_verification(self, task: TaskRecord, files_changed: list[str]) -> dict:
        """Run tests only for the modules that were changed."""
        # Determine which test files to run based on changed source files
        test_files = []
        for f in files_changed:
            if f.startswith("src/"):
                module_name = Path(f).stem
                test_file = f"tests/test_{module_name}.py"
                if (self.workspace / test_file).exists():
                    test_files.append(test_file)
        
        if not test_files:
            # No specific tests found, run a quick smoke test
            test_files = ["tests/"]
        
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", *test_files, "-q", "--tb=short"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=120,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired as e:
            return {"exit_code": 124, "stdout": e.stdout or "", "stderr": e.stderr or ""}
    
    def _git_commit(self, message: str, branch: str) -> str:
        """Create a git commit on a feature branch."""
        # Create/switch to branch
        subprocess.run(["git", "checkout", "-b", branch], cwd=self.workspace, 
                      capture_output=True, text=True)
        
        # Add and commit
        subprocess.run(["git", "add", "-A"], cwd=self.workspace, capture_output=True)
        subprocess.run(["git", "commit", "-m", message], cwd=self.workspace, 
                      capture_output=True, text=True)
        
        # Get commit hash
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.workspace,
                               capture_output=True, text=True)
        commit_hash = result.stdout.strip()
        
        # Return to main
        subprocess.run(["git", "checkout", "main"], cwd=self.workspace, capture_output=True)
        
        # Merge branch
        subprocess.run(["git", "merge", "--no-ff", branch, "-m", f"Merge {branch}"],
                      cwd=self.workspace, capture_output=True)
        
        return commit_hash
    
    def run_all(self) -> list[TaskReceipt]:
        """Execute all pending tasks in dependency order."""
        receipts = []
        
        while True:
            # Find next available task
            next_task = self._next_available_task()
            if not next_task:
                break
            
            print(f"\n>>> Executing {next_task.task_id}: {next_task.title}")
            receipt = self.execute_task(next_task.task_id)
            receipts.append(receipt)
            
            if receipt.status == "failed":
                print(f"  [{next_task.task_id}] FAILED: {receipt.error}")
                # Stop on failure unless we want to continue
                break
            else:
                print(f"  [{next_task.task_id}] COMPLETED (model: {receipt.model_used}, "
                      f"cost: ${receipt.total_cost_usd:.6f})")
        
        return receipts
    
    def _next_available_task(self) -> Optional[TaskRecord]:
        """Get next task with satisfied dependencies."""
        for task in self.store.all():
            if task.state != TaskState.PENDING:
                continue
            # Check dependencies
            deps_satisfied = all(
                self.store.get(dep_id) and self.store.get(dep_id).state == TaskState.DONE
                for dep_id in task.dependencies
            )
            if deps_satisfied:
                return task
        return None
    
    def save_evidence(self) -> Path:
        """Save complete run evidence."""
        evidence = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workspace": str(self.workspace),
            "output_base": str(self.output_base),
            "tasks": [{
                "id": task.task_id,
                "title": task.title,
                "state": task.state.value,
                "specialist": task.specialist,
                "dependencies": task.dependencies,
                "commit_hash": task.commit_id,
            } for task in self.store.all()],
            "receipts": [],
        }
        
        # Receipts would be loaded from output directories
        # For now, just save task summary
        evidence_path = self.output_base / f"evidence_{int(time.time())}.json"
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        
        return evidence_path
    
    def demonstrate_interruption_recovery(self) -> bool:
        """Demonstrate that task state persists across process restart."""
        # Simulate interruption by creating a new orchestrator instance
        new_orch = IntegratedOrchestrator(self.workspace, self.output_base / "recovery")
        
        # Check that completed tasks are still DONE
        recovered = 0
        for task in self.store.all():
            if task.state == TaskState.DONE:
                new_task = new_orch.store.get(task.task_id)
                if new_task and new_task.state == TaskState.DONE:
                    recovered += 1
        
        return recovered > 0


# Maintain backward compatibility with S2's orchestrator interface
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