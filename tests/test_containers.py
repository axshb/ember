import unittest
from unittest.mock import Mock

from docker.errors import NotFound

from daemon.containers import _collect, _cpu_percent


def stats(total, pre_total, system, pre_system, cpus=2):
    return {
        "cpu_stats": {"cpu_usage": {"total_usage": total}, "system_cpu_usage": system, "online_cpus": cpus},
        "precpu_stats": {"cpu_usage": {"total_usage": pre_total}, "system_cpu_usage": pre_system},
    }


class ContainerTests(unittest.TestCase):
    def test_cpu_percent_matches_docker_stats(self):
        # 10% of the system delta on 2 cpus -> 20%
        self.assertAlmostEqual(_cpu_percent(stats(200, 100, 2000, 1000)), 20.0)

    def test_cpu_percent_is_zero_without_previous_sample(self):
        self.assertEqual(_cpu_percent(stats(200, 0, 2000, 0)), 0.0)

    def test_stopped_container_is_kept_without_stats(self):
        c = Mock(status="exited")
        c.name = "web"
        m = _collect(c)
        self.assertEqual((m.name, m.status, m.cpu_percent), ("web", "exited", 0))
        c.stats.assert_not_called()

    def test_container_removed_mid_poll_is_skipped(self):
        c = Mock(status="running", **{"stats.side_effect": NotFound("gone")})
        self.assertIsNone(_collect(c))
