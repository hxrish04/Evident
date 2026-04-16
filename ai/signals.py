"""
Shared signal scoring for support strength and confidence.
"""

from __future__ import annotations

from typing import Any


def keyword_tokens(text: str) -> set[str]:
    return {
        token.strip(" ,.;:()[]{}").lower()
        for token in str(text or "").replace("\n", " ").split()
        if len(token.strip(" ,.;:()[]{}")) > 3
    }


def _coerce_dict_list(values: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in (values or []) if isinstance(item, dict)]


def _candidate_source_urls(*collections: list[dict[str, Any]] | None) -> set[str]:
    urls: set[str] = set()
    for collection in collections:
        for item in _coerce_dict_list(collection):
            source_url = str(item.get("source_url", "") or "").strip().lower()
            if source_url:
                urls.add(source_url)
    return urls


def build_support_snapshot(
    *,
    research_text: str = "",
    email: str = "",
    identity_verified: bool = False,
    evidence: list[dict[str, Any]] | None = None,
    chunks: list[dict[str, Any]] | None = None,
    cited_evidence: list[dict[str, Any]] | None = None,
    user_goal: str = "",
) -> dict[str, Any]:
    evidence_items = _coerce_dict_list(evidence)
    chunk_items = _coerce_dict_list(chunks)
    cited_items = _coerce_dict_list(cited_evidence)
    source_urls = _candidate_source_urls(evidence_items, chunk_items, cited_items)
    source_types = {
        str(item.get("source_type", "") or "").strip().lower()
        for item in [*evidence_items, *chunk_items, *cited_items]
        if str(item.get("source_type", "") or "").strip()
    }
    searchable_text = " ".join(
        filter(
            None,
            [
                str(research_text or ""),
                " ".join(str(item.get("snippet", "") or "") for item in evidence_items),
                " ".join(str(item.get("chunk_text", "") or "") for item in chunk_items),
                " ".join(str(item.get("quote", "") or "") for item in cited_items),
                " ".join(str(item.get("why_relevant", "") or "") for item in cited_items),
            ],
        )
    ).lower()
    goal_terms = keyword_tokens(user_goal)
    keyword_hits = sum(1 for keyword in goal_terms if keyword and keyword in searchable_text)
    return {
        "source_count": len(source_urls),
        "evidence_item_count": len(evidence_items),
        "chunk_count": len(chunk_items),
        "cited_count": len(cited_items),
        "has_goal_overlap": keyword_hits > 0,
        "keyword_hits": keyword_hits,
        "has_research_detail": len(str(research_text or "").strip()) >= 80,
        "has_direct_email": bool(str(email or "").strip()),
        "identity_verified": bool(identity_verified),
        "has_lab_page": "lab_page" in source_types,
        "source_types": sorted(source_types),
    }


def compute_evidence_strength_score(
    *,
    research_text: str = "",
    email: str = "",
    identity_verified: bool = False,
    evidence: list[dict[str, Any]] | None = None,
    chunks: list[dict[str, Any]] | None = None,
    cited_evidence: list[dict[str, Any]] | None = None,
    user_goal: str = "",
) -> float:
    snapshot = build_support_snapshot(
        research_text=research_text,
        email=email,
        identity_verified=identity_verified,
        evidence=evidence,
        chunks=chunks,
        cited_evidence=cited_evidence,
        user_goal=user_goal,
    )
    score = 0.0
    score += min(snapshot["source_count"], 4) * 1.4
    if snapshot["evidence_item_count"] >= 2 or snapshot["chunk_count"] >= 3:
        score += 1.4
    elif snapshot["evidence_item_count"] >= 1 or snapshot["chunk_count"] >= 1:
        score += 0.7
    if snapshot["cited_count"] >= 1:
        score += 0.6
    if snapshot["has_goal_overlap"]:
        score += 0.8
    if snapshot["has_research_detail"]:
        score += 0.8
    if snapshot["identity_verified"]:
        score += 0.8
    if snapshot["has_direct_email"]:
        score += 0.8
    if snapshot["has_lab_page"]:
        score += 0.4
    if snapshot["source_count"] == 0 and snapshot["evidence_item_count"] == 0 and snapshot["chunk_count"] == 0:
        score -= 3.0
    elif snapshot["source_count"] <= 1 and (snapshot["evidence_item_count"] + snapshot["chunk_count"]) <= 1:
        score -= 1.2
    return round(max(0.0, min(10.0, score)), 1)


def evidence_strength_label(score: float) -> str:
    numeric = float(score or 0)
    if numeric >= 7.0:
        return "Strong support"
    if numeric >= 4.5:
        return "Solid support"
    if numeric >= 2.0:
        return "Limited support"
    return "Thin support"


def compute_confidence_label(
    *,
    relevance_score: float,
    evidence_strength_score: float,
    identity_verified: bool,
    source_count: int,
    evaluation_status: str = "",
) -> tuple[str, float]:
    if evaluation_status == "insufficient_evidence":
        return "Low Confidence", 0.3
    if identity_verified and source_count >= 2 and evidence_strength_score >= 6.0 and relevance_score >= 7.0:
        return "High Confidence", 1.0
    if evidence_strength_score >= 3.0 and (identity_verified or relevance_score >= 5.0):
        return "Moderate Confidence", 0.65
    return "Low Confidence", 0.3


def degrade_confidence_label(confidence_label: str) -> tuple[str, float]:
    if confidence_label == "High Confidence":
        return "Moderate Confidence", 0.65
    if confidence_label == "Moderate Confidence":
        return "Low Confidence", 0.3
    return "Low Confidence", 0.3


def maybe_degrade_for_agreement(confidence_label: str, agreement: dict[str, Any] | None) -> tuple[str, float]:
    verdict = str((agreement or {}).get("verdict", "") or "").strip().lower()
    if verdict in {"conflict", "insufficient"}:
        return degrade_confidence_label(confidence_label)
    if confidence_label == "High Confidence":
        return "High Confidence", 1.0
    if confidence_label == "Moderate Confidence":
        return "Moderate Confidence", 0.65
    return "Low Confidence", 0.3


def cap_confidence_for_model(confidence_label: str, model_used: str = "") -> tuple[str, float]:
    """
    Heuristic-only runs are still useful, but they should not present themselves as the
    strongest possible confidence state when no live model judgment was involved.
    """
    normalized_model = str(model_used or "").strip().lower()
    if normalized_model == "heuristic-fallback" and confidence_label == "High Confidence":
        return "Moderate Confidence", 0.65
    if confidence_label == "High Confidence":
        return "High Confidence", 1.0
    if confidence_label == "Moderate Confidence":
        return "Moderate Confidence", 0.65
    return "Low Confidence", 0.3


def compute_confidence_justification(
    *,
    relevance_score: float,
    confidence_label: str,
    evidence_strength_score: float,
    support_snapshot: dict[str, Any],
) -> str:
    source_count = int(support_snapshot.get("source_count", 0) or 0)
    has_email = bool(support_snapshot.get("has_direct_email"))
    email_note = "direct email" if has_email else "no direct email"

    if confidence_label == "High Confidence":
        return f"{source_count} aligned sources, identity verified, {email_note}; evidence and fit signals agree."
    if confidence_label == "Moderate Confidence":
        limiter = "mixed source depth"
        if support_snapshot.get("identity_verified") and not support_snapshot.get("has_research_detail"):
            limiter = "limited research detail"
        elif float(evidence_strength_score or 0) >= 4.5 and not has_email:
            limiter = "missing direct email"
        return f"{source_count} source check with {limiter}; evidence is usable but not strong enough for high confidence."
    return f"Thin support across {source_count} source{'s' if source_count != 1 else ''}; confidence stays low."


def support_summary(
    *,
    evidence_strength_score: float,
    support_snapshot: dict[str, Any],
) -> str:
    parts: list[str] = [evidence_strength_label(evidence_strength_score)]
    source_count = int(support_snapshot.get("source_count", 0) or 0)
    if source_count:
        parts.append(f"{source_count} public source{'s' if source_count != 1 else ''}")
    if support_snapshot.get("identity_verified"):
        parts.append("identity verified")
    if support_snapshot.get("has_direct_email"):
        parts.append("direct email found")
    if support_snapshot.get("has_goal_overlap"):
        parts.append("goal-aligned evidence")
    return ", ".join(parts)
