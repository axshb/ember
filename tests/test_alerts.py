import unittest
from unittest.mock import patch

from daemon.alerts import check_alerts


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.metrics = {
            "system": {"cpu": {"percent": 90}},
            "containers": [{"name": "web", "memory_usage": 512}],
        }
        self.alert = {"path": "system.cpu.percent", "op": ">", "value": 80}

    def test_fired_alert_includes_measurement_without_changing_config(self):
        last_fired = {}
        with patch("daemon.alerts.time", return_value=1000):
            active = check_alerts(self.metrics, [self.alert], last_fired)

        self.assertEqual(active, [{**self.alert, "measured": 90}])
        self.assertEqual(last_fired, {"system.cpu.percent": 1000})
        self.assertNotIn("measured", self.alert)

    def test_comparison_operators(self):
        for op, threshold, fires in [
            (">", 90, False),
            (">", 89, True),
            ("<", 90, False),
            ("<", 91, True),
            (">=", 90, True),
            ("<=", 90, True),
        ]:
            with self.subTest(op=op, threshold=threshold):
                alert = {**self.alert, "op": op, "value": threshold}
                last_fired = {}
                active = check_alerts(self.metrics, [alert], last_fired)
                self.assertEqual(bool(active), fires)
                self.assertEqual(bool(last_fired), fires)

    def test_containers_can_be_selected_by_name_or_index(self):
        for path in ("containers.web.memory_usage", "containers.0.memory_usage"):
            with self.subTest(path=path):
                alert = {"path": path, "op": ">", "value": 500}
                self.assertEqual(
                    check_alerts(self.metrics, [alert], {}),
                    [{**alert, "measured": 512}],
                )

    def test_dotted_container_names_resolve(self):
        metrics = {"containers": [{"name": "svc", "cpu": 1}, {"name": "svc.1.abc", "cpu": 95}]}
        alert = {"path": "containers.svc.1.abc.cpu", "op": ">", "value": 90}
        self.assertEqual(check_alerts(metrics, [alert], {}), [{**alert, "measured": 95}])

    def test_paths_to_non_numbers_are_skipped(self):
        metrics = {"system": {"cpu": {"name": "cpu", "used": None, "percent": 90}}}
        for path in ("system.cpu.name", "system.cpu.used", "system.cpu", "system.cpu.percent.x"):
            with self.subTest(path=path):
                self.assertEqual(check_alerts(metrics, [{"path": path, "op": ">", "value": 1}], {}), [])

    def test_missing_metrics_are_skipped(self):
        alerts = [
            {**self.alert, "path": path}
            for path in ("system.missing", "containers.9.memory_usage", "containers.gone.memory_usage")
        ]
        last_fired = {}
        self.assertEqual(check_alerts(self.metrics, alerts, last_fired), [])
        self.assertEqual(last_fired, {})

    def test_cooldown_is_in_minutes_and_expires_at_boundary(self):
        alert = {**self.alert, "cooldown": 2}
        last_fired = {alert["path"]: 1000}
        with patch("daemon.alerts.time", return_value=1119):
            self.assertEqual(check_alerts(self.metrics, [alert], last_fired), [])
        self.assertEqual(last_fired[alert["path"]], 1000)

        with patch("daemon.alerts.time", return_value=1120):
            self.assertEqual(len(check_alerts(self.metrics, [alert], last_fired)), 1)
        self.assertEqual(last_fired[alert["path"]], 1120)

    def test_no_cooldown_allows_repeated_alerts(self):
        last_fired = {}
        with patch("daemon.alerts.time", return_value=1000):
            for _ in range(2):
                self.assertEqual(len(check_alerts(self.metrics, [self.alert], last_fired)), 1)
