import logging

from pydantic import BaseModel, Field

from daemon.containers import ContainerMetrics, collect_container_metrics
from daemon.system import SystemMetrics, collect_system_metrics

logger = logging.getLogger(__name__)


class Metrics(BaseModel):
    system: SystemMetrics
    containers: list[ContainerMetrics] = Field(json_schema_extra={"label": "container"})


async def collect_metrics() -> Metrics:
    system = await collect_system_metrics()
    try:
        containers = await collect_container_metrics()
    except Exception as e:
        logger.warning("docker metrics unavailable: %s", e)
        containers = []
    return Metrics(system=system, containers=containers)


def catalog(model: BaseModel, path: str = "", label: str = "") -> list[dict]:
    # every field with a unit is alertable, paths match what check_alerts resolves
    out = []
    for field, info in type(model).model_fields.items():
        extra = info.json_schema_extra or {}
        value = getattr(model, field)
        p = f"{path}.{field}".lstrip(".")
        l = f"{label} {extra.get('label', '')}".strip()
        if isinstance(value, BaseModel):
            out += catalog(value, p, l)
        elif isinstance(value, list):
            for item in value:  # list items are addressed by name
                out += catalog(item, f"{p}.{item.name}", f"{l} {item.name}")
        elif "unit" in extra and value is not None:
            out.append({
                "path": p, "label": l, "group": label, "name": extra["label"],
                "unit": extra["unit"], "value": round(value, 2),
            })
    return out
