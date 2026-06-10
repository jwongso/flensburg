"""Shared async Playwright browser session for legislation fetching and web search."""

from __future__ import annotations

from playwright.async_api import Browser, BrowserContext, async_playwright

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) "
    "Gecko/20100101 Firefox/136.0"
)
_VIEWPORT = {"width": 1366, "height": 768}


class BrowserSession:
    def __init__(
        self,
        headless: bool = True,
        user_agent: str = _USER_AGENT,
        locale: str = "en-NZ",
        timezone: str = "Pacific/Auckland",
        timeout_ms: int = 30_000,
    ) -> None:
        self._headless = headless
        self._ua = user_agent
        self._locale = locale
        self._timezone = timezone
        self._timeout_ms = timeout_ms
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def open(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.firefox.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            user_agent=self._ua,
            viewport=_VIEWPORT,
            locale=self._locale,
            timezone_id=self._timezone,
        )
        self._context.set_default_timeout(self._timeout_ms)

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def __aenter__(self) -> "BrowserSession":
        await self.open()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def fetch_text(self, url: str, wait: str = "networkidle") -> str:
        if not self._context:
            raise RuntimeError("BrowserSession not open.")
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until=wait, timeout=self._timeout_ms)
            return await page.inner_text("body")
        finally:
            await page.close()

    async def fetch_html(self, url: str, wait: str = "networkidle") -> str:
        if not self._context:
            raise RuntimeError("BrowserSession not open.")
        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until=wait, timeout=self._timeout_ms)
            return await page.content()
        finally:
            await page.close()

    async def search_ddg(self, query: str, max_results: int = 5) -> list[dict]:
        from urllib.parse import quote_plus, unquote, urlparse, parse_qs
        from bs4 import BeautifulSoup

        search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        html = await self.fetch_html(search_url, wait="networkidle")
        soup = BeautifulSoup(html, "html.parser")

        results = []
        for div in soup.select(".result"):
            title_el = div.select_one(".result__a")
            snippet_el = div.select_one(".result__snippet")
            if not title_el:
                continue
            title = " ".join(title_el.get_text(separator=" ", strip=True).split())
            href = title_el.get("href", "")
            if "uddg=" in href:
                uddg = parse_qs(urlparse(href).query).get("uddg", [""])[0]
                if uddg:
                    href = unquote(uddg)
            if "duckduckgo.com/y.js" in href:
                continue
            body = " ".join(snippet_el.get_text(separator=" ", strip=True).split()) if snippet_el else ""
            results.append({"title": title, "url": href, "body": body})
            if len(results) >= max_results:
                break

        return results
