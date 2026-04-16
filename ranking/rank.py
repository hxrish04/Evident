"""
ranking/rank.py
Applies rule-based scoring boosts on top of AI relevance scores.
Keeps ranking logic transparent and auditable.
"""

from dataclasses import dataclass

from ai.evaluate import ContactEvaluation


TITLE_SCORES = {
    "professor": 2.0,
    "associate professor": 1.8,
    "assistant professor": 1.6,
    "principal investigator": 1.8,
    "pi": 1.8,
    "research scientist": 1.4,
    "postdoc": 1.2,
    "postdoctoral": 1.2,
    "lecturer": 1.0,
    "fellow": 1.0,
    "phd student": 0.6,
    "graduate student": 0.5,
    "unknown": 0.0,
}

EMAIL_BONUS = 1.0
RECOMMENDATION_BONUS = 0.5
STATUS_ORDER = {
    "recommended": 0,
    "not_recommended": 1,
    "insufficient_evidence": 2,
}


@dataclass
class RankedContact:
    evaluation: ContactEvaluation
    final_score: float
    score_breakdown: dict


def compute_score(evaluation: ContactEvaluation) -> RankedContact:
    ai_score = evaluation.final_score
    title_lower = (
        evaluation.raw_contact.title.lower()
        if evaluation.raw_contact and evaluation.raw_contact.title
        else "unknown"
    )
    title_boost = 0.0
    for keyword, boost in TITLE_SCORES.items():
        if keyword in title_lower:
            title_boost = boost
            break

    has_email = bool(evaluation.raw_contact and evaluation.raw_contact.email)
    email_boost = EMAIL_BONUS if has_email else 0.0
    rec_boost = RECOMMENDATION_BONUS if evaluation.recommended else 0.0
    # Evidence strength still matters after evaluation because two equally relevant contacts should not rank the same if one is much better supported.
    evidence_strength_boost = evaluation.evidence_strength_score * 0.3
    final_score = ai_score + title_boost + email_boost + rec_boost + evidence_strength_boost
    if evaluation.final_status == "insufficient_evidence":
        final_score -= 5.0

    breakdown = {
        "ai_relevance_score": ai_score,
        "title_boost": title_boost,
        "email_bonus": email_boost,
        "recommendation_bonus": rec_boost,
        "evidence_strength_score": evaluation.evidence_strength_score,
        "evidence_strength_boost": round(evidence_strength_boost, 2),
        "evaluation_status": evaluation.final_status,
        "original_score": evaluation.original_score,
        "second_pass_triggered": evaluation.second_pass_triggered,
        "role_category": evaluation.raw_contact.role_category if evaluation.raw_contact else "unknown",
        "final_score": round(final_score, 2),
    }

    return RankedContact(
        evaluation=evaluation,
        final_score=round(final_score, 2),
        score_breakdown=breakdown,
    )


def rank_contacts(evaluations: list[ContactEvaluation]) -> list[RankedContact]:
    ranked = [compute_score(evaluation) for evaluation in evaluations]
    ranked.sort(
        key=lambda item: (
            STATUS_ORDER.get(item.evaluation.final_status, 99),
            -item.final_score,
        )
    )
    return ranked
