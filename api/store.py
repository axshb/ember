import copy
import json
import sqlite3
import time
from pathlib import Path

MAX_REPORTS = 50
SECRETS = [("agent", "api_key"), ("discord", "webhook_url"), ("email", "password")]

DEFAULT_CONFIG = {
    "polling": {"interval": 10},
    "alerts": [
        {"path": "system.cpu.percent", "op": ">", "value": 80, "remind": 0},
        {"path": "system.memory.percent", "op": ">", "value": 80, "remind": 0},
    ],
    "agent": {
        "api_key": "",
        "model": "z-ai/glm-5.3-flash",
        "max_tool_calls": 15,
        "shell": {"default_timeout": 30.0, "max_output_chars": 50_000},
    },
    "sinks": {"discord": False, "email": False},
    "discord": {"webhook_url": ""},
    "email": {
        "host": "", "port": 587, "username": "", "password": "",
        "from": "", "to": "", "starttls": True,
    },
}


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # only used from the event loop, but TestClient runs the app in another thread
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS prompts (name TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY, time REAL NOT NULL, alerts TEXT NOT NULL, report TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_state (key TEXT PRIMARY KEY, data TEXT NOT NULL);
        """)
        self.db.execute("INSERT OR IGNORE INTO config VALUES (1, ?)", (json.dumps(DEFAULT_CONFIG),))
        self.db.commit()

    def get_config(self) -> dict:
        return migrate(json.loads(self.db.execute("SELECT data FROM config").fetchone()[0]))

    def save_config(self, cfg: dict) -> None:
        self.db.execute("UPDATE config SET data = ?", (json.dumps(cfg),))
        self.db.commit()

    def prompt_overrides(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT name, body FROM prompts"))

    def set_prompt(self, name: str, body: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO prompts VALUES (?, ?)", (name, body))
        self.db.commit()

    def reset_prompt(self, name: str) -> None:
        self.db.execute("DELETE FROM prompts WHERE name = ?", (name,))
        self.db.commit()

    def add_report(self, alerts: list[dict], report: dict) -> None:
        self.db.execute(
            "INSERT INTO reports (time, alerts, report) VALUES (?, ?, ?)",
            (time.time(), json.dumps(alerts), json.dumps(report)),
        )
        self.db.execute(
            "DELETE FROM reports WHERE id NOT IN (SELECT id FROM reports ORDER BY id DESC LIMIT ?)",
            (MAX_REPORTS,),
        )
        self.db.commit()

    def load_alert_state(self) -> dict[str, dict]:
        # a bad row is dropped (the alert just fires again) rather than killing the daemon
        out = {}
        for key, data in self.db.execute("SELECT key, data FROM alert_state"):
            try:
                entry = json.loads(data)
                if isinstance(entry["since"], int | float) and isinstance(entry["notified"], int | float) \
                        and isinstance(entry["clear_since"], int | float | None):
                    out[key] = entry
            except (ValueError, KeyError, TypeError):
                pass
        return out

    def save_alert_state(self, key: str, entry: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO alert_state VALUES (?, ?)", (key, json.dumps(entry)))
        self.db.commit()

    def delete_alert_state(self, key: str) -> None:
        self.db.execute("DELETE FROM alert_state WHERE key = ?", (key,))
        self.db.commit()

    def recent_reports(self) -> list[dict]:
        rows = self.db.execute("SELECT id, time, alerts, report FROM reports ORDER BY id DESC")
        return [
            {"id": i, "time": t, "alerts": json.loads(a), "report": json.loads(r)}
            for i, t, a, r in rows
        ]


def migrate(cfg: dict) -> dict:
    # "cooldown" re-fired full triage every N minutes while breached. alerts are stateful now
    # and "remind" means something else, so old cooldowns are dropped rather than converted
    for rule in cfg.get("alerts", []) if isinstance(cfg.get("alerts"), list) else []:
        if isinstance(rule, dict) and "cooldown" in rule:
            rule.pop("cooldown")
            rule.setdefault("remind", 0)
    return cfg


def without_secrets(cfg: dict, mask: str = "") -> dict:
    # set secrets become `mask`, unset ones stay ""
    cfg = copy.deepcopy(cfg)
    for section, key in SECRETS:
        if cfg.get(section, {}).get(key):
            cfg[section][key] = mask
    return cfg
