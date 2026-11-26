from pydantic import BaseModel
import logging
import psutil

logger = logging.getLogger(__name__)

class SystemMetrics(BaseModel):
  cpu: ComponentMetrics
  memory: ComponentMetrics
  disks: list[ComponentMetrics]

class ComponentMetrics(BaseModel):
  name: str
  percent: float
  used: float | None = None
  total: float | None = None

async def collect_system_metrics() -> SystemMetrics:
  results = []
  results.append(_collect_cpu_metrics())
  results.append(_collect_memory_metrics())
  results.extend(_collect_disk_metrics())
  return SystemMetrics(
    cpu=results[0],
    memory=results[1],
    disks=results[2:]
  )

def _collect_cpu_metrics() -> ComponentMetrics:
  cpu_metrics = ComponentMetrics(
    name="cpu",
    percent=0.0,
  )
  try:
    cpu_metrics.percent = psutil.cpu_percent(interval=None, percpu=False)
  except Exception as e:
    logger.error(f"Error collecting CPU metrics: {e}")
  return cpu_metrics

def _collect_memory_metrics() -> ComponentMetrics:
  memory_metrics = ComponentMetrics(
    name="memory",
    percent=0.0,
  )
  try:
    memory_metrics.percent = psutil.virtual_memory().percent
    memory_metrics.used = psutil.virtual_memory().used / (1024**3)
    memory_metrics.total = psutil.virtual_memory().total / (1024**3)
  except Exception as e:
    logger.error(f"Error collecting memory metrics: {e}")
  return memory_metrics

def _collect_disk_metrics() -> list[ComponentMetrics]:
  disks = []
  seen = set()
  try:
    for partition in psutil.disk_partitions(all=False):
      if partition.device in seen:
        continue
      if not partition.mountpoint:
        continue

      disk_usage = psutil.disk_usage(partition.mountpoint)
      disks.append(ComponentMetrics(
        name=partition.device,
        percent=disk_usage.percent,
        used=disk_usage.used / (1024**3),
        total=disk_usage.total / (1024**3)
      ))
      seen.add(partition.device)
  except Exception as e:
    logger.error(f"Error collecting disk metrics: {e}")
  return disks
