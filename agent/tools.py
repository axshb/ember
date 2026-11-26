import asyncio
import json
import os

import docker
import psutil
import requests
from docker.errors import DockerException

# uid for the agent's shell commands. must not be used by anything else on the host,
# because with `pid: host` the agent can read and kill any process running as it
AGENT_UID = 2_100_000_000
MAX_OUTPUT = 20_000  # chars

# one command at a time, so the cleanup after one can't kill another's processes
_shell_lock = asyncio.Lock()


def _truncate(text: str) -> str:
  if len(text) <= MAX_OUTPUT:
    return text
  return "[truncated]\n" + text[-MAX_OUTPUT:]


async def _read_tail(stream) -> bytes:
  output = b""
  while chunk := await stream.read(65536):
    output = (output + chunk)[-MAX_OUTPUT * 4:]
  return output


def _kill_agent_processes():
  for p in psutil.process_iter(["uids"]):
    if p.info["uids"] and p.info["uids"].real == AGENT_UID:
      try:
        p.kill()
      except psutil.NoSuchProcess:
        pass


async def run_command(command: str, timeout: int = 30) -> str:
  """Run a read-only shell command. The host's filesystem is at /host. Timeout in seconds,
  max 120. Don't use & to background commands."""
  if os.geteuid() != 0:
    return "Shell disabled: ember isn't running as root, so it can't sandbox commands."

  timeout = max(1, min(timeout, 120))
  async with _shell_lock:
    proc = await asyncio.create_subprocess_exec(
      "/bin/sh", "-c", command,
      cwd="/host" if os.path.isdir("/host") else "/",
      env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8"},
      user=AGENT_UID, group=AGENT_UID, extra_groups=[],
      stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
      output, _ = await asyncio.wait_for(asyncio.gather(_read_tail(proc.stdout), proc.wait()), timeout)
    except TimeoutError:
      output = None
    _kill_agent_processes()
    await proc.wait()

  if output is None:
    return f"[timed out after {timeout}s]"
  return _truncate(output.decode(errors="replace")) + f"\n[exit code: {proc.returncode}]"


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
    logs = client.containers.get(name).logs(tail=min(lines, 2000), timestamps=True)
  except (DockerException, requests.RequestException) as e:
    return f"Docker error: {e}"
  finally:
    if client:
      client.close()
  return _truncate(logs.decode(errors="replace"))


TOOLS = [run_command, docker_list, docker_inspect, docker_logs]
