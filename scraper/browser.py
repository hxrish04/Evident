"""
scraper/browser.py
Safe page loading with Playwright fallback, throttling, and block detection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from scraper.access import (
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    RunAccessTracker,
    check_robots_policy,
    detect_block_reason,
    domain_from_url,
    normalize_public_url,
    throttle_domain_requests,
)


@dataclass
class PageLoadResult:
    ok: bool
    final_url: str
    text: str
    html: str
    status_code: int | None = None
    block_reason: str | None = None
    used_browser: bool = False
    error: str = ""
    robots_policy: dict | None = None


async def _load_page_browser(url: str, timeout: int = 15000) -> PageLoadResult:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            response = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            content = await page.inner_text("body")
            html = await page.content()
            final_url = page.url or url
            status_code = response.status if response else None
            block_reason = detect_block_reason(status_code, content)
            await browser.close()
            return PageLoadResult(
                ok=bool(content and html and not block_reason),
                final_url=final_url,
                text=content or "",
                html=html or "",
                status_code=status_code,
                block_reason=block_reason,
                used_browser=True,
            )
        except PlaywrightTimeoutError:
            await browser.close()
            return PageLoadResult(ok=False, final_url=url, text="", html="", block_reason="render_failed", used_browser=True, error="Timed out")
        except Exception as exc:
            await browser.close()
            return PageLoadResult(ok=False, final_url=url, text="", html="", block_reason="render_failed", used_browser=True, error=str(exc))


def load_page_http_fallback(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    allowed_domain: str | None = None,
    tracker: RunAccessTracker | None = None,
    robots_policy: dict | None = None,
) -> PageLoadResult:
    try:
        normalized = normalize_public_url(url, allowed_domain=allowed_domain)
    except ValueError as exc:
        if tracker:
            tracker.note_policy_skip(domain_from_url(url), str(exc))
        return PageLoadResult(ok=False, final_url=url, text="", html="", block_reason="blocked_by_policy", error=str(exc), robots_policy=robots_policy)

    if tracker and tracker.should_stop():
        return PageLoadResult(ok=False, final_url=normalized, text="", html="", block_reason="blocked_or_restricted", error=tracker.stop_reason, robots_policy=robots_policy)

    throttle_domain_requests(normalized, tracker)
    domain = domain_from_url(normalized)
    if tracker and not tracker.note_attempt(domain):
        return PageLoadResult(ok=False, final_url=normalized, text="", html="", block_reason="rate_limited", error="Request cap reached", robots_policy=robots_policy)

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=DEFAULT_MAX_REDIRECTS,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ) as client:
            response = client.get(normalized)
            html = response.text
            text = " ".join(html.replace("<", " <").replace(">", "> ").split())
            block_reason = detect_block_reason(response.status_code, text)
            if block_reason and tracker:
                tracker.note_blocked(domain, block_reason)
            return PageLoadResult(
                ok=response.is_success and not block_reason,
                final_url=str(response.url),
                text=text,
                html=html,
                status_code=response.status_code,
                block_reason=block_reason,
                error="" if response.is_success else f"HTTP {response.status_code}",
                robots_policy=robots_policy,
            )
    except Exception as exc:
        error_text = str(exc)
        block_reason = detect_block_reason(None, "", error_text) or "render_failed"
        if tracker:
            tracker.note_blocked(domain, block_reason)
        return PageLoadResult(ok=False, final_url=normalized, text="", html="", block_reason=block_reason, error=error_text, robots_policy=robots_policy)


def load_page_result_sync(
    url: str,
    *,
    allowed_domain: str | None = None,
    tracker: RunAccessTracker | None = None,
    prefer_browser: bool = True,
    enforce_robots: bool = False,
) -> PageLoadResult:
    try:
        normalized = normalize_public_url(url, allowed_domain=allowed_domain)
    except ValueError as exc:
        if tracker:
            tracker.note_policy_skip(domain_from_url(url), str(exc))
        return PageLoadResult(ok=False, final_url=url, text="", html="", block_reason="blocked_by_policy", error=str(exc))

    robots_policy = check_robots_policy(normalized)
    if enforce_robots and robots_policy.get("path_allowed") is False:
        if tracker:
            tracker.note_policy_skip(domain_from_url(normalized), "robots_disallow")
        return PageLoadResult(
            ok=False,
            final_url=normalized,
            text="",
            html="",
            block_reason="blocked_by_policy",
            error="robots.txt appears to disallow this path",
            robots_policy=robots_policy,
        )

    if prefer_browser:
        try:
            browser_result = asyncio.run(_load_page_browser(normalized))
            browser_result.robots_policy = robots_policy
            if browser_result.ok:
                return browser_result
        except Exception as exc:
            browser_result = PageLoadResult(
                ok=False,
                final_url=normalized,
                text="",
                html="",
                block_reason="render_failed",
                used_browser=True,
                error=str(exc),
                robots_policy=robots_policy,
            )
        if browser_result.block_reason in {"forbidden", "rate_limited", "challenge_detected"}:
            if tracker:
                tracker.note_blocked(domain_from_url(normalized), browser_result.block_reason)
            return browser_result

    return load_page_http_fallback(
        normalized,
        allowed_domain=allowed_domain,
        tracker=tracker,
        robots_policy=robots_policy,
    )


async def get_page_links(url: str, base_domain: str | None = None) -> list[str]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            links = await page.eval_on_selector_all("a[href]", "elements => elements.map(el => el.href)")
            await browser.close()
            if base_domain:
                links = [link for link in links if domain_from_url(link) == base_domain or domain_from_url(link).endswith(f'.{base_domain}')]
            return list(dict.fromkeys(links))
        except Exception:
            await browser.close()
            return []


def load_page_sync(url: str) -> tuple[Optional[str], Optional[str]]:
    result = load_page_result_sync(url)
    return result.text or None, result.html or None
