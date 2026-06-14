"""Pluggable contact-ingestion sources for the Evident pipeline.

A `ContactSource` is the *front-end* of a run: it produces the `RawContact`
objects that the evaluation / ranking / refusal stages then consume. Today the
only source is a single faculty/directory page (`DirectoryPageSource`).

Because everything downstream of `RawContact` is generic, a different source —
for example a web scraper that emits the same `RawContact` shape — can be
swapped in without changing any decision logic. This is the seam the
universal-scraper integration plugs into.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from extractor.extract import RawContact


@dataclass
class SourceResult:
    """What a source hands back to the pipeline.

    `page_text` / `page_html` are the raw page for sources that load a single
    document (used downstream for site-adapter detection); non-document sources
    such as a scraper leave them empty.
    """

    raw_contacts: list[RawContact]
    page_text: str = ""
    page_html: str = ""
    label: str = "source"


class ContactSource:
    """Base class for a contact-ingestion front-end."""

    # `pipeline` is the AgentPipeline (duck-typed to avoid a circular import).
    def fetch(self, pipeline, url: str, run_id: int) -> SourceResult:
        raise NotImplementedError


class DirectoryPageSource(ContactSource):
    """Default behavior: load one faculty/directory page and parse it.

    Wraps the pipeline's existing `load_page` + `extract_raw_contacts` so the
    out-of-the-box run is byte-for-byte unchanged.
    """

    def fetch(self, pipeline, url: str, run_id: int) -> SourceResult:
        pipeline.emit_progress(run_id, "loading_page", f"Loading {url}")
        text, html = pipeline.load_page(url)

        pipeline.emit_progress(run_id, "extracting_contacts", "Extracting contacts from faculty page")
        raw_contacts = pipeline.extract_raw_contacts(text, html, url)

        return SourceResult(
            raw_contacts=raw_contacts,
            page_text=text,
            page_html=html,
            label="directory_page",
        )


# ---------------------------------------------------------------------------
# evidence_scraper integration
#
# The vendored `evidence_scraper` extracts structured records from arbitrary
# sites (driven by a profile) and writes them as ItemRecord JSON. A ScraperSource
# adapts those records into RawContacts so Evident can evaluate/rank/refuse on
# contacts pulled from far more sites than the built-in faculty parser handles.
# ---------------------------------------------------------------------------


@dataclass
class ScraperFieldMapping:
    """Maps an evidence_scraper profile's attribute names onto the fields
    Evident's decision layer needs. Lives on Evident's side so the scraper
    profiles stay generic/reusable.
    """

    name_attr: str | None = None      # attribute holding the person/entity name
    research_attr: str | None = None  # attribute holding the relevance/bio text
    email_attr: str | None = None     # attribute holding a contact email
    title_attr: str | None = None     # attribute holding a role/title
    min_confidence: float = 0.5       # drop attribute observations below this


def _attr(attrs: dict, key: str | None) -> dict | None:
    if not key:
        return None
    value = attrs.get(key)
    return value if isinstance(value, dict) else None


def adapt_item_to_contact(item: dict, mapping: ScraperFieldMapping) -> RawContact | None:
    """Convert one evidence_scraper ItemRecord (dict) into a RawContact.

    Each scraper attribute carries {value, source_text, confidence}. We turn
    high-confidence, sourced observations into Evident evidence + chunks (so the
    citations survive end to end), and derive identity_verified the same way the
    enrichment layer does (>= 0.6 mean confidence AND >= 2 sources). Thin items
    therefore land in `insufficient_evidence` via the structural floor.
    """
    attrs = item.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}

    name_attr = _attr(attrs, mapping.name_attr)
    name = str((name_attr or {}).get("value") or item.get("item_name") or "").strip()
    if not name:
        return None

    url = str(item.get("url") or "")

    evidence: list[dict] = []
    chunks: list[dict] = []
    confidences: list[float] = []
    research_parts: list[str] = []

    for attr_name, observation in attrs.items():
        if not isinstance(observation, dict):
            continue
        source_text = str(observation.get("source_text") or "").strip()
        try:
            confidence = float(observation.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidences.append(confidence)
        # Only sourced, sufficiently-confident observations become evidence —
        # this keeps junk from inflating evidence strength and fooling the
        # "refuse when weak" gate.
        if not source_text or confidence < mapping.min_confidence:
            continue
        evidence.append({
            "source_url": url,
            "source_type": "scraped_attribute",
            "title": attr_name,
            "snippet": source_text,
            "identity_score": confidence,
        })
        chunks.append({"source_url": url, "source_type": "scraped", "chunk_text": source_text})
        research_parts.append(source_text)

    research_attr = _attr(attrs, mapping.research_attr)
    research_value = str((research_attr or {}).get("value") or "")
    research_text = " ".join(part for part in [research_value, *research_parts] if part).strip()

    email_attr = _attr(attrs, mapping.email_attr)
    email = str((email_attr or {}).get("value") or "").strip()

    title_attr = _attr(attrs, mapping.title_attr)
    title = str((title_attr or {}).get("value") or "").strip()

    identity_confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    identity_verified = identity_confidence >= 0.6 and len(evidence) >= 2

    return RawContact(
        name=name,
        title=title or "Unknown",
        role_category="unknown",
        email=email,
        url=url,
        research_text=research_text,
        source_page=url,
        identity_verified=identity_verified,
        identity_confidence=identity_confidence,
        evidence=evidence,
        evidence_chunks=chunks,
    )


class ScraperSource(ContactSource):
    """Ingest contacts the evidence_scraper has extracted into ItemRecord JSON.

    Reads `index.json` (or `items/item-*.json`) under a data directory, adapts
    each target item to a RawContact, and hands them to the pipeline. The
    scraper runs as its own step and writes JSON, so Evident's access/cost
    controls are unaffected by the scrape itself.
    """

    def __init__(self, data_dir: str | Path, mapping: ScraperFieldMapping, only_targets: bool = True):
        self.data_dir = Path(data_dir)
        self.mapping = mapping
        self.only_targets = only_targets

    def _load_items(self) -> list[dict]:
        index = self.data_dir / "index.json"
        if index.exists():
            loaded = json.loads(index.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, list) else []
        items_dir = self.data_dir / "items"
        items: list[dict] = []
        if items_dir.exists():
            for path in sorted(items_dir.glob("item-*.json")):
                try:
                    items.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return items

    def fetch(self, pipeline, url: str, run_id: int) -> SourceResult:
        items = self._load_items()
        contacts: list[RawContact] = []
        for item in items:
            if self.only_targets and not item.get("is_target", True):
                continue
            contact = adapt_item_to_contact(item, self.mapping)
            if contact is not None:
                contacts.append(contact)
        pipeline.emit_progress(
            run_id,
            "extracting_contacts",
            f"Ingested {len(contacts)} scraped contacts from {self.data_dir.name}",
        )
        return SourceResult(raw_contacts=contacts, label="evidence_scraper")
