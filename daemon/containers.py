import asyncio
from concurrent.futures import ThreadPoolExecutor

import docker
from docker.errors import NotFound
from pydantic import BaseModel
from typing import List

class DockerMetrics(BaseModel):
  containers: List[ContainerMetrics]

class ContainerMetrics(BaseModel):
  name: str
  status: str
  cpu_percent: float
  memory_usage: float
  network_rx: float
  network_tx: float

def _cpu_percent(stats: dict) -> float:
  # same formula as `docker stats`
  cpu, pre = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
  cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
  sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
  if not pre.get("system_cpu_usage") or cpu_delta <= 0 or sys_delta <= 0:
    return 0.0
  return cpu_delta / sys_delta * cpu.get("online_cpus", 1) * 100

def _collect(container) -> ContainerMetrics | None:
  # stopped containers stay in the list so rules can catch them (status == "exited")
  if container.status != "running":
    return ContainerMetrics(name=container.name, status=container.status,
                            cpu_percent=0, memory_usage=0, network_rx=0, network_tx=0)
  try:
    stats = container.stats(stream=False)
  except NotFound:  # removed since list()
    return None
  return ContainerMetrics(
    name=container.name,
    status=container.status,
    cpu_percent=_cpu_percent(stats),
    memory_usage=stats.get("memory_stats", {}).get("usage", 0),
    network_rx=stats.get("networks", {}).get("eth0", {}).get("rx_bytes", 0),
    network_tx=stats.get("networks", {}).get("eth0", {}).get("tx_bytes", 0),
  )

def _collect_all() -> list[ContainerMetrics]:
  client = docker.from_env()
  try:
    containers = client.containers.list(all=True, ignore_removed=True)
    # stats() blocks ~1-2s per container, so fetch them side by side
    with ThreadPoolExecutor() as pool:
      return [m for m in pool.map(_collect, containers) if m]
  finally:
    client.close()

async def collect_docker_metrics() -> DockerMetrics:
  # the docker client blocks, keep it off the event loop
  return DockerMetrics(containers=await asyncio.to_thread(_collect_all))
