import base64
import json
import re
from dataclasses import dataclass
import logging

from pydantic_ai import Agent, BinaryContent, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart, TextPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness import Shell, FileSystem
from pydantic_ai_harness.shell import LLM_API_KEY_ENV_PATTERNS
from pydantic_ai.usage import UsageLimits

from agent.memorybook import Memorybook, register_memorybook
from agent.browser import register_browser
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"
DEFAULT_PROMPTS_DIR = CONFIG_DIR / "defaults" / "prompts"

logger = logging.getLogger(__name__)

def _load_prompt(filename: str) -> str | None:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text()
    fallback = DEFAULT_PROMPTS_DIR / filename
    if fallback.exists():
        return fallback.read_text()
    return None


def build_system_prompt(
    platform_context: str | None = None,
    memories: list[dict] | None = None,
    caller_name: str | None = None,
) -> str:
    parts = [p for p in [_load_prompt("character.md"), _load_prompt("rules.md")] if p]

    if memories:
        for m in memories:
            parts.append(f"RELEVANT MEMORY [{m['keywords']}]: {m['value']}")

    if caller_name:
        parts.append(f"You are currently talking to: {caller_name}")

    if platform_context:
        parts.append(platform_context)

    return "\n\n".join(parts)


@dataclass
class Deps:
    platform_context: str | None = None
    memories: list[dict] = None
    is_admin: bool = False
    caller_name: str | None = None


_WRITE_TOOLS = frozenset({"write_file", "edit_file", "create_directory", "hashline_edit"})

def _is_read_only(_ctx: RunContext[Deps], tool_def: ToolDefinition) -> bool:
    return tool_def.name not in _WRITE_TOOLS


def _build_model(cfg: dict) -> OpenAIChatModel:
    agent_cfg = cfg.get("agent")
    return OpenAIChatModel(
        agent_cfg.get("model", "moonshotai/kimi-k2.6"),
        provider=OpenAIProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key=agent_cfg.get("api_key", ""),
        ),
    )


def create_agent(cfg: dict | None = None, is_admin: bool = False) -> Agent[Deps, str]:
    if cfg is None:
        cfg = load_config()

    agent_cfg = cfg.get("agent", {})
    shell_cfg = agent_cfg.get("shell", {})
    fs_cfg = agent_cfg.get("filesystem", {})

    async def instructions(ctx: RunContext[Deps]) -> str:
        return build_system_prompt(ctx.deps.platform_context, ctx.deps.memories, ctx.deps.caller_name)

    agent = Agent(
        _build_model(cfg),
        deps_type=Deps,
        output_type=str,
        instructions=instructions,
        retries=5,
    )

    register_memorybook(agent)
    register_browser(agent)

    if is_admin:
        shell = Shell(
            cwd=shell_cfg.get("cwd", "."),
            allowed_commands=shell_cfg.get("allowed_commands", ["ls", "cat", "grep", "find", "rg"]),
            denied_commands=shell_cfg.get("denied_commands", []),
            denied_operators=shell_cfg.get("denied_operators", []),
            default_timeout=shell_cfg.get("default_timeout", 30.0),
            max_output_chars=shell_cfg.get("max_output_chars", 50_000),
            denied_env_patterns=LLM_API_KEY_ENV_PATTERNS,
        )
        filesystem = FileSystem(
            root_dir=fs_cfg.get("root_dir", "/host"),
            allowed_patterns=fs_cfg.get("allowed_patterns", []),
            denied_patterns=fs_cfg.get("denied_patterns", [".env", ".env.*", "*.pem", "*.key", "**/secrets*", ".git/*"]),
        )
        fs_toolset = filesystem.get_toolset().filtered(_is_read_only)

        agent = Agent(
            _build_model(cfg),
            deps_type=Deps,
            output_type=str,
            instructions=instructions,
            retries=2,
            capabilities=[shell],
            toolsets=[fs_toolset],
        )
        register_memorybook(agent)
        register_browser(agent)

    return agent


# chat & alert functions

_DATA_URL_RE = re.compile(r"^data:([^;]+);base64,(.+)$")

def _parse_data_url(url: str) -> tuple[str, str] | None:
    match = _DATA_URL_RE.match(url)
    if match:
        return match.group(1), match.group(2)
    return None

def _parse_images(images: list[str]) -> list[BinaryContent]:
    result = []
    for url in images:
        parsed = _parse_data_url(url)
        if parsed:
            mime, data = parsed
            result.append(BinaryContent(data=base64.b64decode(data), media_type=mime))
        else:
            result.append(BinaryContent(data=url.encode(), media_type="image/png"))
    return result

def _inject_history(history: list[dict]) -> list[ModelMessage]:
    messages = []
    for h in history:
        if h.get("isBot"):
            messages.append(ModelResponse(parts=[TextPart(content=h["content"])]))
        else:
            author = h.get("authorName", "user")
            messages.append(ModelRequest(parts=[UserPromptPart(content=f"{author}: {h['content']}")]))
    return messages


def build_alert_context(context: dict) -> str:
    parts = [
        "You are a server's alert triage agent.",
        "One or more alerts have fired. Investigate quickly using shell tools, then return a concise analysis.",
        "Limit yourself to 3-5 tool calls maximum. Do not explore broadly — target the specific metric that triggered the alert.",
        "Return a concise technical analysis of what might be causing the alert(s), and what to check next.",
    ]
    if context:
        parts.append("")
        parts.append("CONTEXT: " + json.dumps(context, indent=2))
    return "\n".join(parts)


async def run_chat(
    message: str,
    history: list[dict] | None = None,
    images: list[str] | None = None,
    platform_context: str | None = None,
    user: dict | None = None,
    cfg: dict | None = None,
) -> dict:

    is_admin = (user or {}).get("isAdmin", False)
    caller_name = (user or {}).get("name")

    mb = Memorybook()
    memories = mb.match_keywords(message)

    agent = create_agent(cfg, is_admin=is_admin)
    deps = Deps(
        platform_context=platform_context,
        memories=memories,
        is_admin=is_admin,
        caller_name=caller_name,
    )

    msg_history = _inject_history(history or [])
    user_parts = [message]
    if images:
        user_parts.extend(_parse_images(images))

    result = await agent.run(user_parts, deps=deps, message_history=msg_history)

    tool_calls = []
    # TODO: extract tool call details from result messages if needed

    return {"reply": result.output, "tool_calls": tool_calls}


async def run_alert(
    alerts: list[str],
    context: dict | None = None,
    cfg: dict | None = None,
) -> dict:

    print(alerts)
    flattened_alerts = "and ".join(alerts)
    platform_context = build_alert_context(context or {})

    agent = create_agent(cfg, is_admin=True)
    deps = Deps(
        platform_context=platform_context,
        is_admin=True,
        caller_name="Alerts",
    )
    try:
        result = await agent.run(
            f"ALERTS: {flattened_alerts}", 
            deps=deps,
            usage_limits=UsageLimits(request_limit=10, tool_calls_limit=10))
    except Exception as e:
        logger.warning(e)
        return {"alert": flattened_alerts, "analysis": "error", "tool_calls": []}
    return {"alert": flattened_alerts, "analysis": result.output, "tool_calls": []}
