# S2 Blocker Ledger

## BLOCKER-001: Execution Confinement (Docker/gVisor)
- **Milestone:** S2-01 / S2-02
- **Status:** BLOCKED (true isolation); MOCK LAYER PASS
- **Requirement:** Privileged Docker daemon access or WSL2/gVisor installation
- **Why it matters:** Model-generated code currently executes in host Python process without true isolation
- **Evidence:** Temp directories provide file-scope isolation only; no process/container sandbox
- **Mock layer achieved:** `isolation/DESIGN.md`, `run_isolated.py`, `test_confinement.py` (9 pass, 1 skip)
- **Mock layer limitation:** Mock tests verify design intent. True process/filesystem/network confinement NOT VERIFIED. Negative tests against actual security boundary NOT COMPLETED.
- **Safe work completed:** Isolation design document, adapter scaffold, mock tests
- **Next action after resolution:** Run confinement negative tests (S2-02) against true boundary, then Benchmarks B-H

## BLOCKER-002: Owner License Decision
- **Milestone:** S2-11
- **Status:** BLOCKED
- **Requirement:** Owner approval for open-source license selection
- **Why it matters:** Cannot publish without license
- **Safe work completed:** License review of dependencies, sanitized export preparation
- **Next action after resolution:** Finalize release candidate

## BLOCKER-003: Production Scheduler Authorization
- **Milestone:** S2-05
- **Status:** DEFERRED
- **Requirement:** Explicit owner authorization for unattended execution
- **Why it matters:** Cannot activate production scheduling
- **Safe work completed:** Scheduler lifecycle documented
- **Next action after resolution:** Activate and test production scheduler