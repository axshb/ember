from sinks.discord.bot import DiscordBot
import logging
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = "config/config.toml"
EXAMPLE_PATH = "config/config.example.toml"

def _load_config() -> dict:
    p = Path(CONFIG_PATH)
    if not p.exists():
        raise SystemExit(
            f"config file not found: {CONFIG_PATH}\n"
            f"copy {EXAMPLE_PATH} to {CONFIG_PATH} and fill in your values."
        )
    with open(p, "rb") as f:
        return tomllib.load(f)

_cfg = _load_config()
discord_client = DiscordBot(_cfg["discord"], _cfg)

async def start_discord_client(cfg, agent_cfg):
    await discord_client.start(cfg["token"])
    logger.info("Discord client initialized.")
