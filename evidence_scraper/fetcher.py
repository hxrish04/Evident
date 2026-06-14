"""Playwright-based page fetcher with on-disk cache.

Improvements over a naive fetcher:
  - optional HTTP-first mode: try a cheap httpx GET before launching a browser
    page; fall back to Playwright when the page looks JS-rendered
  - rotating realistic user agents + matching client hints
  - light stealth (hide navigator.webdriver, plausible languages/platform)
  - auto-scroll to trigger lazy-loaded content / infinite scroll
  - best-effort cookie-banner dismissal
  - retries with exponential backoff + jitter
  - per-domain politeness delay
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENTS = [
    # Chrome / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    # Chrome / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    # Edge / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
    # Firefox / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
]

VIEWPORTS = [
    {"width": 1366, "height": 900},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
]

STEALTH_INIT_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = window.chrome || { runtime: {} };
"""

COOKIE_BUTTON_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Allow all')",
    "button:has-text('Got it')",
    "[id*='cookie'] button:has-text('OK')",
]


@dataclass
class FetchResult:
    url: str
    final_url: str
    title: str
    html: str
    visible_text: str
    fetched_at: float
    fetch_method: str = "browser"   # "browser" | "http" | "manual"
    from_cache: bool = False


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def cache_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


def cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / f"{cache_key(url)}.json"


def load_cache(cache_dir: Path, url: str) -> Optional[FetchResult]:
    p = cache_path(cache_dir, url)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d.pop("from_cache", None)
        return FetchResult(from_cache=True, **d)
    except Exception as e:
        log.warning("cache load failed for %s: %s", url, e)
        return None


def save_cache(cache_dir: Path, fr: FetchResult) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    d = {
        "url": fr.url,
        "final_url": fr.final_url,
        "title": fr.title,
        "html": fr.html,
        "visible_text": fr.visible_text,
        "fetched_at": fr.fetched_at,
        "fetch_method": fr.fetch_method,
    }
    cache_path(cache_dir, fr.url).write_text(json.dumps(d), encoding="utf-8")


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text).strip()
    return text


def _page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return (m.group(1).strip() if m else "")[:300]


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class Fetcher:
    """Context manager. Playwright is imported/launched lazily on first
    browser fetch, so HTTP-only runs never pay the browser cost."""

    def __init__(self, cfg: dict, cache_dir: Path):
        self.cfg = cfg or {}
        self.cache_dir = cache_dir
        self._pw = None
        self._browser = None
        self._last_fetch_by_domain: dict[str, float] = {}
        self._ua = random.choice(USER_AGENTS)

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()
        self._browser = None
        self._pw = None

    def _ensure_browser(self):
        if self._browser:
            return
        from playwright.sync_api import sync_playwright  # lazy import

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.cfg.get("headless", True),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
            ],
        )

    # -- politeness ---------------------------------------------------------

    def _be_polite(self, url: str) -> None:
        delay_ms = self.cfg.get("per_request_delay_ms", 1000)
        if delay_ms <= 0:
            return
        domain = urlparse(url).hostname or ""
        last = self._last_fetch_by_domain.get(domain, 0.0)
        wait = (delay_ms / 1000.0) - (time.time() - last)
        # Small jitter so request timing doesn't look robotic.
        wait += random.uniform(0, delay_ms / 4000.0)
        if wait > 0:
            time.sleep(wait)
        self._last_fetch_by_domain[domain] = time.time()

    # -- public API ---------------------------------------------------------

    def fetch(self, url: str, use_cache: bool = True) -> Optional[FetchResult]:
        if use_cache:
            cached = load_cache(self.cache_dir, url)
            if cached:
                log.debug("cache hit %s", url)
                return cached

        retries = int(self.cfg.get("retries", 2))
        min_chars = int(self.cfg.get("http_first_min_chars", 800))

        # 1) Cheap HTTP attempt (optional).
        if self.cfg.get("http_first", True):
            fr = self._fetch_http(url)
            if fr and len(fr.visible_text) >= min_chars:
                save_cache(self.cache_dir, fr)
                return fr
            if fr:
                log.info("http fetch too thin (%d chars), rendering %s",
                         len(fr.visible_text), url)

        # 2) Browser render with retry/backoff.
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            if attempt:
                backoff = (2 ** attempt) + random.uniform(0, 1)
                log.info("retry %d for %s after %.1fs", attempt, url, backoff)
                time.sleep(backoff)
            try:
                fr = self._fetch_browser(url)
                if fr:
                    save_cache(self.cache_dir, fr)
                    return fr
            except Exception as e:
                last_err = e
                log.warning("browser fetch attempt %d failed %s: %s", attempt, url, e)
        if last_err:
            log.warning("giving up on %s: %s", url, last_err)
        return None

    # -- HTTP path ----------------------------------------------------------

    def _fetch_http(self, url: str) -> Optional[FetchResult]:
        self._be_polite(url)
        headers = {
            "User-Agent": self._ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        timeout = self.cfg.get("navigation_timeout_ms", 45000) / 1000.0
        try:
            with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
                r = client.get(url)
                if r.status_code >= 400:
                    log.debug("http %d for %s", r.status_code, url)
                    return None
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype and "<html" not in r.text[:2000].lower():
                    return None
                html = r.text
        except httpx.HTTPError as e:
            log.debug("http fetch failed %s: %s", url, e)
            return None
        return FetchResult(
            url=url,
            final_url=str(r.url),
            title=_page_title(html),
            html=html,
            visible_text=visible_text(html),
            fetched_at=time.time(),
            fetch_method="http",
        )

    # -- browser path -------------------------------------------------------

    def _fetch_browser(self, url: str) -> Optional[FetchResult]:
        self._ensure_browser()
        self._be_polite(url)
        ctx = self._browser.new_context(
            user_agent=self._ua,
            viewport=random.choice(VIEWPORTS),
            locale="en-US",
            timezone_id="America/Chicago",
        )
        ctx.add_init_script(STEALTH_INIT_JS)
        page = ctx.new_page()
        try:
            log.info("fetching (browser) %s", url)
            wait_until = self.cfg.get("wait_until", "networkidle")
            timeout = self.cfg.get("navigation_timeout_ms", 45000)
            try:
                page.goto(url, wait_until=wait_until, timeout=timeout)
            except Exception as e:
                # networkidle never settles on chatty pages; accept the DOM.
                if wait_until == "networkidle" and "Timeout" in str(e):
                    log.warning("networkidle timeout; continuing with loaded DOM for %s", url)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                else:
                    raise

            self._dismiss_cookie_banner(page)
            if self.cfg.get("auto_scroll", True):
                self._auto_scroll(page)

            html = page.content()
            title = page.title()
            final_url = page.url
        finally:
            try:
                ctx.close()
            except Exception:
                pass

        return FetchResult(
            url=url,
            final_url=final_url,
            title=title or "",
            html=html,
            visible_text=visible_text(html),
            fetched_at=time.time(),
            fetch_method="browser",
        )

    @staticmethod
    def _dismiss_cookie_banner(page) -> None:
        for sel in COOKIE_BUTTON_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=400):
                    loc.click(timeout=2000)
                    page.wait_for_timeout(400)
                    return
            except Exception:
                continue

    def _auto_scroll(self, page) -> None:
        """Scroll down in steps to trigger lazy-loaded content and infinite
        scroll. Also clicks common 'load more' buttons. Stops when the page
        height stops growing or limits are hit."""
        max_steps = int(self.cfg.get("max_scroll_steps", 8))
        step_pause_ms = int(self.cfg.get("scroll_pause_ms", 700))
        load_more_clicks = int(self.cfg.get("max_load_more_clicks", 3))

        last_height = 0
        for _ in range(max_steps):
            try:
                height = page.evaluate("document.body ? document.body.scrollHeight : 0")
            except Exception:
                break
            if height <= last_height:
                # Height stalled; try a 'load more' button before giving up.
                if load_more_clicks > 0 and self._click_load_more(page):
                    load_more_clicks -= 1
                    page.wait_for_timeout(step_pause_ms)
                    continue
                break
            last_height = height
            try:
                page.mouse.wheel(0, height)
                page.wait_for_timeout(step_pause_ms)
            except Exception:
                break
        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

    @staticmethod
    def _click_load_more(page) -> bool:
        for sel in [
            "button:has-text('Load more')",
            "button:has-text('Show more')",
            "button:has-text('View more')",
            "a:has-text('Load more')",
            "[class*='load-more'] button",
        ]:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=300):
                    loc.click(timeout=2000)
                    return True
            except Exception:
                continue
        return False
