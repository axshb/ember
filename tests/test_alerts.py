import unittest

from daemon.alerts import check_alerts, rule_key
from daemon.state import RESOLVE_AFTER, AlertState


class MemoryStore:
    def __init__(self, saved=None):
        self.saved = dict(saved or {})

    def load_alert_state(self):
        return {k: dict(v) for k, v in self.saved.items()}

    def save_alert_state(self, key, entry):
        self.saved[key] = dict(entry)

    def delete_alert_state(self, key):
        self.saved.pop(key, None)


def fired(metrics, alerts):
    return check_alerts(metrics, alerts, AlertState(MemoryStore()), now=1000)["fired"]


class PathTests(unittest.TestCase):
    def setUp(self):
        self.metrics = {
            "system": {"cpu": {"percent": 90}},
            "containers": [{"name": "web", "memory_usage": 512}],
        }
        self.alert = {"path": "system.cpu.percent", "op": ">", "value": 80}

    def test_fired_alert_includes_measurement_without_changing_config(self):
        self.assertEqual(fired(self.metrics, [self.alert]), [{**self.alert, "measured": 90, "since": 1000}])
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
                self.assertEqual(fired(self.metrics, [alert]), [{**alert, "measured": 512, "since": 1000}])

    def test_dotted_container_names_resolve(self):
        metrics = {"containers": [{"name": "svc", "cpu": 1}, {"name": "svc.1.abc", "cpu": 95}]}
        alert = {"path": "containers.svc.1.abc.cpu", "op": ">", "value": 90}
        self.assertEqual(fired(metrics, [alert]), [{**alert, "measured": 95, "since": 1000}])

    def test_paths_to_non_numbers_and_missing_metrics_are_skipped(self):
        metrics = {"system": {"cpu": {"name": "cpu", "used": None, "percent": 90}}, "containers": []}
        for path in ("system.cpu.name", "system.cpu.used", "system.cpu", "system.cpu.percent.x",
                     "system.missing", "containers.9.cpu", "containers.gone.cpu"):
            with self.subTest(path=path):
                self.assertEqual(fired(metrics, [{"path": path, "op": ">", "value": 1}]), [])


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.state = AlertState(self.store)
        self.rule = {"path": "system.cpu.percent", "op": ">", "value": 80, "remind": 0}

    def poll(self, cpu, now, rules=None):
        metrics = {"system": {"cpu": {"percent": cpu}}}
        out = check_alerts(metrics, rules or [self.rule], self.state, now)
        return {k: v for k, v in out.items() if v}

    def test_fires_once_while_breached(self):
        self.assertEqual(list(self.poll(90, 0)), ["fired"])
        for t in (10, 20, 3600):
            self.assertEqual(self.poll(95, t), {})

    def test_resolves_only_after_staying_clear(self):
        self.poll(90, 0)
        self.assertEqual(self.poll(50, 10), {})
        self.assertEqual(self.poll(90, 20), {})  # breached again resets the hold-down
        self.assertEqual(self.poll(50, 30), {})
        self.assertEqual(self.poll(50, 30 + RESOLVE_AFTER - 1), {})
        resolved = self.poll(50, 30 + RESOLVE_AFTER)["resolved"]
        self.assertEqual(resolved, [{**self.rule, "measured": 50, "since": 0}])
        self.assertEqual(list(self.poll(90, 200)), ["fired"])  # can fire again afterwards

    def test_reminds_every_interval_while_firing(self):
        rule = {**self.rule, "remind": 60}
        self.poll(90, 0, [rule])
        self.assertEqual(self.poll(90, 3599, [rule]), {})
        self.assertEqual(self.poll(90, 3600, [rule])["remind"][0]["since"], 0)
        self.assertEqual(self.poll(90, 7199, [rule]), {})
        self.assertIn("remind", self.poll(90, 7200, [rule]))

    def test_state_survives_restart(self):
        self.poll(90, 0)
        self.store = MemoryStore(self.store.saved)  # restart: fresh tracker on the same db
        self.state = AlertState(self.store)
        self.assertEqual(self.poll(90, 10), {})
        self.poll(50, 20)
        self.assertIn("resolved", self.poll(50, 20 + RESOLVE_AFTER))
        self.assertEqual(self.store.saved, {})

    def test_two_rules_on_one_path_are_independent(self):
        warn, crit = self.rule, {**self.rule, "value": 95}
        self.assertEqual(len(self.poll(90, 0, [warn, crit])["fired"]), 1)
        self.assertEqual(self.poll(97, 10, [warn, crit])["fired"], [{**crit, "measured": 97, "since": 10}])

    def test_missing_metric_keeps_state(self):
        self.poll(90, 0)
        for t in (10, 10 + RESOLVE_AFTER * 2):
            self.assertEqual(check_alerts({"system": {}}, [self.rule], self.state, t)["resolved"], [])
        self.assertIsNotNone(self.state.since(rule_key(self.rule)))

    def test_removed_or_edited_rules_drop_their_state_silently(self):
        self.poll(90, 0)
        edited = {**self.rule, "value": 85}
        self.assertEqual(list(self.poll(90, 10, [edited])), ["fired"])
        self.assertEqual(list(self.store.saved), [rule_key(edited)])
        self.assertEqual(check_alerts({}, [], self.state, 20), {"fired": [], "resolved": [], "remind": []})
        self.assertEqual(self.store.saved, {})

    def test_prune_only_touches_its_own_prefix(self):
        self.state.update("script:backup", True, 0)
        self.poll(90, 0)
        check_alerts({}, [], self.state, 10)
        self.assertEqual(list(self.store.saved), ["script:backup"])
