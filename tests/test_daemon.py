import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import main
from agent.report import AlertReport


class ServerTests(unittest.TestCase):
    def test_proxy_headers_are_off(self):
        # with them on, X-Forwarded-For from 127.0.0.1 would rewrite the client past the API's local-client guard
        server = main.build_server(Mock(), {})
        self.assertFalse(server.config.proxy_headers)


class DaemonLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failing_poll_does_not_kill_the_loop(self):
        store = Mock(get_firing=Mock(return_value={}))
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
    def setUp(self):
        self.store = Mock(prompt_overrides=Mock(return_value={}))
        self.sink = Mock(send=AsyncMock(), notify=AsyncMock())
        self.cfg = {"alerts": [{"path": "system.cpu.percent", "op": ">", "value": 80, "remind": 5}]}
        self.firing = {}
        self.restarts = {}
        self.containers = []  # or an exception: docker is down

    def _containers(self):
        if isinstance(self.containers, Exception):
            raise self.containers
        return self.containers

    async def poll(self, cpu, now, catalog=()):
        metrics = Mock()
        metrics.model_dump.return_value = {"system": {"cpu": {"percent": cpu}}}
        run_alert = AsyncMock(return_value=AlertReport(title="CPU busy", summary="x"))
        with (
            patch("main.collect_metrics", AsyncMock(return_value=metrics)),
            patch("main.catalog", return_value=list(catalog)),
            patch("main.build_sinks", return_value=[self.sink]),
            patch("main.run_alert", run_alert),
            patch("main.load_prompts"),
            patch("main.time.time", return_value=now),
            patch("main.container_states", side_effect=self._containers),
            patch("main.STARTED", 0),
        ):
            await main.tick(self.cfg, self.store, {}, self.firing, self.restarts)
        return run_alert

    async def test_fire_runs_triage_then_resolve_sends_a_short_notice(self):
        cpu = [{"path": "system.cpu.percent", "label": "CPU usage", "unit": "%"}]
        run_alert = await self.poll(90, 0, cpu)
        self.assertEqual(run_alert.await_args.args[0], [
            {"path": "system.cpu.percent", "op": ">", "value": 80, "measured": 90, "state": "fired"},
        ])
        self.sink.send.assert_awaited_once()

        (await self.poll(95, 10, cpu)).assert_not_awaited()
        await self.poll(40, 20, cpu)
        await self.poll(40, 47 * 60, cpu)
        self.sink.notify.assert_awaited_once_with(
            "resolved", "Resolved: CPU usage", "CPU usage is back to 40% (rule: > 80%) after 47m.",
        )
        self.assertEqual(self.store.add_report.call_count, 2)
        self.assertEqual(self.store.add_report.call_args.args[1]["severity"], "info")

    async def test_reminder_notifies_without_history_or_agent(self):
        await self.poll(90, 0)
        self.store.add_report.reset_mock()
        (await self.poll(90, 300)).assert_not_awaited()
        self.sink.notify.assert_awaited_once_with(
            "remind", "Still firing: system.cpu.percent",
            "system.cpu.percent is at 90 (rule: > 80), firing for 5m.",
        )
        self.store.add_report.assert_not_called()

    async def test_a_broken_db_still_sends_and_never_triages_twice(self):
        cpu = [{"path": "system.cpu.percent", "label": "CPU usage", "unit": "%"}]
        self.store.save_firing.side_effect = OSError("disk full")
        self.store.add_report.side_effect = OSError("disk full")
        with self.assertLogs("main", "ERROR"):
            await self.poll(90, 0, cpu)
        self.sink.send.assert_awaited_once()
        (await self.poll(90, 10, cpu)).assert_not_awaited()
        with self.assertLogs("main", "ERROR"):
            await self.poll(40, 20, cpu)
            await self.poll(40, 100, cpu)
        self.sink.notify.assert_awaited_once()  # resolve still goes out
        self.sink.send.assert_awaited_once()

    async def test_quiet_polls_write_nothing(self):
        await self.poll(10, 0)
        self.store.save_firing.assert_not_called()

    async def test_container_crash_triages_then_resolves_when_running_again(self):
        web = {"name": "web", "status": "running", "exit_code": 0, "oom": False, "health": None,
               "restarts": 0, "finished": 0, "policy": "no"}
        self.containers = [web]
        await self.poll(10, 0)
        self.containers = [{**web, "status": "exited", "exit_code": 1, "finished": 5}]
        run_alert = await self.poll(10, 10)
        self.assertEqual(run_alert.await_args.args[0], [{
            "source": "docker", "problem": "crashed", "name": "web", "status": "exited", "exit_code": 1,
            "oom": False, "health": None, "restarts": 0, "state": "fired",
        }])
        self.containers = [web]
        await self.poll(10, 20)
        await self.poll(10, 20 + 60 * 5)
        self.sink.notify.assert_awaited_once_with("resolved", "Resolved: web crashed", "web is running again after 5m.")

    async def test_docker_being_down_does_not_stop_metric_alerts(self):
        self.containers = OSError("no socket")
        with self.assertLogs("main", "WARNING"):
            await self.poll(90, 0)
        self.sink.send.assert_awaited_once()

    async def test_a_broken_sink_does_not_stop_the_others(self):
        broken = Mock(send=AsyncMock(side_effect=OSError("down")))
        with self.assertLogs("main", "ERROR"):
            await main._send([broken, self.sink], lambda sink: sink.send("r", "t"))
        self.sink.send.assert_awaited_once()


class DurationTests(unittest.TestCase):
    def test_durations(self):
        for seconds, text in [(-90, "<1m"), (30, "<1m"), (47 * 60, "47m"), (3600, "1h"), (3660 * 6, "6h 6m"),
                              (86400, "1d"), (86400 + 7200, "1d 2h")]:
            self.assertEqual(main.duration(seconds), text)
