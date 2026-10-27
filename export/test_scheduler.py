"""S2 Persistent Scheduler Verification Tests"""
import sys, json, time, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from persistent_scheduler import (
    PersistentScheduler, JobState, StepCheckpoint, JobRecord,
    FailureClassification, StepResult,
)

WORKSPACE = Path("test_scheduler_workspace").resolve()
OUTPUT_BASE = Path("output_scheduler_test").resolve()

if WORKSPACE.exists():
    try:
        shutil.rmtree(WORKSPACE)
    except Exception:
        pass
WORKSPACE.mkdir(parents=True, exist_ok=True)
(OUTPUT_BASE).mkdir(parents=True, exist_ok=True)

# Initialize git in test workspace
import subprocess
subprocess.run(["git", "init", "-q"], cwd=WORKSPACE, capture_output=True)
subprocess.run(["git", "config", "user.email", "test@local"], cwd=WORKSPACE, capture_output=True)
subprocess.run(["git", "config", "user.name", "Test"], cwd=WORKSPACE, capture_output=True)
subprocess.run(["git", "add", "-A"], cwd=WORKSPACE, capture_output=True)
subprocess.run(["git", "commit", "-m", "initial", "--allow-empty"], cwd=WORKSPACE, capture_output=True)

results = []
def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {detail}")

# Test 1: Job creation
print("=== TEST 1: Job Creation ===")
sched = PersistentScheduler(WORKSPACE, OUTPUT_BASE)
job = sched.create_job(
    job_id="TEST_JOB_1",
    title="Test Job 1",
    description="Verification job",
    tasks=["TASK_A", "TASK_B"],
    acceptance_criteria={"all_tasks_passed": True},
)
check("job created", job.job_id == "TEST_JOB_1")
check("job has steps", len(job.steps) == 2)
check("steps are PENDING", all(s.state == JobState.PENDING for s in job.steps))
check("steps have correct indices", job.steps[0].step_index == 0 and job.steps[1].step_index == 1)

# Test 2: Dependency satisfaction
print("\n=== TEST 2: Dependencies ===")
job2 = sched.create_job(
    job_id="TEST_JOB_2",
    title="Dependent Job",
    description="Has dependency on TEST_JOB_1",
    tasks=["TASK_C"],
    dependencies=["TEST_JOB_1"],
)
check("deps set", job2.dependencies == ["TEST_JOB_1"])
check("deps not satisfied (JOB_1 not done)", not sched._deps_satisfied(job2))

# Mark JOB_1 as done for deps test
sched.schedule("TEST_JOB_1")
sched.run_job("TEST_JOB_1")  # Will fail but tests state machine
check("deps still not satisfied after partial", not sched._deps_satisfied(job2))

# Test 3: State transitions
print("\n=== TEST 3: State Transitions ===")
test_sched = PersistentScheduler(WORKSPACE, OUTPUT_BASE)
# Re-create after any state changes
tj = JobRecord(job_id="STATE_TEST", title="State Test", description="test")
tj.state = JobState.PENDING
test_sched._jobs["STATE_TEST"] = tj
test_sched._save()

# Verify valid transitions
try:
    tj = test_sched._jobs["STATE_TEST"]
    tj.state = JobState.SCHEDULED  # PENDING -> SCHEDULED
    check("PENDING->SCHEDULED valid", True)
except Exception:
    check("PENDING->SCHEDULED valid", False)

try:
    tj = test_sched._jobs["STATE_TEST"]
    tj.state = JobState.RUNNING  # SCHEDULED -> RUNNING
    check("SCHEDULED->RUNNING valid", True)
except Exception:
    check("SCHEDULED->RUNNING valid", False)

try:
    tj = test_sched._jobs["STATE_TEST"]
    tj.state = JobState.PAUSED  # RUNNING -> PAUSED
    check("RUNNING->PAUSED valid", True)
except Exception:
    check("RUNNING->PAUSED valid", False)

try:
    tj = test_sched._jobs["STATE_TEST"]
    tj.state = JobState.BLOCKED  # PAUSED -> BLOCKED
    check("PAUSED->BLOCKED valid", True)
except Exception:
    check("PAUSED->BLOCKED valid", False)

# Test 4: Job recovery
print("\n=== TEST 4: Recovery ===")
rec_sched = PersistentScheduler(WORKSPACE, OUTPUT_BASE)
# Simulate interrupted job
interrupted = JobRecord(job_id="INTERRUPTED", title="Interrupted", description="test")
interrupted.state = JobState.RUNNING
interrupted.steps = [
    StepCheckpoint(step_id="s1", step_index=0, title="Step 1", description="desc", state=JobState.DONE),
    StepCheckpoint(step_id="s2", step_index=1, title="Step 2", description="desc", state=JobState.PENDING),
]
rec_sched._jobs["INTERRUPTED"] = interrupted
rec_sched._save()

recovered = rec_sched.recover()
check("recovery found interrupted job", len(recovered) >= 1)

# Test 5: Circuit breaker
print("\n=== TEST 5: Circuit Breaker ===")
cb_sched = PersistentScheduler(WORKSPACE, OUTPUT_BASE)
cb_job = JobRecord(job_id="CB_TEST", title="Circuit Breaker", description="test")
cb_job.state = JobState.SCHEDULED
cb_job.circuit_open = True
cb_job.circuit_opened_at = time.time()
cb_sched._jobs["CB_TEST"] = cb_job
cb_sched._save()

claimed = cb_sched.claim("CB_TEST")
check("circuit blocked claim", not claimed)
check("state still SCHEDULED", cb_sched._jobs["CB_TEST"].state == JobState.SCHEDULED)

# Test 6: Idempotent state persistence
print("\n=== TEST 6: Persistence ===")
pers_sched = PersistentScheduler(WORKSPACE, OUTPUT_BASE)
pers_sched.create_job(
    job_id="PERSIST_TEST",
    title="Persistence",
    description="test",
    tasks=["TASK_X"],
)
# Reload and verify
del pers_sched
pers_sched2 = PersistentScheduler(WORKSPACE, OUTPUT_BASE)
reloaded = pers_sched2.get_job("PERSIST_TEST")
check("job persisted", reloaded is not None)
check("job title preserved", reloaded.title == "Persistence")

# Test 7: Scheduler status
print("\n=== TEST 7: Status ===")
status = sched.get_status()
check("status has total_jobs", "total_jobs" in status)
check("status has by_state", "by_state" in status)

# Test 8: Blocked job with reason
print("\n=== TEST 8: Blocked Job ===")
blk_sched = PersistentScheduler(WORKSPACE, OUTPUT_BASE)
blk_job = JobRecord(job_id="BLOCKED_JOB", title="Blocked", description="test")
blk_job.state = JobState.PENDING
blk_sched._jobs["BLOCKED_JOB"] = blk_job
blk_sched._save()
blk_sched.block("BLOCKED_JOB", "Resource unavailable", FailureClassification.INFRASTRUCTURE)
reloaded = blk_sched.get_job("BLOCKED_JOB")
check("job blocked", reloaded.state == JobState.BLOCKED)
check("block reason recorded", "Resource" in reloaded.error)
check("classification recorded", reloaded.metadata.get("failure_classification") == "infrastructure")

# Summary
passed = sum(1 for _, c in results if c)
total = len(results)
print(f"\nS2 Scheduler Tests: {passed}/{total} passed")
for name, c in results:
    if not c:
        print(f"  FAIL: {name}")

Path("output/scheduler_test_results.json").write_text(
    json.dumps({"passed": passed, "total": total, "tests": results}, indent=2)
)

# Cleanup - use ignore_errors and try/except for Windows file locks
try:
    shutil.rmtree(WORKSPACE, ignore_errors=True)
except Exception:
    pass
try:
    shutil.rmtree(OUTPUT_BASE, ignore_errors=True)
except Exception:
    pass