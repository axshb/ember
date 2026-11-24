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


def fallback_report(
    alerts: list[dict],
    summary: str = "The triage agent could not complete its analysis. Raw alert data below.",
) -> AlertReport:
    """Report built from raw alert data when the triage agent fails."""
    paths = ", ".join(f"{a['name']} {a['problem']}" if a.get("source") == "docker" else a["path"] for a in alerts)
    findings = [_raw_finding(a) for a in alerts]
    if len(findings) > 10:  # AlertReport caps findings at 10, raising here would loop the retry
        findings = [*findings[:9], f"+{len(findings) - 9} more"]
    return AlertReport(
        title=f"Alert fired: {paths}"[:120],
        severity="warning",
        summary=summary,
        findings=findings,
        actions=["Check it yourself, the agent couldn't."],
    )


def _raw_finding(a: dict) -> str:
    if a.get("source") == "docker":
        return f"container {a['name']} {a['problem']} (status {a['status']}, exit code {a['exit_code']}, restarts {a['restarts']})"
    return f"{a['path']} {a['op']} {a['value']} (measured: {a.get('measured', '?')})"
