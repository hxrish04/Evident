"""URL discovery: sitemap probing + BFS crawl, filtered by the profile.

Used directly for `--discovery-method crawl`, and as a candidate source for
AI discovery. All domain knowledge comes from the injected UrlFilter.
"""
from __future__ import annotations

import gzip
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set, Tuple
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .records import SiteUrls, UrlRecord
from .profile import SiteDef
from .url_filter import UrlFilter, slug_from_url

log = logging.getLogger(__name__)

SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"]


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalize(url: str) -> str:
    """Drop fragments and trailing slashes (except root)."""
    url, _ = urldefrag(url)
    if url.endswith("/") and urlparse(url).path != "/":
        url = url.rstrip("/")
    return url


def same_registrable_domain(a: str, b: str) -> bool:
    ha = urlparse(a).hostname or ""
    hb = urlparse(b).hostname or ""
    return ha.split(".")[-2:] == hb.split(".")[-2:] and bool(ha) and bool(hb)


def _path_scope(start_url: str) -> str:
    path = urlparse(start_url).path.rstrip("/")
    if not path or path == "/":
        return "/"
    last = path.rsplit("/", 1)[-1]
    if "." in last:
        if "/" not in path.strip("/"):
            return "/" + last.rsplit(".", 1)[0]
        path = path.rsplit("/", 1)[0] or "/"
    return path or "/"


def _under_path_scope(url: str, scope: str) -> bool:
    path = urlparse(url).path.rstrip("/") or "/"
    if scope == "/":
        return True
    return path == scope or path.startswith(scope + "/")


def in_start_scope(url: str, start_urls: List[str]) -> bool:
    """True when url is on a configured host and under a configured path.

    Sitemaps often contain every locale of a domain; the start URL's path
    expresses the user's intended scope.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return False
    for start in start_urls:
        sp = urlparse(start)
        if parsed.hostname != sp.hostname:
            continue
        if _under_path_scope(url, _path_scope(start)):
            return True
    return False


def _client(user_agent: str, timeout: int) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept": "*/*",
                 "Accept-Language": "en-US,en;q=0.9"},
        timeout=timeout,
        follow_redirects=True,
    )


def _fetch_bytes(client: httpx.Client, url: str) -> Optional[bytes]:
    try:
        r = client.get(url)
        if r.status_code >= 400:
            return None
        body = r.content
        if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
        return body
    except (httpx.HTTPError, OSError) as e:
        log.debug("fetch failed %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Sitemap discovery
# ---------------------------------------------------------------------------

def _parse_sitemap(body: bytes) -> Tuple[List[str], List[str]]:
    try:
        soup = BeautifulSoup(body, "xml")
    except Exception:
        soup = BeautifulSoup(body, "html.parser")
    nested = [loc.text.strip() for loc in soup.select("sitemap > loc") if loc.text]
    pages = [loc.text.strip() for loc in soup.select("url > loc") if loc.text]
    if not pages and not nested:
        pages = [loc.text.strip() for loc in soup.find_all("loc") if loc.text]
    return nested, pages


def _locale_prefixes(start_urls: List[str]) -> Set[str]:
    prefixes: Set[str] = set()
    for s in start_urls:
        parts = [p for p in urlparse(s).path.split("/") if p]
        if len(parts) >= 2:
            prefixes.add("/" + "/".join(parts[:2]))
        elif parts:
            prefixes.add("/" + parts[0])
    return prefixes


def _sitemap_in_locale(sitemap_url: str, prefixes: Set[str]) -> bool:
    if not prefixes:
        return True
    path = urlparse(sitemap_url).path
    segments = [p for p in path.split("/") if p]
    if len(segments) <= 1:
        return True
    return any(path == pre or path.startswith(pre + "/") for pre in prefixes)


def discover_via_sitemap(
    start_urls: List[str], user_agent: str, timeout: int, max_pages: int
) -> Set[str]:
    found: Set[str] = set()
    seen_sitemaps: Set[str] = set()
    queue: deque[str] = deque()
    locale_prefixes = _locale_prefixes(start_urls)

    with _client(user_agent, timeout) as client:
        hosts: Set[str] = set()
        for u in start_urls:
            p = urlparse(u)
            if p.scheme and p.hostname:
                hosts.add(f"{p.scheme}://{p.hostname}")
        for host in hosts:
            for path in SITEMAP_PATHS:
                queue.append(host + path)

        while queue and len(found) < max_pages:
            sm = queue.popleft()
            if sm in seen_sitemaps:
                continue
            seen_sitemaps.add(sm)
            body = _fetch_bytes(client, sm)
            if not body:
                continue
            nested, pages = _parse_sitemap(body)
            for s in nested:
                if not any(same_registrable_domain(s, start) for start in start_urls):
                    continue
                if not _sitemap_in_locale(s, locale_prefixes):
                    continue
                if s not in seen_sitemaps:
                    queue.append(s)
            for p in pages:
                normalized = normalize(p)
                if not in_start_scope(normalized, start_urls):
                    continue
                found.add(normalized)
                if len(found) >= max_pages:
                    break
    return found


# ---------------------------------------------------------------------------
# BFS crawl
# ---------------------------------------------------------------------------

def _extract_links(html_bytes: bytes, base_url: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(html_bytes, "html.parser")
    out: List[Tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absolute = normalize(urljoin(base_url, href))
        text = a.get_text(strip=True)
        out.append((absolute, text))
    return out


def discover_via_crawl(
    start_urls: List[str],
    url_filter: UrlFilter,
    user_agent: str,
    timeout: int,
    max_depth: int,
    max_pages: int,
) -> Set[str]:
    """BFS same-domain crawl, biased toward profile-relevant links."""
    visited: Set[str] = set()
    found: Set[str] = set()
    queue: deque[Tuple[str, int]] = deque((u, 0) for u in start_urls)

    with _client(user_agent, timeout) as client:
        while queue and len(visited) < max_pages:
            url, depth = queue.popleft()
            url = normalize(url)
            if url in visited:
                continue
            visited.add(url)
            body = _fetch_bytes(client, url)
            if not body:
                continue
            found.add(url)
            if depth >= max_depth:
                continue
            for link, anchor in _extract_links(body, url):
                if link in visited:
                    continue
                if not same_registrable_domain(link, url):
                    continue
                if not in_start_scope(link, start_urls):
                    continue
                if url_filter.is_excluded(link):
                    continue
                if url_filter.looks_relevant(anchor) or url_filter.looks_relevant(link):
                    queue.appendleft((link, depth + 1))
                else:
                    queue.append((link, depth + 1))
    return found


# ---------------------------------------------------------------------------
# Entry point for crawl-based discovery
# ---------------------------------------------------------------------------

def discover_for_site(
    site: SiteDef,
    url_filter: UrlFilter,
    disc_cfg: dict,
    data_dir: Path,
    force: bool = False,
) -> Path:
    """Run sitemap+crawl discovery for one site and write urls-{slug}.json."""
    log.info("[%s] discovery starting (%d start urls)", site.name, len(site.start_urls))
    out_path = data_dir / "urls" / f"urls-{site.slug}.json"

    sm_urls = discover_via_sitemap(
        site.start_urls,
        user_agent=disc_cfg["user_agent"],
        timeout=disc_cfg["request_timeout_sec"],
        max_pages=disc_cfg["max_pages_per_site"],
    )
    log.info("[%s] sitemap yielded %d urls", site.name, len(sm_urls))

    candidates = {u for u in sm_urls if url_filter.is_candidate(u)}
    log.info("[%s] sitemap candidates after filter: %d", site.name, len(candidates))

    if len(candidates) < disc_cfg.get("min_candidates_before_crawl", 5):
        crawl_urls = discover_via_crawl(
            site.start_urls,
            url_filter,
            user_agent=disc_cfg["user_agent"],
            timeout=disc_cfg["request_timeout_sec"],
            max_depth=disc_cfg["max_crawl_depth"],
            max_pages=disc_cfg["max_pages_per_site"],
        )
        log.info("[%s] crawl yielded %d urls", site.name, len(crawl_urls))
        candidates |= {u for u in crawl_urls if url_filter.is_candidate(u)}

    new_records = [
        UrlRecord(
            url=u,
            candidate_id=slug_from_url(u),
            discovery_method="sitemap" if u in sm_urls else "crawl",
            discovered_at=datetime.now(timezone.utc).isoformat(),
        )
        for u in sorted(candidates)
    ]
    merged = write_site_urls(site, data_dir, new_records, force=force)
    log.info("[%s] wrote %s (%d urls)", site.name, out_path, len(merged.urls))
    return out_path


def write_site_urls(
    site: SiteDef,
    data_dir: Path,
    new_records: List[UrlRecord],
    force: bool = False,
) -> SiteUrls:
    """Write urls-{slug}.json, merging with existing manual edits unless force."""
    out_path = data_dir / "urls" / f"urls-{site.slug}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and not force:
        try:
            existing = SiteUrls.model_validate_json(out_path.read_text(encoding="utf-8"))
            existing_urls = {r.url: r for r in existing.urls}
            for r in new_records:
                if r.url not in existing_urls:
                    existing_urls[r.url] = r
            merged = SiteUrls(
                site=existing.site or site.name,
                slug=existing.slug or site.slug,
                discovered_at=datetime.now(timezone.utc).isoformat(),
                urls=sorted(existing_urls.values(), key=lambda r: r.url),
            )
        except Exception as e:
            log.warning("[%s] could not merge existing file (%s); rewriting", site.name, e)
            merged = SiteUrls(site=site.name, slug=site.slug, urls=new_records)
    else:
        merged = SiteUrls(site=site.name, slug=site.slug, urls=new_records)

    out_path.write_text(merged.model_dump_json(indent=2), encoding="utf-8")
    return merged
