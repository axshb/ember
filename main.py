import asyncio
import logging
import time
import tomllib
from pathlib import Path

from agent.agent import run_alert
from agent.report import render_markdown, resolved_report
from daemon.alerts import check_alerts
from daemon.checks import check_rules, run_due_checks
from daemon.containers import collect_docker_metrics
from daemon.system import collect_system_metrics
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


async def send(sinks, report):
    text = render_markdown(report)
    for sink in sinks:
        try:
            await sink.send(report, text)
        except Exception as e:
            logger.error("sink %s failed: %s", type(sink).__name__, e)


async def daemon_loop(cfg, sinks):
    firing, last_fired, checks = set(), {}, {}
    commands = cfg.get("commands", [])
    while True:
        docker = await collect_docker_metrics()
        system = await collect_system_metrics()
        await run_due_checks(commands, checks)
        metrics = {
            "system": system.model_dump(),
            "containers": docker.model_dump()["containers"],
            "checks": [{"name": name, "exit_code": r["exit_code"]} for name, r in checks.items()],
        }

        rules = cfg["alerts"] + check_rules(commands, checks)
        fired, resolved = check_alerts(metrics, rules, firing, last_fired)
        if fired:
            alerts = [{k: v for k, v in a.items() if k != "cooldown"} for a in fired]
            await send(sinks, await run_alert(alerts, cfg))
        for alert in resolved:  # short notice, no triage
            minutes = round((time.time() - alert["since"]) / 60)
            await send(sinks, resolved_report(alert, minutes))

        await asyncio.sleep(cfg["polling"]["interval"])


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
