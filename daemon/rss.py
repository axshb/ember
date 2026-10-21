"""Independent RSS/Atom polling with durable, per-feed entry identities."""

import asyncio
import hashlib
import logging
import sqlite3
from html import unescape
from pathlib import Path

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _strip_html(html: str) -> str:
    if "<" not in html:
        return unescape(html).strip()
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def parse_feed(data: bytes, url: str) -> list[dict]:
    feed = feedparser.parse(data)
    if not feed.version or (feed.bozo and not feed.entries):
        raise ValueError(f"invalid RSS/Atom feed: {url}")
    entries = {}
    for entry in feed.entries:
        title = _strip_html(entry.get("title", "Untitled"))
        link = entry.get("link", "")
        content = entry.get("summary", "")
        if not content and entry.get("content"):
            content = entry.content[0].get("value", "")
        published = entry.get("published", entry.get("updated", ""))
        identity = entry.get("id") or link or hashlib.sha256(
            f"{title}\n{published}\n{content}".encode()
        ).hexdigest()
        entries[identity] = {
            "id": identity,
            "feed": _strip_html(feed.feed.get("title", url)),
            "feed_url": url,
            "title": title,
            "link": link,
            "published": published,
            "content": _strip_html(content)[:12000],
        }
    return list(entries.values())


class FeedState:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS feeds (url TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS entries (
                url TEXT, id TEXT, PRIMARY KEY (url, id)
            );
        """)

    def initialized(self, url: str) -> bool:
        return self.db.execute("SELECT 1 FROM feeds WHERE url = ?", (url,)).fetchone() is not None

    def unseen(self, url: str, entries: list[dict]) -> list[dict]:
        return [entry for entry in entries if not self.db.execute(
            "SELECT 1 FROM entries WHERE url = ? AND id = ?", (url, entry["id"])
        ).fetchone()]

    def mark(self, url: str, entries: list[dict]) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO feeds VALUES (?)", (url,))
            self.db.executemany(
                "INSERT OR IGNORE INTO entries VALUES (?, ?)",
                [(url, entry["id"]) for entry in entries],
            )

    def close(self) -> None:
        self.db.close()


async def poll_feeds(client, state: FeedState, urls: list[str], deliver,
                     *, send_existing: bool = False, batch_size: int = 10) -> None:
    for url in dict.fromkeys(urls):
        try:
            response = await client.get(url)
            response.raise_for_status()
            entries = await asyncio.to_thread(parse_feed, response.content, url)
            if not state.initialized(url) and not send_existing:
                state.mark(url, entries)
                continue
            pending = state.unseen(url, entries)
            # Feeds normally put newest first; deliver older entries first.
            pending.reverse()
            for start in range(0, len(pending), batch_size):
                batch = pending[start:start + batch_size]
                if not await deliver(batch):
                    break
                state.mark(url, batch)
            if not pending:
                state.mark(url, [])
        except Exception:
            logger.exception("RSS polling failed for %s; will retry next cycle", url)


async def rss_loop(cfg: dict, deliver) -> None:
    rss = cfg.get("rss", {})
    urls = rss.get("feeds", [])
    if not urls:
        return
    interval = float(rss.get("interval", 900))
    batch_size = int(rss.get("batch_size", 10))
    timeout = float(rss.get("timeout", 30))
    if interval <= 0 or timeout <= 0 or not 1 <= batch_size <= 10:
        raise ValueError("RSS interval/timeout must be positive and batch_size must be 1–10")
    if not isinstance(urls, list) or any(
        not isinstance(url, str) or not url.startswith(("https://", "http://")) for url in urls
    ):
        raise ValueError("rss.feeds must be a list of HTTP(S) feed URLs")
    state = FeedState(rss.get("state_path", "data/rss.sqlite3"))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            while True:
                await poll_feeds(
                    client, state, urls, deliver,
                    send_existing=rss.get("send_existing", False), batch_size=batch_size,
                )
                await asyncio.sleep(interval)
    finally:
        state.close()
