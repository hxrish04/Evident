"""Generate the LLM tool schema and prompts from a Profile.

Everything domain-specific in the LLM stage is derived here, at runtime,
from the profile YAML. The tool schema forces the model to return strict
JSON matching the profile's attributes.
"""
from __future__ import annotations

from typing import Optional

from .profile import AttributeDef, Profile

# Per-type JSON schema for an attribute observation. Every observation
# carries value + source_text + confidence so hallucinations are auditable.

def _attr_schema(attr: AttributeDef) -> dict:
    value_types = {
        "number": ["number", "null"],
        "integer": ["integer", "null"],
        "boolean": ["boolean", "null"],
        "string": ["string", "null"],
    }[attr.type]
    props = {
        "value": {"type": value_types, "description": attr.description or attr.name},
        "source_text": {
            "type": ["string", "null"],
            "description": "Short verbatim snippet from the page that supports this value (max ~120 chars).",
        },
        "confidence": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    }
    required = ["value", "source_text", "confidence"]
    if attr.type in ("number", "integer"):
        props["unit"] = {
            "type": ["string", "null"],
            "description": f"Unit of the value{f', expected: {attr.unit}' if attr.unit else ''}.",
        }
        required.insert(1, "unit")
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


def build_extraction_tool(profile: Profile) -> dict:
    """Tool the model must call once per page: classify + extract."""
    attr_props = {a.name: _attr_schema(a) for a in profile.attributes}
    return {
        "name": "record_target_page",
        "description": (
            "Record the result of analyzing one web page. Must be called exactly "
            "once. Set is_target=false if the page is not "
            f"{profile.target.description.strip().rstrip('.')}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "is_target": {
                    "type": "boolean",
                    "description": f"True iff this page is {profile.target.description}",
                },
                "classification_reason": {
                    "type": "string",
                    "description": "One short sentence justifying the classification.",
                },
                "item_name": {
                    "type": ["string", "null"],
                    "description": (
                        "Short name identifying the specific item this page describes "
                        "(e.g. a model name or title). Null if unknown or not a target page."
                    ),
                },
                "attributes": {
                    "type": "object",
                    "properties": attr_props,
                    "required": list(attr_props.keys()),
                    "additionalProperties": False,
                },
            },
            "required": ["is_target", "classification_reason", "item_name", "attributes"],
            "additionalProperties": False,
        },
    }


def _attr_lines(profile: Profile) -> str:
    lines = []
    for a in profile.attributes:
        unit = f" Report in: {a.unit}." if a.unit else ""
        lines.append(f'- "{a.name}" ({a.type}): {a.description or "(no description)"}{unit}')
    return "\n".join(lines)


def build_extraction_system_prompt(profile: Profile) -> str:
    t = profile.target
    include = "".join(f"\n- {r}" for r in t.include_rules)
    exclude = "".join(f"\n- {r}" for r in t.exclude_rules)
    extra = "".join(f"\n- {r}" for r in profile.extraction_rules)
    return f"""\
You analyze web pages for the project "{profile.name}".

A TARGET page is: {t.description}
{f'A page also counts as a target if:{include}' if include else ''}
{f'A page does NOT count as a target if:{exclude}' if exclude else ''}

If the page is a target, extract these attributes:

{_attr_lines(profile)}

Attribute extraction rules:

- Use only values explicitly present on the page text. NEVER guess or use
  outside knowledge. If a value is not stated, set value=null and confidence=0.
- For every non-null value, include a `source_text` snippet of <= 120
  characters copied VERBATIM from the page that justifies the value.
- `confidence` is your 0-1 confidence that the value is correct for this
  exact item.
- For numeric attributes with an expected unit, convert to that unit when the
  page uses a different one, and note the original in source_text.
{f'Additional project-specific rules:{extra}' if extra else ''}

You MUST call the tool `record_target_page` exactly once.
"""


def build_discovery_tool() -> dict:
    # `urls` is a flat array of URL strings (no per-item prose) so the response
    # stays compact — a directory with hundreds of profiles would otherwise blow
    # past max_tokens, truncate the JSON, and yield zero usable URLs.
    return {
        "name": "record_target_urls",
        "description": "Record the URLs of likely target pages found among the candidate links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "description": "Absolute URLs copied EXACTLY from the candidate list, one per target page.",
                    "items": {"type": "string"},
                },
            },
            "required": ["urls"],
            "additionalProperties": False,
        },
    }


def build_discovery_system_prompt(profile: Profile) -> str:
    t = profile.target
    exclude = "".join(f"\n- {r}" for r in t.exclude_rules)
    return f"""\
You find target page URLs for the project "{profile.name}".

A TARGET page is: {t.description}

Return only links that very likely lead to target pages. Do not return
category/listing pages, search pages, news, careers, legal pages, or other
non-target pages.{f' Also exclude:{exclude}' if exclude else ''}

You must choose URLs only from the candidate links supplied by the user. Do
not invent, rewrite, shorten, canonicalize, or otherwise modify URLs. If an
item seems to exist but no candidate link is supplied for it, omit it.

You MUST call the tool `record_target_urls` exactly once.
"""


def coerce_attributes(profile: Profile, raw: Optional[dict]) -> dict:
    """Normalize the LLM's attributes dict against the profile.

    Guarantees every profile attribute is present with value/unit/source_text/
    confidence keys, drops unknown attributes, and nulls out type mismatches.
    """
    raw = raw or {}
    out: dict = {}
    for a in profile.attributes:
        obs = raw.get(a.name) or {}
        if not isinstance(obs, dict):
            obs = {}
        value = obs.get("value")
        if value is not None:
            ok = (
                (a.type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
                or (a.type == "integer" and isinstance(value, int) and not isinstance(value, bool))
                or (a.type == "boolean" and isinstance(value, bool))
                or (a.type == "string" and isinstance(value, str))
            )
            if a.type == "integer" and isinstance(value, float) and value.is_integer():
                value, ok = int(value), True
            if not ok:
                value = None
        entry = {
            "value": value,
            "source_text": obs.get("source_text"),
            "confidence": obs.get("confidence"),
        }
        if a.type in ("number", "integer"):
            entry["unit"] = obs.get("unit") or a.unit
        out[a.name] = entry
    return out
