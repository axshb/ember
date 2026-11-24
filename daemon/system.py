import asyncio
import logging

import psutil
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class SystemMetrics(BaseModel):
    cpu: ComponentMetrics = Field(json_schema_extra={"label": "CPU"})
    memory: ComponentMetrics = Field(json_schema_extra={"label": "memory"})
    disks: list[ComponentMetrics] = Field(json_schema_extra={"label": "disk"})

class ComponentMetrics(BaseModel):
    name: str
    # fields with a unit show up as alertable in the UI
    percent: float = Field(json_schema_extra={"label": "usage", "unit": "%"})
    used: float | None = Field(None, json_schema_extra={"label": "used", "unit": "GB"})
    total: float | None = None

async def collect_system_metrics() -> SystemMetrics:
    # cpu_percent(interval=1) sleeps, keep it off the event loop
    return await asyncio.to_thread(_collect_system_metrics)

def _collect_system_metrics() -> SystemMetrics:
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
        cpu_metrics.percent = psutil.cpu_percent(interval=1, percpu=False)
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
