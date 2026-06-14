"""AI-assisted URL discovery.

Gathers candidate links from each site's start URLs (browser-rendered so
JS-injected links are visible, with HTTP fallback, plus optional sitemap
candidates), then asks the LLM to pick the genuine target pages. The LLM
may only choose from the supplied candidates — it cannot invent URLs.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import httpx
from anthropic import Anthropic
from bs4 import BeautifulSoup

from .discovery import discover_via_sitemap, in_start_scope, normalize, same_registrable_domain
from .profile import Profile, SiteDef
from .records import UrlRecord
from .schema_gen import build_discovery_system_prompt, build_discovery_tool
from .url_filter import UrlFilter, slug_from_url

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def _hostnames(start_urls: List[str]) -> List[str]:
    hosts: List[str] = []
    for url in start_urls:
        hostname = urlparse(url).hostname
        if hostname and hostname not in hosts:
            hosts.append(hostname)
    return hosts


def _is_allowed_domain(url: str, start_urls: List[str]) -> bool:
    return any(same_registrable_domain(url, start_url) for start_url in start_urls)


def _http_timeout(timeout_sec: Optional[int]) -> httpx.Timeout:
    base = float(timeout_sec or 30)
    return httpx.Timeout(timeout=base, connect=min(base, 10.0), read=base * 2,
                         write=base, pool=base)


def _collect_anchor_candidates(
    html: str,
    base_url: str,
    start_urls: List[str],
    url_filter: UrlFilter,
    seen: set,
    candidates: List[dict],
) -> None:
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = normalize(urljoin(base_url, href))
        if url in seen:
            continue
        if not _is_allowed_domain(url, start_urls):
            continue
        if not in_start_scope(url, start_urls):
            continue
        if url_filter.is_excluded(url):
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        seen.add(url)
        candidates.append({"url": url, "text": text})


def _fetch_candidates_http(
    start_urls: List[str],
    url_filter: UrlFilter,
    timeout_sec: Optional[int],
    user_agent: Optional[str],
    retries: int = 2,
) -> List[dict]:
    candidates: List[dict] = []
    seen: set = set()
    headers = {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(timeout=_http_timeout(timeout_sec), headers=headers,
                      follow_redirects=True) as client:
        for start_url in start_urls:
            response = None
            last_error: Optional[Exception] = None
            for attempt in range(retries + 1):
                try:
                    fetched = client.get(start_url)
                    fetched.raise_for_status()
                    response = fetched
                    break
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_error = e
                    if attempt >= retries:
                        break
                except httpx.HTTPError as e:
                    last_error = e
                    break
            if response is None:
                log.warning("AI discovery could not fetch %s: %s", start_url, last_error)
                continue
            _collect_anchor_candidates(
                response.text, start_url, start_urls, url_filter, seen, candidates
            )
    return candidates


def _fetch_candidates_rendered(
    start_urls: List[str],
    url_filter: UrlFilter,
    fetch_cfg: dict,
    cache_dir: Path,
) -> List[dict]:
    """Render each start URL in a real browser (lazy-load scroll included)."""
    from .fetcher import Fetcher  # lazy import

    candidates: List[dict] = []
    seen: set = set()
    with Fetcher(fetch_cfg, cache_dir) as fetcher:
        for start_url in start_urls:
            try:
                fr = fetcher.fetch(start_url, use_cache=True)
            except Exception as e:
                log.warning("rendered discovery could not fetch %s: %s", start_url, e)
                continue
            if not fr or not fr.html:
                continue
            _collect_anchor_candidates(
                fr.html, start_url, start_urls, url_filter, seen, candidates
            )
    return candidates


def _format_candidates(candidates: List[dict], max_links: int = 250) -> str:
    lines = []
    for index, candidate in enumerate(candidates[:max_links], start=1):
        text = candidate["text"] or "(no anchor text)"
        lines.append(f"{index}. text={text!r} url={candidate['url']}")
    return "\n".join(lines) or "(none)"


def discover_via_ai(
    site: SiteDef,
    profile: Profile,
    api_key: str,
    model: str,
    disc_cfg: dict,
    fetch_cfg: Optional[dict] = None,
    cache_dir: Optional[Path] = None,
) -> List[UrlRecord]:
    """Gather candidate links from the site, ask the LLM to select targets."""
    url_filter = UrlFilter(profile)
    start_urls = site.start_urls
    timeout_sec = disc_cfg.get("request_timeout_sec", 30)
    user_agent = disc_cfg.get("user_agent")

    candidates: List[dict] = []
    if disc_cfg.get("render_js", True) and fetch_cfg is not None and cache_dir is not None:
        try:
            candidates = _fetch_candidates_rendered(start_urls, url_filter, fetch_cfg, cache_dir)
        except Exception as e:
            log.warning("[%s] rendered discovery failed (%s); falling back to HTTP",
                        site.name, e)
    if not candidates:
        candidates = _fetch_candidates_http(start_urls, url_filter, timeout_sec, user_agent)

    if disc_cfg.get("use_sitemap", True):
        try:
            existing = {c["url"] for c in candidates}
            sm = discover_via_sitemap(
                start_urls,
                user_agent=user_agent or DEFAULT_USER_AGENT,
                timeout=int(timeout_sec),
                max_pages=disc_cfg.get("max_pages_per_site", 400),
            )
            for u in sorted(sm):
                if u not in existing and not url_filter.is_excluded(u):
                    candidates.append({"url": u, "text": ""})
                    existing.add(u)
        except Exception as e:
            log.warning("[%s] sitemap discovery failed: %s", site.name, e)

    # Most-relevant links first so they survive the candidate cap.
    def _relevant(c: dict) -> int:
        if url_filter.looks_relevant(c["url"]) or url_filter.looks_relevant(c.get("text", "")):
            return 0
        if url_filter.has_url_hint(c["url"]):
            return 1
        return 2

    candidates.sort(key=_relevant)
    log.info("[%s] gathered %d candidate links", site.name, len(candidates))

    if not candidates:
        log.warning("[%s] AI discovery found no candidate links", site.name)
        return []

    allowed_urls = {c["url"] for c in candidates}
    client = Anthropic(api_key=api_key, timeout=timeout_sec)
    user_msg = (
        f"Site: {site.name}\n"
        f"Site domain(s): {', '.join(_hostnames(start_urls))}\n"
        f"Configured start URL(s): {', '.join(start_urls)}\n\n"
        f"Find target pages: {profile.target.description}\n\n"
        "Candidate links gathered from the start URL(s). Select only from this "
        "list and return each URL exactly as shown:\n"
        f"{_format_candidates(candidates, max_links=disc_cfg.get('max_candidates_to_llm', 250))}"
    )

    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        system=build_discovery_system_prompt(profile),
        tools=[build_discovery_tool()],
        tool_choice={"type": "tool", "name": "record_target_urls"},
        messages=[{"role": "user", "content": user_msg}],
    )

    payload = None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            payload = block.input
            break
    if not payload:
        log.warning("[%s] AI discovery returned no tool payload", site.name)
        return []

    records: List[UrlRecord] = []
    seen: set = set()
    for item in payload.get("urls") or []:
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        if url not in allowed_urls:
            log.info("[%s] skipped URL not in candidate list: %s", site.name, url)
            continue
        if not _is_allowed_domain(url, start_urls):
            log.info("[%s] skipped out-of-domain URL: %s", site.name, url)
            continue
        seen.add(url)
        item_name = str(item.get("item_name") or "").strip()
        notes = str(item.get("notes") or "").strip() or None
        if item_name:
            notes = f"AI returned item: {item_name}. {notes or ''}".strip()
        records.append(
            UrlRecord(
                url=url,
                candidate_id=slug_from_url(url),
                discovery_method="ai",
                notes=notes,
            )
        )
    return records
