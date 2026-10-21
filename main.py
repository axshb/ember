import asyncio
import logging
import tomllib
from pathlib import Path

from agent.agent import run_alert, run_rss
from agent.report import render_markdown
from daemon.alerts import check_alerts
from daemon.containers import collect_docker_metrics
from daemon.system import collect_system_metrics
from daemon.rss import rss_loop
from sinks import build_sinks

CONFIG_PATH = "config.toml"
EXAMPLE_PATH = "config.example.toml"

logger = logging.getLogger(__name__)


def load_config() -> dict:
    p = Path(CONFIG_PATH)
    if not p.exists():
        raise SystemExit(
            f"config file not found: {CONFIG_PATH}\n"
            f"copy {EXAMPLE_PATH} to {CONFIG_PATH} and fill in your values."
        )
    with open(p, "rb") as f:
        return tomllib.load(f)


async def send_report(report, sinks) -> bool:
    text = render_markdown(report)
    delivered = bool(sinks)
    for sink in sinks:
        try:
            await sink.send(report, text)
        except Exception as e:
            delivered = False
            logger.error("sink %s failed: %s", type(sink).__name__, e)
    return delivered


async def metrics_loop(cfg, sinks):
    last_fired = {}
    while True:
        docker = await collect_docker_metrics()
        system = await collect_system_metrics()
        metrics = {"system": system.model_dump(), "containers": docker.model_dump()["containers"]}

        active_alerts = check_alerts(metrics, cfg.get("alerts", []), last_fired)
        if active_alerts:
            alerts = [{k: v for k, v in a.items() if k != "cooldown"} for a in active_alerts]
            report = await run_alert(alerts, cfg)
            await send_report(report, sinks)

        await asyncio.sleep(cfg["polling"]["interval"])


async def daemon_loop(cfg, sinks):
    async def deliver_rss(entries):
        report = await run_rss(entries, cfg)
        return await send_report(report, sinks)

    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(metrics_loop(cfg, sinks))
        tasks.create_task(rss_loop(cfg, deliver_rss))


async def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = load_config()
    sinks = build_sinks(cfg)
    if not sinks:
        raise SystemExit("no sinks enabled in config.toml — enable at least one under [sinks].")
    await daemon_loop(cfg, sinks)


if __name__ == "__main__":
    asyncio.run(main())
