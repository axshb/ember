import unittest

from daemon.alerts import RESOLVE_AFTER, check_alerts


def fired(metrics, rules):
    return check_alerts(metrics, rules, {}, 1000)[0]


class PathTests(unittest.TestCase):
    def setUp(self):
        self.metrics = {
            "system": {"cpu": {"percent": 90}},
            "containers": [{"name": "web", "memory_usage": 512}],
        }
        self.alert = {"path": "system.cpu.percent", "op": ">", "value": 80}

    def test_fired_alert_includes_measurement_without_changing_config(self):
        self.assertEqual(fired(self.metrics, [self.alert]), [{**self.alert, "measured": 90}])
        self.assertNotIn("measured", self.alert)

    def test_comparison_operators(self):
        for op, threshold, fires in [
            (">", 90, False), (">", 89, True), ("<", 90, False),
            ("<", 91, True), (">=", 90, True), ("<=", 90, True),
        ]:
            with self.subTest(op=op, threshold=threshold):
                self.assertEqual(bool(fired(self.metrics, [{**self.alert, "op": op, "value": threshold}])), fires)

    def test_containers_can_be_selected_by_name_or_index(self):
        for path in ("containers.web.memory_usage", "containers.0.memory_usage"):
            with self.subTest(path=path):
                alert = {"path": path, "op": ">", "value": 500}
                self.assertEqual(fired(self.metrics, [alert]), [{**alert, "measured": 512}])

    def test_dotted_container_names_resolve(self):
        metrics = {"containers": [{"name": "svc", "cpu": 1}, {"name": "svc.1.abc", "cpu": 95}]}
        alert = {"path": "containers.svc.1.abc.cpu", "op": ">", "value": 90}
        self.assertEqual(fired(metrics, [alert]), [{**alert, "measured": 95}])

    def test_paths_to_non_numbers_and_missing_metrics_are_skipped(self):
        metrics = {"system": {"cpu": {"name": "cpu", "used": None, "percent": 90}}, "containers": []}
        for path in ("system.cpu.name", "system.cpu.used", "system.cpu", "system.cpu.percent.x",
                     "system.missing", "containers.9.cpu", "containers.gone.cpu"):
            with self.subTest(path=path):
                self.assertEqual(fired(metrics, [{"path": path, "op": ">", "value": 1}]), [])


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.firing = {}
        self.rule = {"path": "system.cpu.percent", "op": ">", "value": 80, "remind": 0}

    def poll(self, cpu, now, rules=None):
        out = check_alerts({"system": {"cpu": {"percent": cpu}}}, rules or [self.rule], self.firing, now)
        return [len(x) for x in out]  # fired, resolved, reminders

    def test_fires_once_while_breached(self):
        self.assertEqual(self.poll(90, 0), [1, 0, 0])
        for t in (10, 20, 3600):
            self.assertEqual(self.poll(95, t), [0, 0, 0])

    def test_resolves_only_after_staying_clear(self):
        self.poll(90, 0)
        self.assertEqual(self.poll(50, 10), [0, 0, 0])
        self.assertEqual(self.poll(90, 20), [0, 0, 0])  # breached again resets the wait
        self.assertEqual(self.poll(50, 30), [0, 0, 0])
        self.assertEqual(self.poll(50, 30 + RESOLVE_AFTER - 1), [0, 0, 0])
        _, resolved, _ = check_alerts({"system": {"cpu": {"percent": 50}}}, [self.rule], self.firing, 30 + RESOLVE_AFTER)
        self.assertEqual(resolved, [{**self.rule, "measured": 50, "since": 0}])
        self.assertEqual(self.poll(90, 200), [1, 0, 0])  # can fire again afterwards

    def test_reminds_every_interval_while_firing(self):
        rule = {**self.rule, "remind": 60}
        self.poll(90, 0, [rule])
        self.assertEqual(self.poll(90, 3599, [rule]), [0, 0, 0])
        self.assertEqual(self.poll(90, 3600, [rule]), [0, 0, 1])
        self.assertEqual(self.poll(90, 7199, [rule]), [0, 0, 0])
        self.assertEqual(self.poll(90, 7200, [rule]), [0, 0, 1])

    def test_two_rules_on_one_path_are_independent(self):
        warn, crit = self.rule, {**self.rule, "value": 95}
        self.assertEqual(self.poll(90, 0, [warn, crit]), [1, 0, 0])
        self.assertEqual(self.poll(97, 10, [warn, crit]), [1, 0, 0])

    def test_missing_metric_keeps_state(self):
        self.poll(90, 0)
        for t in (10, 10 + RESOLVE_AFTER * 2):
            check_alerts({"system": {}}, [self.rule], self.firing, t)
        self.assertEqual(len(self.firing), 1)

    def test_removed_or_edited_rules_drop_their_state(self):
        self.poll(90, 0)
        edited = {**self.rule, "value": 85}
        self.assertEqual(self.poll(90, 10, [edited]), [1, 0, 0])
        self.assertEqual(list(self.firing), ["system.cpu.percent > 85"])
        check_alerts({}, [], self.firing, 20)
        self.assertEqual(self.firing, {})
