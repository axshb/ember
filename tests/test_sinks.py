import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent.report import AlertReport
from sinks import build_sinks
from sinks.discord import DiscordWebhookSink
from sinks.email import EmailSink


class BuildSinksTests(unittest.TestCase):
    def test_no_sinks_enabled(self):
        self.assertEqual(build_sinks({}), [])
        self.assertEqual(build_sinks({"sinks": {"discord": False}}), [])

    def test_enabled_sinks_are_built(self):
        sinks = build_sinks({
            "sinks": {"discord": True, "email": True},
            "discord": {"webhook_url": "https://example.com/webhook"},
            "email": {"host": "smtp.example.com", "to": "ash@example.com"},
        })
        self.assertEqual([type(sink) for sink in sinks], [DiscordWebhookSink, EmailSink])

    def test_unimplemented_sinks_warn_and_are_skipped(self):
        with self.assertLogs("sinks", level="WARNING") as logs:
            self.assertEqual(build_sinks({"sinks": {"slack": True, "telegram": True}}), [])
        self.assertEqual(len(logs.output), 2)


class SinkSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_discord_sends_embed_and_clips_long_fields(self):
        report = AlertReport(
            title="CPU busy", severity="critical", summary="High usage",
            findings=["x" * 1100], actions=["Check processes"],
        )
        with patch("sinks.discord.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.post.return_value = Mock(status_code=204)
            await DiscordWebhookSink("https://example.com/webhook").send(report, "unused")

        client.post.assert_awaited_once()
        self.assertEqual(client.post.call_args.args, ("https://example.com/webhook",))
        embed = client.post.call_args.kwargs["json"]["embeds"][0]
        self.assertEqual(embed["title"], report.title)
        self.assertEqual(embed["description"], report.summary)
        self.assertEqual(embed["color"], 0xE74C3C)
        self.assertEqual(len(embed["fields"][0]["value"]), 1024)
        self.assertTrue(embed["fields"][0]["value"].endswith("…"))
        self.assertEqual(embed["fields"][1]["value"], "• Check processes")

    async def test_discord_omits_empty_fields_and_logs_http_errors(self):
        report = AlertReport(title="Alert", summary="Details")
        with patch("sinks.discord.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.post.return_value = Mock(status_code=400, text="Bad request")
            with self.assertLogs("sinks.discord", level="ERROR") as logs:
                await DiscordWebhookSink("https://example.com/webhook").send(report, "unused")

        embed = client.post.call_args.kwargs["json"]["embeds"][0]
        self.assertNotIn("fields", embed)
        self.assertIn("400 Bad request", logs.output[0])

    async def test_email_sends_report_with_configured_recipients(self):
        report = AlertReport(title="CPU busy", summary="High usage")
        for recipients in ("ash@example.com", ["ash@example.com", "other@example.com"]):
            with self.subTest(recipients=recipients):
                sink = EmailSink({
                    "host": "smtp.example.com", "username": "ember@example.com",
                    "password": "test-password", "to": recipients,
                })
                with patch("sinks.email.aiosmtplib.send", new_callable=AsyncMock) as send:
                    await sink.send(report, "Report body")

                send.assert_awaited_once()
                message = send.call_args.args[0]
                self.assertEqual(message["From"], "ember@example.com")
                self.assertEqual(message["To"], ", ".join(sink.recipients))
                self.assertEqual(message["Subject"], "[ember] warning — CPU busy")
                self.assertEqual(message.get_content(), "Report body\n")
                self.assertEqual(send.call_args.kwargs, {
                    "hostname": "smtp.example.com", "port": 587,
                    "username": "ember@example.com", "password": "test-password",
                    "start_tls": True,
                })

    async def test_email_logs_delivery_failure(self):
        sink = EmailSink({"host": "smtp.example.com", "from": "ember@example.com", "to": "ash@example.com"})
        with patch("sinks.email.aiosmtplib.send", new_callable=AsyncMock, side_effect=OSError("offline")):
            with self.assertLogs("sinks.email", level="ERROR") as logs:
                await sink.send(AlertReport(title="Alert", summary="Details"), "Report body")
        self.assertIn("offline", logs.output[0])
