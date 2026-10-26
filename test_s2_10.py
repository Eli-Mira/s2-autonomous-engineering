"""S2-10 Performance & Token Efficiency"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

results = []
def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {detail}")

# 1. Measure orchestrator import time
start = time.time()
import integrated_orchestrator
import_time = time.time() - start
check("orchestrator import < 2s", import_time < 2, f"({import_time:.3f}s)")

# 2. Measure task_state import time
start = time.time()
import task_state
ts_time = time.time() - start
check("task_state import < 1s", ts_time < 1, f"({ts_time:.3f}s)")

# 3. Measure skill router import
start = time.time()
import skill_router
sr_time = time.time() - start
check("skill_router import < 1s", sr_time < 1, f"({sr_time:.3f}s)")

# 4. Check token efficiency - prompt size
orch = integrated_orchestrator.IntegratedOrchestrator(
    Path(".").resolve(), Path("output").resolve())
prompt = orch._build_task_prompt(
    orch.store.get("TASK_D") if "TASK_D" in [t.task_id for t in orch.store.all()] else None,
    "skill brief", {"test.py": "x" * 100})
# Use a mock task if TASK_D not in store
if prompt is None:
    from task_state import make_task
    t = make_task("PERF", "perf test", "desc")
    prompt = orch._build_task_prompt(t, "skill brief", {"test.py": "x" * 100})
check("prompt size reasonable", len(prompt) < 50000, f"({len(prompt)} chars)")

# 5. Check evidence file sizes
import os
evidence_sizes = [os.path.getsize(f) for f in Path("output").glob("evidence_*.json")]
if evidence_sizes:
    check("evidence files < 1MB", all(s < 1_000_000 for s in evidence_sizes),
          f"(max {max(evidence_sizes)} bytes)")

# 6. Check test suite runtime
start = time.time()
import subprocess
r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
                   capture_output=True, text=True, timeout=120)
test_time = time.time() - start
check("test suite < 60s", test_time < 60, f"({test_time:.1f}s, exit {r.returncode})")

# 7. Check memory footprint of orchestrator
import tracemalloc
tracemalloc.start()
o = integrated_orchestrator.IntegratedOrchestrator(Path(".").resolve(), Path("output").resolve())
current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
check("orchestrator memory < 50MB", peak < 50_000_000, f"(peak {peak/1024/1024:.1f}MB)")

# 8. Check git log performance
start = time.time()
r = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, timeout=10)
git_time = time.time() - start
check("git log < 1s", git_time < 1, f"({git_time:.3f}s)")

# Summary
passed = sum(1 for _, c in results if c)
total = len(results)
print(f"\nS2-10: {passed}/{total} passed")
for name, c in results:
    if not c:
        print(f"  FAIL: {name}")

Path("output/s2_10_results.json").write_text(json.dumps({"passed": passed, "total": total, "tests": results}, indent=2))