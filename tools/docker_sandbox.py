"""S2 Docker Sandbox Adapter

Minimal disposable Docker execution backend for the S2 autonomous coding worker.

Preflight -> verify image -> copy packet -> run tests -> capture output ->
effective security verification -> cleanup.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Literal

IMAGE = "verify-isolation:latest"
WORKSPACE = "C:/Users/emadg/AppData/Local/hermes/cache/scratch/autonomous-integration"
S2_CONTAINER_LABEL = "s2.sandbox=disposable"
S2_PACKET_PREFIX = "s2-packet-"


@dataclass
class SandboxResult:
    status: Literal["completed", "blocked", "failed"]
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    runtime: dict = field(default_factory=dict)
    security: dict = field(default_factory=dict)
    image_id: str = ""


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _preflight() -> dict:
    """Host health preflight. Returns dict with status and details."""
    result = {
        "docker_available": _docker_available(),
        "erp_verifier_running": False,
        "s2_containers_running": 0,
        "available_mem_mb": 0,
        "minimum_met": False,
    }

    if not result["docker_available"]:
        return result

    # ERP verifier
    ps = _run(["docker", "ps", "--format", "{{.Names}}"])
    result["erp_verifier_running"] = any(
        name.lower() in {"erp", "verifier", "med_dms", "b01"} for name in ps.stdout.splitlines()
    )

    # S2 containers
    result["s2_containers_running"] = sum(
        1 for line in ps.stdout.splitlines()
        if S2_CONTAINER_LABEL in _run(["docker", "inspect", line.strip(), "--format", "{{.Config.Labels}}"]).stdout
    ) if ps.stdout.strip() else 0

    # WSL memory
    try:
        free = _run(["wsl", "-d", "docker-desktop", "--", "free", "-m"])
        if free.returncode == 0:
            lines = free.stdout.splitlines()
            if len(lines) >= 2:
                parts = lines[1].split()
                result["available_mem_mb"] = int(parts[6]) if len(parts) > 6 else 0
    except Exception:
        pass

    result["minimum_met"] = result["available_mem_mb"] >= 1500
    return result


def _image_verified() -> tuple[bool, str, str]:
    """Verify image exists and return (ok, id, digest)."""
    r = _run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}\t{{.RepoDigests}}"])
    if r.returncode != 0:
        return False, "", ""
    parts = r.stdout.strip().split("\t")
    image_id = parts[0] if parts else ""
    digest = parts[1].strip("[]") if len(parts) > 1 else ""
    return True, image_id, digest


def _copy_packet(packet_dir: Path, workdir: Path) -> None:
    """Copy packet contents into a disposable workdir."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    for f in packet_dir.rglob("*"):
        if f.is_file():
            rel = f.relative_to(packet_dir)
            dst = workdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)


def _cleanup(workdir: Path, container_name: str) -> None:
    """Remove only S2-owned disposable resources."""
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    _run(["docker", "rm", "-f", container_name])


def run_sandbox(
    packet_dir: Path,
    test_command: list[str],
    timeout: int = 30,
    memory: str = "1g",
    memory_swap: str = "1g",
    cpus: float = 0.5,
    pids_limit: int = 128,
) -> SandboxResult:
    """Execute test_command inside the Docker sandbox with full controls."""
    workdir = Path(WORKSPACE) / "sandbox_workdir" / f"{S2_PACKET_PREFIX}{int(time.time())}"
    container_name = f"s2-sandbox-disposable-{int(time.time()*1000)}"

    # Preflight
    preflight = _preflight()
    if not preflight["minimum_met"]:
        return SandboxResult(
            status="blocked",
            error=f"Host memory below minimum: {preflight['available_mem_mb']} MB available, need 1500 MB",
            runtime={"preflight": preflight},
        )

    # Image verification
    img_ok, image_id, digest = _image_verified()
    if not img_ok:
        return SandboxResult(status="blocked", error=f"Image {IMAGE} not available", runtime={"preflight": preflight})

    # Copy packet
    try:
        _copy_packet(packet_dir, workdir)
    except Exception as e:
        return SandboxResult(status="failed", error=f"Packet copy failed: {e}", runtime={"preflight": preflight})

    host_packet_path = str(workdir.resolve())
    container_packet_path = "/packet"

    cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        f"--memory={memory}", f"--memory-swap={memory_swap}",
        f"--cpus={cpus}",
        f"--pids-limit={pids_limit}",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=1000:1000",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--workdir", container_packet_path,
        "-v", f"{host_packet_path}:{container_packet_path}:ro",
        "--ulimit", "fsize=10485760",
        "--ulimit", "nofile=256:256",
        "--label", S2_CONTAINER_LABEL,
        IMAGE,
        *test_command,
    ]

    start = time.time()
    try:
        proc = _run(cmd, timeout=timeout + 10)
        elapsed_ms = int((time.time() - start) * 1000)
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.time() - start) * 1000)
        _cleanup(workdir, container_name)
        return SandboxResult(
            status="failed",
            error=f"Container timed out after {timeout}s",
            exit_code=124,
            runtime={"preflight": preflight, "elapsed_ms": elapsed_ms, "image_id": image_id},
        )
    except Exception as e:
        _cleanup(workdir, container_name)
        return SandboxResult(
            status="failed",
            error=f"Container launch failed: {e}",
            runtime={"preflight": preflight, "image_id": image_id},
        )

    # Effective security verification
    security = _verify_runtime(container_name, proc.returncode)

    # Cleanup
    _cleanup(workdir, container_name)

    return SandboxResult(
        status="completed" if proc.returncode == 0 else "failed",
        exit_code=proc.returncode,
        stdout=proc.stdout[-10000:],
        stderr=proc.stderr[-10000:],
        runtime={
            "preflight": preflight,
            "elapsed_ms": elapsed_ms,
            "image_id": image_id,
            "digest": digest,
            "command": cmd,
        },
        security=security,
        image_id=image_id,
    )


def _verify_runtime(container_name: str, exit_code: int) -> dict:
    """Verify effective runtime controls after execution."""
    security = {}

    if not _docker_available():
        return {"error": "docker not available"}

    # Check if container still exists
    r = _run(["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"])
    container_active = r.stdout.strip() == container_name

    if container_active:
        security["container_still_running"] = True
    else:
        security["container_removed"] = True

    # Inspect security settings (only if container exists)
    if container_active:
        r = _run([
            "docker", "inspect", container_name,
            "--format", json.dumps({
                "user": "{{.Config.User}}",
                "read_only": "{{.HostConfig.ReadonlyRootfs}}",
                "network_mode": "{{.HostConfig.NetworkMode}}",
                "cap_drop": "{{.HostConfig.CapDrop}}",
                "security_opt": "{{.HostConfig.SecurityOpt}}",
                "memory": "{{.HostConfig.Memory}}",
                "memory_swap": "{{.HostConfig.MemorySwap}}",
                "cpus": "{{.HostConfig.CpuQuota}}",
                "pids_limit": "{{.HostConfig.PidsLimit}}",
                "privileged": "{{.HostConfig.Privileged}}",
            })
        ])
        if r.returncode == 0:
            try:
                security["runtime_config"] = json.loads(r.stdout)
            except json.JSONDecodeError:
                security["runtime_config_raw"] = r.stdout

    return security


def run_tests_in_sandbox(
    packet_dir: Path,
    test_files: list[str],
    timeout: int = 30,
) -> SandboxResult:
    """Run pytest tests inside the Docker sandbox."""
    test_cmd = ["python", "-m", "pytest", *test_files, "-q", "--tb=short"]
    return run_sandbox(packet_dir=packet_dir, test_command=test_cmd, timeout=timeout)


__all__ = ["run_sandbox", "run_tests_in_sandbox", "SandboxResult", "IMAGE"]