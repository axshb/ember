import json
import logging
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from agent.report import AlertReport, fallback_report
from agent.tools import build_tools

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_CALLS = 15


def default_prompts() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(PROMPTS_DIR.glob("*.md"))}


def load_prompts(overrides: dict[str, str]) -> str:
    # prompts/*.md in filename order, each replaced wholesale by its UI override
    defaults = default_prompts()
    return "\n\n".join(overrides.get(name, body) for name, body in defaults.items())


def _build_model(cfg: dict) -> OpenAIChatModel:
    agent_cfg = cfg.get("agent", {})
    return OpenAIChatModel(
        agent_cfg.get("model", "moonshotai/kimi-k2.6"),
        provider=OpenAIProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key=agent_cfg.get("api_key", ""),
        ),
    )


def create_agent(cfg: dict, instructions: str) -> Agent[None, AlertReport]:
    return Agent(
        _build_model(cfg),
        output_type=AlertReport,
        instructions=instructions,
        retries=2,
        tools=build_tools(cfg),
    )


async def run_alert(alerts: list[dict], cfg: dict, instructions: str) -> AlertReport:
    if not cfg.get("agent", {}).get("api_key"):
        logger.warning("no API key set, sending raw alert data without triage")
        return fallback_report(alerts, "No API key is set, so the agent didn't investigate. Raw alert data below.")
    max_tool_calls = cfg.get("agent", {}).get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    try:
        agent = create_agent(cfg, instructions)
        result = await agent.run(
            "ALERTS:\n" + json.dumps(alerts, indent=2),
            usage_limits=UsageLimits(
                request_limit=max_tool_calls + 5, tool_calls_limit=max_tool_calls
            ),
        )
        return result.output
    except Exception as e:
        logger.warning("alert triage failed: %s", e)
        return fallback_report(alerts)
