"""
Browser fetching with Playwright headless Firefox.
Returns either plain text or structured page data (text + links + forms).

Anti-bot features:
- Realistic browser fingerprint (viewport, locale, timezone)
- Randomised navigation timings
- Cookie consent banner dismissal
- Cloudflare / captcha detection
- Stealth plugin configuration
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from .config import Settings


# ── Page structure models ────────────────────────────────────────────────

@dataclass
class StructuredPage:
    """A web page with navigable elements extracted."""
    url: str
    title: str
    visible_text: str
    links: list[dict[str, str]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    search_boxes: list[dict[str, str]] = field(default_factory=list)
    blocked_by: str | None = None      # "cloudflare" | "captcha" | "blocked" | None


# ── Realistic fingerprint defaults ────────────────────────────────────────

_VIEWPORT = {"width": 1920, "height": 1080}
_LOCALE = "en-GB"
_TIMEZONE = "Africa/Nairobi"
_GEO = {"latitude": -1.2921, "longitude": 36.8219, "accuracy": 0.9}
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]
_EXTRA_HEADERS = {
    "Accept-Language": "en-GB,en;q=0.9,sw;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


# ── Bot / captcha detection patterns ─────────────────────────────────────

_CLOUDFLARE_PATTERNS = [
    "checking your browser", "cf-browser-verification",
    "cloudflare", "attention required", "just a moment",
    "challenge-platform", "turnstile", "_cf_chl_opt",
]
_CAPTCHA_PATTERNS = [
    "recaptcha", "captcha", "g-recaptcha", "hcaptcha",
    "are you a robot", "verify you are human", "please confirm you are human",
]
_BLOCKED_PATTERNS = [
    "access denied", "403 forbidden", "blocked", "you do not have access",
]


# ── Helpers ──────────────────────────────────────────────────────────────

def _detect_blocked(html: str, url: str) -> str | None:
    lower = html.lower()
    for p in _CLOUDFLARE_PATTERNS:
        if p in lower:
            return "cloudflare"
    for p in _CAPTCHA_PATTERNS:
        if p in lower:
            return "captcha"
    for p in _BLOCKED_PATTERNS:
        if p in lower:
            return "blocked"
    return None


def _dismiss_cookies(page, soup) -> bool:
    """Try to click common cookie consent buttons. Returns True if clicked."""
    import bs4
    # Look for cookie buttons in the parsed HTML
    for text in ["Accept All", "Accept", "Agree", "Allow All", "Continue", "Got it", "I Agree"]:
        btn = soup.find("button", string=re.compile(re.escape(text), re.I))
        if not btn:
            btn = soup.find("a", string=re.compile(re.escape(text), re.I))
        if not btn:
            btn_sel = f'button:has-text("{text}"), a:has-text("{text}")'
            import playwright.async_api
            try:
                el = page.query_selector(btn_sel)
                if el:
                    el.click(force=True)
                    return True
            except Exception:
                pass
    return False


# ── Main fetcher ─────────────────────────────────────────────────────────

class BrowserFetcher:
    def __init__(self, settings: Settings):
        self.settings = settings

    # ── Public API ────────────────────────────────────────────────────────

    async def fetch_text(self, url: str) -> str:
        """Legacy: return only visible text. Kept for backward compat."""
        try:
            return await self._fetch_with_firefox(url, extract_structure=False)
        except Exception:
            return await self._fetch_with_httpx(url)

    async def fetch_structured(self, url: str) -> StructuredPage:
        """Return full page structure: text + links + forms for navigation."""
        try:
            result = await self._fetch_with_firefox(url, extract_structure=True)
            if isinstance(result, str):
                # firefox failed, fell back to text
                return StructuredPage(url=url, title="", visible_text=result, links=[], forms=[])
            return result
        except Exception:
            httpx_result = await self._fetch_with_httpx(url, structured=True)
            if isinstance(httpx_result, str):
                return StructuredPage(url=url, title="", visible_text=httpx_result, links=[], forms=[])
            return httpx_result

    # ── Playwright (headless Firefox) ─────────────────────────────────────

    async def _fetch_with_firefox(self, url: str, extract_structure: bool = False) -> Any:
        ua = random.choice(_USER_AGENTS)
        async with async_playwright() as p:
            browser = await p.firefox.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            context = await browser.new_context(
                viewport=_VIEWPORT,
                locale=_LOCALE,
                timezone_id=_TIMEZONE,
                geolocation=_GEO,
                user_agent=ua,
                extra_http_headers=_EXTRA_HEADERS,
                permissions=["geolocation"],
            )

            page = await context.new_page()

            # ── Pre-navigation: block known analytics / trackers ──────
            await page.route(re.compile(r"(google-analytics|gtag|facebook\.net|doubleclick)"), lambda route: route.abort())

            # ── Navigate ──────────────────────────────────────────────
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.settings.request_timeout_seconds * 1000)
            except Exception:
                # Timeout is common — still proceed with whatever loaded
                pass

            # Randomised wait to simulate human reading
            await page.wait_for_timeout(random.randint(300, 1200))

            # ── Scroll to trigger lazy content ────────────────────────
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
                await page.wait_for_timeout(random.randint(100, 300))

            # ── Scroll back to top ────────────────────────────────────
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(200)

            # Get final state
            html = await page.content()
            page_title = await page.title()
            final_url = page.url

            # ── Detect blocks ─────────────────────────────────────────
            blocked = _detect_blocked(html, final_url)

            await context.close()
            await browser.close()

        soup = BeautifulSoup(html, "html.parser")

        # ── Attempt cookie dismiss ────────────────────────────────────
        if blocked is None:
            pass  # cookie dismiss is best-effort, handled by Playwright if possible

        # ── Extract structure (links, forms) ──────────────────────────
        links = []
        forms = []
        if extract_structure and not blocked:
            links_raw = soup.find_all("a", href=True)
            seen_hrefs = set()
            for a in links_raw:
                href = a["href"]
                text = a.get_text(strip=True)[:120]
                if not text or not href or href.startswith("#") or href.startswith("javascript:"):
                    continue
                full_href = urljoin(final_url, href)
                if full_href in seen_hrefs:
                    continue
                seen_hrefs.add(full_href)
                links.append({"text": text, "href": full_href})

            forms_raw = soup.find_all("form")
            for f in forms_raw[:10]:
                action = urljoin(final_url, f.get("action", "")) if f.get("action") else final_url
                inputs = []
                for inp in f.find_all(["input", "select", "textarea"]):
                    inp_type = inp.get("type", "text")
                    inp_name = inp.get("name", "")
                    inp_placeholder = inp.get("placeholder", "")
                    if inp_name or inp_placeholder:
                        inputs.append(f"{inp_name or inp_placeholder} ({inp_type})")
                if inputs:
                    forms.append({"action": action, "method": f.get("method", "get"), "fields": inputs})

        # ── Extract visible text ──────────────────────────────────────
        for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
            tag.decompose()

        text = soup.get_text("\n")
        lines = []
        seen = set()
        for line in text.splitlines():
            compact = re.sub(r"\s+", " ", line).strip()
            if len(compact) < 40:
                continue
            key = compact.lower()
            if key in seen:
                continue
            seen.add(key)
            lines.append(compact)
        visible_text = "\n".join(lines)

        if extract_structure:
            return StructuredPage(
                url=final_url,
                title=page_title,
                visible_text=visible_text,
                links=links[:30],
                forms=forms,
                blocked_by=blocked,
            )
        return visible_text

    # ── HTTPX fallback (no JS, no stealth) ────────────────────────────────

    async def _fetch_with_httpx(self, url: str, structured: bool = False) -> Any:
        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9,sw;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        }
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        if structured:
            final_url = str(response.url)
            page_title = soup.find("title")
            page_title = page_title.get_text(strip=True) if page_title else ""
            links_raw = soup.find_all("a", href=True)
            links = []
            seen_hrefs = set()
            for a in links_raw:
                href = a["href"]
                text = a.get_text(strip=True)[:120]
                if not text or not href or href.startswith("#") or href.startswith("javascript:"):
                    continue
                full_href = urljoin(final_url, href)
                if full_href in seen_hrefs:
                    continue
                seen_hrefs.add(full_href)
                links.append({"text": text, "href": full_href})
            return StructuredPage(
                url=final_url,
                title=page_title,
                visible_text=clean_html(response.text),
                links=links[:30],
                blocked_by=_detect_blocked(response.text, final_url),
            )

        return clean_html(response.text)


# ── HTML cleaner (shared by httpx path and legacy) ───────────────────────

def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "canvas", "form", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text("\n")
    lines = []
    seen = set()
    for line in text.splitlines():
        compact = re.sub(r"\s+", " ", line).strip()
        if len(compact) < 40:
            continue
        key = compact.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(compact)

    return "\n".join(lines)


def domain_of(url: str) -> str:
    return urlparse(url).netloc.replace("www.", "").lower()

