import unittest
from unittest.mock import Mock, patch

from docker.errors import NotFound

from daemon.containers import collect_container_metrics

MB = 2**20
STATS = {
    "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000, "online_cpus": 4},
    "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
    "memory_stats": {"usage": 600 * MB, "limit": 1000 * MB, "stats": {"inactive_file": 100 * MB}},
    "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}},
}


def _container(name, stats):
    c = Mock(status="running")
    c.name = name
    if isinstance(stats, Exception):
        c.stats.side_effect = stats
    else:
        c.stats.return_value = stats
    return c


class ContainerMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_computes_cpu_and_memory_like_docker_stats(self):
        client = Mock()
        client.containers.list.return_value = [_container("web", STATS), _container("gone", NotFound("removed"))]
        with patch("daemon.containers.docker.from_env", return_value=client):
            metrics = await collect_container_metrics()

        client.close.assert_called_once()
        [web] = metrics
        self.assertEqual(web.name, "web")
        self.assertEqual(web.cpu_percent, 80.0)  # 200 / 1000 * 4 cpus * 100
        self.assertEqual(web.memory_usage, 500)  # MB, 600 - 100 inactive_file
        self.assertEqual(web.memory_percent, 50.0)
        self.assertEqual((web.network_rx, web.network_tx), (10, 20))

    async def test_first_sample_without_precpu_reports_zero_cpu(self):
        client = Mock()
        client.containers.list.return_value = [_container("web", {"cpu_stats": STATS["cpu_stats"]})]
        with patch("daemon.containers.docker.from_env", return_value=client):
            [web] = await collect_container_metrics()
        self.assertEqual((web.cpu_percent, web.memory_usage, web.memory_percent), (0.0, 0, 0.0))
