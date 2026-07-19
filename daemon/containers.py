import docker
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

async def collect_docker_metrics() -> DockerMetrics:
  client = docker.from_env()
  containers_list = []
  for container in client.containers.list():
    stats = container.stats(stream=False)
    containers_list.append(ContainerMetrics(
      name=container.name,
      status=container.status,
      cpu_percent=stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0),
      memory_usage=stats.get("memory_stats", {}).get("usage", 0),
      network_rx=stats.get("networks", {}).get("eth0", {}).get("rx_bytes", 0),
      network_tx=stats.get("networks", {}).get("eth0", {}).get("tx_bytes", 0),
    ))
  return DockerMetrics(containers=containers_list)
