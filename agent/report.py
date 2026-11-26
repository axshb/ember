from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["info", "warning", "critical"]


class AlertReport(BaseModel):
    title: str = Field(max_length=120)
    severity: Severity = "warning"
    summary: str = Field(max_length=2000)
    findings: list[str] = Field(default_factory=list, max_length=10)
    actions: list[str] = Field(default_factory=list, max_length=10)


def render_markdown(report: AlertReport) -> str:
    lines = [
        f"## {report.title}",
        f"**Severity:** {report.severity}",
        "",
        report.summary,
    ]
    if report.findings:
        lines += ["", "**Findings**", *[f"- {f}" for f in report.findings]]
    if report.actions:
        lines += ["", "**Recommended actions**", *[f"- {a}" for a in report.actions]]
    return "\n".join(lines)


def resolved_report(alert: dict, minutes: int) -> AlertReport:
    measured = alert["measured"]
    if isinstance(measured, float):
        measured = round(measured, 1)
    return AlertReport(
        title=f"Resolved: {alert['path']}"[:120],
        severity="info",
        summary=f"{alert['path']} is back to {measured} (rule: {alert['op']} {alert['value']}) after {minutes} min.",
    )


def fallback_report(alerts: list[dict], summary: str = "The triage agent could not complete its analysis. Raw alert data below.") -> AlertReport:
    """Report built from raw alert data when the triage agent fails."""
    paths = ", ".join(a["path"] for a in alerts)
    findings = [
        f"{a['path']} {a['op']} {a['value']} (measured: {a.get('measured', '?')})"
        for a in alerts
    ]
    findings += [f"{a['path']} output:\n{a['output'][-800:]}" for a in alerts if a.get("output")]
    return AlertReport(
        title=f"Alert fired: {paths}"[:120],
        severity="warning",
        summary=summary,
        findings=findings[:10],
        actions=["Re-check the affected metric or re-run triage manually."],
    )
