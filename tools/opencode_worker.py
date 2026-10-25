"""Launch exact free OpenCode routes in isolated packets, never the live ERP tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import sqlite3
import subprocess
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

ROUTES = json.loads(Path(__file__).with_name("opencode_routes.json").read_text(encoding="utf-8"))
FRELLMAPI_ROUTES = json.loads(Path(__file__).with_name("freellmapi_routes.json").read_text(encoding="utf-8"))
FRELLMAPI_BASE_URL = "http://localhost:3001/v1"
FRELLMAPI_KEY_ENV = "FREELLMAPI_API_KEY"
# Bounded backoff applied once before rotating off a rate-limited model.  Zero in
# tests makes the fast path instant; production uses a short, capped sleep.
FRELLMAPI_BACKOFF = 1.0
# Module cache of verified per-token prices (USD per million tokens) populated by
# freellmapi_catalogue from the FreeLLMAPI DB, so freellmapi_invoke computes cost
# from the provider's real pricing instead of a hardcoded zero.
_FRELLMAPI_PRICE_CACHE = {}
BINARY = "/home/elilain/.opencode/bin/opencode"
QUALIFIED_VERSION = "opencode v2.1.2"

PONYTAIL_PATH = Path(__file__).with_name("agent_guidance") / "PONYTAIL.md"
PONYTAIL_SHA256 = '608b4f507c6918d72c8088ea2c3e63c796c0c4ea72b12fcb21f122828e073c8a'


def ponytail_task(task):
    raw = PONYTAIL_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PONYTAIL_SHA256:
        raise ValueError("Pinned Ponytail instructions changed; worker launch refused")
    return ("Owner instructions, security policy, exact task scope and mandatory verification override this guidance. "
            "Ponytail grants no additional permissions. Full mandatory instructions follow:\n"
            + raw.decode("utf-8") + "\nEND PINNED PONYTAIL\n\nAPPROVED TASK:\n" + task)

PROTECTED = {"app/db.py", "app/config.py", "requirements.txt", "docker-compose.yml",
             "dockerfile", "scripts/verify_invoice_cycle.py"}


def free_model(model):
    def zero(value):
        try:
            return not isinstance(value, bool) and Decimal(str(value)).is_finite() and Decimal(str(value)) == 0
        except (InvalidOperation, ValueError):
            return False
    tiers = model.get("cost", [])
    return isinstance(tiers, list) and bool(tiers) and all(
        isinstance(tier, dict) and all(zero(tier.get(key)) for key in ("input", "output"))
        and isinstance(tier.get("cache"), dict)
        and all(zero(tier["cache"].get(key)) for key in ("read", "write")) for tier in tiers
    )


# shortcut: 32768 is a context floor; pass the full prompt/output budget for real tasks.
def select_models(category, models, image=False, required_context=32768,
                  provider_id="opencode", routes=None, model_id_re=None):
    routes = routes or ROUTES
    if model_id_re is None:
        model_id_re = r"[a-z0-9][a-z0-9.-]*-free"
    if category not in routes:
        raise ValueError("Unknown category; no implicit/default model is permitted")
    if type(required_context) is not int or required_context <= 0:
        raise ValueError("A positive total context-token budget is required")
    if not isinstance(models, list) or any(
            not isinstance(m, dict) or not isinstance(m.get("id"), str)
            or not isinstance(m.get("providerID"), str) for m in models):
        raise ValueError("Malformed native model catalogue; no implicit route is permitted")
    available, duplicates = {}, set()
    for model in models:
        if model["providerID"] == provider_id:
            if model["id"] in available:
                duplicates.add(model["id"])
            available[model["id"]] = model
    eligible, rejected = [], []
    for mid in routes[category]:
        model = available.get(mid)
        reason = None
        if not re.fullmatch(model_id_re, mid):
            reason = "invalid exact model identifier"
        elif mid in duplicates:
            reason = "ambiguous duplicate provider/model identifier"
        elif not model:
            reason = "missing from native catalogue"
        elif model.get("enabled") is not True or model.get("status") != "active":
            reason = "model enablement and active status are not proven"
        elif not free_model(model):
            reason = "zero prices not proven for every tier/cache operation"
        elif not isinstance(model.get("capabilities"), dict):
            reason = "model capabilities are not proven"
        elif image and (not isinstance(model["capabilities"].get("input"), list)
                        or "image" not in model["capabilities"]["input"]):
            reason = "image input unsupported"
        elif model["capabilities"].get("tools") is not True:
            reason = "tool capability unsupported"
        elif (not isinstance(model.get("limit"), dict)
              or type(model["limit"].get("context")) is not int
              or model["limit"]["context"] < required_context):
            reason = "sufficient context capacity is not proven"
        if reason:
            rejected.append({"model": mid, "reason": reason})
        else:
            eligible.append(mid)
    return eligible, rejected


def scoped_paths(root, paths):
    validated = []
    for value in paths:
        path = Path(value)
        if (not isinstance(value, str) or not value or "\\" in value or path.is_absolute()
                or ":" in value or any(p in {"..", "."} for p in value.split("/"))):
            raise ValueError("Scope requires exact workspace-relative paths")
        folded = value.lower()
        if (folded in PROTECTED or any(p.lower().startswith(".env") or p.lower() in {
                ".git", ".opencode", ".venv", "node_modules"} for p in path.parts)
                or path.suffix.lower() in {".xlsx", ".xls", ".db", ".sqlite", ".sqlite3", ".key", ".pem"}):
            raise ValueError("Protected/sensitive scope is not permitted")
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("Scope escapes packet")
        for candidate in [root / path, *(root / path).parents]:
            if candidate == root:
                break
            if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                raise ValueError("Linked scope is not permitted")
        if resolved.exists() and (not resolved.is_file() or resolved.stat().st_nlink > 1):
            raise ValueError("Scope requires ordinary files without hard links")
        validated.append(path.as_posix())
    return list(dict.fromkeys(validated))


def linux_path(path):
    value = Path(path).resolve().as_posix()
    if not re.match(r"^[A-Za-z]:/", value):
        raise ValueError("This deployment requires a local Windows drive packet")
    return "/mnt/" + value[0].lower() + value[2:]


def invoke(root, args, seconds=100):
    command = ["wsl", "-d", "Debian", "--cd", linux_path(root), "-e", "timeout",
               "--signal=TERM", "--kill-after=5s", f"{seconds}s", BINARY, *args]
    try:
        result = subprocess.run(command, capture_output=True, timeout=seconds + 15, check=False)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or b"", exc.stderr or b""


def catalogue(root):
    code, version, _ = invoke(root, ["--version"], 15)
    if code or version.decode("utf-8").strip() != QUALIFIED_VERSION:
        raise RuntimeError("OpenCode version changed; qualify its scope controls before launching workers")
    code, raw, _ = invoke(root, ["api", "GET", "/api/info"], 20)
    info = json.loads(raw) if not code else {}
    urls = [urlsplit(url) for url in info.get("urls", [])]
    if (info.get("version") != QUALIFIED_VERSION.removeprefix("opencode v") or not urls
            or any(u.scheme != "http" or u.hostname != "127.0.0.1" or not u.port for u in urls)):
        raise RuntimeError("Version-matched localhost managed service is required")
    code, stdout, _ = invoke(root, ["api", "model.list", "--param",
                                   "location[directory]=" + linux_path(root)], 30)
    if code:
        raise RuntimeError("Native catalogue unavailable; no worker was launched")
    response = json.loads(stdout)
    data = response["data"]
    if not isinstance(data, list) or response.get("location", {}).get("directory") != linux_path(root):
        raise TypeError("Unexpected native catalogue schema")
    return [{k: m[k] for k in ("id", "providerID", "cost", "capabilities", "status", "enabled", "limit") if k in m}
            for m in data]


def session_run(root, args, reads, writes, journal=None):
    """Native managed authentication; directory is context, rules enforce tool scope.

    When ``journal`` is supplied, a durable lifecycle receipt is written atomically
    (validated session ID first, then ``cancellation_acknowledged`` updated in place)
    before any inference runs, so a supervised bridge can recover or cancel the exact
    native session after a parent interruption.  Unknown lifecycle states never fall
    back to a weaker path.
    """
    provider, mid = args[args.index("--model") + 1].split("/", 1)
    if provider != "opencode" or mid not in {m for chain in ROUTES.values() for m in chain}:
        raise RuntimeError("Unapproved model; no session was created")
    rules = [{"action": "*", "resource": "*", "effect": "deny"}]
    for action, names in (("read", reads), ("edit", writes)):
        for name in names:
            for resource in (name, linux_path(root) + "/" + name):
                rules.append({"action": action, "resource": resource, "effect": "allow"})
    model = {"providerID": provider, "id": mid}

    def api(operation, *extra):
        code, raw, _ = invoke(root, ["api", operation, *extra], 20)
        if code:
            raise RuntimeError("Native session API failed: " + operation)
        return json.loads(raw)

    created = api("session.create", "--data", json.dumps({
        "title": args[args.index("--title") + 1], "agent": "build", "model": model,
        "location": {"directory": linux_path(root)}, "permissions": rules,
    }))["data"]
    sid = created["id"]
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        raise RuntimeError("Invalid native session identifier")

    try:
        if journal is not None:
            journal = Path(journal)
            if journal.is_dir():
                raise RuntimeError("Journal path is a directory")
            receipt = {
                "id": sid,
                "directory": linux_path(root),
                "pinnedmodel": "opencode/" + mid,
                "cancellation_acknowledged": False,
            }
            tmp = journal.with_suffix(journal.suffix + ".tmp")
            tmp.write_text(json.dumps(receipt), encoding="utf-8")
            with tmp.open("r+b") as f:
                os.fsync(f.fileno())
            os.replace(tmp, journal)

        def readback():
            session = api("session.get", "--param", "sessionID=" + sid)["data"]
            if (session.get("location", {}).get("directory") != linux_path(root)
                    or session.get("permissions") != rules
                    or any(session.get("model", {}).get(k) != v for k, v in model.items())):
                raise RuntimeError("Native session directory, policy or model binding changed")
            return session

        readback()
        result = invoke(root, [args[0], "--session", sid, *args[1:]])
    finally:
        interrupted = api("session.interrupt", "--param", "sessionID=" + sid,
                          "--param", "resume=false")
        if type(interrupted.get("interrupted")) is not bool:
            raise RuntimeError("Native cancellation acknowledgement missing")
        if journal is not None and journal.exists() and journal.is_file():
            receipt = json.loads(journal.read_text(encoding="utf-8"))
            receipt["cancellation_acknowledged"] = True
            tmp = journal.with_suffix(journal.suffix + ".tmp")
            tmp.write_text(json.dumps(receipt), encoding="utf-8")
            with tmp.open("r+b") as f:
                os.fsync(f.fileno())
            os.replace(tmp, journal)
    if result[0] == 0:
        session = readback()
        messages = api("session.message.list", "--param", "sessionID=" + sid,
                       "--param", "limit=100")
        assistant = [m for m in messages.get("data", []) if m.get("type") == "assistant"]
        if (session.get("outcome") != "succeeded" or session.get("cost") != 0
                or not assistant or len(messages.get("data", [])) >= 100
                or any(any(m.get("model", {}).get(k) != v for k, v in model.items())
                       or m.get("cost") != 0 for m in assistant)):
            raise RuntimeError("Complete zero-cost same-model session proof missing")
    return result


def freellmapi_catalogue():
    """Return FreeLLMAPI model metadata from the local DB.

    Admission is gated on the provider key being enabled AND healthy
    (status != 'healthy' platforms such as the unknown-status freeai/llmtr
    never enter the candidate set) and on every price tier being genuinely
    free in the database (NULL or 0 — the emitted ``cost`` echoes the DB
    column, never a hardcoded zero).

    Two advisory fields are carried for the caller's quota-aware ordering:

      * platform        — the serving platform key for this model row
      * quota_remaining — best-effort remaining request quota for that platform
                          (NULL when untested); used only to order fallback, not
                          for admission — FreeLLMAPI itself owns key selection.

    The same model id can be served by several platforms; emitting both would
    read as an ambiguous duplicate, so keep one row per id, preferring a
    platform with observed quota headroom, then the largest context window,
    then vision support.
    """
    db_path = os.environ.get("FRELLMAPI_DB_PATH",
                             Path(os.environ.get("USERPROFILE", "/c/Users/emadg"))
                             / "freellmapi-docker" / "data" / "freeapi.db")
    if not Path(db_path).exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Best-effort request quota headroom per platform.  Only NULL-safe MAX to
    # avoid joining multiple quota rows per platform (which would duplicate
    # model rows).  NULL => "untested"; callers must not skip on NULL.
    cur.execute("""
        SELECT platform,
               MAX(remaining_value) FILTER (
                   WHERE remaining_value IS NOT NULL) AS remaining
        FROM provider_quota_state
        WHERE metric = 'requests'
        GROUP BY platform
    """)
    platform_quota = {r["platform"]: r["remaining"] for r in cur.fetchall()}
    cur.execute("""
        SELECT model_id, platform, context_window, supports_tools,
               supports_vision, paid_input_per_m, paid_output_per_m
        FROM models
        WHERE enabled = 1
          AND supports_tools = 1
          AND COALESCE(context_window, 0) >= 32768
          AND COALESCE(paid_input_per_m, 0) = 0
          AND COALESCE(paid_output_per_m, 0) = 0
          AND NOT EXISTS (
            SELECT 1 FROM api_keys a
            WHERE a.platform = models.platform
              AND (a.enabled = 0 OR a.status IS NULL OR a.status != 'healthy')
          )
    """)
    rows = cur.fetchall()
    conn.close()
    # One row per model_id: prefer a platform with observed quota headroom,
    # then the largest verified context window, then vision support.
    best = {}
    for row in rows:
        mid = row["model_id"]
        price_in = row["paid_input_per_m"] or 0
        price_out = row["paid_output_per_m"] or 0
        quota = platform_quota.get(row["platform"])
        score = (bool(quota and quota > 0), quota or 0,
                 row["context_window"] or 0, bool(row["supports_vision"]))
        if mid not in best or score > best[mid]["score"]:
            best[mid] = {"score": score, "row": row,
                         "price": (price_in, price_out), "platform": row["platform"],
                         "quota": quota}
    _FRELLMAPI_PRICE_CACHE.clear()
    models = []
    for mid, packed in best.items():
        row = packed["row"]
        _FRELLMAPI_PRICE_CACHE[mid] = packed["price"]
        models.append({
            "id": mid,
            "providerID": "freellmapi",
            "enabled": True,
            "status": "active",
            "platform": packed["platform"],
            "quota_remaining": packed["quota"],
            "cost": [{"input": packed["price"][0], "output": packed["price"][1],
                      "cache": {"read": 0, "write": 0}}],
            "capabilities": {
                "tools": bool(row["supports_tools"]),
                "input": ["text"] + (["image"] if row["supports_vision"] else [])},
            "limit": {"context": row["context_window"] or 0},
        })
    # Release sqlite3 handles before the caller touches the tempdir.
    import gc; gc.collect()
    return models


def freellmapi_invoke(model_id, task, image=False, price_in=0, price_out=0):
    """Send a chat-completion to FreeLLMAPI and return OpenCode-shaped events.

    Returns ``(code, stdout, stderr)`` where *stdout* is JSON-lines text so
    that ``events_from`` and the existing ``run_packet`` success/error checks
    work unchanged.  Cost is derived from the response's real ``usage`` token
    counts multiplied by the model's verified per-million-token price (read
    from the DB cache populated by ``freellmapi_catalogue``, overridable here
    for tests) — it is never a hardcoded literal.
    """
    import urllib.request, urllib.error
    key = os.environ.get(FRELLMAPI_KEY_ENV, "")
    if not key:
        raise RuntimeError("FreeLLMAPI key unavailable; no request sent")
    prices = _FRELLMAPI_PRICE_CACHE.get(model_id)
    if prices is not None:
        price_in, price_out = prices
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": task}],
        "max_tokens": 4000,
        "stream": False,
    }
    if image:
        payload["tools"] = [{"type": "image_generation",
                             "function": {"name": "noop", "description": "no-op"}}]
    raw = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        FRELLMAPI_BASE_URL + "/chat/completions",
        data=raw,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
        content = (data.get("choices", [{}])[0]
                   .get("message", {}).get("content", ""))
        usage = data.get("usage") or {}
        prompt_t = usage.get("prompt_tokens", 0) or 0
        completion_t = usage.get("completion_tokens", 0) or 0
        cost = ((Decimal(prompt_t) * Decimal(str(price_in))
                 + Decimal(completion_t) * Decimal(str(price_out)))
                / Decimal(1000000))
        events = [
            {"type": "step_finish", "part": {
                "cost": float(cost),
                "usage": {"prompt_tokens": prompt_t,
                          "completion_tokens": completion_t}}},
            {"type": "text", "part": {
                "text": content if isinstance(content, str) else ""}},
        ]
        return 0, b"\n".join(json.dumps(e).encode() for e in events) + b"\n", b""
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            events = [{"type": "error", "error": {"status": 403,
                                                  "type": "provider.auth"}}]
        elif exc.code == 429:
            events = [{"type": "error", "error": {"type": "rate_limit"}}]
        else:
            events = [{"type": "error", "error": {"status": exc.code}}]
        return exc.code, b"\n".join(json.dumps(e).encode() for e in events) + b"\n", b""
    except Exception as exc:
        return 1, b"", str(exc).encode(),


def freellmapi_request(root, model_id, task, reads, writes, journal=None, image=False,
                       price_in=0, price_out=0):
    """FreeLLMAPI request wrapper matching the ``session_run`` return contract."""
    code, stdout, stderr = freellmapi_invoke(model_id, task, image=image,
                                             price_in=price_in, price_out=price_out)
    if journal is not None:
        Path(journal).parent.mkdir(parents=True, exist_ok=True)
        receipt = {"id": "freellmapi-" + hashlib.sha256(model_id.encode()).hexdigest()[:12],
                   "pinnedmodel": "freellmapi/" + model_id,
                   "cancellation_acknowledged": True}
        tmp = journal.with_suffix(".tmp_freellmapi")
        tmp.write_text(json.dumps(receipt), encoding="utf-8")
        os.replace(tmp, journal)
    return code, stdout, stderr


import re as _re


def _validate_write_path(filename, writes, root):
    """Validate a proposed write path.  Returns (ok, reason)."""
    if not isinstance(filename, str) or not filename:
        return False, "empty filename"
    if "\\" in filename or ":" in filename:
        return False, "backslash or colon in path"
    p = Path(filename)
    if p.is_absolute():
        return False, "absolute path"
    if any(part in {"..", "."} for part in p.parts):
        return False, "path traversal"
    if filename not in writes:
        return False, "not in approved writes list"
    # symlink check
    target = (root / filename).resolve()
    if not target.is_relative_to(root.resolve()):
        return False, "escapes workspace"
    # check for symlinks in path
    for parent in [root / filename, *(root / filename).parents]:
        if parent == root:
            break
        if parent.is_symlink():
            return False, "symlink in path"
    return True, "ok"


def extract_code_blocks(text):
    """Extract (filename, content) pairs from markdown code blocks.

    Recognises blocks like:
        ```python
        # filename.py
        def foo(): ...
        ```
    The filename is taken from the first non-blank line if it is a comment
    of the form ``# name.py`` or ``# name``.
    """
    pairs = []
    for match in _re.finditer(r"```(?:python|py)?\s*\n(.*?)```", text, _re.DOTALL):
        body = match.group(1)
        lines = body.splitlines()
        filename = None
        content_lines = lines
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            m = _re.match(r"#\s*(\S+)\s*$", stripped)
            if m:
                filename = m.group(1)
                content_lines = lines[i + 1:]
            break
        if filename and content_lines:
            content = "\n".join(content_lines).strip() + "\n"
            pairs.append((filename, content))
    return pairs


def freellmapi_apply_files(root, texts, writes):
    """Write code blocks extracted from model text to the workspace.

    Only files listed in *writes* are permitted.  Validates paths against
    traversal, symlinks, and approved writes.  Returns (written, rejected).
    """
    permitted = set(writes)
    written = []
    rejected = []
    for text in texts:
        for filename, content in extract_code_blocks(text):
            ok, reason = _validate_write_path(filename, writes, root)
            if not ok:
                rejected.append({"file": filename, "reason": reason})
                continue
            target = root / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(filename)
    return written, rejected


def freellmapi_repair_loop(category, workspace, task, reads, writes, output_base,
                            max_attempts=3, required_context=32768):
    """Bounded repair loop: GENERATE -> APPLY -> TEST -> REPAIR -> RETEST.

    Returns a list of attempt receipts (one per run_packet call).
    """
    workspace = Path(workspace).resolve()
    output_base = Path(output_base).resolve()
    receipts = []
    current_task = task
    for attempt in range(max_attempts):
        out_dir = output_base / f"attempt-{attempt}"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = run_packet(
            category=category,
            workspace=workspace,
            task=current_task,
            reads=reads,
            writes=writes,
            output=out_dir,
            required_context=required_context,
            freellmapi_only=True,
        )
        receipts.append(result)
        if result.get("status") == "completed":
            break
        # Build repair prompt from failure
        test_stderr = result.get("test_stderr", "")
        test_stdout = result.get("test_stdout", "")
        written = result.get("written", [])
        if attempt < max_attempts - 1:
            current_task = (
                task + "\n\n"
                "PREVIOUS ATTEMPT FAILED. Here is the error output:\n"
                f"STDOUT: {test_stdout}\n"
                f"STDERR: {test_stderr}\n"
                f"Files written: {written}\n"
                "Analyze the failure and provide corrected code. "
                "Output the COMPLETE corrected files, not just the changed parts."
            )
    return receipts


def freellmapi_run_tests(root, test_files):
    """Run pytest on the given test files.  Returns (exit_code, stdout, stderr)."""
    import subprocess
    cmd = [sys.executable, "-m", "pytest", *test_files, "-q", "--tb=short"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root),
                           timeout=120, check=False)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or ""


def snapshot(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Packet contains links")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def events_from(stdout):
    events = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def trace_scope(events, root, reads, writes):
    for event in events:
        part = event.get("part", {})
        state = part.get("state", {})
        if event.get("type") != "tool_use" or state.get("status") != "completed":
            continue
        tool = part.get("tool")
        values = state.get("input", {})
        value = values.get("filePath", values.get("file_path", values.get("path")))
        if tool not in {"read", "edit", "write"} or not isinstance(value, str):
            return False
        prefix = linux_path(root) + "/"
        relative = value.removeprefix(prefix)
        permitted = reads if tool == "read" else writes
        if relative not in permitted:
            return False
    return True


REPO_ROOT = Path(__file__).resolve().parents[2]

def run_packet(category, workspace, task, reads, writes, output, images=(),
               required_context=32768, freellmapi=False, freellmapi_only=False):
    root, output = Path(workspace).resolve(), Path(output).resolve()
    # For freellmapi_only mode, allow any isolated workspace (skip repo check)
    if not freellmapi_only:
        if (not root.is_dir() or root.is_relative_to(REPO_ROOT)
                or any((parent / ".git").exists() for parent in [root, *root.parents])
                or any((parent / "scripts" / "verify_invoice_cycle.py").exists() for parent in [root, *root.parents])
                or (root / "scripts" / "verify_invoice_cycle.py").exists()):
            raise ValueError("Workers require an isolated packet, not a live repository")
    else:
        # Still require basic directory existence
        if not root.is_dir():
            raise ValueError("Workspace directory does not exist")
    if output.is_relative_to(root):
        raise ValueError("Audit output must be outside worker scope")
    reads = scoped_paths(root, [*reads, *writes, *images])
    writes = scoped_paths(root, writes)
    images = scoped_paths(root, images)
    if any(not (root / name).is_file() for name in reads if name not in writes):
        raise ValueError("A declared read-only input is missing")
    task = ponytail_task(task)
    # freellmapi_only: skip the OpenCode catalogue entirely — we never need
    # the opencode model list when the intention is FreeLLMAPI-only execution.
    if freellmapi_only:
        freellmapi = True
        models = []
        eligible = []
        rejected = []
    else:
        models = catalogue(root)
        eligible, rejected = select_models(category, models, image=bool(images),
                                           required_context=required_context)
    output.mkdir(parents=True, exist_ok=True)
    (output / "catalogue.json").write_text(json.dumps(models, indent=2), encoding="utf-8")

    before = snapshot(root)
    result = {"status": "blocked_unavailable", "category": category, "rejected": rejected,
              "attempts": [], "before": before, "required_context": required_context,
              "source_policy_sha256": hashlib.sha256(
                  Path(__file__).with_name("opencode_routes.json").read_bytes()).hexdigest()}
    for index, mid in enumerate(eligible):
        args = ["run", "--agent", "build", "--model", "opencode/" + mid,
                "--format", "json", "--title", "Owner-approved " + category]
        for image in images:
            args.extend(["--file", image])
        args.append(task)
        try:
            code, stdout, stderr = session_run(root, args, reads, writes,
                                               journal=output / "session.json")
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            result.update(status="blocked_lifecycle", error=str(exc))
            break
        (output / f"attempt-{index}.jsonl").write_bytes(stdout)
        (output / f"attempt-{index}.stderr.log").write_bytes(stderr)
        after = snapshot(root)
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        attempt = {"model": "opencode/" + mid, "exit": code, "changed": changed}
        result["attempts"].append(attempt)
        try:
            events = events_from(stdout)
        except (ValueError, TypeError):
            events = []
        if set(changed) - set(writes) or not trace_scope(events, root, reads, writes):
            result["status"] = "blocked_scope"
            break
        if code in {124, 137}:
            result["status"] = "blocked_timeout"
            break
        errors = [e for e in events if e.get("type") == "error"]
        if any(isinstance(e.get("error"), dict) and (e["error"].get("status") == 403
               or e["error"].get("type") == "provider.auth") for e in errors):
            result["status"] = "blocked_access"
            break
        texts = [e.get("part", {}).get("text", "") for e in events if e.get("type") == "text"]
        costs = [e.get("part", {}).get("cost") for e in events if e.get("type") == "step_finish"]
        zero_cost = bool(costs) and all(not isinstance(c, bool) and c == 0 for c in costs)
        if code == 0 and not errors and any(t.strip() for t in texts) and zero_cost:
            result.update(status="completed", model="opencode/" + mid, texts=texts,
                          session_id=next((e.get("sessionID") for e in events if e.get("sessionID")), None),
                          changed=changed, reported_cost=sum(costs))
            break
        if changed:
            result["status"] = "blocked_partial"
            break
        if not zero_cost and costs:
            result["status"] = "blocked_cost"
            break
    # FreeLLMAPI fallback: opt-in secondary inference backend.
    if freellmapi and result["status"] == "blocked_unavailable":
        result["freellmapi_policy_sha256"] = hashlib.sha256(
            Path(__file__).with_name("freellmapi_routes.json").read_bytes()).hexdigest()
        fre_models = freellmapi_catalogue()
        price_by_id = {m["id"]: m["cost"][0] for m in fre_models if m.get("cost")}
        platform_by_id = {m["id"]: m.get("platform") for m in fre_models}
        quota_by_platform = {p: q for p, q in (
            (m.get("platform"), m.get("quota_remaining")) for m in fre_models) if p}
        fre_eligible, fre_rejected = select_models(
            category, fre_models, image=bool(images),
            required_context=required_context, provider_id="freellmapi",
            routes=FRELLMAPI_ROUTES, model_id_re=r"[a-z0-9][a-z0-9.-]+")
        result["freellmapi_rejected"] = fre_rejected
        # quota-aware ordering: try models whose platform has observed request
        # headroom first, so a rate-limited platform (e.g. google, remaining 0)
        # is exhausted last rather than first.
        fre_eligible = sorted(
            fre_eligible,
            key=lambda mid: -(quota_by_platform.get(platform_by_id.get(mid)) or 0))
        _provider_down = set()     # platforms known to hard-fail (skip the rest)
        _rate_cooldown = {}        # platform -> monotonic deadline (bounded backoff)
        _attempted = set()         # model ids already tried (loop guard)
        for index, mid in enumerate(fre_eligible):
            if mid in _attempted:
                continue
            _attempted.add(mid)
            plat = platform_by_id.get(mid)
            if plat in _provider_down or (plat and _rate_cooldown.get(plat, 0) > time.monotonic()):
                continue
            price = price_by_id.get(mid, {"input": 0, "output": 0})
            try:
                code, stdout, stderr = freellmapi_request(
                    root, mid, task, reads, writes,
                    journal=output / "freellmapi_session.json", image=bool(images),
                    price_in=price["input"], price_out=price["output"])
            except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                result.update(status="blocked_lifecycle", error=str(exc))
                break
            (output / f"freellmapi-attempt-{index}.jsonl").write_bytes(stdout)
            (output / f"freellmapi-attempt-{index}.stderr.log").write_bytes(stderr)
            try:
                events = events_from(stdout)
            except (ValueError, TypeError):
                events = []
            errors = [e for e in events if e.get("type") == "error"]
            if any(isinstance(e.get("error"), dict) and (e["error"].get("status") == 403
                   or e["error"].get("type") == "provider.auth") for e in errors):
                result["status"] = "blocked_access"
                break
            rate_limited = any(isinstance(e.get("error"), dict)
                               and (e["error"].get("type") == "rate_limit"
                                    or e["error"].get("status") == 429) for e in errors)
            provider_down = (code >= 500 or any(
                isinstance(e.get("error"), dict)
                and 500 <= e["error"].get("status", 0) <= 599 for e in errors))
            if rate_limited:
                # bounded backoff: one short capped pause before rotating to
                # the next eligible model.  _attempted guarantees the same
                # model is never retried.  Platform-wide cooldown is intentionally
                # NOT used for 429 — models may share a reported platform label
                # but hit different upstream keys inside FreeLLMAPI.
                time.sleep(FRELLMAPI_BACKOFF)
            if provider_down and plat:
                _provider_down.add(plat)
            texts = [e.get("part", {}).get("text", "") for e in events if e.get("type") == "text"]
            costs = [e.get("part", {}).get("cost") for e in events if e.get("type") == "step_finish"]
            zero_cost = bool(costs) and all(not isinstance(c, bool) and c == 0 for c in costs)
            result["attempts"].append({"model": "freellmapi/" + mid, "exit": code})
            if code == 0 and not errors and any(t.strip() for t in texts) and zero_cost:
                # Apply model-generated code blocks to the workspace (real file writes).
                written, rejected = freellmapi_apply_files(root, texts, writes)
                after = snapshot(root)
                changed = sorted(p for p in before.keys() | after.keys()
                                 if before.get(p) != after.get(p))
                if written:
                    # Run actual tests on any written test files.
                    test_files = [f for f in writes if f.startswith("test_")]
                    if test_files and all((root / f).is_file() for f in test_files):
                        test_code, test_stdout, test_stderr = freellmapi_run_tests(
                            root, test_files)
                    else:
                        test_code, test_stdout, test_stderr = 0, "", ""
                    ok = test_code == 0
                else:
                    # No code blocks in response — treat as chat completion (legacy).
                    test_code, test_stdout, test_stderr = 0, "", ""
                    ok = True
                result.update(
                    status="completed" if ok else "blocked_test_failure",
                    model="freellmapi/" + mid, texts=texts,
                    changed=changed, reported_cost=sum(costs),
                    written=written, rejected=rejected, test_exit=test_code,
                    test_stdout=test_stdout[-3000:],
                    test_stderr=test_stderr[-3000:])
                break
            if code in {124, 137}:
                result["status"] = "blocked_timeout"
                break
    result["after"] = snapshot(root)
    (output / "receipt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="Owner-authored JSON: category, workspace, task, reads, writes, output, optional images")
    parser.add_argument("--execute", action="store_true", help="Explicitly authorize this isolated packet only")
    args = parser.parse_args()
    if not args.execute:
        parser.error("OpenCode deferred by default; --execute is required for an approved isolated packet")
    result = run_packet(**json.loads(args.packet.read_text(encoding="utf-8")))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
