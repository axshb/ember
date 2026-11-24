import unittest

from pydantic import ValidationError

from agent.report import AlertReport, fallback_report, render_markdown


class ReportTests(unittest.TestCase):
    def test_markdown_includes_findings_and_actions(self):
        report = AlertReport(
            title="CPU busy",
            severity="critical",
            summary="CPU usage is high.",
            findings=["CPU at 99%"],
            actions=["Check running processes"],
        )
        self.assertEqual(
            render_markdown(report),
            "## CPU busy\n**Severity:** critical\n\nCPU usage is high.\n\n"
            "**Findings**\n- CPU at 99%\n\n"
            "**Recommended actions**\n- Check running processes",
        )

    def test_markdown_omits_empty_sections(self):
        report = AlertReport(title="Recovered", summary="All clear.")
        self.assertEqual(
            render_markdown(report),
            "## Recovered\n**Severity:** warning\n\nAll clear.",
        )

    def test_invalid_reports_are_rejected(self):
        for overrides in (
            {"severity": "urgent"},
            {"title": "x" * 121},
            {"summary": "x" * 2001},
            {"findings": ["finding"] * 11},
            {"actions": ["action"] * 11},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                AlertReport(**{"title": "Alert", "summary": "Details", **overrides})

    def test_fallback_preserves_raw_alert_details(self):
        report = fallback_report([
            {"path": "system.cpu.percent", "op": ">", "value": 80, "measured": 95},
            {"path": "system.memory.percent", "op": ">=", "value": 90},
        ])
        self.assertEqual(report.severity, "warning")
        self.assertEqual(report.findings, [
            "system.cpu.percent > 80 (measured: 95)",
            "system.memory.percent >= 90 (measured: ?)",
        ])
        self.assertTrue(report.actions)

    def test_fallback_caps_findings_when_many_alerts_fire(self):
        report = fallback_report([{"path": f"p{i}", "op": ">", "value": 1} for i in range(15)])
        self.assertEqual(len(report.findings), 10)
        self.assertEqual(report.findings[-1], "+6 more")
        self.assertEqual(len(fallback_report([{"path": f"p{i}", "op": ">", "value": 1} for i in range(10)]).findings), 10)

    def test_fallback_truncates_long_titles(self):
        report = fallback_report([{"path": "x" * 200, "op": ">", "value": 1}])
        self.assertEqual(len(report.title), 120)
