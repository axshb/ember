import json
import os
import re
import subprocess
import time

import docker
import psutil
import requests
from docker.errors import DockerException

HOST = "/host" if os.path.isdir("/host") else "/"
MAX_OUTPUT = 20_000  # chars


def _truncate(text: str) -> str:
  if len(text) <= MAX_OUTPUT:
    return text
  return "[truncated]\n" + text[-MAX_OUTPUT:]


def _container_names() -> dict[str, str]:
  client = docker.from_env()
  try:
    return {c.id: c.name for c in client.containers.list()}
  finally:
    client.close()


def _container_of(pid: int, names: dict[str, str]) -> str | None:
  try:
    with open(f"/proc/{pid}/cgroup") as f:
      match = re.search(r"[0-9a-f]{64}", f.read())
  except OSError:
    return None
  return names.get(match.group()) if match else None


def top_processes(sort_by: str = "cpu", limit: int = 15) -> str:
  """The host's busiest processes, sorted by "cpu" or "memory", with the container each runs in."""
  procs = list(psutil.process_iter(["name"]))
  for p in procs:
    try:
      p.cpu_percent()
    except psutil.Error:
      pass
  time.sleep(1)  # cpu_percent measures since the call above

  rows = []
  for p in procs:
    try:
      rows.append({
        "pid": p.pid,
        "name": p.info["name"],
        "cpu_percent": p.cpu_percent(),
        "memory_mb": round(p.memory_info().rss / 2**20),
      })
    except psutil.Error:
      pass
  key = "memory_mb" if sort_by == "memory" else "cpu_percent"
  rows = sorted(rows, key=lambda r: r[key], reverse=True)[:max(1, min(limit, 50))]

  try:
    names = _container_names()
  except (DockerException, requests.RequestException):
    names = {}
  for r in rows:
    r["container"] = _container_of(r["pid"], names)
  return json.dumps(rows, indent=2)


def _disk_usage(path: str, device: int, deadline: float) -> int:
  total = 0
  try:
    entries = list(os.scandir(path))
  except OSError:
    return 0
  for e in entries:
    if time.monotonic() > deadline:
      break
    try:
      st = e.stat(follow_symlinks=False)
    except OSError:
      continue
    if st.st_dev != device:  # another filesystem, like `du -x`
      continue
    if e.is_dir(follow_symlinks=False):
      total += _disk_usage(e.path, device, deadline)
    else:
      total += st.st_blocks * 512
  return total


def largest_dirs(path: str = "/") -> str:
  """Disk usage of each folder directly inside `path` on the host, largest first."""
  root = os.path.join(HOST, path.lstrip("/"))
  deadline = time.monotonic() + 30
  try:
    device = os.lstat(root).st_dev
    entries = [e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
  except OSError as e:
    return f"Can't read {path}: {e.strerror}"

  sizes, mounts = [], []
  for e in entries:
    try:
      st = e.stat(follow_symlinks=False)
    except OSError:
      continue
    if st.st_dev == device:
      sizes.append((_disk_usage(e.path, device, deadline), e.name))
    else:
      mounts.append(e.name)

  lines = []
  for size, name in sorted(sizes, reverse=True)[:20]:
    shown = f"{size / 2**30:.1f} GB" if size >= 2**30 else f"{size / 2**20:.0f} MB"
    lines.append(f"{shown}  {os.path.join(path, name)}")
  for name in mounts:
    lines.append(f"{os.path.join(path, name)} is a separate filesystem, not counted")
  if time.monotonic() > deadline:
    lines.append("[stopped after 30s, sizes are incomplete]")
  return "\n".join(lines) or "No folders"


def kernel_log(lines: int = 100) -> str:
  """Recent kernel messages on the host, e.g. OOM kills and disk errors."""
  command = ["chroot", HOST, "journalctl", "-k", "-n", str(max(1, min(lines, 1000))), "--no-pager", "-q"]
  try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
  except (OSError, subprocess.TimeoutExpired) as e:
    return f"Can't read the kernel log: {e}"
  return _truncate(result.stdout or result.stderr or "No kernel messages")


def docker_list() -> str:
  """List all containers, including stopped ones."""
  client = None
  try:
    client = docker.from_env()
    containers = client.containers.list(all=True, ignore_removed=True)
  except (DockerException, requests.RequestException) as e:
    return f"Docker error: {e}"
  finally:
    if client:
      client.close()

  rows = []
  for c in containers:
    rows.append({
      "name": c.name,
      "status": c.status,
      "health": (c.attrs["State"].get("Health") or {}).get("Status"),
      "restarts": c.attrs.get("RestartCount", 0),
      "image": c.attrs["Config"].get("Image"),
    })
  return json.dumps(rows, indent=2)


def docker_inspect(name: str) -> str:
  """A container's state, restart policy, resource limits, mounts and env var names."""
  client = None
  try:
    client = docker.from_env()
    c = client.containers.get(name)
  except (DockerException, requests.RequestException) as e:
    return f"Docker error: {e}"
  finally:
    if client:
      client.close()

  host = c.attrs["HostConfig"]
  return json.dumps({
    "name": c.name,
    "image": c.attrs["Config"].get("Image"),
    "state": c.attrs["State"],
    "restarts": c.attrs.get("RestartCount", 0),
    "restart_policy": host.get("RestartPolicy"),
    "memory_limit_bytes": host.get("Memory"),
    "cpus": (host.get("NanoCpus") or 0) / 1e9,
    "mounts": [f"{m.get('Source')} -> {m.get('Destination')}" for m in c.attrs.get("Mounts", [])],
    # names only, the values are often secrets
    "env": [e.split("=")[0] for e in c.attrs["Config"].get("Env") or []],
  }, indent=2)


def docker_logs(name: str, lines: int = 200) -> str:
  """The last lines of a container's logs. Max 2000."""
  client = None
  try:
    client = docker.from_env()
    logs = client.containers.get(name).logs(tail=max(1, min(lines, 2000)), timestamps=True)
  except (DockerException, requests.RequestException) as e:
    return f"Docker error: {e}"
  finally:
    if client:
      client.close()
  return _truncate(logs.decode(errors="replace"))


TOOLS = [top_processes, largest_dirs, kernel_log, docker_list, docker_inspect, docker_logs]
