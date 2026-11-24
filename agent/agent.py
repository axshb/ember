import json
import logging
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from agent.report import AlertReport, fallback_report
from agent.tools import build_tools

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"
DEFAULT_PROMPTS_DIR = CONFIG_DIR / "defaults" / "prompts"

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_CALLS = 15


def load_prompts() -> str:
    """Every *.md file in config/prompts (custom) and config/defaults/prompts,
    concatenated in sorted filename order. A custom file overrides the
    same-named default wholesale; nothing is merged or copied, so git pulls
    never touch customizations (config/prompts is gitignored)."""
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
            base_url="https://openrouter.ai/api/v1",
            api_key=agent_cfg.get("api_key", ""),
        ),
    )


def create_agent(cfg: dict) -> Agent[None, AlertReport]:
    return Agent(
        _build_model(cfg),
        output_type=AlertReport,
        instructions=load_prompts(),
        retries=2,
        tools=build_tools(cfg),
    )


async def run_alert(alerts: list[dict], cfg: dict) -> AlertReport:
    agent = create_agent(cfg)
    max_tool_calls = cfg.get("agent", {}).get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
    try:
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
