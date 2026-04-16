"""
Site-family detection for cleaner multi-site support.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from extractor.extract import RawContact, normalize_whitespace


ADAPTERS = ("faculty_directory", "lab_roster", "people_listing", "mixed_profile_grid")


def detect_site_adapter(_text: str, html: str, contacts: list[RawContact]) -> dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")
    repeated_cards = len(soup.select("article, .person, .faculty, .card, .profile, li"))
    profile_links = sum(1 for contact in contacts if contact.url)
    titles = sum(1 for contact in contacts if contact.title and contact.title != "Unknown")
    research_blurbs = sum(1 for contact in contacts if normalize_whitespace(contact.research_text))

    scores = {
        "faculty_directory": (titles * 0.4) + (profile_links * 0.3) + (repeated_cards * 0.1),
        "lab_roster": (research_blurbs * 0.45) + (profile_links * 0.2) + (repeated_cards * 0.15),
        "people_listing": (profile_links * 0.35) + (repeated_cards * 0.2) + (len(contacts) * 0.2),
        "mixed_profile_grid": (repeated_cards * 0.25) + (titles * 0.2) + (research_blurbs * 0.2),
    }
    adapter_selected = max(scores, key=scores.get) if scores else "faculty_directory"
    total = max(scores.values()) if scores else 1.0
    confidence = round(min(1.0, total / max(1, len(contacts) * 0.7)), 2)
    return {
        "adapter_selected": adapter_selected,
        "adapter_confidence": confidence,
        "fallback_used": confidence < 0.5,
        "adapter_scores": {key: round(value, 2) for key, value in scores.items()},
    }
