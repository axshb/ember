import asyncio
import logging
import os
import time

import uvicorn

from agent.agent import load_prompts, run_alert
from agent.report import AlertReport, render_markdown
from api.app import create_app
from api.store import Store
from daemon.alerts import check_alerts, rule_key
from daemon.metrics import catalog, collect_metrics
from daemon.state import AlertState
from sinks import build_sinks

DB_PATH = os.environ.get("EMBER_DB", "data/ember.db")
HOST = os.environ.get("EMBER_HOST", "127.0.0.1")
PORT = int(os.environ.get("EMBER_PORT", "8080"))

logger = logging.getLogger(__name__)


async def daemon_loop(store: Store, latest: dict):
    state = AlertState(store)
    while True:
        interval = 10
        # a bad config must not kill the loop, the UI to fix it lives in the same process
        try:
            cfg = store.get_config()  # re-read every tick so UI changes apply live
            interval = cfg["polling"]["interval"]
            await tick(cfg, store, latest, state)
        except Exception:
            logger.exception("poll failed")
        await asyncio.sleep(interval)


async def tick(cfg: dict, store: Store, latest: dict, state: AlertState):
    metrics = await collect_metrics()
    latest["metrics"] = metrics
    now = time.time()

    changes = check_alerts(metrics.model_dump(), cfg["alerts"], state, now)
    if not any(changes.values()):
        return
    sinks = build_sinks(cfg)
    if not sinks:
        logger.warning("alert changed state but no sinks are enabled, it's only in the UI history")

    # state is saved before delivery, so a failure here must undo the fire or it never
    # re-fires while the breach lasts. each block is isolated so one can't skip the others
    if changes["fired"]:
        alerts = [_alert_record(a, "fired") for a in changes["fired"]]
        try:
            report = await run_alert(alerts, cfg, load_prompts(store.prompt_overrides()))
            text = render_markdown(report)
        except Exception:
            logger.exception("couldn't build the alert report, will retry next poll")
            for alert in changes["fired"]:
                state.forget(rule_key(alert))
        else:
            try:  # a report that exists goes out even if history can't take it
                store.add_report(alerts, report.model_dump())
            except Exception:
                logger.exception("couldn't save report to history")
            await _deliver(sinks, lambda sink: sink.send(report, text))

    # resolves and reminders are short and skip the agent, the report already went out on fire
    try:
        metric_info = {m["path"]: m for m in catalog(metrics)}
    except Exception:
        logger.exception("metric catalog failed, notices use raw paths")
        metric_info = {}
    for kind in ("resolved", "remind"):
        for alert in changes[kind]:
            try:
                title, text = notice(kind, alert, metric_info.get(alert["path"]), now)
            except Exception:
                logger.exception("couldn't build %s notice for %s", kind, alert["path"])
                continue
            if kind == "resolved":
                try:  # same as reports, the message goes out even if history can't take it
                    store.add_report([_alert_record(alert, kind)], AlertReport(title=title, severity="info", summary=text).model_dump())
                except Exception:
                    logger.exception("couldn't save resolve to history")
            await _deliver(sinks, lambda sink: sink.notify(kind, title, text))


def _alert_record(alert: dict, kind: str) -> dict:
    return {**{k: alert[k] for k in ("path", "op", "value", "measured")}, "state": kind}


def notice(kind: str, alert: dict, info: dict | None, now: float) -> tuple[str, str]:
    label = info["label"] if info else alert["path"]
    unit = info["unit"] if info else ""
    value, threshold = (f"{round(v, 1)}{unit if unit == '%' else f' {unit}'.rstrip()}" for v in (alert["measured"], alert["value"]))
    rule = f"{alert['op']} {threshold}"
    duration = _duration(now - alert["since"])
    if kind == "resolved":
        return f"Resolved: {label}"[:120], f"{label} is back to {value} (rule: {rule}) after {duration}."
    return f"Still firing: {label}"[:120], f"{label} is at {value} (rule: {rule}), firing for {duration}."


def _duration(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 1:
        return "<1m"
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d {h}h" if h else f"{d}d"


async def _deliver(sinks, send):
    for sink in sinks:
        try:
            await send(sink)
        except Exception as e:
            logger.error("sink %s failed: %s", type(sink).__name__, e)


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
