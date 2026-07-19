import asyncio
from time import time, sleep
import tomllib

from pathlib import Path
from daemon.containers import collect_docker_metrics
from daemon.system import collect_system_metrics
from daemon.alerts import check_alerts
from sinks.discord.discord_bridge import start_discord_client
from agent.agent import run_alert
from sinks.discord.discord_bridge import discord_client

CONFIG_PATH = "config/config.toml"
EXAMPLE_PATH = "config/config.example.toml"

def load_config() -> dict:
    p = Path(CONFIG_PATH)
    if not p.exists():
        raise SystemExit(
            f"config file not found: {CONFIG_PATH}\n"
            f"copy {EXAMPLE_PATH} to {CONFIG_PATH} and fill in your values."
        )
    with open(p, "rb") as f:
        return tomllib.load(f)

async def daemon_loop(cfg):
    last_fired = {}
    while True:
        docker = await collect_docker_metrics()
        system = await collect_system_metrics()
        metrics = {"system": system.model_dump(), "containers": docker.model_dump()["containers"]}

        active_alerts = check_alerts(metrics, cfg["alerts"], last_fired)
        agent_response = await handle_alerts(active_alerts, cfg)
        toggled_sinks = [k for k, v in cfg["sinks"].items() if v]
        await distribute_alerts(agent_response, toggled_sinks)

        await asyncio.sleep(cfg["polling"]["interval"])

async def handle_alerts(active_alerts, cfg):
    if active_alerts:
        cleaned_alerts = []
        for alert in active_alerts:
            values_list = [str(val) for val in alert.values()]
            res = "".join(values_list[:-1]) # [:-1] slice to exclude cooldown
            cleaned_alerts.append(res)
        return await run_alert(alerts=cleaned_alerts, cfg=cfg)

async def distribute_alerts(agent_response, toggled_sinks):
    if not agent_response:
        return
    text = agent_response.get("analysis", "") if isinstance(agent_response, dict) else str(agent_response)
    if "discord" in toggled_sinks:
        await discord_client.send_alert(text)
    if "email" in toggled_sinks:
        pass
    if "slack" in toggled_sinks:
        pass
    if "telegram" in toggled_sinks:
        pass

def gather_sinks(cfg):
    tasks = []
    active_sinks = []

    for k, v in cfg["sinks"].items():
        if v:
           active_sinks.append(k)

    if "discord" in active_sinks:
        tasks.append(start_discord_client(cfg["discord"], cfg["agent"]))
    if "email" in active_sinks:
        pass
    if "slack" in active_sinks:
        pass
    if "telegram" in active_sinks:
        pass
    return tasks, active_sinks

async def main():
    cfg = load_config()

    tasks, _ = gather_sinks(cfg)
    tasks.append(daemon_loop(cfg))

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
