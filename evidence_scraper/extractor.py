"""LLM extraction stage: classify a fetched page and extract profile attributes.

Anthropic tool-use enforces a strict JSON schema generated from the profile,
so the output always matches the attributes you declared in YAML.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from anthropic import Anthropic

from .profile import Profile
from .records import ItemRecord
from .schema_gen import (
    build_extraction_system_prompt,
    build_extraction_tool,
    coerce_attributes,
)

log = logging.getLogger(__name__)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    return text[:head] + "\n\n…[snip]…\n\n" + text[-tail:]


def item_slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9\-]+", "-", name.strip()).strip("-").lower()
    return s or "unknown"


class Extractor:
    def __init__(self, profile: Profile, api_key: str, model: str, max_chars: int = 60000):
        self.profile = profile
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.max_chars = max_chars
        # Built once per run; both derive entirely from the profile.
        self.system_prompt = build_extraction_system_prompt(profile)
        self.tool = build_extraction_tool(profile)

    def classify_and_extract(
        self,
        url: str,
        page_title: str,
        page_text: str,
        site_name: str,
    ) -> Optional[dict]:
        """Return the parsed tool-input dict, or None on hard failure."""
        text = _truncate(page_text, self.max_chars)
        user_msg = (
            f"Site: {site_name}\n"
            f"URL: {url}\n"
            f"Page title: {page_title}\n\n"
            f"--- BEGIN PAGE TEXT ---\n{text}\n--- END PAGE TEXT ---\n"
        )
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                tools=[self.tool],
                tool_choice={"type": "tool", "name": "record_target_page"},
                messages=[{"role": "user", "content": user_msg}],
            )
        except Exception as e:
            log.warning("LLM call failed for %s: %s", url, e)
            return None

        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return block.input
        log.warning("No tool_use block in LLM response for %s", url)
        return None

    def to_item_record(
        self,
        tool_input: dict,
        site_name: str,
        site_slug: str,
        url: str,
        page_title: str,
        raw_text_chars: int,
        fallback_slug: str,
    ) -> ItemRecord:
        name = tool_input.get("item_name") or fallback_slug or "unknown"
        return ItemRecord(
            profile=self.profile.slug,
            site=site_name,
            site_slug=site_slug,
            item_name=name,
            item_slug=item_slug(name),
            url=url,
            is_target=bool(tool_input.get("is_target", False)),
            classification_reason=tool_input.get("classification_reason"),
            attributes=coerce_attributes(self.profile, tool_input.get("attributes")),
            page_title=page_title,
            raw_text_chars=raw_text_chars,
            extractor_model=self.model,
        )
