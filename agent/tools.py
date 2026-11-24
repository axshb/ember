import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

import docker
from docker.errors import DockerException, NotFound

logger = logging.getLogger(__name__)

AGENT_UID = 10001
AGENT_GID = 10001
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 120.0
MAX_LOG_LINES = 2000
MAX_SINCE_MINUTES = 30 * 24 * 60
CLEAN_SHELL_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
}

_STATE_KEYS = ("Status", "Running", "OOMKilled", "ExitCode", "Error", "StartedAt", "FinishedAt")
_RESOURCE_KEYS = ("Memory", "MemorySwap", "NanoCpus", "CpuQuota", "CpuPeriod")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"[truncated]\n{text[-limit:]}"


def _timeout(value, fallback: float) -> float:
    # None, <= 0 and NaN fall back; everything else is capped (inf included)
    if value is None or not float(value) > 0:
        return fallback
    return min(float(value), MAX_TIMEOUT)


async def _read_tail(stream, max_bytes: int) -> bytes:
    # read to EOF, keep only the last max_bytes
    if stream is None:
        return b""
    buf = bytearray()
    while chunk := await stream.read(8192):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            del buf[: len(buf) - max_bytes]
    return bytes(buf)


async def _kill_process_group(proc, grace: float = 1.0) -> None:
    # TERM the group, then KILL survivors (orphaned children, TERM trappers)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(proc.wait(), _read_tail(proc.stdout, 0), _read_tail(proc.stderr, 0)),
                timeout=grace,
            )
        except TimeoutError:
            pass


def _with_docker(fn):
    client = docker.from_env()
    try:
        return fn(client)
    finally:
        client.close()


async def _docker_call(fn, container: str = "") -> str:
    try:
        return await asyncio.to_thread(_with_docker, fn)
    except NotFound:
        return f"Container not found: {container}"
    except (DockerException, OSError) as e:
        return f"Docker error: {e}"


def build_tools(cfg: dict) -> list:
    shell_cfg = cfg.get("agent", {}).get("shell", {})
    default_timeout = _timeout(shell_cfg.get("default_timeout"), DEFAULT_TIMEOUT)
    max_output_chars = max(int(shell_cfg.get("max_output_chars", 50_000)), 1)
    cwd = shell_cfg.get("cwd") or ("/host" if Path("/host").is_dir() else ".")

    if os.geteuid() == 0:
        drop_privs = {"user": AGENT_UID, "group": AGENT_GID, "extra_groups": []}
    else:
        drop_privs = {}
        logger.warning("not running as root, shell commands will run as euid %d", os.geteuid())

    async def run_command(command: str, timeout_seconds: float | None = None) -> str:
        """Run a shell command via /bin/sh to inspect the host.

        In the container, the host filesystem is mounted read-only at /host (the default cwd)
        and commands run as an unprivileged user without Docker socket access.
        """
        timeout = _timeout(timeout_seconds, default_timeout)
        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh", "-c", command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=CLEAN_SHELL_ENV,
                **drop_privs,
            )
        except OSError as e:
            logger.warning("shell command failed to start: %s", e)
            return f"Shell error: {e}"

        max_bytes = max_output_chars * 4
        try:
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(
                    _read_tail(proc.stdout, max_bytes),
                    _read_tail(proc.stderr, max_bytes),
                    proc.wait(),
                ),
                timeout=timeout,
            )
        except TimeoutError:
            await _kill_process_group(proc)
            return f"[Command timed out after {timeout}s]"
        except BaseException:
            await _kill_process_group(proc)
            raise

        # kill anything the command left running in the background
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

        parts = []
        if out := stdout.decode("utf-8", errors="replace"):
            parts.append(f"[stdout]\n{out}")
        if err := stderr.decode("utf-8", errors="replace"):
            parts.append(f"[stderr]\n{err}")
        parts.append(f"[exit code: {proc.returncode}]")
        return _truncate("\n".join(parts), max_output_chars)

    async def docker_list(include_stopped: bool = True) -> str:
        """List Docker containers with status, health, restart count, and image."""
        def fetch(client) -> str:
            items = []
            for c in client.containers.list(all=include_stopped, ignore_removed=True):
                attrs = c.attrs or {}
                items.append({
                    "name": c.name,
                    "id": c.id[:12],
                    "status": c.status,
                    "health": attrs.get("State", {}).get("Health", {}).get("Status"),
                    "restart_count": attrs.get("RestartCount", 0),
                    "image": attrs.get("Config", {}).get("Image", ""),
                })
            return json.dumps(items, indent=2)

        return await _docker_call(fetch)

    async def docker_inspect(container: str) -> str:
        """Inspect a container's state, restart policy, resource limits, mounts, and env key names."""
        def fetch(client) -> str:
            c = client.containers.get(container)
            attrs = c.attrs or {}
            config = attrs.get("Config", {})
            host_cfg = attrs.get("HostConfig", {})
            state_raw = attrs.get("State", {})
            state = {k: state_raw.get(k) for k in _STATE_KEYS}
            state["Health"] = state_raw.get("Health", {}).get("Status")
            return json.dumps({
                "name": c.name,
                "id": c.id[:12],
                "image": config.get("Image", ""),
                "restart_count": attrs.get("RestartCount", 0),
                "state": state,
                "restart_policy": host_cfg.get("RestartPolicy"),
                "resources": {k: host_cfg.get(k) for k in _RESOURCE_KEYS},
                "mounts": attrs.get("Mounts", []),
                # key names only, values often hold secrets
                "env_keys": [e.split("=", 1)[0] for e in config.get("Env") or []],
            }, indent=2)

        return await _docker_call(fetch, container)

    async def docker_logs(container: str, tail: int = 200, since_minutes: int | None = None) -> str:
        """Fetch recent logs for a container.

        `tail` is capped at 2000 lines. `since_minutes` limits output to the last N minutes.
        """
        tail = max(1, min(int(tail), MAX_LOG_LINES))
        since = None
        if since_minutes and since_minutes > 0:
            since = datetime.now(timezone.utc) - timedelta(minutes=min(since_minutes, MAX_SINCE_MINUTES))

        def fetch(client) -> str:
            raw = client.containers.get(container).logs(tail=tail, since=since, timestamps=True)
            return _truncate(raw.decode("utf-8", errors="replace"), max_output_chars)

        return await _docker_call(fetch, container)

    return [run_command, docker_list, docker_inspect, docker_logs]
