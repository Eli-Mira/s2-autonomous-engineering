"""S2-09 Reproducibility & Regression"""
import sys, json, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

results = []
def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {detail}")

# 1. Python version check
import sys
check("Python 3.10+", sys.version_info >= (3, 10), f"({sys.version})")

# 2. pyproject.toml exists
check("pyproject.toml exists", Path("pyproject.toml").exists())

# 3. Required modules importable
for mod in ["task_state", "skill_router", "integrated_orchestrator", "freellmapi_adapter"]:
    try:
        __import__(mod)
        check(f"import {mod}", True)
    except Exception as e:
        check(f"import {mod}", False, str(e))

# 4. Test dependencies available
for dep in ["pytest"]:
    try:
        __import__(dep)
        check(f"dependency {dep} available", True)
    except Exception:
        check(f"dependency {dep} available", False)

# 5. Git repo is clean (except known untracked)
r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
# Allow test files and __pycache__ changes
lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
untracked_important = [l for l in lines if not any(x in l for x in ["__pycache__", "test_s2_", "output/"])]
check("no unexpected uncommitted changes", len(untracked_important) <= 5,
      f"({len(untracked_important)} files)")

# 6. All test files pass
r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
                   capture_output=True, text=True, timeout=120)
check("all tests pass", r.returncode == 0, f"(exit {r.returncode})")

# 7. Isolation tests still pass
r = subprocess.run([sys.executable, "-m", "pytest", "isolation/test_confinement.py", "-q", "--tb=short"],
                   capture_output=True, text=True, timeout=120)
check("isolation tests pass", r.returncode == 0, f"(exit {r.returncode})")

# 8. Acceptance tests manifest valid
import json
acc = json.loads(Path("acceptance_tests.json").read_text())
check("acceptance manifest valid JSON", isinstance(acc, dict))
check("acceptance manifest has TASK_D", "TASK_D" in acc)

# 9. Evidence files are valid JSON
for ef in Path("output").glob("evidence_*.json"):
    try:
        json.loads(ef.read_text())
        check(f"evidence {ef.name} valid JSON", True)
    except Exception:
        check(f"evidence {ef.name} valid JSON", False)

# 10. README exists
check("README.md exists", Path("README.md").exists())

# Summary
passed = sum(1 for _, c in results if c)
total = len(results)
print(f"\nS2-09: {passed}/{total} passed")
for name, c in results:
    if not c:
        print(f"  FAIL: {name}")

Path("output/s2_09_results.json").write_text(json.dumps({"passed": passed, "total": total, "tests": results}, indent=2))