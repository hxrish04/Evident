"""Pydantic models for URL lists and extracted item records (profile-agnostic)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UrlRecord(BaseModel):
    """One candidate target URL discovered for a site. Hand-editable."""
    url: str
    candidate_id: Optional[str] = None      # slug guessed from the URL; used by --item filters
    discovery_method: str = "unknown"       # "ai" | "sitemap" | "crawl" | "manual"
    skip: bool = False
    notes: Optional[str] = None
    discovered_at: Optional[str] = None


class SiteUrls(BaseModel):
    """File contents of urls/urls-{site-slug}.json."""
    site: str
    slug: str
    discovered_at: str = Field(default_factory=_now)
    urls: List[UrlRecord] = Field(default_factory=list)


class ItemRecord(BaseModel):
    """File contents of items/item-{site}-{item}.json.

    `attributes` is a dict keyed by the profile's attribute names; each value
    has value / unit? / source_text / confidence. The shape is enforced by the
    LLM tool schema plus schema_gen.coerce_attributes.
    """
    profile: str
    site: str
    site_slug: str
    item_name: str
    item_slug: str
    url: str
    is_target: bool
    classification_reason: Optional[str] = None
    attributes: Dict[str, dict] = Field(default_factory=dict)
    extracted_at: str = Field(default_factory=_now)
    extractor_model: Optional[str] = None
    page_title: Optional[str] = None
    raw_text_chars: Optional[int] = None
    notes: Optional[str] = None
