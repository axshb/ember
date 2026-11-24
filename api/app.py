import ipaddress
import math
import os
import socket
from pathlib import Path

import psutil
from starlette.applications import Starlette
from starlette.middleware import Middleware
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


def _number(v) -> bool:
    # json allows NaN/Infinity, and an infinite poll interval would hang the loop
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


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(host.split("%")[0])  # drop ipv6 zone, e.g. fe80::1%eth0
    return getattr(ip, "ipv4_mapped", None) or ip


def _is_local(host: str) -> bool:
    try:
        ip = _ip(host)
    except ValueError:
        return True  # can't tell who it is, refuse
    return ip.is_loopback or any(
        _ip(a.address) == ip
        for addrs in psutil.net_if_addrs().values() for a in addrs
        if a.family in (socket.AF_INET, socket.AF_INET6)
    )


# the agent shell shares our network namespace and could otherwise use the API (redirect
# sinks, leak secrets via a new email host, rewrite prompts). it can only connect from a
# local address; browsers come in through docker's port forward. only enforced when shell
# commands are sandboxed (running as root), so local `uv run` still works
class NoLocalClients:
    def __init__(self, app, enabled: bool):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        if self.enabled and scope["type"] == "http" and _is_local((scope.get("client") or ("?",))[0]):
            await JSONResponse({"error": "forbidden"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


# other sites can't send json with a plain form post, so this blocks cross-site writes
class JSONWritesOnly:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD") and scope["path"].startswith("/api/"):
            content_type = dict(scope["headers"]).get(b"content-type", b"")
            if not content_type.startswith(b"application/json"):
                await JSONResponse({"error": "expected application/json"}, status_code=415)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(store: Store, latest: dict) -> Starlette:
    # latest["metrics"] is set by the daemon loop each tick
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
        Middleware(NoLocalClients, enabled=os.geteuid() == 0),
        Middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", *extra_hosts]),
        Middleware(JSONWritesOnly),
    ])
