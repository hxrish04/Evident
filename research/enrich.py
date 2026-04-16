"""
This stage adds what extraction could not get from the directory page alone: profile evidence, better research text, direct emails, and identity confidence.
By the time a contact leaves this file, the evaluator should have enough public context to make a real call instead of guessing.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from extractor.extract import RawContact, extract_emails, normalize_whitespace
from scraper.access import RunAccessTracker, domain_from_url
from scraper.browser import load_page_http_fallback

RESEARCH_MARKERS = [
    "research interests",
    "research areas",
    "research focus",
    "current research",
    "lab",
    "program",
    "learning and memory",
    "neuro",
    "cognition",
]


@dataclass
class EvidenceItem:
    source_url: str
    source_type: str
    title: str
    snippet: str
    identity_score: float


SOURCE_PRIORITY = {
    "profile": 3,
    "directory": 2,
    "search": 1,
}

SOURCE_TYPE_MAP = {
    "profile": "faculty_page",
    "directory": "directory",
    "search": "search_result",
}


def fetch_page(url: str, *, allowed_domain: str | None = None, tracker: RunAccessTracker | None = None) -> tuple[str, str]:
    result = load_page_http_fallback(url, allowed_domain=allowed_domain, tracker=tracker)
    if not result.ok:
        return "", url
    return result.html, result.final_url


def soup_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    return normalize_whitespace(soup.get_text(" ", strip=True))


def page_title(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    title_tag = soup.find("title")
    return normalize_whitespace(title_tag.get_text(" ", strip=True)) if title_tag else ""


def name_signals(name: str) -> tuple[str, str]:
    parts = [part for part in normalize_whitespace(name).split() if part]
    first = parts[0].lower() if parts else ""
    last = parts[-1].lower() if len(parts) >= 2 else first
    full = " ".join(parts).lower()
    return first, last or "", full


def compute_identity_score(contact: RawContact, url: str, title: str, text: str) -> float:
    first, last, full = name_signals(contact.name)
    haystack = f"{url} {title} {text}".lower()

    score = 0.0
    if full and full in haystack:
        score += 0.55
    if first and last and first in haystack and last in haystack:
        score += 0.4
    if "uab" in haystack or "university of alabama at birmingham" in haystack:
        score += 0.15
    if contact.title and contact.title.lower() != "unknown" and contact.title.lower() in haystack:
        score += 0.1

    return min(score, 1.0)


def extract_research_snippet(text: str) -> str:
    cleaned = normalize_whitespace(text)
    lowered = cleaned.lower()
    for marker in RESEARCH_MARKERS:
        index = lowered.find(marker)
        if index != -1:
            start = max(0, index - 80)
            end = min(len(cleaned), index + 320)
            return cleaned[start:end].strip()
    return cleaned[:280].strip()


def chunk_text(text: str, chunk_size: int = 300, overlap: int = 50) -> list[str]:
    cleaned = normalize_whitespace(text)
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + chunk_size)
        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(cleaned):
            break
        start = max(0, end - overlap)
    return chunks


def dedupe_and_sort_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    deduped: dict[tuple[str, str], EvidenceItem] = {}
    for item in items:
        key = (item.source_url, item.snippet)
        existing = deduped.get(key)
        if existing is None or (
            item.identity_score,
            SOURCE_PRIORITY.get(item.source_type, 0),
        ) > (
            existing.identity_score,
            SOURCE_PRIORITY.get(existing.source_type, 0),
        ):
            deduped[key] = item

    return sorted(
        deduped.values(),
        key=lambda item: (
            SOURCE_PRIORITY.get(item.source_type, 0),
            item.identity_score,
            len(item.snippet),
        ),
        reverse=True,
    )


def collect_candidate_urls(contact: RawContact) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    if contact.url:
        urls.append((contact.url, "profile"))
    if contact.source_page:
        urls.append((contact.source_page, "directory"))

    seen: set[str] = set()
    unique_urls: list[tuple[str, str]] = []
    allowed_domain = domain_from_url(contact.source_page or contact.url or "")
    for url, source_type in urls:
        normalized = urljoin(contact.source_page or url, url).strip()
        if not normalized or normalized in seen:
            continue
        if allowed_domain and domain_from_url(normalized) and not (
            domain_from_url(normalized) == allowed_domain or domain_from_url(normalized).endswith(f".{allowed_domain}")
        ):
            continue
        seen.add(normalized)
        unique_urls.append((normalized, source_type))
    return unique_urls[:5]


def enrich_contact(contact: RawContact, *, tracker: RunAccessTracker | None = None, allowed_domain: str | None = None) -> RawContact:
    evidence_items: list[EvidenceItem] = []
    saved_chunks: list[dict] = []
    best_email = contact.email
    best_research = contact.research_text
    best_confidence = contact.identity_confidence

    for url, source_type in collect_candidate_urls(contact):
        html, resolved_url = fetch_page(url, allowed_domain=allowed_domain, tracker=tracker)
        if not html:
            continue

        text = soup_text(html)
        title = page_title(html)
        identity_score = compute_identity_score(contact, resolved_url, title, text)

        if identity_score < 0.35:
            continue

        snippet = extract_research_snippet(text)
        evidence_items.append(
            EvidenceItem(
                source_url=resolved_url,
                source_type=source_type,
                title=title,
                snippet=snippet,
                identity_score=round(identity_score, 2),
            )
        )

        for chunk in chunk_text(text):
            saved_chunks.append(
                {
                    "source_url": resolved_url,
                    "source_type": SOURCE_TYPE_MAP.get(source_type, source_type),
                    "chunk_text": chunk,
                }
            )

        emails = extract_emails(text)
        if emails and not best_email:
            best_email = emails[0]

        if snippet and (identity_score > best_confidence or len(snippet) > len(best_research)):
            best_research = snippet
            best_confidence = identity_score

    evidence_items = dedupe_and_sort_evidence(evidence_items)
    verified = best_confidence >= 0.6 and len(evidence_items) >= 2
    title_set = {normalize_whitespace(item.title).lower() for item in evidence_items if item.title}
    conflict_flag = len(title_set) >= 2
    serialized_evidence = [
        {
            "source_url": item.source_url,
            "source_type": item.source_type,
            "title": item.title,
            "snippet": item.snippet,
            "identity_score": item.identity_score,
            "source_priority": SOURCE_PRIORITY.get(item.source_type, 0),
            "conflict_flag": conflict_flag,
        }
        for item in evidence_items[:3]
    ]

    return RawContact(
        name=contact.name,
        title=contact.title,
        role_category=contact.role_category,
        email=best_email,
        url=contact.url,
        research_text=best_research or contact.research_text,
        source_page=contact.source_page,
        identity_verified=verified,
        identity_confidence=round(best_confidence, 2),
        evidence=serialized_evidence,
        evidence_chunks=saved_chunks,
    )


def enrich_contacts(
    contacts: list[RawContact],
    max_workers: int = 4,
    progress_callback=None,
    tracker: RunAccessTracker | None = None,
    allowed_domain: str | None = None,
) -> list[RawContact]:
    if not contacts:
        return []

    worker_count = max(1, min(max_workers, len(contacts)))
    results: list[RawContact] = [None] * len(contacts)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        effective_domain = allowed_domain or (domain_from_url(contacts[0].source_page or contacts[0].url or "") if contacts else None)
        future_map = {
            executor.submit(enrich_contact, contact, tracker=tracker, allowed_domain=effective_domain): index
            for index, contact in enumerate(contacts)
        }
        for future in as_completed(future_map):
            index = future_map[future]
            contact = contacts[index]
            if progress_callback:
                progress_callback("researching", f"Researching {contact.name}")
            results[index] = future.result()
    return [result for result in results if result is not None]
