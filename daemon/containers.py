import asyncio

import docker
from pydantic import BaseModel, Field


class ContainerMetrics(BaseModel):
    name: str
    status: str
    cpu_percent: float = Field(json_schema_extra={"label": "CPU", "unit": "%"})
    memory_usage: float = Field(json_schema_extra={"label": "memory", "unit": "MB"})
    memory_percent: float = Field(json_schema_extra={"label": "memory", "unit": "%"})
    network_rx: float
    network_tx: float


def _cpu_percent(stats: dict) -> float:
    # same formula as `docker stats`
    cpu, pre = stats.get("cpu_stats", {}), stats.get("precpu_stats", {})
    if not pre.get("system_cpu_usage"):  # no previous sample yet
        return 0.0
    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    if cpu_delta <= 0 or sys_delta <= 0:
        return 0.0
    return cpu_delta / sys_delta * cpu.get("online_cpus", 1) * 100


def _memory(stats: dict) -> tuple[float, float]:
    # MB minus page cache like `docker stats`, and % of limit
    mem = stats.get("memory_stats", {})
    cache = mem.get("stats", {})
    usage = max(mem.get("usage", 0) - cache.get("total_inactive_file", cache.get("inactive_file", 0)), 0)
    limit = mem.get("limit", 0)
    return usage / 2**20, (usage / limit * 100 if limit else 0.0)


async def collect_container_metrics() -> list[ContainerMetrics]:
    client = await asyncio.to_thread(docker.from_env)
    try:
        containers = await asyncio.to_thread(client.containers.list)
        # stats() blocks ~1-2s per container, fetch them all at once
        all_stats = await asyncio.gather(
            *(asyncio.to_thread(c.stats, stream=False) for c in containers),
            return_exceptions=True,
        )
    finally:
        client.close()

    metrics = []
    for container, stats in zip(containers, all_stats):
        if isinstance(stats, Exception):  # e.g. removed since list()
            continue
        memory_usage, memory_percent = _memory(stats)
        network = stats.get("networks", {}).get("eth0", {})
        metrics.append(ContainerMetrics(
            name=container.name,
            status=container.status,
            cpu_percent=_cpu_percent(stats),
            memory_usage=memory_usage,
            memory_percent=memory_percent,
            network_rx=network.get("rx_bytes", 0),
            network_tx=network.get("tx_bytes", 0),
        ))
    return metrics
