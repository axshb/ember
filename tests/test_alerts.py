import unittest
from unittest.mock import patch

from agent.report import resolved_report
from daemon.alerts import check_alerts


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.metrics = {
            "system": {"cpu": {"percent": 90}},
            "containers": [{"name": "web", "memory_usage": 512}],
        }
        self.alert = {"path": "system.cpu.percent", "op": ">", "value": 80}
        self.key = "system.cpu.percent > 80"

    def check(self, alerts, firing, last_fired, now=1000, cpu=90):
        self.metrics["system"]["cpu"]["percent"] = cpu
        with patch("daemon.alerts.time", return_value=now):
            return check_alerts(self.metrics, alerts, firing, last_fired)

    def test_fired_alert_includes_measurement_without_changing_config(self):
        firing, last_fired = set(), {}
        fired, resolved = self.check([self.alert], firing, last_fired)
        self.assertEqual(fired, [{**self.alert, "measured": 90}])
        self.assertEqual(resolved, [])
        self.assertEqual((firing, last_fired), ({self.key}, {self.key: 1000}))
        self.assertNotIn("measured", self.alert)

    def test_fires_once_while_breached_then_resolves(self):
        firing, last_fired = set(), {}
        self.assertEqual(len(self.check([self.alert], firing, last_fired, now=1000)[0]), 1)
        self.assertEqual(self.check([self.alert], firing, last_fired, now=1010), ([], []))
        fired, resolved = self.check([self.alert], firing, last_fired, now=1020, cpu=50)
        self.assertEqual(fired, [])
        self.assertEqual(resolved, [{**self.alert, "measured": 50, "since": 1000}])
        self.assertEqual(firing, set())

    def test_cooldown_stops_refiring_after_a_resolve(self):
        alert = {**self.alert, "cooldown": 2}
        firing, last_fired = set(), {}
        self.check([alert], firing, last_fired, now=1000)
        self.check([alert], firing, last_fired, now=1010, cpu=50)
        self.assertEqual(self.check([alert], firing, last_fired, now=1129), ([], []))
        self.assertEqual(len(self.check([alert], firing, last_fired, now=1130)[0]), 1)

    def test_cooldown_counts_from_the_resolve(self):
        alert = {**self.alert, "cooldown": 2}
        firing, last_fired = set(), {}
        self.check([alert], firing, last_fired, now=1000)
        self.check([alert], firing, last_fired, now=2000, cpu=50)  # long alert, then a dip
        self.assertEqual(self.check([alert], firing, last_fired, now=2010), ([], []))
        self.assertEqual(len(self.check([alert], firing, last_fired, now=2120)[0]), 1)

    def test_rules_on_the_same_path_are_separate(self):
        warn, crit = self.alert, {**self.alert, "value": 85}
        fired, _ = self.check([warn, crit], set(), {})
        self.assertEqual(len(fired), 2)

    def test_comparison_operators(self):
        for op, threshold, fires in [
            (">", 90, False),
            (">", 89, True),
            ("<", 90, False),
            ("<", 91, True),
            (">=", 90, True),
            ("<=", 90, True),
            ("==", 90, True),
            ("==", 89, False),
        ]:
            with self.subTest(op=op, threshold=threshold):
                alert = {**self.alert, "op": op, "value": threshold}
                fired, _ = self.check([alert], set(), {})
                self.assertEqual(bool(fired), fires)

    def test_containers_can_be_selected_by_name_or_index(self):
        for path in ("containers.web.memory_usage", "containers.0.memory_usage"):
            with self.subTest(path=path):
                alert = {"path": path, "op": ">", "value": 500}
                fired, _ = self.check([alert], set(), {})
                self.assertEqual(fired, [{**alert, "measured": 512}])

    def test_missing_metrics_keep_their_state(self):
        firing = {"containers.gone.memory_usage > 80"}
        alerts = [
            {**self.alert, "path": path}
            for path in ("system.missing", "containers.9.memory_usage", "containers.gone.memory_usage")
        ]
        self.assertEqual(self.check(alerts, firing, {}), ([], []))
        self.assertEqual(firing, {"containers.gone.memory_usage > 80"})

    def test_resolved_report(self):
        report = resolved_report({**self.alert, "measured": 41.234}, minutes=12)
        self.assertEqual(report.severity, "info")
        self.assertEqual(report.summary, "system.cpu.percent is back to 41.2 (rule: > 80) after 12 min.")
        status = resolved_report({"path": "containers.web.status", "op": "==", "value": "exited", "measured": "running"}, 3)
        self.assertIn("back to running", status.summary)
