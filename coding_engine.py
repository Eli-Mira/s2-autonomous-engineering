"""Coding engine: locate code, apply modifications, run tests, commit.

Operates on disposable synthetic repos. Real file modifications, real test execution.
"""

import os
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TestResult:
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    output: str = ""
    returncode: int = 0
    test_file: str = ""

    @property
    def all_passed(self) -> bool:
        return self.returncode == 0 and self.failed == 0 and self.errors == 0


@dataclass
class CodeChange:
    file_path: str
    description: str
    before_hash: str
    after_hash: str
    diff_summary: str = ""


@dataclass
class ExecutionEvidence:
    task_id: str
    workspace: str
    test_results: list[TestResult] = field(default_factory=list)
    code_changes: list[CodeChange] = field(default_factory=list)
    commit_hash: str = ""
    branch: str = ""
    live_inference: bool = False
    simulated_provider: bool = True
    repair_attempts: int = 0
    stdout: str = ""
    stderr: str = ""


class CodingEngine:
    """Executes scoped coding tasks against a disposable repository."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.demo_src = workspace / "demo_project" / "src"
        self.tests_dir = workspace / "tests"

    def run_tests(self, test_file: Optional[str] = None) -> TestResult:
        """Run actual Python tests. Returns real execution results."""
        test_target = test_file or str(self.tests_dir / "test_demo_lib.py")
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", str(self.tests_dir), "-v"]
        # Ensure demo_project/src is importable
        env = dict(os.environ)
        src_path = str(self.demo_src)
        env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"]) if "PYTHONPATH" in env else src_path
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                cwd=str(self.workspace), env=env)

        output = result.stdout + result.stderr
        # Parse counts from unittest output
        passed = failed = errors = total = 0
        for line in output.splitlines():
            line_stripped = line.strip()
            if line_stripped.endswith(" ... ok"):
                passed += 1
            elif line_stripped.endswith(" ... FAIL"):
                failed += 1
            elif line_stripped.endswith(" ... ERROR"):
                errors += 1
            elif line_stripped.startswith("Ran "):
                import re
                m = re.search(r"Ran (\d+) test", line_stripped)
                if m:
                    total = int(m.group(1))
            elif "FAILED" in line_stripped and ("failures=" in line_stripped or "errors=" in line_stripped):
                import re
                m2 = re.search(r"failures=(\d+)", line_stripped)
                m3 = re.search(r"errors=(\d+)", line_stripped)
                if m2 and failed == 0:
                    failed = int(m2.group(1))
                if m3 and errors == 0:
                    errors = int(m3.group(1))
        if total == 0:
            total = passed + failed + errors

        return TestResult(
            total=total, passed=passed, failed=failed, errors=errors,
            output=output, returncode=result.returncode, test_file=str(test_target)
        )

    def apply_fix(self, file_path: str, old_content: str, new_content: str,
                  description: str) -> CodeChange:
        """Apply a targeted fix to source code."""
        target = Path(file_path)
        if not target.is_absolute():
            target = self.workspace / target
        before = target.read_text(encoding="utf-8")
        before_hash = hashlib.sha256(before.encode()).hexdigest()
        if old_content not in before:
            raise ValueError(f"Old content not found in {file_path}")
        after = before.replace(old_content, new_content, 1)
        target.write_text(after, encoding="utf-8")
        after_hash = hashlib.sha256(after.encode()).hexdigest()
        return CodeChange(
            file_path=str(target), description=description,
            before_hash=before_hash, after_hash=after_hash,
            diff_summary=f"-{old_content.strip()[:80]}\n+{new_content.strip()[:80]}"
        )

    def git_commit(self, message: str, branch: Optional[str] = None) -> str:
        """Commit changes to git. Returns commit hash."""
        if branch:
            subprocess.run(["git", "checkout", "-B", branch], cwd=str(self.workspace),
                          capture_output=True, text=True)
        subprocess.run(["git", "add", "-A"], cwd=str(self.workspace),
                      capture_output=True, text=True)
        result = subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=str(self.workspace), capture_output=True, text=True
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout + result.stderr:
            raise RuntimeError(f"Git commit failed: {result.stderr}")
        hash_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.workspace),
                                     capture_output=True, text=True)
        return hash_result.stdout.strip()

    def git_log(self, limit: int = 10) -> list[dict]:
        """Get recent commits."""
        result = subprocess.run(
            ["git", "log", f"--max-count={limit}", "--format=%H|%s|%ai"],
            cwd=str(self.workspace), capture_output=True, text=True
        )
        entries = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                entries.append({"hash": parts[0], "message": parts[1], "date": parts[2]})
        return entries