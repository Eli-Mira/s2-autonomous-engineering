"""S2-06 Model Routing & Cost Safety + S2-07 Skill Routing + S2-08 Evidence Quality"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import freellmapi_adapter as fa
from skill_router import SkillRouter

results = []
def check(name, cond, detail=""):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {detail}")

# === S2-06: Model Routing ===
print("=== S2-06: Model Routing & Cost Safety ===")

# 1. Free-tier only - check adapter for paid model references
import re
adapter_src = Path("freellmapi_adapter.py").read_text()
has_paid = bool(re.search(r'gpt-4|claude-3-opus|claude-3-5-sonnet', adapter_src, re.I))
check("no paid models in adapter", not has_paid)

# 2. Quota handling - check error handling for 403/429 in opencode_worker (S3)
worker_src = Path("tools/opencode_worker.py").read_text()
has_403 = "403" in worker_src
has_429 = "429" in worker_src
check("403 handling present", has_403)
check("429 handling present", has_429)

# 3. No paid fallback (worker uses zero-cost models only - check SQL filters use = 0)
has_paid_fallback = "COALESCE(paid_input_per_m, 0) = 0" not in worker_src or "COALESCE(paid_output_per_m, 0) = 0" not in worker_src
check("no paid fallback", not has_paid_fallback)

# 4. Cost tracking
has_cost = "cost" in worker_src.lower() or "token" in worker_src.lower()
check("cost tracking present", has_cost)

# 5. Fail-closed for unavailable models
has_failclosed = "blocked_access" in worker_src or "blocked_unavailable" in worker_src
check("fail-closed routing", has_failclosed)

# 6. Verify model_router.py
router_src = Path("model_router.py").read_text()
check("model_router has free-tier filter", "free" in router_src.lower())

# === S2-07: Skill Routing ===
print("\n=== S2-07: Skill Routing & Quality ===")

router = SkillRouter()
skills = router.select_for_task("Implement inventory service", "Multi-file SQLite inventory")
check("skill selection returns results", len(skills) > 0)
check("skills have names", all(hasattr(s, 'name') for s in skills))
check("skills have descriptions", all(hasattr(s, 'description') for s in skills))

# Verify skill routing doesn't crash on edge cases
try:
    empty_skills = router.select_for_task("", "")
    check("empty task handled", True)
except Exception as e:
    check("empty task handled", False, str(e))

# Check skill_router.py has proper categories
sr_src = Path("skill_router.py").read_text()
check("skill router has categories", "SkillCategory" in sr_src or "category" in sr_src.lower())

# === S2-08: Evidence Quality ===
print("\n=== S2-08: Evidence Quality ===")

# Check evidence files exist
evidence_files = list(Path("output").glob("evidence_*.json"))
check("evidence files exist", len(evidence_files) > 0, f"({len(evidence_files)} files)")

# Check evidence structure
if evidence_files:
    ev = json.loads(evidence_files[-1].read_text())
    check("evidence has timestamp", "timestamp" in ev)
    check("evidence has tasks", "tasks" in ev)
    check("evidence has receipts", "receipts" in ev)
    check("evidence has workspace", "workspace" in ev)

# Check per-task receipts
receipt_count = 0
for task_dir in Path("output").iterdir():
    if task_dir.is_dir() and not task_dir.name.startswith("recovery"):
        for attempt_dir in task_dir.iterdir():
            if attempt_dir.is_dir() and (attempt_dir / "receipt.json").exists():
                receipt_count += 1
check("per-task receipts exist", receipt_count > 0, f"({receipt_count} receipts)")

# Check acceptance_tests.json
acc = Path("acceptance_tests.json")
if acc.exists():
    acc_data = json.loads(acc.read_text())
    check("acceptance tests manifest valid", isinstance(acc_data, dict))
    check("acceptance tests has TASK_D", "TASK_D" in acc_data)

# Check API_CONTRACT.md
contract = Path("API_CONTRACT.md")
check("API_CONTRACT.md exists", contract.exists())
if contract.exists():
    c = contract.read_text()
    check("contract has platform requirements", "platform" in c.lower() or "iso" in c.lower() or "connection-per-operation" in c)
    check("contract has timestamps", "timestamp" in c.lower() or "iso" in c.lower())

# Summary
passed = sum(1 for _, c in results if c)
total = len(results)
print(f"\nCombined: {passed}/{total} passed")
for name, c in results:
    if not c:
        print(f"  FAIL: {name}")

# Save results
Path("output/s2_06_08_results.json").write_text(json.dumps({"passed": passed, "total": total, "tests": results}, indent=2))