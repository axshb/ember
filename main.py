import asyncio
import logging
import os
import time

import uvicorn

from agent.agent import load_prompts, run_alert
from agent.report import AlertReport, render_markdown
from api.app import create_app
from api.store import Store
from daemon.alerts import check_alerts, check_containers
from daemon.containers import container_states
from daemon.metrics import catalog, collect_metrics
from sinks import build_sinks

DB_PATH = os.environ.get("EMBER_DB", "data/ember.db")
HOST = os.environ.get("EMBER_HOST", "127.0.0.1")
PORT = int(os.environ.get("EMBER_PORT", "8080"))

STARTED = time.time()  # containers that died before this aren't news

logger = logging.getLogger(__name__)


async def daemon_loop(store: Store, latest: dict):
    firing = store.get_firing()
    restarts = {}  # each container's restart count at the last poll
    while True:
        interval = 10
        # a bad config must not kill the loop, the UI to fix it lives in the same process
        try:
            cfg = store.get_config()  # re-read every tick so UI changes apply live
            interval = cfg["polling"]["interval"]
            await tick(cfg, store, latest, firing, restarts)
        except Exception:
            logger.exception("poll failed")
        await asyncio.sleep(interval)


async def tick(cfg: dict, store: Store, latest: dict, firing: dict, restarts: dict):
    metrics = await collect_metrics()
    latest["metrics"] = metrics
    try:
        containers = await asyncio.to_thread(container_states)
    except Exception as e:
        logger.warning("can't read container states, skipping container alerts: %s", e)
        containers = None

    now = time.time()
    fired, resolved, reminders = check_alerts(metrics.model_dump(), cfg["alerts"], firing, now)
    if containers is not None:
        more_fired, more_resolved = check_containers(containers, firing, restarts, STARTED, now)
        fired, resolved = fired + more_fired, resolved + more_resolved
    if not (fired or resolved or reminders):
        return
    # memory is already updated, so a crash below never sends or triages twice. saved before
    # sending so a restart doesn't either
    _db_write(store.save_firing, firing)
    sinks = build_sinks(cfg)

    if fired:
        records = [_record(a, "fired") for a in fired]
        report = await run_alert(records, cfg, load_prompts(store.prompt_overrides()))
        text = render_markdown(report)
        await _send(sinks, lambda sink: sink.send(report, text))
        _db_write(store.add_report, records, report.model_dump())

    # resolves and reminders are short and skip the agent, the report went out on fire
    labels = {m["path"]: m for m in catalog(metrics)}
    for kind, alerts in (("resolved", resolved), ("remind", reminders)):
        for alert in alerts:
            title, text = notice(kind, alert, labels.get(alert.get("path")), now)
            await _send(sinks, lambda sink: sink.notify(kind, title, text))
            if kind == "resolved":
                _db_write(store.add_report, [_record(alert, kind)], AlertReport(title=title, severity="info", summary=text).model_dump())


def _db_write(fn, *args):
    # a full disk must not stop the alert about the full disk, it only costs history
    try:
        fn(*args)
    except Exception:
        logger.exception("db write failed")


async def _send(sinks, send):
    for sink in sinks:  # one broken sink shouldn't stop the others
        try:
            await send(sink)
        except Exception as e:
            logger.error("sink %s failed: %s", type(sink).__name__, e)


def _record(alert: dict, kind: str) -> dict:
    # only what the agent and history need
    return {**{k: v for k, v in alert.items() if k not in ("remind", "since", "finished", "policy")}, "state": kind}


RECOVERED = {"crashed": "running again", "restarting": "staying up again", "unhealthy": "healthy again"}


def notice(kind: str, alert: dict, info: dict | None, now: float) -> tuple[str, str]:
    if alert.get("source") == "docker":  # containers never remind, only resolve
        took = duration(now - alert["since"])
        return f"Resolved: {alert['name']} {alert['problem']}"[:120], f"{alert['name']} is {RECOVERED[alert['problem']]} after {took}."
    label = info["label"] if info else alert["path"]
    unit = info["unit"] if info else ""

    def fmt(v):
        return f"{round(v, 1)}{unit}" if unit == "%" else f"{round(v, 1)} {unit}".rstrip()

    rule = f"{alert['op']} {fmt(alert['value'])}"
    took = duration(now - alert["since"])
    if kind == "resolved":
        return f"Resolved: {label}"[:120], f"{label} is back to {fmt(alert['measured'])} (rule: {rule}) after {took}."
    return f"Still firing: {label}"[:120], f"{label} is at {fmt(alert['measured'])} (rule: {rule}), firing for {took}."


def duration(seconds: float) -> str:
    m = int(max(seconds, 0) // 60)  # clock steps back -> 0
    h, d = m // 60, m // 1440
    if d:
        return f"{d}d {h % 24}h" if h % 24 else f"{d}d"
    if h:
        return f"{h}h {m % 60}m" if m % 60 else f"{h}h"
    return f"{m}m" if m else "<1m"


def build_server(store: Store, latest: dict) -> uvicorn.Server:
    # proxy_headers off: uvicorn would otherwise trust X-Forwarded-For from 127.0.0.1,
    # letting the agent shell pose as a remote client past the API's local-client check
    return uvicorn.Server(uvicorn.Config(
        create_app(store, latest), host=HOST, port=PORT, log_level="warning", proxy_headers=False,
    ))


async def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    store = Store(DB_PATH)
    latest = {}
    server = build_server(store, latest)
    logger.info("web UI on http://%s:%d", HOST, PORT)

    # stop the web server if the daemon dies (uvicorn handles SIGINT/SIGTERM itself)
    daemon = asyncio.create_task(daemon_loop(store, latest))
    daemon.add_done_callback(lambda _: setattr(server, "should_exit", True))
    await server.serve()
    if daemon.done():
        daemon.result()  # re-raise whatever killed the daemon loop
    daemon.cancel()


if __name__ == "__main__":
    asyncio.run(main())
