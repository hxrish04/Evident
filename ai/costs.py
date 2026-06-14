"""
Cost and token accounting for Claude-backed stages.

Evident already caps run cost structurally (deterministic pre-filter, bounded
retrieval, eval/draft caps). This module turns those controls into hard numbers:
it converts per-call token usage into an estimated USD cost so a run can report
what it actually spent, what the pre-filter saved, and how a cheaper triage tier
changes the bill.

Prices are USD per million tokens and are intentionally editable in one place.
They are estimates for reporting only; they do not affect any decision.
"""

from __future__ import annotations

import os

# USD per 1M tokens (input, output). Keep this list small and current; anything
# not listed falls back to DEFAULT_PRICING so the math never crashes a run.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "claude-3-5-haiku": (0.80, 4.0),
    "claude-3-haiku": (0.25, 1.25),
}

DEFAULT_PRICING: tuple[float, float] = (3.0, 15.0)

# Models that never hit the API; they cost nothing and should be reported as $0.
FREE_MODELS = {"cache-reuse", "heuristic-fallback", ""}


def _normalize(model: str) -> str:
    return str(model or "").strip().lower()


def price_for_model(model: str) -> tuple[float, float]:
    """Return (input_price_per_mtok, output_price_per_mtok) for a model id.

    Matching is prefix-based so dated aliases (e.g. ``claude-haiku-4-5-20251001``)
    resolve to the same family price as the short alias.
    """
    normalized = _normalize(model)
    if normalized in FREE_MODELS:
        return (0.0, 0.0)
    for prefix, pricing in MODEL_PRICING.items():
        if normalized.startswith(prefix):
            return pricing
    return DEFAULT_PRICING


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD cost for one call. Rounded to 6 dp so micro-costs survive."""
    if _normalize(model) in FREE_MODELS:
        return 0.0
    input_price, output_price = price_for_model(model)
    cost = (int(input_tokens or 0) / 1_000_000) * input_price
    cost += (int(output_tokens or 0) / 1_000_000) * output_price
    return round(cost, 6)


def is_billable(model: str) -> bool:
    return _normalize(model) not in FREE_MODELS


def summarize_costs(rows: list[dict]) -> dict:
    """Aggregate per-evaluation usage rows into a run-level cost summary.

    Each row is expected to expose ``model_used``, ``input_tokens``,
    ``output_tokens`` (and optionally ``tokens_used`` as a fallback total).
    The summary is reporting-only and safe on empty input.
    """
    total_input = 0
    total_output = 0
    total_cost = 0.0
    billable_calls = 0
    by_model: dict[str, dict] = {}

    for row in rows or []:
        model = str(row.get("model_used", "") or "")
        input_tokens = int(row.get("input_tokens", 0) or 0)
        output_tokens = int(row.get("output_tokens", 0) or 0)
        # Fall back to a single total if the split is unavailable (older rows):
        # treat it as output-weighted so we never under-report spend.
        if input_tokens == 0 and output_tokens == 0 and int(row.get("tokens_used", 0) or 0):
            output_tokens = int(row.get("tokens_used", 0) or 0)

        cost = estimate_cost(model, input_tokens, output_tokens)
        total_input += input_tokens
        total_output += output_tokens
        total_cost += cost
        if is_billable(model):
            billable_calls += 1

        bucket = by_model.setdefault(
            model or "unknown",
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)

    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "estimated_cost_usd": round(total_cost, 6),
        "billable_calls": billable_calls,
        "cost_by_model": by_model,
    }


def default_triage_model() -> str:
    return os.getenv("ANTHROPIC_TRIAGE_MODEL", "").strip()
