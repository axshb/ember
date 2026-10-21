import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from agent.agent import run_rss
from agent.report import AlertReport, fallback_rss_report, render_markdown
from daemon.rss import FeedState, parse_feed, poll_feeds, rss_loop
from main import daemon_loop, send_report
from sinks.discord import DiscordWebhookSink
from sinks.email import EmailSink

URL = "https://example.org/feed"


def feed(*ids):
    items = "".join(
        f"<item><guid>{id}</guid><title>Same title</title>"
        f"<link>https://example.org/{id}</link>"
        "<description>&lt;p&gt;Security &lt;b&gt;update&lt;/b&gt;&lt;/p&gt;"
        "&lt;script&gt;bad()&lt;/script&gt;</description></item>"
        for id in ids
    )
    return f'<rss version="2.0"><channel><title>Updates</title>{items}</channel></rss>'.encode()


class ParserTests(unittest.TestCase):
    def test_rss_uses_identity_not_title_and_strips_html(self):
        entries = parse_feed(feed("a", "b", "a"), URL)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["content"], "Security update")
        self.assertEqual(entries[0]["feed_url"], URL)

    def test_atom_and_content_fallback(self):
        data = b'''<feed xmlns="http://www.w3.org/2005/Atom">
          <title>Atom</title><entry><id>abc</id><title>Update</title>
          <link href="https://example.org/abc"/>
          <content type="html">&lt;p&gt;Details&lt;/p&gt;</content>
          </entry></feed>'''
        entry = parse_feed(data, URL)[0]
        self.assertEqual(entry["id"], "abc")
        self.assertEqual(entry["content"], "Details")

    def test_rejects_non_feed(self):
        with self.assertRaises(ValueError):
            parse_feed(b"<html>Not a feed</html>", URL)

    def test_identity_fallback_is_stable(self):
        data = b'<rss version="2.0"><channel><item><title>A</title></item></channel></rss>'
        self.assertEqual(parse_feed(data, URL), parse_feed(data, URL))
        self.assertTrue(parse_feed(data, URL)[0]["id"])


class PollTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "rss.sqlite3")
        self.state = FeedState(self.path)
        self.body = feed("a")
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=self.body)
        ))
        self.deliver = AsyncMock(return_value=True)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.state.close()
        self.tmp.cleanup()

    async def poll(self, **kwargs):
        await poll_feeds(self.client, self.state, [URL], self.deliver, **kwargs)

    async def test_baseline_then_new_entries_and_restart(self):
        await self.poll()
        self.deliver.assert_not_awaited()
        self.body = feed("b", "a")
        await self.poll()
        self.assertEqual(self.deliver.await_args.args[0][0]["id"], "b")
        self.state.close()
        self.state = FeedState(self.path)
        await self.poll()
        self.assertEqual(self.deliver.await_count, 1)

    async def test_send_existing_batches_and_deduplicates(self):
        self.body = feed("c", "b", "a")
        await self.poll(send_existing=True, batch_size=2)
        batches = [call.args[0] for call in self.deliver.await_args_list]
        self.assertEqual([[e["id"] for e in batch] for batch in batches], [["a", "b"], ["c"]])
        await self.poll(send_existing=True)
        self.assertEqual(self.deliver.await_count, 2)

    async def test_delivery_failure_retries(self):
        self.deliver.return_value = False
        await self.poll(send_existing=True)
        self.assertFalse(self.state.initialized(URL))
        self.deliver.return_value = True
        await self.poll(send_existing=True)
        self.assertEqual(self.deliver.await_count, 2)
        self.assertEqual(self.state.unseen(URL, parse_feed(self.body, URL)), [])

    async def test_bad_feed_does_not_baseline_or_block_next_feed(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(500) if str(req.url) == URL
            else httpx.Response(200, content=feed("a"))
        )) as client:
            with self.assertLogs("daemon.rss", level="ERROR"):
                await poll_feeds(client, self.state, [URL, URL + "2"], self.deliver,
                                 send_existing=True)
        self.assertFalse(self.state.initialized(URL))
        self.assertTrue(self.state.initialized(URL + "2"))
        self.deliver.assert_awaited_once()

    async def test_empty_feed_establishes_baseline(self):
        self.body = feed()
        await self.poll()
        self.body = feed("a")
        await self.poll()
        self.deliver.assert_awaited_once()

    async def test_state_is_scoped_to_feed(self):
        entries = parse_feed(feed("a"), URL)
        self.state.mark(URL, entries)
        self.assertEqual(self.state.unseen(URL + "2", entries), entries)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rss_disabled_without_config(self):
        await rss_loop({}, AsyncMock())

    async def test_invalid_interval_rejected(self):
        with self.assertRaises(ValueError):
            await rss_loop({"rss": {"feeds": [URL], "interval": 0}}, AsyncMock())

    async def test_model_failure_produces_source_report(self):
        entries = parse_feed(feed("a"), URL)
        with patch("agent.agent.create_agent", side_effect=RuntimeError("no model")):
            with self.assertLogs("agent.agent", level="WARNING"):
                report = await run_rss(entries, {})
        self.assertEqual(report.severity, "info")
        self.assertIn("https://example.org/a", render_markdown(report))

    async def test_rss_agent_receives_entries_as_rss_not_alerts(self):
        report = fallback_rss_report(parse_feed(feed("a"), URL))
        agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=report)))
        with patch("agent.agent.create_agent", return_value=agent):
            self.assertIs(await run_rss(parse_feed(feed("a"), URL), {}), report)
        self.assertTrue(agent.run.await_args.args[0].startswith("RSS UPDATES"))

    async def test_daemon_routes_rss_directly_to_agent_and_sinks(self):
        entries = parse_feed(feed("a"), URL)
        report = fallback_rss_report(entries)
        sink = SimpleNamespace(send=AsyncMock())

        async def one_poll(cfg, deliver):
            self.assertTrue(await deliver(entries))

        with patch("main.metrics_loop", new=AsyncMock()), \
             patch("main.rss_loop", new=one_poll), \
             patch("main.run_rss", new=AsyncMock(return_value=report)) as agent, \
             patch("main.check_alerts") as alerts:
            await daemon_loop({}, [sink])
        agent.assert_awaited_once_with(entries, {})
        alerts.assert_not_called()
        sink.send.assert_awaited_once_with(report, render_markdown(report))

    async def test_sink_failures_do_not_block_other_sinks(self):
        report = AlertReport(title="RSS updates", summary="News", severity="info")
        bad = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("offline")))
        good = SimpleNamespace(send=AsyncMock())
        with self.assertLogs("main", level="ERROR"):
            self.assertFalse(await send_report(report, [bad, good]))
        good.send.assert_awaited_once()

    async def test_discord_http_failure_propagates(self):
        response = httpx.Response(500, request=httpx.Request("POST", URL))
        client = AsyncMock()
        client.post.return_value = response
        with patch("sinks.discord.httpx.AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            with self.assertLogs("sinks.discord", level="ERROR"):
                with self.assertRaises(httpx.HTTPStatusError):
                    await DiscordWebhookSink(URL).send(
                        AlertReport(title="RSS", summary="News"), "News"
                    )

    async def test_email_failure_propagates(self):
        sink = EmailSink({"host": "smtp.example.org", "to": "ash@example.org"})
        with patch("sinks.email.aiosmtplib.send", side_effect=RuntimeError("offline")):
            with self.assertLogs("sinks.email", level="ERROR"):
                with self.assertRaises(RuntimeError):
                    await sink.send(AlertReport(title="RSS", summary="News"), "News")


if __name__ == "__main__":
    unittest.main()
