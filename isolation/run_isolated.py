#!/usr/bin/env python3
"""Isolated execution wrapper for autonomous-integration.

Usage:
    python run_isolated.py <script_or_command> [args...]

Sets up a confined environment (restricted cwd, limited env, resource caps)
and runs the target inside it. All output lands in docs/ evidence.
"""

import os
import sys
import subprocess
import tempfile
import shutil
import time
from pathlib import Path

# --- Configuration ---
ALLOWED_ENV_KEYS = frozenset({
    "PATH", "PYTHONPATH", "PYTHONIOENCODING", "HOME", "TEMP", "TMP",
    "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT", "COMSPEC",
})
TIMEOUT_SECONDS = 300
WORK_ROOT = Path(tempfile.gettempdir()) / "autonomous-integration-sandbox"


def build_env():
    """Return a sanitised environment dict with only whitelisted keys."""
    return {k: v for k, v in os.environ.items() if k in ALLOWED_ENV_KEYS}


def run_isolated(argv: list[str]) -> dict:
    """Run command with restricted environment and resource limits."""
    sandbox = WORK_ROOT / f"run-{int(time.time())}"
    sandbox.mkdir(parents=True, exist_ok=True)
    env = build_env()
    env["HOME"] = str(sandbox)
    env["TEMP"] = str(sandbox)
    env["TMP"] = str(sandbox)

    start = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=str(sandbox),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        elapsed = time.monotonic() - start
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-8000:],
            "elapsed_s": round(elapsed, 2),
            "sandbox": str(sandbox),
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -9,
            "stdout": "",
            "stderr": f"TIMEOUT after {TIMEOUT_SECONDS}s",
            "elapsed_s": TIMEOUT_SECONDS,
            "sandbox": str(sandbox),
        }
    finally:
        # Cleanup sandbox after run
        shutil.rmtree(sandbox, ignore_errors=True)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    outcome = run_isolated(sys.argv[1:])
    print(f"[exit={outcome['returncode']}] elapsed={outcome['elapsed_s']}s")
    if outcome["stdout"]:
        print("--- stdout ---")
        print(outcome["stdout"])
    if outcome["stderr"]:
        print("--- stderr ---")
        print(outcome["stderr"])
    sys.exit(outcome["returncode"])


if __name__ == "__main__":
    main()
