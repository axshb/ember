import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psutil

from starlette.testclient import TestClient

from api.app import MASK, create_app
from api.store import Store
from daemon.containers import ContainerMetrics
from daemon.metrics import Metrics
from daemon.system import ComponentMetrics, SystemMetrics

JSON = {"Content-Type": "application/json"}


class ApiTests(unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.store = Store(str(Path(tmpdir.name) / "ember.db"))
        self.addCleanup(self.store.db.close)
        self.latest = {}
        self.client = TestClient(create_app(self.store, self.latest), base_url="http://localhost")
        self.own_ip = next(
            a.address for addrs in psutil.net_if_addrs().values() for a in addrs
            if a.family.name == "AF_INET" and not a.address.startswith("127.")
        )

    def config(self) -> dict:
        return self.client.get("/api/config").json()

    def test_secrets_are_masked_and_mask_means_unchanged(self):
        cfg = self.config()
        self.assertEqual(cfg["agent"]["api_key"], "")  # unset shows empty, not the mask

        cfg["agent"]["api_key"] = "sk-or-secret"
        self.client.put("/api/config", json=cfg, headers=JSON)
        cfg = self.config()
        self.assertEqual(cfg["agent"]["api_key"], MASK)

        cfg["agent"]["model"] = "other/model"
        self.client.put("/api/config", json=cfg, headers=JSON)
        stored = self.store.get_config()
        self.assertEqual(stored["agent"]["api_key"], "sk-or-secret")
        self.assertEqual(stored["agent"]["model"], "other/model")

    def test_rejects_configs_that_would_break_the_daemon(self):
        for bad in (
            {"polling": {"interval": 0}},
            {"agent": {**self.config()["agent"], "max_tool_calls": None}},
            {"email": {**self.config()["email"], "port": True}},
            {"alerts": [{"path": "system.cpu.percent", "op": "~", "value": 1}]},
            {"alerts": [{"path": "system.cpu.percent", "op": ">", "value": "80"}]},
            {"alerts": [{"path": "", "op": ">", "value": 80}]},
            {"alerts": [{"path": 5, "op": ">", "value": 80}]},
            {"alerts": [{"path": "system.cpu.percent", "op": "==", "value": 80}]},
            {"polling": {"interval": float("inf")}},
            {"alerts": [{"path": "system.cpu.percent", "op": ">", "value": float("nan")}]},
            {"alerts": [1]},
            {"alerts": {"path": "x"}},
            {"email": "nope"},
            {"alerts": [{"path": "system.cpu.percent", "op": ">", "value": 80, "cooldown": -1}]},
        ):
            with self.subTest(bad=bad):
                body = json.dumps({**self.config(), **bad})  # allows NaN/Infinity like a raw client would
                resp = self.client.put("/api/config", content=body, headers=JSON)
                self.assertEqual(resp.status_code, 400)
                self.assertIn("error", resp.json())

    def test_import_rejects_garbage(self):
        for body in ("not json", "[]", '{"config": 1}'):
            with self.subTest(body=body):
                resp = self.client.post("/api/import", content=body, headers=JSON)
                self.assertEqual(resp.json(), {"error": "not an ember export"})

    def test_guards_reject_non_json_writes_and_foreign_hosts(self):
        resp = self.client.put("/api/config", content="{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(resp.status_code, 415)
        resp = self.client.delete("/api/prompts/triage.md")  # no content type at all
        self.assertEqual(resp.status_code, 415)
        resp = self.client.get("/api/config", headers={"Host": "evil.example"})
        self.assertEqual(resp.status_code, 400)

    def test_local_clients_are_refused_when_sandboxed(self):
        with patch("api.app.os.geteuid", return_value=0):
            app = create_app(self.store, self.latest)
        for ip in ("127.0.0.1", "127.0.0.2", self.own_ip):
            with self.subTest(ip=ip):
                client = TestClient(app, base_url="http://localhost", client=(ip, 50000))
                self.assertEqual(client.get("/api/config").status_code, 403)
        for host in ("::ffff:127.0.0.1", f"::ffff:{self.own_ip}", "not-an-ip"):  # mapped / unknown
            with self.subTest(host=host):
                client = TestClient(app, base_url="http://localhost", client=(host, 50000))
                self.assertEqual(client.get("/api/config").status_code, 403)
        # docker's port forward: arrives from the bridge gateway
        client = TestClient(app, base_url="http://localhost", client=("172.22.0.1", 50000))
        self.assertEqual(client.get("/api/config").status_code, 200)

    def test_local_clients_allowed_when_not_sandboxed(self):
        with patch("api.app.os.geteuid", return_value=1000):
            app = create_app(self.store, self.latest)
        client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
        self.assertEqual(client.get("/api/config").status_code, 200)

    def test_prompt_override_and_reset(self):
        self.client.put("/api/prompts/triage.md", json={"body": "custom"}, headers=JSON)
        [prompt] = self.client.get("/api/prompts").json()
        self.assertEqual(prompt["override"], "custom")
        self.assertTrue(prompt["default"].startswith("# Alert triage"))

        self.client.delete("/api/prompts/triage.md", headers=JSON)
        self.assertIsNone(self.client.get("/api/prompts").json()[0]["override"])
        self.assertEqual(self.client.put("/api/prompts/nope.md", json={"body": "x"}, headers=JSON).status_code, 400)

    def test_export_has_no_secrets_and_import_keeps_current_ones(self):
        cfg = self.config()
        cfg["agent"]["api_key"] = "sk-or-secret"
        cfg["polling"]["interval"] = 30
        self.client.put("/api/config", json=cfg, headers=JSON)
        self.client.put("/api/prompts/triage.md", json={"body": "custom"}, headers=JSON)

        exported = self.client.get("/api/export")
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertNotIn("sk-or-secret", exported.text)
        data = exported.json()

        data["config"]["polling"]["interval"] = 60
        self.assertEqual(self.client.post("/api/import", json=data, headers=JSON).json(), {"ok": True})
        stored = self.store.get_config()
        self.assertEqual(stored["polling"]["interval"], 60)
        self.assertEqual(stored["agent"]["api_key"], "sk-or-secret")
        self.assertEqual(self.store.prompt_overrides(), {"triage.md": "custom"})

    def test_metrics_catalog_from_latest_metrics(self):
        self.assertEqual(self.client.get("/api/metrics").json(), [])
        self.latest["metrics"] = Metrics(
            system=SystemMetrics(
                cpu=ComponentMetrics(name="cpu", percent=12.345),
                memory=ComponentMetrics(name="memory", percent=50, used=4, total=8),
                disks=[ComponentMetrics(name="/dev/sda1", percent=61, used=100, total=200)],
            ),
            containers=[ContainerMetrics(
                name="web", status="running", cpu_percent=1, memory_usage=240, memory_percent=3,
                network_rx=0, network_tx=0,
            )],
        )
        metrics = {m["path"]: m for m in self.client.get("/api/metrics").json()}
        self.assertEqual(metrics["system.cpu.percent"], {
            "path": "system.cpu.percent", "label": "CPU usage", "group": "CPU", "name": "usage",
            "unit": "%", "value": 12.35,
        })
        self.assertEqual(metrics["system.disks./dev/sda1.percent"]["label"], "disk /dev/sda1 usage")
        self.assertEqual(metrics["containers.web.memory_usage"]["label"], "container web memory")
        self.assertEqual(metrics["containers.web.memory_usage"]["group"], "container web")
        self.assertEqual(metrics["containers.web.memory_usage"]["unit"], "MB")
        self.assertNotIn("system.cpu.used", metrics)  # None values are skipped
        self.assertNotIn("containers.web.network_rx", metrics)  # not alertable

    def test_reports_capped_newest_first(self):
        for i in range(55):
            self.store.add_report([{"path": "p", "measured": i}], {"title": str(i)})
        reports = self.client.get("/api/reports").json()
        self.assertEqual(len(reports), 50)
        self.assertEqual(reports[0]["report"]["title"], "54")
