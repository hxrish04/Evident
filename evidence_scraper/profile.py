"""Profile loading and validation.

A *profile* is one YAML file that describes a scraping project:

  - what kind of page you are looking for (the "target")
  - which attributes to extract from each target page, with types/units
  - keywords that help discovery find relevant URLs
  - the list of sites to scrape (name + start URLs)

The engine (this package) is generic; the profile supplies every
domain-specific detail. See profiles/_template.yaml.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

AttrType = Literal["number", "integer", "boolean", "string"]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


class AttributeDef(BaseModel):
    """One attribute to extract from each target page."""
    name: str                       # snake_case identifier, becomes a JSON key
    type: AttrType = "string"
    unit: Optional[str] = None      # e.g. "hp", "USD", "in" (numbers only)
    description: str = ""           # shown to the LLM; be specific!

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"attribute name {v!r} must be snake_case (letters, digits, underscores)"
            )
        return v


class TargetDef(BaseModel):
    """What counts as a page we want to extract from."""
    description: str                # one or two sentences, e.g. "a product detail
                                    # page for a single espresso machine model"
    include_rules: List[str] = Field(default_factory=list)  # extra "counts as target" rules
    exclude_rules: List[str] = Field(default_factory=list)  # "does NOT count" rules


class DiscoveryDef(BaseModel):
    """Hints that help URL discovery find relevant pages."""
    include_keywords: List[str] = Field(default_factory=list)  # phrases matched in URL/anchor text
    url_hints: List[str] = Field(default_factory=list)          # path fragments like "/products/"
    exclude_patterns: List[str] = Field(default_factory=list)   # extra regex excludes


class SiteDef(BaseModel):
    """One website to scrape."""
    name: str
    slug: str
    start_urls: List[str]

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"site slug {v!r} must be lowercase letters/digits/hyphens")
        return v

    @field_validator("start_urls")
    @classmethod
    def _non_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("site needs at least one start_url")
        for u in v:
            if not u.startswith(("http://", "https://")):
                raise ValueError(f"start_url {u!r} must begin with http:// or https://")
        return v


class Profile(BaseModel):
    """Top-level profile file contents."""
    name: str                       # human-readable project name
    slug: str                       # used for the data directory: data/<slug>/
    target: TargetDef
    attributes: List[AttributeDef]
    discovery: DiscoveryDef = Field(default_factory=DiscoveryDef)
    sites: List[SiteDef] = Field(default_factory=list)
    extraction_rules: List[str] = Field(default_factory=list)  # free-form extra rules for the LLM

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError(f"profile slug {v!r} must be lowercase letters/digits/hyphens")
        return v

    @field_validator("attributes")
    @classmethod
    def _unique_attrs(cls, v: List[AttributeDef]) -> List[AttributeDef]:
        if not v:
            raise ValueError("profile needs at least one attribute")
        names = [a.name for a in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate attribute names: {sorted(dupes)}")
        return v

    def site(self, name_or_slug: str) -> Optional[SiteDef]:
        t = name_or_slug.lower()
        for s in self.sites:
            if s.name.lower() == t or s.slug.lower() == t:
                return s
        return None


def load_profile(path: Path) -> Profile:
    """Load and validate a profile YAML, with friendly error messages."""
    if not path.exists():
        raise SystemExit(f"Profile file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SystemExit(f"Profile {path} is not valid YAML:\n{e}") from e
    if not isinstance(raw, dict):
        raise SystemExit(f"Profile {path} must be a YAML mapping (key: value pairs).")
    try:
        return Profile.model_validate(raw)
    except Exception as e:
        raise SystemExit(f"Profile {path} failed validation:\n{e}") from e
