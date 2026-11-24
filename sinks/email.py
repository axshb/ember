import logging
from email.message import EmailMessage

import aiosmtplib

from agent.report import AlertReport

logger = logging.getLogger(__name__)


class EmailSink:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]
        self.port = int(cfg.get("port", 587))
        # the UI stores "" for unset fields, None skips SMTP login
        self.username = cfg.get("username") or None
        self.password = cfg.get("password") or None
        self.sender = cfg.get("from") or self.username
        recipients = cfg["to"]
        self.recipients = recipients if isinstance(recipients, list) else [recipients]
        self.starttls = cfg.get("starttls", True)

    async def send(self, report: AlertReport, text: str) -> None:
        await self._send(f"[ember] {report.severity} — {report.title}", text)

    async def notify(self, kind: str, title: str, text: str) -> None:
        await self._send(f"[ember] {title}", text)

    async def _send(self, subject: str, text: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg["Subject"] = subject
        msg.set_content(text)

        try:
            await aiosmtplib.send(
                msg,
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                start_tls=self.starttls,
            )
        except Exception as e:
            logger.error("email send failed: %s", e)
