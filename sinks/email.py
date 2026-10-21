import logging
from email.message import EmailMessage

import aiosmtplib

from agent.report import AlertReport

logger = logging.getLogger(__name__)


class EmailSink:
    def __init__(self, cfg: dict):
        self.host = cfg["host"]
        self.port = int(cfg.get("port", 587))
        self.username = cfg.get("username")
        self.password = cfg.get("password")
        self.sender = cfg.get("from", self.username)
        recipients = cfg["to"]
        self.recipients = recipients if isinstance(recipients, list) else [recipients]
        self.starttls = cfg.get("starttls", True)

    async def send(self, report: AlertReport, text: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg["Subject"] = f"[ember] {report.severity} — {report.title}"
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
            raise
