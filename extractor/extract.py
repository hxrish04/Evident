"""
Parses raw page text and HTML into structured contacts without using AI.
That is intentional: extraction should be cheap, repeatable, and debuggable before the model ever sees a candidate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
NAME_REGEX = re.compile(r"^[A-Z][A-Za-z.\-']+(?:\s+[A-Z][A-Za-z.\-']+){1,3}$")
WHITESPACE_REGEX = re.compile(r"\s+")
HONORIFIC_REGEX = re.compile(r"^(dr|prof|professor)\.?\s+", re.IGNORECASE)
CREDENTIALS_REGEX = re.compile(r"(,\s*)?(ph\.?d\.?|m\.?d\.?|phd|md|m\.?s\.?|ms|b\.?s\.?|bs)$", re.IGNORECASE)

TITLE_KEYWORDS = [
    "Distinguished Professor",
    "Associate Professor",
    "Assistant Professor",
    "Professor",
    "Principal Investigator",
    "Research Scientist",
    "Postdoctoral Fellow",
    "Postdoctoral Researcher",
    "Postdoc",
    "Lecturer",
    "Instructor",
    "Director",
    "Fellow",
    "PhD Student",
    "Graduate Student",
    "Researcher",
]

ROLE_SKIP_WORDS = {
    "contact",
    "directory",
    "faculty",
    "staff",
    "student",
    "overview",
    "news",
    "events",
    "about",
}

NAME_BLOCKLIST = {
    "manage consent preferences",
    "performance cookies",
    "campus map",
    "graduate school",
    "health professions",
    "honors college",
    "public health",
    "undergraduate students",
    "graduate students",
    "postdoctoral fellows",
    "summer research",
}

NON_PERSON_NAME_TERMS = {
    "alumni",
    "center",
    "cluster",
    "core",
    "directory",
    "education",
    "faculty",
    "fellows",
    "graduate",
    "history",
    "institute",
    "international",
    "neuroscience",
    "office",
    "outreach",
    "postdoc",
    "postdoctoral",
    "program",
    "recruitment",
    "research",
    "resources",
    "student",
    "students",
    "undergraduate",
    "welcome",
}

RESEARCH_HINTS = [
    "research",
    "lab",
    "focus",
    "interest",
    "interests",
    "works on",
    "studies",
    "investigates",
    "project",
    "group",
    "center",
    "computational",
    "machine learning",
    "neural",
    "language",
    "cognition",
    "biology",
]

CLINICAL_KEYWORDS = [
    "clinical",
    "patient",
    "therapeutic",
    "treatment",
    "medical",
    "disease",
    "diagnosis",
    "disorder",
]

COMPUTATIONAL_KEYWORDS = [
    "algorithm",
    "model",
    "computational",
    "machine learning",
    "neural network",
    "simulation",
    "code",
    "software",
]

# These catch honorific-only leftovers and credential fragments that show up when we split messy faculty cards.
JUNK_DISPLAY_VALUES = [
    "dr.",
    "dr",
    "prof.",
    "prof",
    "mr.",
    "mrs.",
    "ms.",
    "phd",
    "md",
    "",
]

MIN_DISPLAY_TEXT_CHARS = 15


@dataclass
class RawContact:
    name: str
    title: str = ""
    role_category: str = "unknown"
    email: str = ""
    url: str = ""
    research_text: str = ""
    source_page: str = ""
    identity_verified: bool = False
    identity_confidence: float = 0.0
    evidence: list[dict] | None = None
    evidence_chunks: list[dict] | None = None


def goal_keywords(text: str) -> set[str]:
    return {
        token.strip(" ,.;:()[]{}").lower()
        for token in str(text or "").replace("\n", " ").split()
        if len(token.strip(" ,.;:()[]{}")) > 3
    }


def classify_chunk_domain(chunk_text: str) -> str:
    text = normalize_whitespace(chunk_text).lower()
    has_clinical = any(keyword in text for keyword in CLINICAL_KEYWORDS)
    has_computational = any(keyword in text for keyword in COMPUTATIONAL_KEYWORDS)
    if has_clinical and not has_computational:
        return "clinical"
    if has_computational and not has_clinical:
        return "computational"
    return "neutral"


def detect_evidence_agreement(chunks: list[dict], contact: RawContact) -> dict:
    usable_chunks = [
        chunk
        for chunk in (chunks or [])
        if normalize_whitespace(chunk.get("chunk_text", ""))
    ]
    clinical_count = 0
    computational_count = 0
    neutral_count = 0
    for chunk in usable_chunks:
        domain = classify_chunk_domain(chunk.get("chunk_text", ""))
        if domain == "clinical":
            clinical_count += 1
        elif domain == "computational":
            computational_count += 1
        else:
            neutral_count += 1

    title_lower = normalize_whitespace(contact.title).lower()
    has_professor_title = "professor" in title_lower
    has_research_text = bool(normalize_whitespace(contact.research_text))
    title_conflict = has_professor_title and not has_research_text and not usable_chunks

    if len(usable_chunks) < 2:
        return {
            "agreement_count": 0,
            "conflict_count": 0,
            "verdict": "insufficient",
            "impact": "Too few sources to assess agreement. Confidence reduced.",
        }

    directional_counts = [clinical_count, computational_count]
    agreement_count = max(directional_counts) if max(directional_counts) > 0 else len(usable_chunks)
    conflict_count = min(clinical_count, computational_count) if clinical_count and computational_count else 0

    if title_conflict:
        return {
            "agreement_count": agreement_count,
            "conflict_count": max(1, conflict_count),
            "verdict": "conflict",
            "impact": "Title suggests active researcher but no research description found. Confidence reduced one tier.",
        }
    if conflict_count >= agreement_count and conflict_count > 0:
        return {
            "agreement_count": agreement_count,
            "conflict_count": conflict_count,
            "verdict": "conflict",
            "impact": "Conflicting signals detected. Confidence reduced one tier.",
        }
    if agreement_count >= 3 and conflict_count == 0:
        return {
            "agreement_count": agreement_count,
            "conflict_count": 0,
            "verdict": "strong_agreement",
            "impact": "Multiple independent sources align. Confidence unaffected.",
        }
    if agreement_count >= 2:
        return {
            "agreement_count": agreement_count,
            "conflict_count": conflict_count,
            "verdict": "partial_agreement",
            "impact": "Majority of sources agree. Minor conflicting signal noted." if conflict_count else "Most available sources align. Confidence mostly unaffected.",
        }
    return {
        "agreement_count": agreement_count or neutral_count,
        "conflict_count": conflict_count,
        "verdict": "insufficient",
        "impact": "Too few sources to assess agreement. Confidence reduced.",
    }


def detect_conflicts(chunks: list[dict], contact: RawContact) -> tuple[bool, str]:
    agreement = detect_evidence_agreement(chunks, contact)
    return agreement["verdict"] in {"conflict", "insufficient"}, agreement["impact"]


def normalize_whitespace(value: str) -> str:
    return WHITESPACE_REGEX.sub(" ", value or "").strip()


def normalize_display_text(value: str) -> str:
    return normalize_whitespace(value).strip(" -,:;")


def is_meaningful_display_text(value: str) -> bool:
    normalized = normalize_display_text(value)
    if not normalized:
        return False
    if normalized.lower() in JUNK_DISPLAY_VALUES:
        return False
    return len(normalized) >= MIN_DISPLAY_TEXT_CHARS


def clean_display_text(value: str, fallback: str = "") -> str:
    normalized = normalize_display_text(value)
    if is_meaningful_display_text(normalized):
        return normalized
    fallback_normalized = normalize_display_text(fallback)
    if is_meaningful_display_text(fallback_normalized):
        return fallback_normalized
    return ""


def canonicalize_name(value: str) -> str:
    cleaned = normalize_whitespace(value)
    cleaned = HONORIFIC_REGEX.sub("", cleaned).strip()
    cleaned = CREDENTIALS_REGEX.sub("", cleaned).strip()

    if "," in cleaned:
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        if len(parts) >= 2:
            cleaned = f"{parts[1]} {parts[0]}"

    return cleaned.strip(" ,")


def extract_emails(text: str) -> list[str]:
    return list(dict.fromkeys(EMAIL_REGEX.findall(text or "")))


def detect_title(text: str) -> str:
    lowered = text.lower()
    for title in TITLE_KEYWORDS:
        if title.lower() in lowered:
            return title
    return "Unknown"


def classify_role(title: str) -> str:
    lowered = normalize_whitespace(title).lower()
    if "distinguished professor" in lowered or "associate professor" in lowered or "assistant professor" in lowered or "professor" in lowered:
        return "faculty"
    if "principal investigator" in lowered or lowered == "pi" or " pi " in f" {lowered} ":
        return "principal_investigator"
    if "postdoc" in lowered or "postdoctoral" in lowered:
        return "postdoc"
    if "phd student" in lowered or "graduate student" in lowered:
        return "graduate_student"
    if "research scientist" in lowered or "researcher" in lowered or "scientist" in lowered:
        return "research_staff"
    if "lecturer" in lowered or "instructor" in lowered or "fellow" in lowered:
        return "academic_staff"
    if "director" in lowered:
        return "leadership"
    return "unknown"


def looks_like_name(text: str) -> bool:
    cleaned = canonicalize_name(text)
    if not cleaned or len(cleaned) > 80:
        return False
    lowered = cleaned.lower()
    if lowered in ROLE_SKIP_WORDS or lowered in NAME_BLOCKLIST:
        return False
    tokens = {token for token in re.findall(r"[a-zA-Z]+", lowered)}
    if tokens & NON_PERSON_NAME_TERMS:
        return False
    if tokens & {"at", "for", "of"}:
        return False
    raw_tokens = cleaned.split()
    if any(token.isupper() and len(token) >= 3 for token in raw_tokens):
        return False
    return bool(NAME_REGEX.match(cleaned))


def extract_names_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b"]):
        text = normalize_whitespace(tag.get_text(" ", strip=True))
        if looks_like_name(text):
            candidates.append(canonicalize_name(text))

    return list(dict.fromkeys(candidates))


def extract_research_blurb(text: str, window: int = 600) -> str:
    cleaned = normalize_whitespace(text)
    lowered = cleaned.lower()
    for signal in RESEARCH_HINTS:
        index = lowered.find(signal)
        if index != -1:
            start = max(0, index - 120)
            end = min(len(cleaned), index + window)
            return cleaned[start:end].strip()
    return cleaned[:window].strip()


def text_chunks(tag: Tag) -> list[str]:
    chunks: list[str] = []
    for node in tag.find_all(["p", "div", "span", "li"], recursive=True):
        text = normalize_whitespace(node.get_text(" ", strip=True))
        if text:
            chunks.append(text)
    return chunks


def best_profile_link(tag: Tag, source_url: str) -> str:
    for anchor in tag.find_all("a", href=True):
        href = normalize_whitespace(anchor.get("href", ""))
        if href and not href.startswith("mailto:"):
            return urljoin(source_url, href)
    return source_url


def best_name_for_block(tag: Tag) -> str:
    for candidate in tag.find_all(["h1", "h2", "h3", "h4", "strong", "b", "a"], recursive=True):
        text = normalize_whitespace(candidate.get_text(" ", strip=True))
        if looks_like_name(text):
            return canonicalize_name(text)
    return ""


def best_title_for_block(tag: Tag) -> str:
    for chunk in text_chunks(tag):
        title = detect_title(chunk)
        if title != "Unknown":
            return title
    return "Unknown"


def best_research_for_block(tag: Tag) -> str:
    chunks = text_chunks(tag)
    if not chunks:
        return ""

    ranked = sorted(
        chunks,
        key=lambda chunk: (
            any(hint in chunk.lower() for hint in RESEARCH_HINTS),
            len(chunk),
        ),
        reverse=True,
    )
    return extract_research_blurb(ranked[0])


def extract_contact_from_block(tag: Tag, source_url: str) -> RawContact | None:
    block_text = normalize_whitespace(tag.get_text(" ", strip=True))
    name = best_name_for_block(tag)
    if not name:
        return None

    emails = extract_emails(block_text)
    return RawContact(
        name=name,
        title=best_title_for_block(tag),
        role_category=classify_role(best_title_for_block(tag)),
        email=emails[0] if emails else "",
        url=best_profile_link(tag, source_url),
        research_text=best_research_for_block(tag),
        source_page=source_url,
    )


def extract_heading_sequence_contacts(soup: BeautifulSoup, source_url: str) -> list[RawContact]:
    contacts: list[RawContact] = []
    seen_names: set[str] = set()

    for heading in soup.find_all(["h2", "h3", "h4"]):
        name = best_name_for_block(heading)
        if not name:
            continue

        lowered_name = name.lower()
        if lowered_name in seen_names:
            continue

        details: list[str] = []
        emails: list[str] = []
        profile_url = source_url

        link = heading.find("a", href=True)
        if link and link.get("href"):
            profile_url = urljoin(source_url, normalize_whitespace(link["href"]))

        sibling = heading.next_sibling
        while sibling is not None:
            sibling_tag = sibling if isinstance(sibling, Tag) else None
            if sibling_tag is not None and sibling_tag.name in {"h2", "h3", "h4"}:
                break

            if sibling_tag is not None:
                text = normalize_whitespace(sibling_tag.get_text(" ", strip=True))
                if text:
                    details.append(text)
                    emails.extend(extract_emails(text))

            sibling = sibling.next_sibling

        combined = " ".join(details)
        title = detect_title(combined)
        research_text = ""
        for detail in details:
            if "research interests" in detail.lower() or "research areas" in detail.lower():
                research_text = detail
                break
        if not research_text:
            research_text = extract_research_blurb(combined)

        if title == "Unknown" and not research_text:
            continue

        seen_names.add(lowered_name)
        contacts.append(
            RawContact(
                name=name,
                title=title,
                role_category=classify_role(title),
                email=emails[0] if emails else "",
                url=profile_url,
                research_text=research_text,
                source_page=source_url,
            )
        )

    return contacts


def candidate_blocks(soup: BeautifulSoup) -> Iterable[Tag]:
    selectors = [
        "article",
        "section",
        "li",
        "div.person",
        "div.profile",
        "div.faculty",
        "div.staff",
        "div.card",
    ]
    seen: set[int] = set()

    for selector in selectors:
        for tag in soup.select(selector):
            identifier = id(tag)
            if identifier in seen:
                continue
            seen.add(identifier)
            yield tag


def likely_profile_links(soup: BeautifulSoup, source_url: str, max_links: int = 16) -> list[str]:
    source_host = (urlparse(source_url).hostname or "").lower()
    source_path = (urlparse(source_url).path or "").rstrip("/").lower()
    links: list[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = normalize_whitespace(anchor.get("href", ""))
        if not href or href.startswith("mailto:"):
            continue
        full_url = urljoin(source_url, href)
        parsed = urlparse(full_url)
        path = (parsed.path or "").rstrip("/").lower()
        if not path or path == source_path:
            continue
        if source_host and parsed.hostname and parsed.hostname.lower() != source_host:
            continue
        if "/faculty/" not in path and "/people/" not in path and "/profile/" not in path:
            continue
        last_segment = path.rsplit("/", 1)[-1]
        if last_segment in {"faculty", "people", "profile", "profiles"}:
            continue
        if full_url in seen:
            continue
        seen.add(full_url)
        links.append(full_url)
        if len(links) >= max_links:
            break

    return links


def extract_profile_name(soup: BeautifulSoup) -> str:
    title_tag = soup.find("title")
    if title_tag:
        title_text = normalize_whitespace(title_tag.get_text(" ", strip=True))
        candidate = canonicalize_name(title_text.split(" - ", 1)[0])
        if looks_like_name(candidate):
            return candidate

    for tag in soup.find_all(["h2", "h1", "h3"]):
        candidate = canonicalize_name(tag.get_text(" ", strip=True))
        if looks_like_name(candidate):
            return candidate
    return ""


def extract_profile_contact(profile_url: str, source_url: str) -> RawContact | None:
    from scraper.browser import load_page_http_fallback

    allowed_domain = (urlparse(source_url).hostname or "").lower() or None
    result = load_page_http_fallback(profile_url, allowed_domain=allowed_domain)
    if not result.ok or not result.html:
        return None

    soup = BeautifulSoup(result.html, "html.parser")
    name = extract_profile_name(soup)
    if not name:
        return None

    page_text = normalize_whitespace(soup.get_text(" ", strip=True))
    title = "Unknown"
    research_text = ""

    heading = None
    for candidate in soup.find_all(["h2", "h1", "h3"]):
        if canonicalize_name(candidate.get_text(" ", strip=True)) == name:
            heading = candidate
            break

    if heading is not None:
        sibling = heading.next_sibling
        while sibling is not None:
            sibling_tag = sibling if isinstance(sibling, Tag) else None
            if sibling_tag is not None and sibling_tag.name in {"h1", "h2"}:
                break
            if sibling_tag is not None:
                sibling_text = normalize_whitespace(sibling_tag.get_text(" ", strip=True))
                if sibling_text:
                    if title == "Unknown":
                        detected = detect_title(sibling_text)
                        if detected != "Unknown":
                            title = detected
                            sibling = sibling.next_sibling
                            continue
                    if not research_text and (
                        sibling_tag.name in {"h3", "h4"} or any(hint in sibling_text.lower() for hint in RESEARCH_HINTS)
                    ):
                        research_parts = [sibling_text]
                        detail_tag = sibling_tag.next_sibling
                        while detail_tag is not None:
                            detail_candidate = detail_tag if isinstance(detail_tag, Tag) else None
                            if detail_candidate is not None:
                                if detail_candidate.name in {"h1", "h2", "h3", "h4"}:
                                    break
                                detail_text = normalize_whitespace(detail_candidate.get_text(" ", strip=True))
                                if detail_text:
                                    research_parts.append(detail_text)
                                    break
                            detail_tag = detail_tag.next_sibling
                        research_text = " ".join(research_parts)
                        break
            sibling = sibling.next_sibling

    if title == "Unknown":
        title = detect_title(page_text)

    if not research_text:
        for heading_tag in soup.find_all(["h3", "h4"]):
            heading_text = normalize_whitespace(heading_tag.get_text(" ", strip=True))
            lowered = heading_text.lower()
            if not heading_text or any(skip in lowered for skip in {"search", "menu", "about", "contact", "welcome", "recruitment"}):
                continue
            detail_tag = heading_tag.next_sibling
            while detail_tag is not None:
                detail_candidate = detail_tag if isinstance(detail_tag, Tag) else None
                if detail_candidate is not None:
                    if detail_candidate.name in {"h1", "h2", "h3", "h4"}:
                        break
                    detail_text = normalize_whitespace(detail_candidate.get_text(" ", strip=True))
                    if detail_text:
                        research_text = f"{heading_text}. {detail_text}"
                        break
                detail_tag = detail_tag.next_sibling
            if research_text:
                break

    if not research_text:
        research_text = extract_research_blurb(page_text)

    emails = extract_emails(page_text)
    return RawContact(
        name=name,
        title=title,
        role_category=classify_role(title),
        email=emails[0] if emails else "",
        url=result.final_url or profile_url,
        research_text=research_text,
        source_page=source_url,
    )


def harvest_profile_contacts(soup: BeautifulSoup, source_url: str, max_contacts: int = 8) -> list[RawContact]:
    harvested: list[RawContact] = []
    for profile_url in likely_profile_links(soup, source_url, max_links=max_contacts * 2):
        contact = extract_profile_contact(profile_url, source_url)
        if contact:
            harvested.append(contact)
        if len(clean_contacts(harvested, max_contacts=max_contacts)) >= max_contacts:
            break
    return harvested


def parse_faculty_page(text: str, html: str, source_url: str = "") -> list[RawContact]:
    soup = BeautifulSoup(html or "", "html.parser")
    contacts: list[RawContact] = []

    heading_contacts = extract_heading_sequence_contacts(soup, source_url)
    if heading_contacts and len(clean_contacts(heading_contacts, max_contacts=20)) >= 5:
        return heading_contacts[:20]
    contacts.extend(heading_contacts)

    for block in candidate_blocks(soup):
        contact = extract_contact_from_block(block, source_url)
        if contact:
            contacts.append(contact)

    if len(clean_contacts(contacts, max_contacts=20)) >= 5:
        return contacts[:20]

    if source_url:
        contacts.extend(harvest_profile_contacts(soup, source_url))
        if contacts:
            return contacts[:20]

    names = extract_names_from_html(html or "")
    emails = extract_emails(text or "")
    page_title = detect_title(text or "")
    page_research = extract_research_blurb(text or "")

    for index, name in enumerate(names[:10]):
        contacts.append(
            RawContact(
                name=name,
                title=page_title,
                role_category=classify_role(page_title),
                email=emails[index] if index < len(emails) else "",
                url=source_url,
                research_text=page_research,
                source_page=source_url,
            )
        )

    return contacts


def clean_contacts(contacts: list[RawContact], max_contacts: int = 20) -> list[RawContact]:
    seen: set[tuple[str, str]] = set()
    cleaned: list[RawContact] = []

    for contact in contacts:
        name = canonicalize_name(contact.name)
        title = normalize_whitespace(contact.title)
        research = normalize_whitespace(contact.research_text)

        if not looks_like_name(name):
            continue

        key = (name.lower(), (contact.email or "").lower())
        if key in seen:
            continue

        if any(skip in name.lower() for skip in ["read more", "click here", "home", "contact us"]):
            continue

        seen.add(key)
        cleaned.append(
            RawContact(
                name=name,
                title=title or "Unknown",
                role_category=classify_role(title or "Unknown"),
                email=normalize_whitespace(contact.email),
                url=contact.url,
                research_text=research,
                source_page=contact.source_page,
            )
        )

        if len(cleaned) >= max_contacts:
            break

    return cleaned
