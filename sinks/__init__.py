import logging
from typing import Protocol

from agent.report import AlertReport

logger = logging.getLogger(__name__)


class Sink(Protocol):
    """One-way notification channel: send a report, never listen for replies."""

    async def send(self, report: AlertReport, text: str) -> None: ...


def build_sinks(cfg: dict) -> list[Sink]:
    sinks: list[Sink] = []
    enabled = [k for k, v in cfg.get("sinks", {}).items() if v]

    if "discord" in enabled:
        from sinks.discord import DiscordWebhookSink

        sinks.append(DiscordWebhookSink(cfg["discord"]["webhook_url"]))
    if "email" in enabled:
        from sinks.email import EmailSink

        sinks.append(EmailSink(cfg["email"]))
    for name in ("slack", "telegram"):
        if name in enabled:
            logger.warning("sink %r is not implemented yet, skipping", name)

    return sinks
