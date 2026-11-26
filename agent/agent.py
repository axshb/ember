import json
import logging
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from agent.report import AlertReport, fallback_report
from agent.tools import TOOLS, command_tools

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_PROMPTS_DIR = PROMPTS_DIR / "defaults"

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_CALLS = 15


def load_prompts() -> str:
    # a file in prompts/ replaces the one with the same name in prompts/defaults/
    custom = {p.name: p for p in PROMPTS_DIR.glob("*.md")}
    defaults = {p.name: p for p in DEFAULT_PROMPTS_DIR.glob("*.md")}
    parts = [
        (custom.get(name) or defaults[name]).read_text()
        for name in sorted(custom.keys() | defaults.keys())
    ]
    return "\n\n".join(parts)


def _build_model(cfg: dict) -> OpenAIChatModel:
    agent_cfg = cfg.get("agent", {})
    return OpenAIChatModel(
        agent_cfg.get("model", "moonshotai/kimi-k2.6"),
        provider=OpenAIProvider(
            base_url=agent_cfg.get("base_url", "https://openrouter.ai/api/v1"),
            api_key=agent_cfg.get("api_key", ""),
        ),
    )


def create_agent(cfg: dict) -> Agent[None, AlertReport]:
    return Agent(
        _build_model(cfg),
        output_type=AlertReport,
        instructions=load_prompts(),
        retries=2,
        tools=TOOLS + command_tools(cfg.get("commands", [])),
    )


async def run_alert(alerts: list[dict], cfg: dict) -> AlertReport:
    if not cfg.get("agent", {}).get("api_key"):
        return fallback_report(alerts)
    max_tool_calls = cfg.get("agent", {}).get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    try:
        agent = create_agent(cfg)
        result = await agent.run(
            "ALERTS:\n" + json.dumps(alerts, indent=2),
            usage_limits=UsageLimits(
                request_limit=max_tool_calls + 5, tool_calls_limit=max_tool_calls
            ),
        )
        return result.output
    except Exception:
        logger.exception("alert triage failed")
        return fallback_report(alerts)
