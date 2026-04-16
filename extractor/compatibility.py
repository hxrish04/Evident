"""
Shared site-compatibility checks for faculty-style public pages.

The API and validation tooling should grade a site the same way, so the
compatibility logic lives here instead of being duplicated across routes/scripts.
"""

from __future__ import annotations

from extractor.adapters import detect_site_adapter
from extractor.extract import clean_contacts, parse_faculty_page
from scraper.access import check_robots_policy, normalize_public_url
from scraper.browser import load_page_http_fallback


def assess_site_compatibility(target_url: str) -> dict:
    normalized_url = normalize_public_url(target_url)
    robots_policy = check_robots_policy(normalized_url)
    result = load_page_http_fallback(normalized_url)

    if not result.text or not result.html:
        raise ValueError("Could not load site with deterministic compatibility check.")

    raw_contacts = parse_faculty_page(result.text, result.html, source_url=normalized_url)
    cleaned_contacts = clean_contacts(raw_contacts, max_contacts=20)
    adapter = detect_site_adapter(result.text, result.html, cleaned_contacts)
    named_contacts = [contact for contact in cleaned_contacts if contact.name]
    contacts_with_urls = [contact for contact in cleaned_contacts if contact.url]
    contacts_with_emails = [contact for contact in cleaned_contacts if contact.email]
    contacts_with_titles = [contact for contact in cleaned_contacts if contact.title and contact.title != "Unknown"]
    contacts_with_research = [contact for contact in cleaned_contacts if (contact.research_text or "").strip()]

    # Structure signals stay deterministic so we can compare sites without burning model calls.
    structure_score = min(
        1.0,
        round(
            (min(len(named_contacts), 10) / 10) * 0.6
            + (min(len(contacts_with_urls), 10) / 10) * 0.25
            + (min(len(contacts_with_titles), 10) / 10) * 0.15,
            2,
        ),
    )
    access_score = 1.0
    if robots_policy.get("path_allowed") is False:
        access_score = 0.1
    elif result.block_reason in {"forbidden", "rate_limited", "challenge_detected"}:
        access_score = 0.2
    elif result.block_reason in {"render_failed", "low_content"}:
        access_score = 0.45

    compatibility_score = round((structure_score * 0.7) + (access_score * 0.3), 2)
    notes: list[str] = []
    if len(named_contacts) < 3:
        notes.append("Too few structured contact blocks were detected.")
    if not contacts_with_urls:
        notes.append("No clear profile links were found from the page.")
    if not contacts_with_emails:
        notes.append("No direct emails were found during deterministic extraction.")
    if result.block_reason:
        notes.append(f"Access signal detected: {result.block_reason}.")

    if robots_policy.get("path_allowed") is False:
        compatibility_status = "blocked_or_restricted"
        notes.append("robots.txt appears to disallow this path.")
    elif result.block_reason in {"forbidden", "rate_limited", "challenge_detected"}:
        compatibility_status = "blocked_or_restricted"
    elif structure_score >= 0.75 and len(contacts_with_research) >= 3:
        compatibility_status = "supported"
    elif structure_score >= 0.45:
        compatibility_status = "partially_supported"
    else:
        compatibility_status = "unsupported"

    return {
        "target_url": normalized_url,
        "compatible": compatibility_status in {"supported", "partially_supported"},
        "compatibility_status": compatibility_status,
        "compatibility_score": compatibility_score,
        "structure_score": structure_score,
        "access_score": access_score,
        "block_reason": result.block_reason,
        "robots_policy": robots_policy,
        "raw_contacts_found": len(raw_contacts),
        "cleaned_contacts_found": len(cleaned_contacts),
        "contacts_with_profile_urls": len(contacts_with_urls),
        "contacts_with_direct_emails": len(contacts_with_emails),
        "contacts_with_titles": len(contacts_with_titles),
        "contacts_with_research_text": len(contacts_with_research),
        "adapter_selected": adapter["adapter_selected"],
        "adapter_confidence": adapter["adapter_confidence"],
        "fallback_used": adapter["fallback_used"],
        "sample_contacts": [
            {
                "name": contact.name,
                "title": contact.title,
                "email": contact.email,
                "url": contact.url,
            }
            for contact in cleaned_contacts[:5]
        ],
        "notes": notes,
    }
