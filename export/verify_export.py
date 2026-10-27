"""Sanitized export verifier — run after cloning the private repo."""
import subprocess, sys, json
from pathlib import Path

def check(cmd, desc):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    ok = r.returncode == 0
    print(f"  {'PASS' if ok else 'FAIL'}: {desc}")
    if not ok:
        print(f"    stderr: {r.stderr[:300]}")
    return ok

print("=== Export Verification ===")
root = Path(__file__).parent
all_ok = True

# 1. Core imports
for mod in ["task_state", "skill_router", "model_router"]:
    try:
        __import__(mod)
        print(f"  PASS: import {mod}")
    except Exception as e:
        print(f"  FAIL: import {mod}: {e}")
        all_ok = False

# 2. Test suites
for test_file in sorted(root.glob("test_s2_*.py")) + sorted(root.glob("test_scheduler.py")):
    r = subprocess.run([sys.executable, str(test_file)], capture_output=True, text=True, timeout=120)
    ok = r.returncode == 0
    print(f"  {'PASS' if ok else 'FAIL'}: {test_file.name}")
    if not ok:
        print(f"    stderr: {r.stderr[-500:]}")
    all_ok = all_ok and ok

# 3. Git check
r = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
untracked = [l for l in r.stdout.strip().splitlines() if l.strip() and "task_store" not in l and ".gitignore" not in l]
print(f"  {'PASS' if not untracked else 'WARN'}: git clean ({len(untracked)} unexpected files)")

print(f"\nExport verification: {'ALL PASS' if all_ok else 'SOME FAILURES'}")
sys.exit(0 if all_ok else 1)
