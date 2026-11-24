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
