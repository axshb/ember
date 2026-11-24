import ipaddress
import math
import os
from pathlib import Path

import psutil
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from agent.agent import default_prompts
from api.store import SECRETS, Store, migrate, without_secrets
from daemon.alerts import OPS
from daemon.metrics import catalog

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
MASK = "********"
OK = {"ok": True}
MIN_REMIND = 5  # minutes, anything shorter is just spam


def _error(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


def _number(v) -> bool:  # json allows NaN/Infinity, an infinite poll interval hangs the loop
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _validate(cfg: dict) -> str | None:
    # catch anything that would crash the daemon loop
    try:
        for name, value in [
            ("polling interval", cfg["polling"]["interval"]),
            ("max tool calls", cfg["agent"]["max_tool_calls"]),
            ("shell timeout", cfg["agent"]["shell"]["default_timeout"]),
            ("max shell output", cfg["agent"]["shell"]["max_output_chars"]),
            ("email port", cfg["email"]["port"]),
        ]:
            if not _number(value) or value <= 0:
                return f"{name} must be a positive number"
        for rule in cfg["alerts"]:
            if not isinstance(rule.get("path"), str) or not rule["path"]:
                return "every alert needs a metric"
            if rule.get("op") not in OPS:
                return f"unknown condition {rule.get('op')!r}"
            if not _number(rule.get("value")):
                return f"threshold for {rule['path']} must be a number"
            remind = rule.get("remind", 0)
            if not _number(remind) or not (remind == 0 or remind >= MIN_REMIND):
                return f"reminder for {rule['path']} must be off or at least {MIN_REMIND} minutes"
        for section, _ in SECRETS:
            if not isinstance(cfg[section], dict):
                return "malformed config"
    except (KeyError, TypeError, AttributeError):
        return "malformed config"
    return None


def _is_local(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return True  # can't tell who it is, refuse
    ip = getattr(ip, "ipv4_mapped", None) or ip  # ::ffff:127.0.0.1
    own = {a.address.split("%")[0] for addrs in psutil.net_if_addrs().values() for a in addrs}
    return ip.is_loopback or str(ip) in own


def create_app(store: Store, latest: dict) -> Starlette:
    sandboxed = os.geteuid() == 0

    async def guard(request: Request, call_next):
        # the agent shell shares our network, so it would connect from one of our own addresses.
        # browsers come in through docker's port forward. only enforced when the shell is
        # sandboxed (we're root), so a local `uv run` still works
        if sandboxed and _is_local(request.client.host if request.client else "?"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        # a plain cross-site form post can't send json, so this blocks cross-site writes
        if request.method not in ("GET", "HEAD") and not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"error": "expected application/json"}, status_code=415)
        return await call_next(request)

    async def get_config(request: Request):
        return JSONResponse(without_secrets(store.get_config(), MASK))

    async def put_config(request: Request):
        try:
            cfg = await request.json()
        except ValueError:
            return _error("invalid JSON")
        if error := _validate(cfg):
            return _error(error)
        current = store.get_config()
        for section, key in SECRETS:  # mask means unchanged
            if cfg.get(section, {}).get(key) == MASK:
                cfg[section][key] = current.get(section, {}).get(key, "")
        store.save_config(cfg)
        return JSONResponse(OK)

    async def get_metrics(request: Request):
        return JSONResponse(catalog(latest["metrics"]) if "metrics" in latest else [])

    async def get_prompts(request: Request):
        overrides = store.prompt_overrides()
        return JSONResponse([
            {"name": name, "default": body, "override": overrides.get(name)}
            for name, body in default_prompts().items()
        ])

    async def put_prompt(request: Request):
        name = request.path_params["name"]
        if name not in default_prompts():
            return _error(f"unknown prompt {name!r}")
        try:
            body = (await request.json()).get("body")
        except (ValueError, AttributeError):
            body = None
        if not isinstance(body, str):
            return _error("body must be a string")
        store.set_prompt(name, body)
        return JSONResponse(OK)

    async def delete_prompt(request: Request):
        store.reset_prompt(request.path_params["name"])
        return JSONResponse(OK)

    async def get_reports(request: Request):
        return JSONResponse(store.recent_reports())

    async def export(request: Request):
        data = {"config": without_secrets(store.get_config()), "prompts": store.prompt_overrides()}
        return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="ember-export.json"'})

    async def import_(request: Request):
        try:
            data = await request.json()
        except ValueError:
            return _error("not an ember export")
        cfg = data.get("config") if isinstance(data, dict) else None
        if not isinstance(cfg, dict):
            return _error("not an ember export")
        cfg = migrate(cfg)  # older exports have cooldowns
        if error := _validate(cfg):
            return _error(error)
        current = store.get_config()
        for section, key in SECRETS:  # exports have no secrets, keep current ones
            cfg.setdefault(section, {})[key] = current.get(section, {}).get(key, "")
        store.save_config(cfg)
        defaults = default_prompts()
        for name, body in (data.get("prompts") or {}).items():
            if name in defaults and isinstance(body, str):
                store.set_prompt(name, body)
        return JSONResponse(OK)

    routes = [
        Route("/api/config", get_config, methods=["GET"]),
        Route("/api/config", put_config, methods=["PUT"]),
        Route("/api/metrics", get_metrics),
        Route("/api/prompts", get_prompts),
        Route("/api/prompts/{name}", put_prompt, methods=["PUT"]),
        Route("/api/prompts/{name}", delete_prompt, methods=["DELETE"]),
        Route("/api/reports", get_reports),
        Route("/api/export", export),
        Route("/api/import", import_, methods=["POST"]),
    ]
    if FRONTEND_DIR.is_dir():
        routes.append(Mount("/", StaticFiles(directory=FRONTEND_DIR, html=True)))

    # host check blocks DNS rebinding
    extra_hosts = [h.strip() for h in os.environ.get("EMBER_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return Starlette(routes=routes, middleware=[
        Middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", *extra_hosts]),
        Middleware(BaseHTTPMiddleware, dispatch=guard),
    ])
