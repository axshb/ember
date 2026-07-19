import json
from pathlib import Path

from pydantic_ai import Agent

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "memorybook.json"

class Memorybook:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self._path = Path(path)
        self._entries = self._load()

    def _load(self) -> dict:
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("{}")
            return {}
        try:
            raw = self._path.read_text()
            if not raw.strip():
                return {}
            parsed = json.loads(raw)
            return {k: v for k, v in parsed.items()
                    if isinstance(v, dict) and "keywords" in v and "value" in v}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _save(self):
        self._path.write_text(json.dumps(self._entries, indent=2))

    def match_keywords(self, message: str) -> list[dict]:
        lower = message.lower()
        matches = []
        for entry in self._entries.values():
            for kw in entry["keywords"]:
                if kw.lower() in lower:
                    matches.append({
                        "keywords": ", ".join(entry["keywords"]),
                        "value": entry["value"],
                    })
                    break
        return matches

    def set_entry(self, key: str, keywords: list[str], value: str):
        self._entries[key] = {"keywords": keywords, "value": value}
        self._save()


def register_memorybook(agent: Agent):
    @agent.tool_plain
    async def update_memory(key: str, keywords: list[str], value: str) -> str:
        """Create new or update existing memorybook entries. Do this when you learn something new about a user.

        Args:
            key: Unique key for this memory entry.
            keywords: List of keywords that trigger this memory.
            value: The memory content.
        """
        mb = Memorybook()
        mb.set_entry(key, keywords, value)
        return f"memory entry '{key}' updated"
