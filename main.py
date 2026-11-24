import asyncio
import logging
import os

import uvicorn

from agent.agent import load_prompts, run_alert
from agent.report import render_markdown
from api.app import create_app
from api.store import Store
from daemon.alerts import check_alerts
from daemon.metrics import collect_metrics
from sinks import build_sinks

DB_PATH = os.environ.get("EMBER_DB", "data/ember.db")
HOST = os.environ.get("EMBER_HOST", "127.0.0.1")
PORT = int(os.environ.get("EMBER_PORT", "8080"))

logger = logging.getLogger(__name__)


async def daemon_loop(store: Store, latest: dict):
    last_fired = {}
    while True:
        interval = 10
        # a bad config must not kill the loop, the UI to fix it lives in the same process
        try:
            cfg = store.get_config()  # re-read every tick so UI changes apply live
            interval = cfg["polling"]["interval"]
            await tick(cfg, store, latest, last_fired)
        except Exception:
            logger.exception("poll failed")
        await asyncio.sleep(interval)


async def tick(cfg: dict, store: Store, latest: dict, last_fired: dict):
    metrics = await collect_metrics()
    latest["metrics"] = metrics

    active_alerts = check_alerts(metrics.model_dump(), cfg["alerts"], last_fired)
    if not active_alerts:
        return
    alerts = [{k: v for k, v in a.items() if k != "cooldown"} for a in active_alerts]
    report = await run_alert(alerts, cfg, load_prompts(store.prompt_overrides()))
    store.add_report(active_alerts, report.model_dump())
    text = render_markdown(report)
    sinks = build_sinks(cfg)
    if not sinks:
        logger.warning("alert fired but no sinks are enabled, it's only in the UI history")
    for sink in sinks:
        try:
            await sink.send(report, text)
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
