"""
This file does the cheap first-pass sorting before we ask Claude anything.
The whole point is to spend model budget only on contacts that already look plausible from public signals alone.
"""

from __future__ import annotations

import re

from extractor.extract import RawContact


def _interest_keywords(user_interest: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", user_interest or "")
    }


def score_contact_deterministically(contact: RawContact, user_interest: str) -> float:
    title_lower = (contact.title or "").lower()
    research_text = (contact.research_text or "").lower()
    research_length = len((contact.research_text or "").strip())
    score = 0.0

    # Keep this stage fully deterministic: email, title, research depth, keyword overlap, and identity checks are enough here.
    if contact.email:
        score += 3.0
    if any(keyword in title_lower for keyword in ("professor", "principal investigator")) or title_lower.strip() == "pi" or " pi " in f" {title_lower} ":
        score += 2.0
    if "associate professor" in title_lower or "assistant professor" in title_lower:
        score += 1.5
    if research_length >= 150:
        score += 2.0
    if research_length < 50:
        score -= 5.0

    keyword_hits = sum(1 for keyword in _interest_keywords(user_interest) if keyword in research_text)
    score += min(4.0, keyword_hits * 2.0)

    if contact.identity_verified:
        score += 2.0

    return round(score, 2)
