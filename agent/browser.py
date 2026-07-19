import base64
from typing import Any

from playwright.async_api import async_playwright
from pydantic_ai import Agent

_browser: Any = None
_page: Any = None
_playwright: Any = None

async def _ensure_page():
    global _browser, _page, _playwright
    if not _playwright:
        _playwright = await async_playwright().start()
    if not _browser:
        _browser = await _playwright.chromium.launch(headless=True)
    if not _page:
        context = await _browser.new_context(viewport={"width": 1280, "height": 720})
        _page = await context.new_page()
    return _page

async def cleanup_browser():
    global _browser, _page, _playwright
    if _page:
        await _page.close()
        _page = None
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None


def register_browser(agent: Agent):
    @agent.tool_plain
    async def browser_navigate(url: str) -> str:
        """Navigate the headless browser to a URL and return the page title. Use this to browse websites.

        Args:
            url: The URL to navigate to.
        """
        p = await _ensure_page()
        await p.goto(url, wait_until="domcontentloaded")
        title = await p.title()
        return f"Navigated to {url}. Title: {title}"

    @agent.tool_plain
    async def browser_screenshot() -> dict[str, str]:
        """Take a screenshot of the current browser viewport as a base64 PNG. Use this to show the user what the page looks like."""
        p = await _ensure_page()
        screenshot = await p.screenshot(type="png")
        b64 = base64.b64encode(screenshot).decode("ascii")
        return {"text": "Screenshot of current page:", "image_base64": b64}

    @agent.tool_plain
    async def browser_click(selector: str) -> str:
        """Click an element on the page by CSS selector. Use this to interact with buttons, links, etc.

        Args:
            selector: CSS selector of the element to click.
        """
        p = await _ensure_page()
        await p.click(selector)
        return f"Clicked element: {selector}"

    @agent.tool_plain
    async def browser_extract_text() -> str:
        """Extract all visible text content from the current page. Use this to read page content."""
        p = await _ensure_page()
        text = await p.evaluate("() => document.body.innerText")
        if len(text) > 8000:
            text = text[:8000] + "\n... (truncated)"
        return text
