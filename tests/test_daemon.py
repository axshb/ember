import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import main


class ServerTests(unittest.TestCase):
    def test_proxy_headers_are_off(self):
        # with them on, X-Forwarded-For from 127.0.0.1 would rewrite the client past NoLocalClients
        server = main.build_server(Mock(), {})
        self.assertFalse(server.config.proxy_headers)


class DaemonLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failing_poll_does_not_kill_the_loop(self):
        store = Mock()
        store.get_config.side_effect = [{"polling": {"interval": 3}}, {"polling": {}}]
        sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
        with (
            patch("main.tick", AsyncMock(side_effect=RuntimeError("boom"))) as tick,
            patch("main.asyncio.sleep", sleep),
            self.assertLogs("main", "ERROR") as logs,
            self.assertRaises(asyncio.CancelledError),
        ):
            await main.daemon_loop(store, {})

        self.assertEqual(tick.await_count, 1)  # second config has no interval, fails before tick
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [3, 10])
        self.assertEqual(len(logs.output), 2)


class TickTests(unittest.IsolatedAsyncioTestCase):
    async def test_fire_runs_triage_then_resolve_sends_a_short_notice(self):
        from agent.report import AlertReport
        from daemon.state import RESOLVE_AFTER, AlertState
        from tests.test_alerts import MemoryStore

        state = AlertState(MemoryStore())
        store = Mock(prompt_overrides=Mock(return_value={}))
        sink = Mock(send=AsyncMock(), notify=AsyncMock())
        cfg = {"alerts": [{"path": "system.cpu.percent", "op": ">", "value": 80, "remind": 0}]}
        metrics = Mock()
        report = AlertReport(title="CPU busy", summary="x")

        async def poll(cpu, now):
            metrics.model_dump.return_value = {"system": {"cpu": {"percent": cpu}}}
            with (
                patch("main.collect_metrics", AsyncMock(return_value=metrics)),
                patch("main.catalog", return_value=[{"path": "system.cpu.percent", "label": "CPU usage", "unit": "%"}]),
                patch("main.build_sinks", return_value=[sink]),
                patch("main.run_alert", AsyncMock(return_value=report)) as run_alert,
                patch("main.load_prompts"),
                patch("main.time.time", return_value=now),
            ):
                await main.tick(cfg, store, {}, state)
            return run_alert

        run_alert = await poll(90, 0)
        self.assertEqual(run_alert.await_args.args[0], [
            {"path": "system.cpu.percent", "op": ">", "value": 80, "measured": 90, "state": "fired"},
        ])
        sink.send.assert_awaited_once()

        run_alert = await poll(95, 10)
        run_alert.assert_not_awaited()
        await poll(40, 20)
        await poll(40, 47 * 60)  # clear since 20s, well past RESOLVE_AFTER
        sink.notify.assert_awaited_once_with(
            "resolved", "Resolved: CPU usage", "CPU usage is back to 40% (rule: > 80%) after 47m.",
        )
        self.assertEqual(store.add_report.call_count, 2)
        self.assertEqual(store.add_report.call_args.args[1]["severity"], "info")


class DurationTests(unittest.TestCase):
    def test_durations(self):
        for seconds, text in [(30, "<1m"), (47 * 60, "47m"), (3600, "1h"), (3660 * 6, "6h 6m"),
                              (86400, "1d"), (86400 + 7200, "1d 2h")]:
            self.assertEqual(main._duration(seconds), text)


class TickFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from daemon.state import AlertState
        from tests.test_alerts import MemoryStore

        self.state = AlertState(MemoryStore())
        self.store = Mock(prompt_overrides=Mock(return_value={}))
        self.sink = Mock(send=AsyncMock(), notify=AsyncMock())
        self.cfg = {"alerts": [{"path": "system.cpu.percent", "op": ">", "value": 80, "remind": 5}]}

    async def poll(self, cpu, now, run_alert=None):
        from agent.report import AlertReport

        metrics = Mock()
        metrics.model_dump.return_value = {"system": {"cpu": {"percent": cpu}}}
        run_alert = run_alert or AsyncMock(return_value=AlertReport(title="t", summary="s"))
        with (
            patch("main.collect_metrics", AsyncMock(return_value=metrics)),
            patch("main.catalog", return_value=[]),
            patch("main.build_sinks", return_value=[self.sink]),
            patch("main.run_alert", run_alert),
            patch("main.load_prompts"),
            patch("main.time.time", return_value=now),
        ):
            await main.tick(self.cfg, self.store, {}, self.state)
        return run_alert

    async def test_failed_report_undoes_the_fire_so_it_retries(self):
        with self.assertLogs("main", "ERROR"):
            await self.poll(90, 0, AsyncMock(side_effect=RuntimeError("boom")))
        self.sink.send.assert_not_awaited()
        run_alert = await self.poll(90, 10)
        run_alert.assert_awaited_once()
        self.sink.send.assert_awaited_once()

    async def test_history_failure_still_sends_and_keeps_state(self):
        self.store.add_report.side_effect = OSError("disk full")
        with self.assertLogs("main", "ERROR"):
            await self.poll(90, 0)
        self.sink.send.assert_awaited_once()
        (await self.poll(90, 10)).assert_not_awaited()  # not re-fired

    async def test_reminder_notifies_without_history_or_agent(self):
        await self.poll(90, 0)
        self.store.add_report.reset_mock()
        run_alert = await self.poll(90, 300)
        run_alert.assert_not_awaited()
        self.sink.notify.assert_awaited_once_with(
            "remind", "Still firing: system.cpu.percent",
            "system.cpu.percent is at 90 (rule: > 80), firing for 5m.",
        )
        self.store.add_report.assert_not_called()

    async def test_resolve_still_notifies_when_history_fails(self):
        from daemon.state import RESOLVE_AFTER

        await self.poll(90, 0)
        await self.poll(40, 10)
        self.store.add_report.side_effect = OSError("disk full")
        with self.assertLogs("main", "ERROR"):
            await self.poll(40, 10 + RESOLVE_AFTER)
        self.assertEqual(self.sink.notify.await_args.args[0], "resolved")
