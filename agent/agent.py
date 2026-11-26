import json
import logging
from pathlib import Path

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits
from pydantic_ai_harness import Shell, FileSystem
from pydantic_ai_harness.shell import LLM_API_KEY_ENV_PATTERNS

from agent.report import AlertReport, fallback_report

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_PROMPTS_DIR = PROMPTS_DIR / "defaults"

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_COMMANDS = [
    "ls", "cat", "grep", "find", "rg", "head", "tail", "wc",
    "ps", "df", "du", "free", "uptime", "stat", "awk",
    "docker", "systemctl", "journalctl", "dmesg",
    "ss", "ping", "curl", "uname", "hostname", "id",
]

DEFAULT_MAX_TOOL_CALLS = 15

_WRITE_TOOLS = frozenset({"write_file", "edit_file", "create_directory", "hashline_edit"})


def _is_read_only(_ctx: RunContext[None], tool_def: ToolDefinition) -> bool:
    return tool_def.name not in _WRITE_TOOLS


def load_prompts() -> str:
    """Every *.md in prompts/ (yours, gitignored) and prompts/defaults/, in sorted
    filename order. Yours replaces the same-named default wholesale, so a git pull
    never touches your customizations."""
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
    agent_cfg = cfg.get("agent", {})
    shell_cfg = agent_cfg.get("shell", {})
    fs_cfg = agent_cfg.get("filesystem", {})

    shell = Shell(
        cwd=shell_cfg.get("cwd", "."),
        allowed_commands=shell_cfg.get("allowed_commands", DEFAULT_ALLOWED_COMMANDS),
        denied_commands=shell_cfg.get("denied_commands", []),
        denied_operators=shell_cfg.get("denied_operators", []),
        default_timeout=shell_cfg.get("default_timeout", 30.0),
        max_output_chars=shell_cfg.get("max_output_chars", 50_000),
        denied_env_patterns=LLM_API_KEY_ENV_PATTERNS,
    )
    filesystem = FileSystem(
        root_dir=fs_cfg.get("root_dir", "/host"),
        allowed_patterns=fs_cfg.get("allowed_patterns", []),
        denied_patterns=fs_cfg.get(
            "denied_patterns",
            [".env", ".env.*", "*.pem", "*.key", "**/secrets*", ".git/*"],
        ),
    )
    fs_toolset = filesystem.get_toolset().filtered(_is_read_only)

    return Agent(
        _build_model(cfg),
        output_type=AlertReport,
        instructions=load_prompts(),
        retries=2,
        capabilities=[shell],
        toolsets=[fs_toolset],
    )


async def run_alert(alerts: list[dict], cfg: dict) -> AlertReport:
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
    except Exception as e:
        logger.warning("alert triage failed: %s", e)
        return fallback_report(alerts)
