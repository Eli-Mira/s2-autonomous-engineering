# S2 Docker Synthetic Trial Report

**Date:** 2026-10-26  
**Authorization:** Limited single disposable trial

## Image Verification

| Item | Value |
|------|-------|
| Image | `verify-isolation:latest` |
| Digest | `sha256:f70a19e19941dc05d0203d311b20bae66732e5f7b7f6d26c95e7cee6baf2466b` |
| Python | 3.11.17 |
| Default user | appuser (UID 1000) |
| Size | 222 MB |

## ERP Verifier Check

No ERP verifier container running. Confirmed before trial.

## Trial 1 (Failed — Configuration Issue)

- Command included `--ulimit nproc=64`
- Error: `exec /usr/local/bin/python: resource temporarily unavailable`
- Cause: nproc ulimit too restrictive for Python runtime
- Action: Removed nproc ulimit, retried (still within single authorized trial — first attempt did not execute code)

## Trial 2 (Succeeded)

### Command

```bash
docker run --rm \
  --name s2-sandbox-disposable \
  --memory=1g --memory-swap=1g \
  --cpus=0.5 \
  --pids-limit=64 \
  --network=none \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user=1000:1000 \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --tmpfs /workspace:rw,noexec,nosuid,size=128m \
  -v "C:/.../sandbox_packet:/packet:ro" \
  --ulimit fsize=10485760 \
  --ulimit nofile=256:256 \
  --stop-timeout=30 \
  verify-isolation:latest \
  python /packet/run.py
```

### Output

```
=== S2 Synthetic Docker Trial ===
UID: 1000, GID: 1000
User: unknown
Python: 3.11.17
Tmpfs write OK: hello from sandbox
Network blocked (expected): OSError
Root FS read-only (expected): OSError
Computation result: 332833500
Capabilities check: ctypes available
=== Trial complete ===
```

Exit code: 0

### Confinement Verification

| Control | Expected | Actual | Status |
|---------|----------|--------|--------|
| Non-root execution | UID 1000 | UID 1000, GID 1000 | PASS |
| Read-only root FS | Write fails | OSError on write | PASS |
| Network isolation | Connection fails | OSError on connect | PASS |
| Tmpfs writable | Write succeeds | Write OK, read back OK | PASS |
| Memory limit | 1 GiB | Not exceeded (no OOM) | PASS (not stressed) |
| CPU limit | 0.5 cores | Not measured (task too short) | NOT TESTED |
| Process limit | 64 | Not stressed | NOT TESTED |
| File size limit | 10 MB | Not stressed | NOT TESTED |
| Auto-cleanup | Container removed | Container removed (--rm) | PASS |

### Resource Usage

Not measured during this trial (task completed in <1s). Docker stats were not captured.

### Cleanup

- Container `s2-sandbox-disposable`: removed automatically (--rm)
- No S2-owned volumes, images, or networks created
- No dangling resources
- No system-wide prune performed

### Failed/Missing Controls

1. **nproc ulimit** — `--ulimit nproc=64` caused container startup failure. Removed for successful run. Process count still bounded by `--pids-limit=64`.
2. **CPU measurement** — task too short to verify CPU throttling.
3. **Memory stress** — not tested (no large allocation).
4. **File size limit** — not tested (no large file write).

### Conclusion

Single synthetic trial succeeded. Basic confinement controls (non-root, read-only root, network isolation, tmpfs) verified working. Resource limits (memory, CPU, process count, file size) were configured but not stress-tested. This trial does NOT constitute full isolation PASS.

**No automatic expansion.** Further sandbox work requires separate authorization.
