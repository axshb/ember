import asyncio
import unittest
from unittest.mock import patch

from agent.tools import command_tools
from daemon.alerts import check_alerts
from daemon.checks import check_rules, run_due_checks

BACKUP = {"name": "backup", "command": ["/opt/check.sh", "--quiet"], "every": "1h"}


class CheckTests(unittest.TestCase):
  def run_checks(self, commands, results, now, code=0):
    with patch("daemon.checks.time.time", return_value=now), \
         patch("daemon.checks.run_command", return_value=(code, "out")) as run:
      asyncio.run(run_due_checks(commands, results))
    return run

  def test_runs_on_start_then_on_schedule(self):
    results = {}
    tool_only = {"name": "uptime", "command": ["uptime"]}
    run = self.run_checks([BACKUP, tool_only], results, now=1000)
    run.assert_called_once_with(["/opt/check.sh", "--quiet"])
    self.assertEqual(self.run_checks([BACKUP], results, now=4599).call_count, 0)
    self.assertEqual(self.run_checks([BACKUP], results, now=4600).call_count, 1)

  def test_failing_check_fires_and_resolves_like_a_rule(self):
    results, firing, last_fired = {}, set(), {}

    def poll(now, code):
      self.run_checks([BACKUP], results, now=now, code=code)
      metrics = {"checks": [{"name": n, "exit_code": r["exit_code"]} for n, r in results.items()]}
      with patch("daemon.alerts.time", return_value=now):
        return check_alerts(metrics, check_rules([BACKUP], results), firing, last_fired)

    fired, _ = poll(1000, code=1)
    self.assertEqual((fired[0]["measured"], fired[0]["output"]), (1, "out"))
    self.assertEqual(poll(4600, code=None), ([], []))  # timed out, still failing
    _, resolved = poll(8200, code=0)
    self.assertEqual(len(resolved), 1)

  def test_tools_take_no_arguments_and_run_the_configured_command(self):
    tool = command_tools([BACKUP])[0]
    self.assertEqual(tool.name, "backup")
    self.assertEqual(tool.function_schema.json_schema, {"type": "object", "properties": {}})
    with patch("agent.tools.run_command", return_value=(3, "nope")) as run:
      self.assertEqual(tool.function(), "nope\n[exit code: 3]")
    run.assert_called_once_with(["/opt/check.sh", "--quiet"])

  def test_stray_tool_arguments_are_ignored(self):
    tool = command_tools([BACKUP])[0]
    with patch("agent.tools.run_command", return_value=(0, "ok")) as run:
      tool.function(argv=["rm", "-rf", "/"], x=1)
    run.assert_called_once_with(["/opt/check.sh", "--quiet"])

  def test_many_failing_checks_still_make_a_report(self):
    from agent.report import fallback_report
    alerts = [{"path": f"checks.c{i}.exit_code", "op": "!=", "value": 0, "measured": 1, "output": "boom"} for i in range(8)]
    self.assertEqual(len(fallback_report(alerts).findings), 10)
