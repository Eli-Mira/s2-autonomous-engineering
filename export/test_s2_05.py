"""S2-05 Orchestration Reliability Verification Tests"""
import sys, json, time, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from task_state import TaskStore, TaskState, make_task

store_root = Path(__file__).parent / "test_store_05"
if store_root.exists():
    shutil.rmtree(store_root)

store = TaskStore(store_root)
# Create tasks with dependencies
t1 = make_task("T1", "First", "desc", specialist="backend", dependencies=[])
t2 = make_task("T2", "Second", "desc", specialist="backend", dependencies=["T1"])
t3 = make_task("T3", "Third", "desc", specialist="backend", dependencies=["T1","T2"])
for t in [t1,t2,t3]:
    store.save(t)

results = []
def check(name, cond):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}: {name}")

# 1. Valid transition sequence
try:
    store.transition("T1", TaskState.CLAIMED)
    store.transition("T1", TaskState.RUNNING)
    store.transition("T1", TaskState.VERIFYING)
    store.transition("T1", TaskState.REVIEWING)
    store.transition("T1", TaskState.COMMITTED)
    store.transition("T1", TaskState.DONE)
    check("full transition sequence T1", store.get("T1").state == TaskState.DONE)
except Exception as e:
    check("full transition sequence T1", False)
    print(f"    error: {e}")

# 2. Invalid transition rejection
try:
    store.transition("T2", TaskState.DONE)  # PENDING -> DONE invalid
    check("invalid transition rejected", False)
except ValueError:
    check("invalid transition rejected", True)

# 3. Dependency ordering
t2 = store.get("T2")
deps_ok = all(store.get(d).state == TaskState.DONE for d in t2.dependencies)
check("T2 dependency satisfied", deps_ok)

# 4. Duplicate prevention - idempotency key
t1_key = store.get("T1").idempotency_key
check("idempotency key exists", t1_key is not None and len(t1_key) > 0)

# 5. Cancellation
store.save(make_task("T4", "Cancel me", "desc"))
store.transition("T4", TaskState.CLAIMED)
store.transition("T4", TaskState.CANCELLED)
check("cancellation works", store.get("T4").state == TaskState.CANCELLED)

# 6. Recovery - RECOVERABLE state
store.save(make_task("T5", "Recoverable", "desc"))
store.transition("T5", TaskState.CLAIMED)
store.transition("T5", TaskState.RECOVERABLE)
store.transition("T5", TaskState.PENDING)
check("recovery to PENDING", store.get("T5").state == TaskState.PENDING)

# 7. Restart recovery - reload from disk
new_store = TaskStore(store_root)
check("restart recovery T1 DONE", new_store.get("T1").state == TaskState.DONE)
check("restart recovery T2 PENDING", new_store.get("T2").state == TaskState.PENDING)

# 8. Atomic write verification
tmp_files = list(store_root.glob("*.tmp"))
check("no leftover tmp files", len(tmp_files) == 0)

# 9. Concurrency safety - lock
import threading
errors = []
def worker():
    try:
        s = TaskStore(store_root)
        s.get("T1")
    except Exception as e:
        errors.append(str(e))
threads = [threading.Thread(target=worker) for _ in range(5)]
for t in threads: t.start()
for t in threads: t.join()
check("concurrent read safe", len(errors) == 0)

# Summary
passed = sum(1 for _, c in results if c)
total = len(results)
print(f"\nS2-05: {passed}/{total} passed")
for name, c in results:
    if not c:
        print(f"  FAIL: {name}")

# Cleanup
shutil.rmtree(store_root, ignore_errors=True)