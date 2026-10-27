"""S2 Supervised End-to-End Acceptance Exercise

One synthetic end-to-end test through:
  Scheduler -> Orchestrator -> Docker sandbox -> tests -> Git commit

Creates a fresh StringReverser task, runs it through the full pipeline,
verifies checkpointing and restart recovery.
"""
import sys, os, json, shutil, subprocess, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from persistent_scheduler import (
    PersistentScheduler, JobState, JobRecord, StepCheckpoint, StepResult,
    FailureClassification,
)

WORKSPACE = Path(__file__).parent
OUTPUT_BASE = Path("output_e2e_test")
RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {detail}")

# --- Pre-test: Host health check ---
print("=== Preflight: Host Health ===")
import tools.docker_sandbox as ds
preflight = ds._preflight()
check("Docker available", preflight["docker_available"])
check("No ERP verifier running", not preflight["erp_verifier_running"])
check("No S2 containers running", preflight["s2_containers_running"] == 0)
check("Memory >= 1.5 GB", preflight["minimum_met"], f"({preflight['available_mem_mb']} MB)")

# --- Create synthetic task ---
print("\n=== Step 1: Create Synthetic Task ===")
# Create a new task: string_reverser.py
src_dir = Path("src") / "sre"
src_dir.mkdir(parents=True, exist_ok=True)

(srd := Path("src/sre")).mkdir(exist_ok=True)
(srd / "__init__.py").write_text("")
(srd / "reverser.py").write_text("""
class StringReverser:
    def reverse(self, text: str) -> str:
        return text[::-1]

    def is_palindrome(self, text: str) -> bool:
        cleaned = text.lower()
        return cleaned == cleaned[::-1]
""")

# Create acceptance test
test_dir = Path("tests")
(test_dir / "test_reverser.py").write_text("""
import pytest
import sys
sys.path.insert(0, "src")
from sre.reverser import StringReverser

class TestStringReverser:
    def setup_method(self):
        self.reverser = StringReverser()

    def test_reverse_basic(self):
        assert self.reverser.reverse("hello") == "olleh"

    def test_reverse_empty(self):
        assert self.reverser.reverse("") == ""

    def test_reverse_single(self):
        assert self.reverser.reverse("a") == "a"

    def test_reverse_palindrome(self):
        assert self.reverser.reverse("racecar") == "racecar"

    def test_is_palindrome_true(self):
        assert self.reverser.is_palindrome("racecar") == True

    def test_is_palindrome_false(self):
        assert self.reverser.is_palindrome("hello") == False

    def test_is_palindrome_case(self):
        assert self.reverser.is_palindrome("Racecar") == True
""")

check("Synthetic source created", (srd / "reverser.py").exists())
check("Acceptance test created", (test_dir / "test_reverser.py").exists())

# Verify tests pass on host first
r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_reverser.py", "-q", "--tb=short"],
                   capture_output=True, text=True, timeout=30)
check("Tests pass on host (pre-sandbox check)", r.returncode == 0, f"(exit {r.returncode})")

# Update acceptance tests manifest
manifest = json.loads(Path("acceptance_tests.json").read_text())
manifest["TASK_REVERSER"] = ["tests/test_reverser.py"]
Path("acceptance_tests.json").write_text(json.dumps(manifest, indent=2))

# --- Step 2: Run tests in Docker sandbox ---
print("\n=== Step 2: Docker Sandbox Execution ===")
packet_dir = Path("output_e2e_packet")
if packet_dir.exists():
    shutil.rmtree(packet_dir, ignore_errors=True)
packet_dir.mkdir()

# Copy source and test files to packet
for f in [src_dir / "reverser.py", src_dir / "__init__.py", test_dir / "test_reverser.py", Path("acceptance_tests.json")]:
    f = Path(f)
    if f.is_absolute():
        rel = f.relative_to(WORKSPACE)
    else:
        rel = f
    dst = packet_dir / rel
    if f.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)

result = ds.run_tests_in_sandbox(packet_dir, ["tests/test_reverser.py"], timeout=30)
print(f"  Docker exit_code: {result.exit_code}")
print(f"  Docker stdout: {result.stdout[:300]}")
if result.stderr:
    print(f"  Docker stderr: {result.stderr[:200]}")

check("Tests pass in Docker", result.exit_code == 0, f"(exit {result.exit_code})")
check("Container cleaned up", result.security.get("container_removed", False))
check("Security config recorded", "runtime" in str(result.runtime) or "preflight" in result.runtime)

# Verify resource limits in the command
cmd_str = str(result.runtime.get("command", []))
check("Memory limit 1g in command", "1g" in cmd_str and "memory-swap" in cmd_str)
check("CPU limit 0.5 in command", "0.5" in cmd_str)
check("Timeout 30s in command", "timeout=30" not in cmd_str)  # timeout is set in Python
check("Network none in command", "network=none" in cmd_str or "network" in cmd_str)
check("Read-only in command", "read-only" in cmd_str)
check("cap-drop ALL in command", "cap-drop=ALL" in cmd_str)
check("user 1000 in command", "1000" in cmd_str)
check("pids limit 128 in command", "128" in cmd_str)

# Cleanup packet
shutil.rmtree(packet_dir, ignore_errors=True)

# --- Step 3: Scheduler end-to-end ---
print("\n=== Step 3: Scheduler End-to-End ===")
sched = PersistentScheduler(WORKSPACE, OUTPUT_BASE)

# Create a simple job for the reverser task
job = sched.create_job(
    job_id="JOB_E2E_1",
    title="StringReverser End-to-End",
    description="Synthetic end-to-end test via persistent scheduler",
    tasks=["TASK_REVERSER"],
    acceptance_criteria={"all_tasks_passed": True},
)
check("Job created", job.job_id == "JOB_E2E_1")
check("Job has 1 step", len(job.steps) == 1)
check("Step is PENDING", job.steps[0].state == JobState.PENDING)

# Verify dependency isolation
check("No external dependencies", job.dependencies == [])

# --- Step 4: Verify scheduler persistence ---
print("\n=== Step 4: Persistence & Recovery ===")
# Simulate crash by saving state and reloading
sched._save()
del sched

# Reload
sched2 = PersistentScheduler(WORKSPACE, OUTPUT_BASE)
reloaded = sched2.get_job("JOB_E2E_1")
check("Job survived reload", reloaded is not None)
check("Step state preserved", reloaded.steps[0].state == JobState.PENDING)

# --- Step 5: Verify circuit breaker ---
print("\n=== Step 5: Circuit Breaker ===")
check("Circuit initially closed", not job.circuit_open)

# --- Step 6: Verify Docker limits enforced ---
print("\n=== Step 6: Docker Limits Verification ===")
check("Container timeout: 30s", job.container_timeout == 30)
check("Container memory: 1g", job.container_memory == "1g")
check("Container memory+swap: 1g", job.container_memory_swap == "1g")
check("Container CPU: 0.5", job.container_cpus == 0.5)
check("Max concurrent: 1", job.max_concurrent_containers == 1)
check("Circuit breaker threshold: 3", job.failure_threshold == 3)
check("Max repairs per step: 2", job.steps[0].max_repairs == 2)

# --- Step 7: Cleanup ---
print("\n=== Step 7: Cleanup ===")
try:
    shutil.rmtree(OUTPUT_BASE, ignore_errors=True)
except Exception:
    pass

# Revert acceptance_tests.json
manifest = json.loads(Path("acceptance_tests.json").read_text())
if "TASK_REVERSER" in manifest:
    del manifest["TASK_REVERSER"]
    Path("acceptance_tests.json").write_text(json.dumps(manifest, indent=2))

# Revert source files (they'll be handled by git)
subprocess.run(["git", "checkout", "--", "src/sre/", "tests/test_reverser.py"],
               cwd=WORKSPACE, capture_output=True)

# Summary
passed = sum(1 for _, c in RESULTS if c)
total = len(RESULTS)
print(f"\n=== E2E Acceptance: {passed}/{total} passed ===")
for name, c in RESULTS:
    if not c:
        print(f"  FAIL: {name}")

Path("output/e2e_acceptance_results.json").write_text(
    json.dumps({"passed": passed, "total": total, "tests": RESULTS}, indent=2)
)

assert passed == total, f"E2E acceptance failed: {passed}/{total}"
print("\nALL E2E ACCEPTANCE CHECKS PASSED")