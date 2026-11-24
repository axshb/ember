import logging

import httpx

from agent.report import AlertReport

logger = logging.getLogger(__name__)

SEVERITY_COLORS = {"info": 0x3498DB, "warning": 0xE67E22, "critical": 0xE74C3C}
NOTICE_COLORS = {"resolved": 0x2ECC71, "remind": 0x95A5A6}

# Discord embed limits
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _field(name: str, items: list[str]) -> dict | None:
    if not items:
        return None
    value = "\n".join(f"• {item}" for item in items)
    return {"name": name, "value": _clip(value, FIELD_VALUE_LIMIT), "inline": False}


class DiscordWebhookSink:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, report: AlertReport, text: str) -> None:
        embed = {
            "title": _clip(report.title, TITLE_LIMIT),
            "description": _clip(report.summary, DESCRIPTION_LIMIT),
            "color": SEVERITY_COLORS.get(report.severity, SEVERITY_COLORS["warning"]),
        }
        fields = [
            f
            for f in (
                _field("Findings", report.findings),
                _field("Recommended actions", report.actions),
            )
            if f
        ]
        if fields:
            embed["fields"] = fields

        await self._post(embed)

    async def notify(self, kind: str, title: str, text: str) -> None:
        await self._post({
            "title": _clip(title, TITLE_LIMIT),
            "description": _clip(text, DESCRIPTION_LIMIT),
            "color": NOTICE_COLORS.get(kind, NOTICE_COLORS["remind"]),
        })

    async def _post(self, embed: dict) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self.webhook_url, json={"embeds": [embed]})
        if resp.status_code >= 300:
            logger.error("discord webhook failed: %s %s", resp.status_code, resp.text[:200])
