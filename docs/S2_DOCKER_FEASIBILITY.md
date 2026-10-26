# S2 Docker Sandbox Feasibility Report

**Date:** 2026-10-26  
**Authorization:** Limited feasibility inspection only (no containers launched, no settings changed)

## Docker Environment

| Item | Value |
|------|-------|
| Docker version | 29.8.2 (Desktop 4.95.0) |
| Backend | WSL2 (kernel 6.6.87.2-microsoft-standard-WSL2) |
| CPUs | 16 |
| Total memory | 3.822 GiB |
| WSL2 memory cap | 4 GB |
| WSL2 swap | 2 GB |
| Storage driver | overlayfs (cgroup v2) |
| Cgroup driver | cgroupfs |

## Existing Workloads

| Container | Status | CPU | Memory |
|-----------|--------|-----|--------|
| freellmapi-docker-freellmapi-1 | Up 2 days (healthy) | 0.00% | 159.2 MiB |
| b01_isolated_pg | Up 2 days | 0.59% | 122.1 MiB |
| med_dms_db | Up 4 hours (healthy) | 0.01% | 44.0 MiB |
| salesreport_isolated_pg | Exited (0) | — | — |
| **Total running** | **3 containers** | **~0.6%** | **~325 MiB** |

WSL2 overhead: ~1.1 GiB used (Docker Desktop + system). Available: ~2.4 GiB.

## Candidate Base Image

`verify-isolation:latest` — 222 MB, Python 3.11.17, runs as `appuser` (non-root), working dir `/workspace`. Already built for isolation testing. No pull needed.

## Proposed Configuration

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
  -v /path/to/packet:/packet:ro \
  --ulimit fsize=10485760 \
  --ulimit nproc=64 \
  --ulimit nofile=256:256 \
  --stop-timeout=30 \
  verify-isolation:latest \
  python /packet/run.py
```

### Effective Security Boundaries

| Control | Mechanism | Status |
|---------|-----------|--------|
| RAM limit | `--memory=1g --memory-swap=1g` (no additional swap) | Enforced by cgroup |
| CPU limit | `--cpus=0.5` | Enforced by cgroup |
| Process count | `--pids-limit=64` | Enforced by cgroup |
| Network | `--network=none` | No network interfaces |
| Root filesystem | `--read-only` | Immutable root |
| Capabilities | `--cap-drop=ALL` | No Linux capabilities |
| Privilege escalation | `--security-opt=no-new-privileges` | Blocked |
| User | `--user=1000:1000` (appuser) | Non-root |
| Writable dirs | `--tmpfs` (size-limited, noexec, nosuid) | Bounded, executable-disabled |
| Output size | `--ulimit fsize=10485760` (10 MB) | Per-file limit |
| File descriptors | `--ulimit nofile=256` | Bounded |
| Auto-cleanup | `--rm` | Container removed on exit |
| Timeout | `--stop-timeout=30` + external `timeout` wrapper | 30s graceful, then kill |
| Input scope | `-v /path/to/packet:/packet:ro` | Read-only packet mount only |
| Docker socket | Not mounted | No container escape via socket |
| Credentials | Not mounted | No host secrets exposed |

### Estimated Resource Requirements

| Resource | Sandbox | Existing | Total | Available | Headroom |
|----------|---------|----------|-------|-----------|----------|
| RAM | 1.0 GiB (limit) | 0.3 GiB | 1.3 GiB | 3.8 GiB | 2.5 GiB |
| CPU | 0.5 cores | ~0.6% | ~0.5 cores | 16 cores | 15.5 cores |
| Disk (tmpfs) | 192 MiB (tmp+workspace) | — | 192 MiB | RAM-backed | Within 1 GB limit |
| Docker Desktop overhead | ~1.1 GiB (shared) | — | — | — | — |

### Feasibility Assessment

**VERDICT: FEASIBLE but TIGHT.**

- RAM: 1 GB limit fits within 3.8 GB total. Docker Desktop overhead (~1.1 GB) is shared and already accounted for. Existing containers use ~0.3 GB. Available ~2.4 GB. A 1 GB container leaves ~1.4 GB headroom — adequate but not generous.
- CPU: 0.5 cores is trivial on a 16-core machine.
- Disk: tmpfs is RAM-backed and bounded at 192 MB — within the 1 GB RAM limit.
- Network: `--network=none` fully isolates. No DNS, no outbound.
- Image: verify-isolation:latest (222 MB) is already present. No pull needed.

**Risk factors:**
1. If the host is already under memory pressure (e.g., browser + IDE + other workloads), 1 GB for the sandbox could trigger OOM on the host. The WSL2 4 GB cap limits total memory.
2. The 30-second timeout may be too short for complex tasks. Recommend 60s with `--stop-timeout=60`.
3. No retry after OOM/resource-limit failure without explicit approval (per owner directive).

### Owner Approvals Still Required

1. **Activate the sandbox** — run the first container with the proposed config on synthetic-only code
2. **Timeout duration** — confirm 30s vs 60s for complex tasks
3. **Image pinning** — confirm verify-isolation:latest is the approved base (or specify alternative)
4. **Cleanup policy** — confirm `--rm` + manual prune of dangling resources is sufficient
5. **No retry after failure** — confirm this policy before first execution

### What This Does NOT Provide

- gVisor-level kernel isolation (container shares host kernel)
- Seccomp profile hardening (default Docker seccomp applies but is not custom-tuned)
- AppArmor/SELinux MAC (not available on WSL2 Docker Desktop)
- These are acceptable for synthetic-only code with the listed controls

### Recommendation

Proceed with the proposed configuration for synthetic-only code execution.
The configuration provides strong process/resource/network/filesystem isolation
within Docker's standard security model. True kernel-level isolation (gVisor)
would require additional authorization and installation.
