"""
Seed a curated example run for demos and private deployments.
"""

from __future__ import annotations

import json
from typing import Any

from db import database as db


DEMO_RUN_VERSION = 3
DEMO_TARGET_URL = f"https://demo.evident.local/example-neuroscience-faculty?v={DEMO_RUN_VERSION}"
DEMO_INTEREST = "neuroscience research"
DEMO_STATUS = "completed"
DEMO_STAGE = "complete"
DEMO_STAGE_DETAIL = "Loaded curated example run."
DEMO_EVALUATION_MODE = "demo-seeded"
DEMO_MODEL_USED = "demo-seeded"


def _evidence(
    source_type: str,
    title: str,
    snippet: str,
    *,
    source_url: str,
    identity_score: float,
    conflict_flag: bool = False,
    source_priority: int = 3,
) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "title": title,
        "snippet": snippet,
        "source_url": source_url,
        "identity_score": identity_score,
        "conflict_flag": conflict_flag,
        "source_priority": source_priority,
    }


def _citation(index: int, quote: str, why_relevant: str) -> dict[str, Any]:
    return {"index": index, "quote": quote, "why_relevant": why_relevant}


DEMO_CONTACTS: list[dict[str, Any]] = [
    {
        "name": "Maya Rios",
        "title": "Assistant Professor",
        "role_category": "faculty",
        "email": "mrios@demo-university.edu",
        "url": "https://demo.evident.local/faculty/maya-rios",
        "research_text": "Studies cortical plasticity and openly trains undergraduate researchers in sensory circuit neuroscience.",
        "source_page": DEMO_TARGET_URL,
        "identity_verified": True,
        "identity_confidence": 0.96,
        "evidence": [
            _evidence(
                "faculty_page",
                "Department faculty profile",
                "Cortical plasticity and sensory circuit adaptation.",
                source_url="https://demo.evident.local/faculty/maya-rios",
                identity_score=0.96,
            ),
            _evidence(
                "lab_page",
                "Rios Lab overview",
                "The lab trains undergraduate researchers in imaging and weekly analysis meetings.",
                source_url="https://demo.evident.local/labs/rios-lab",
                identity_score=0.94,
                source_priority=4,
            ),
            _evidence(
                "directory",
                "Faculty directory listing",
                "Assistant Professor with direct contact email and active neuroscience profile.",
                source_url="https://demo.evident.local/directory/neuroscience-faculty",
                identity_score=0.91,
                source_priority=2,
            ),
        ],
        "chunks": [
            "Cortical plasticity and sensory circuit adaptation.",
            "The lab trains undergraduate researchers in imaging and weekly analysis meetings.",
            "Assistant Professor with direct contact email and active neuroscience profile.",
        ],
        "evaluation": {
            "relevance_score": 8.9,
            "recommended": True,
            "evaluation_status": "recommended",
            "research_summary": "Studies cortical plasticity and explicitly trains undergraduate researchers in sensory circuit neuroscience.",
            "reason_trace": {
                "match": "Her lab combines core neuroscience research with clear undergraduate mentorship signals, which matches the user's goal well.",
                "gap": "The public profile does not confirm current opening timelines, so outreach should ask about availability rather than assume space.",
                "evidence": "Department, lab, and directory sources align on active neuroscience work plus direct undergraduate training.",
            },
            "confidence_label": "High Confidence",
            "confidence_score": 1.0,
            "confidence_justification": "3 independent sources align, identity verified, direct email present, support 7.4/10, fit 8.9/10.",
            "evidence_strength_score": 7.4,
            "cited_evidence": [
                _citation(1, "trains undergraduate researchers", "Shows direct undergraduate mentorship."),
                _citation(2, "cortical plasticity", "Shows clear neuroscience fit."),
                _citation(3, "direct contact email", "Makes outreach practical right away."),
            ],
            "not_recommended_reason": None,
            "insufficient_reason": None,
            "evidence_agreement": {"agreement_count": 3, "conflict_count": 0, "verdict": "strong_agreement", "impact": "Multiple independent sources align. Confidence unaffected."},
            "conflicts_detected": False,
            "conflict_note": "",
            "original_score": 8.9,
            "original_status": "recommended",
            "second_pass_triggered": False,
            "revised_score": None,
            "revised_status": None,
            "revision_reason": None,
            "confidence_changed": False,
            "final_status": "recommended",
            "tokens_used": 0,
            "model_used": DEMO_MODEL_USED,
            "final_score": 9.1,
            "ranking_score": 9.5,
            "score_breakdown": {"ai_relevance_score": 8.9, "title_boost": 1.0, "email_bonus": 1.0, "recommendation_bonus": 0.5, "evidence_strength_score": 7.4, "evidence_strength_boost": 2.22, "evaluation_status": "recommended", "original_score": 8.9, "second_pass_triggered": False},
        },
        "draft": {
            "subject": "Question about your cortical plasticity research",
            "body": "Hi Dr. Rios,\n\nI'm Alex Carter, an undergraduate student looking for neuroscience lab experience. I was especially interested in your work on cortical plasticity and the way your lab describes undergraduate research training.\n\nI'm looking for hands-on research exposure this semester, and your lab stood out because it seems like a place where undergraduates get real mentorship and responsibilities.\n\nMy background includes biology coursework and basic data cleaning in Python/Excel. I'm early in research, but consistent and ready to learn.\n\nIf you have space for an undergraduate in your lab, I'd appreciate the chance to talk briefly about how students typically get involved.\n\nThank you for your time,\nAlex Carter\nalex.carter@example.edu\n000-000-0000",
            "model_used": DEMO_MODEL_USED,
        },
    },
    {
        "name": "Elena Park",
        "title": "Associate Professor",
        "role_category": "faculty",
        "email": "epark@demo-university.edu",
        "url": "https://demo.evident.local/faculty/elena-park",
        "research_text": "Studies axonal repair and neural regeneration, with a lab page that confirms active undergraduate involvement.",
        "source_page": DEMO_TARGET_URL,
        "identity_verified": True,
        "identity_confidence": 0.91,
        "evidence": [
            _evidence(
                "faculty_page",
                "Department faculty profile",
                "Axonal repair and neural regeneration in mammalian models.",
                source_url="https://demo.evident.local/faculty/elena-park",
                identity_score=0.91,
            ),
            _evidence(
                "lab_page",
                "Park Lab opportunities",
                "Undergraduate assistants help with imaging prep and data annotation.",
                source_url="https://demo.evident.local/labs/park-lab",
                identity_score=0.89,
                source_priority=4,
            ),
            _evidence(
                "directory",
                "Faculty directory listing",
                "Associate Professor with direct contact email and active lab site.",
                source_url="https://demo.evident.local/directory/neuroscience-faculty",
                identity_score=0.84,
                source_priority=2,
            ),
        ],
        "chunks": [
            "Axonal repair and neural regeneration in mammalian models.",
            "Undergraduate assistants help with imaging prep and data annotation.",
            "Associate Professor with direct contact email and active lab site.",
        ],
        "evaluation": {
            "relevance_score": 6.9,
            "recommended": True,
            "evaluation_status": "recommended",
            "research_summary": "Works on neural regeneration and became a stronger match once the lab page confirmed active undergraduate involvement.",
            "reason_trace": {
                "match": "Her neural repair work is clearly within neuroscience and the deeper lab page shows real undergraduate participation.",
                "gap": "The directory page alone looked borderline, so the recommendation depended on deeper confirmation from the lab page.",
                "evidence": "Faculty, lab, and directory sources support both active research fit and a realistic undergraduate path.",
            },
            "confidence_label": "High Confidence",
            "confidence_score": 1.0,
            "confidence_justification": "3 independent sources align, identity verified, direct email present, support 6.5/10, fit 8.1/10.",
            "evidence_strength_score": 6.5,
            "cited_evidence": [
                _citation(1, "axonal repair", "Shows direct neuroscience relevance."),
                _citation(2, "undergraduate assistants help", "Confirms hands-on student involvement."),
                _citation(3, "active lab site", "Supports that the lab is current and reachable."),
            ],
            "not_recommended_reason": None,
            "insufficient_reason": None,
            "evidence_agreement": {"agreement_count": 3, "conflict_count": 0, "verdict": "strong_agreement", "impact": "Multiple independent sources align. Confidence unaffected."},
            "conflicts_detected": False,
            "conflict_note": "",
            "original_score": 6.9,
            "original_status": "not_recommended",
            "second_pass_triggered": True,
            "revised_score": 8.1,
            "revised_status": "recommended",
            "revision_reason": "A deeper lab-page pass confirmed active undergraduate mentorship and moved the contact above the recommendation line.",
            "confidence_changed": True,
            "final_status": "recommended",
            "tokens_used": 0,
            "model_used": DEMO_MODEL_USED,
            "final_score": 8.1,
            "ranking_score": 8.6,
            "score_breakdown": {"ai_relevance_score": 8.1, "title_boost": 1.0, "email_bonus": 1.0, "recommendation_bonus": 0.5, "evidence_strength_score": 6.5, "evidence_strength_boost": 1.95, "evaluation_status": "recommended", "original_score": 6.9, "second_pass_triggered": True},
        },
        "draft": {
            "subject": "Question about your neural regeneration research",
            "body": "Hi Dr. Park,\n\nI'm Alex Carter, an undergraduate student looking for neuroscience lab experience. I was interested in your work on neural regeneration and axonal repair, especially because your lab page mentions undergraduate involvement.\n\nI'm looking for hands-on research exposure this semester and your group stood out as a place where I could learn good research habits and contribute where helpful.\n\nMy background includes biology coursework and basic data cleaning in Python/Excel. I'm early in research, but consistent and ready to learn.\n\nIf you have space for an undergraduate in your lab, I would appreciate the chance to talk briefly about how students typically get involved.\n\nThank you for your time,\nAlex Carter\nalex.carter@example.edu\n000-000-0000",
            "model_used": DEMO_MODEL_USED,
        },
    },
    {
        "name": "Adrian Bell",
        "title": "Professor",
        "role_category": "faculty",
        "email": "abell@demo-university.edu",
        "url": "https://demo.evident.local/faculty/adrian-bell",
        "research_text": "Studies hippocampal memory formation and electrophysiology, with strong neuroscience fit but fewer explicit mentoring signals than the top labs.",
        "source_page": DEMO_TARGET_URL,
        "identity_verified": True,
        "identity_confidence": 0.88,
        "evidence": [
            _evidence(
                "faculty_page",
                "Department faculty profile",
                "Hippocampal memory formation and synaptic encoding.",
                source_url="https://demo.evident.local/faculty/adrian-bell",
                identity_score=0.88,
            ),
            _evidence(
                "directory",
                "Faculty directory listing",
                "Professor with direct contact email and active neuroscience profile.",
                source_url="https://demo.evident.local/directory/neuroscience-faculty",
                identity_score=0.83,
                source_priority=2,
            ),
        ],
        "chunks": [
            "Hippocampal memory formation and synaptic encoding.",
            "Professor with direct contact email and active neuroscience profile.",
        ],
        "evaluation": {
            "relevance_score": 6.8,
            "recommended": True,
            "evaluation_status": "recommended",
            "research_summary": "Strong neuroscience fit with solid public evidence, but fewer explicit undergraduate mentorship signals than the top two labs.",
            "reason_trace": {
                "match": "His hippocampal memory work is a clear neuroscience fit and the public profile is research-active.",
                "gap": "The public sources do not say much about undergraduate onboarding, so the outreach should stay exploratory.",
                "evidence": "Faculty and directory sources agree on active memory-systems research and direct contact availability.",
            },
            "confidence_label": "Moderate Confidence",
            "confidence_score": 0.65,
            "confidence_justification": "2 independent sources align, identity verified, direct email present, support 5.4/10, fit 6.9/10.",
            "evidence_strength_score": 5.4,
            "cited_evidence": [
                _citation(1, "hippocampal memory formation", "Shows a direct neuroscience match."),
                _citation(2, "direct contact email", "Makes outreach practical without extra lookup work."),
            ],
            "not_recommended_reason": None,
            "insufficient_reason": None,
            "evidence_agreement": {"agreement_count": 2, "conflict_count": 0, "verdict": "strong_agreement", "impact": "Multiple independent sources align. Confidence unaffected."},
            "conflicts_detected": False,
            "conflict_note": "",
            "original_score": 6.8,
            "original_status": "recommended",
            "second_pass_triggered": False,
            "revised_score": None,
            "revised_status": None,
            "revision_reason": None,
            "confidence_changed": False,
            "final_status": "recommended",
            "tokens_used": 0,
            "model_used": DEMO_MODEL_USED,
            "final_score": 6.9,
            "ranking_score": 7.4,
            "score_breakdown": {"ai_relevance_score": 6.8, "title_boost": 1.2, "email_bonus": 1.0, "recommendation_bonus": 0.5, "evidence_strength_score": 5.4, "evidence_strength_boost": 1.62, "evaluation_status": "recommended", "original_score": 6.8, "second_pass_triggered": False},
        },
        "draft": {
            "subject": "Question about your hippocampal memory research",
            "body": "Hi Dr. Bell,\n\nI'm Alex Carter, an undergraduate student looking for neuroscience lab experience. I came across your work on hippocampal memory formation and electrophysiology, and it stood out as a strong fit with my interests.\n\nI'm looking for hands-on research exposure this semester, and I would love the chance to learn in a lab with an active experimental neuroscience focus.\n\nMy background includes biology coursework and basic data cleaning in Python/Excel. I'm early in research, but eager to learn and contribute where helpful.\n\nIf you are open to it, I'd appreciate the chance to learn how students typically get involved in your lab.\n\nThank you for your time,\nAlex Carter\nalex.carter@example.edu\n000-000-0000",
            "model_used": DEMO_MODEL_USED,
        },
    },
    {
        "name": "Victor Hale",
        "title": "Professor",
        "role_category": "faculty",
        "email": "",
        "url": "https://demo.evident.local/faculty/victor-hale",
        "research_text": "Public sources split between neuroscience-adjacent imaging work and broader oncology administration, which made the final decision less favorable after a second pass.",
        "source_page": DEMO_TARGET_URL,
        "identity_verified": True,
        "identity_confidence": 0.81,
        "evidence": [
            _evidence(
                "faculty_page",
                "Department faculty profile",
                "Neuroimaging methods with references to translational oncology.",
                source_url="https://demo.evident.local/faculty/victor-hale",
                identity_score=0.81,
                conflict_flag=True,
            ),
            _evidence(
                "lab_page",
                "Center leadership page",
                "Administrative leadership across translational oncology programs.",
                source_url="https://demo.evident.local/centers/translational-oncology",
                identity_score=0.79,
                conflict_flag=True,
                source_priority=4,
            ),
            _evidence(
                "directory",
                "Faculty directory listing",
                "Professor profile with broad imaging and cancer-center leadership responsibilities.",
                source_url="https://demo.evident.local/directory/neuroscience-faculty",
                identity_score=0.72,
                conflict_flag=True,
                source_priority=2,
            ),
        ],
        "chunks": [
            "Neuroimaging methods with references to translational oncology.",
            "Administrative leadership across translational oncology programs.",
            "Professor profile with broad imaging and cancer-center leadership responsibilities.",
        ],
        "evaluation": {
            "relevance_score": 6.4,
            "recommended": False,
            "evaluation_status": "not_recommended",
            "research_summary": "Initially looked plausible because of neuroimaging overlap, but the stronger signal is administrative oncology leadership rather than a clear neuroscience lab fit.",
            "reason_trace": {
                "match": "There is some neuroscience-adjacent imaging overlap, which is why the contact initially looked borderline relevant.",
                "gap": "The stronger public signal points to administrative and oncology-focused work rather than an active neuroscience lab for an undergraduate researcher.",
                "evidence": "Faculty, lab, and center sources disagree on whether the contact should be treated as a neuroscience research target, so confidence is reduced.",
            },
            "confidence_label": "Low Confidence",
            "confidence_score": 0.3,
            "confidence_justification": "Conflicting public sources reduce certainty even though identity is verified.",
            "evidence_strength_score": 4.6,
            "cited_evidence": [
                _citation(1, "translational oncology programs", "Suggests the role is broader and less centered on neuroscience lab mentorship."),
                _citation(2, "neuroimaging methods", "Explains why the contact was initially considered."),
            ],
            "not_recommended_reason": "Research area is adjacent, but the stronger public signal points to administrative oncology work instead of a clear neuroscience lab match.",
            "insufficient_reason": None,
            "evidence_agreement": {"agreement_count": 1, "conflict_count": 2, "verdict": "conflict", "impact": "Conflicting signals detected. Confidence reduced one tier."},
            "conflicts_detected": True,
            "conflict_note": "Conflicting signals: sources suggest both neuroscience-adjacent imaging and broader oncology leadership.",
            "original_score": 7.0,
            "original_status": "recommended",
            "second_pass_triggered": True,
            "revised_score": 5.8,
            "revised_status": "not_recommended",
            "revision_reason": "A second pass found stronger evidence of administrative oncology leadership than hands-on neuroscience mentorship, so the contact was downgraded.",
            "confidence_changed": True,
            "final_status": "not_recommended",
            "tokens_used": 0,
            "model_used": DEMO_MODEL_USED,
            "final_score": 5.8,
            "ranking_score": 5.9,
            "score_breakdown": {"ai_relevance_score": 5.8, "title_boost": 1.2, "email_bonus": 0.0, "recommendation_bonus": 0.0, "evidence_strength_score": 4.6, "evidence_strength_boost": 1.38, "evaluation_status": "not_recommended", "original_score": 7.0, "second_pass_triggered": True},
        },
        "draft": None,
    },
    {
        "name": "Priya Nandakumar",
        "title": "Assistant Professor",
        "role_category": "faculty",
        "email": "",
        "url": "https://demo.evident.local/faculty/priya-nandakumar",
        "research_text": "Public profile text is brief and mostly biographical, with no clear research summary or lab description.",
        "source_page": DEMO_TARGET_URL,
        "identity_verified": False,
        "identity_confidence": 0.48,
        "evidence": [
            _evidence(
                "directory",
                "Faculty directory listing",
                "Assistant Professor listed without a direct email or lab summary.",
                source_url="https://demo.evident.local/directory/neuroscience-faculty",
                identity_score=0.48,
                source_priority=2,
            ),
        ],
        "chunks": [
            "Assistant Professor listed without a direct email or lab summary.",
        ],
        "evaluation": {
            "relevance_score": 3.8,
            "recommended": False,
            "evaluation_status": "insufficient_evidence",
            "research_summary": "The public record is too thin to tell whether this contact is an active research fit or whether an undergraduate path exists.",
            "reason_trace": {
                "match": "The department listing suggests possible relevance, but there is not enough concrete research detail to support a strong match.",
                "gap": "No lab page, no direct email, and no clear research description were found, so the system should not make a confident outreach recommendation.",
                "evidence": "Only a thin directory-style profile was available, leaving too little public support for a strong decision.",
            },
            "confidence_label": "Low Confidence",
            "confidence_score": 0.3,
            "confidence_justification": "Too few sources and no identity verification keep this contact in insufficient-evidence territory.",
            "evidence_strength_score": 1.8,
            "cited_evidence": [
                _citation(1, "without a direct email or lab summary", "Shows why the system refused to recommend outreach."),
            ],
            "not_recommended_reason": None,
            "insufficient_reason": "Public evidence is too weak to make a confident recommendation.",
            "evidence_agreement": {"agreement_count": 1, "conflict_count": 0, "verdict": "insufficient", "impact": "Too few sources to assess agreement. Confidence reduced."},
            "conflicts_detected": False,
            "conflict_note": "",
            "original_score": 3.8,
            "original_status": "insufficient_evidence",
            "second_pass_triggered": False,
            "revised_score": None,
            "revised_status": None,
            "revision_reason": None,
            "confidence_changed": False,
            "final_status": "insufficient_evidence",
            "tokens_used": 0,
            "model_used": DEMO_MODEL_USED,
            "final_score": 3.8,
            "ranking_score": 3.7,
            "score_breakdown": {"ai_relevance_score": 3.8, "title_boost": 1.0, "email_bonus": 0.0, "recommendation_bonus": 0.0, "evidence_strength_score": 1.8, "evidence_strength_boost": 0.54, "evaluation_status": "insufficient_evidence", "original_score": 3.8, "second_pass_triggered": False},
        },
        "draft": None,
    },
]


def _find_existing_demo_run_id() -> int | None:
    connection = db.get_connection()
    row = connection.execute(
        """
        SELECT id
        FROM runs
        WHERE target_url = ?
          AND status = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (DEMO_TARGET_URL, DEMO_STATUS),
    ).fetchone()
    connection.close()
    return row["id"] if row else None


def _build_metrics() -> dict[str, Any]:
    evaluated = len(DEMO_CONTACTS)
    recommended = sum(1 for item in DEMO_CONTACTS if item["evaluation"]["final_status"] == "recommended")
    insufficient = sum(1 for item in DEMO_CONTACTS if item["evaluation"]["final_status"] == "insufficient_evidence")
    conflicts = sum(1 for item in DEMO_CONTACTS if item["evaluation"]["conflicts_detected"])
    second_pass = sum(1 for item in DEMO_CONTACTS if item["evaluation"]["second_pass_triggered"])
    direct_emails = sum(1 for item in DEMO_CONTACTS if item["email"])
    verified = sum(1 for item in DEMO_CONTACTS if item["identity_verified"])
    drafts_generated = sum(1 for item in DEMO_CONTACTS if item["draft"])
    avg_relevance = round(sum(item["evaluation"]["relevance_score"] for item in DEMO_CONTACTS) / evaluated, 1)
    avg_support = round(sum(item["evaluation"]["evidence_strength_score"] for item in DEMO_CONTACTS) / evaluated, 1)
    avg_confidence = round(sum(item["evaluation"]["confidence_score"] for item in DEMO_CONTACTS) / evaluated, 2)
    confidence_distribution = {
        "high": sum(1 for item in DEMO_CONTACTS if item["evaluation"]["confidence_label"] == "High Confidence"),
        "moderate": sum(1 for item in DEMO_CONTACTS if item["evaluation"]["confidence_label"] == "Moderate Confidence"),
        "low": sum(1 for item in DEMO_CONTACTS if item["evaluation"]["confidence_label"] == "Low Confidence"),
        "insufficient": insufficient,
    }
    return {
        "contacts_discovered": 14,
        "contacts_after_clean": 14,
        "contacts_pre_filtered": evaluated,
        "contacts_evaluated": evaluated,
        "identities_verified": verified,
        "direct_emails_found": direct_emails,
        "recommended_count": recommended,
        "insufficient_evidence_count": insufficient,
        "drafts_generated": drafts_generated,
        "avg_relevance_score": avg_relevance,
        "avg_confidence": avg_confidence,
        "avg_evidence_strength": avg_support,
        "avg_tokens_per_evaluation": 0,
        "api_calls_made": 0,
        "model_calls_saved": 8,
        "estimated_minutes_saved": 84,
        "contacts_excluded_sent": 0,
        "requests_attempted": 16,
        "blocked_responses_count": 0,
        "compatibility_status": "supported",
        "second_pass_count": second_pass,
        "conflicts_detected_count": conflicts,
        "deep_retrieval_triggered_count": 2,
        "deep_retrieval_chunks_added": 4,
        "evidence_coverage": 2.3,
        "confidence_distribution": confidence_distribution,
        "run_kind": "curated_example",
    }


def _build_run_insight(metrics: dict[str, Any]) -> str:
    return (
        f"This curated example run evaluates {metrics['contacts_evaluated']} contacts from a cleaned pool of "
        f"{metrics['contacts_after_clean']}. {metrics['recommended_count']} are recommended, "
        f"{metrics['contacts_evaluated'] - metrics['recommended_count'] - metrics['insufficient_evidence_count']} are not recommended, "
        f"and {metrics['insufficient_evidence_count']} is held for insufficient evidence. "
        f"Direct emails are available for {metrics['direct_emails_found']} of the evaluated contacts."
    )


def ensure_demo_run() -> int:
    db.init_db()
    existing_run_id = _find_existing_demo_run_id()
    if existing_run_id is not None:
        return existing_run_id

    run_id = db.create_run(DEMO_TARGET_URL, DEMO_INTEREST, status="running")
    metrics = _build_metrics()

    for contact in DEMO_CONTACTS:
        contact_id = db.save_contact(
            run_id=run_id,
            name=contact["name"],
            title=contact["title"],
            role_category=contact["role_category"],
            email=contact["email"],
            url=contact["url"],
            research_text=contact["research_text"],
            source_page=contact["source_page"],
            identity_verified=contact["identity_verified"],
            identity_confidence=contact["identity_confidence"],
            evidence_json=json.dumps(contact["evidence"]),
        )

        for index, chunk in enumerate(contact["chunks"]):
            evidence_item = contact["evidence"][min(index, len(contact["evidence"]) - 1)] if contact["evidence"] else {}
            db.save_evidence_chunk(
                contact_id=contact_id,
                run_id=run_id,
                source_url=evidence_item.get("source_url", contact["url"]),
                source_type=evidence_item.get("source_type", "demo_source"),
                chunk_text=chunk,
            )

        evaluation = contact["evaluation"]
        db.save_evaluation(
            run_id=run_id,
            contact_id=contact_id,
            relevance_score=evaluation["relevance_score"],
            recommended=evaluation["recommended"],
            evaluation_status=evaluation["evaluation_status"],
            research_summary=evaluation["research_summary"],
            reason_match=evaluation["reason_trace"]["match"],
            reason_gap=evaluation["reason_trace"]["gap"],
            reason_evidence=evaluation["reason_trace"]["evidence"],
            confidence_label=evaluation["confidence_label"],
            confidence_score=evaluation["confidence_score"],
            confidence_justification=evaluation["confidence_justification"],
            evidence_strength_score=evaluation["evidence_strength_score"],
            cited_evidence_json=json.dumps(evaluation["cited_evidence"]),
            not_recommended_reason=evaluation["not_recommended_reason"],
            insufficient_reason=evaluation["insufficient_reason"],
            evidence_agreement_json=json.dumps(evaluation["evidence_agreement"]),
            conflicts_detected=evaluation["conflicts_detected"],
            conflict_note=evaluation["conflict_note"],
            original_score=evaluation["original_score"],
            original_status=evaluation["original_status"],
            second_pass_triggered=evaluation["second_pass_triggered"],
            revised_score=evaluation["revised_score"],
            revised_status=evaluation["revised_status"],
            revision_reason=evaluation["revision_reason"],
            confidence_changed=evaluation["confidence_changed"],
            final_status=evaluation["final_status"],
            tokens_used=evaluation["tokens_used"],
            model_used=evaluation["model_used"],
            final_score=evaluation["final_score"],
            ranking_score=evaluation["ranking_score"],
            score_breakdown=json.dumps(evaluation["score_breakdown"]),
        )

        if contact["draft"]:
            db.save_draft(
                run_id=run_id,
                contact_id=contact_id,
                subject=contact["draft"]["subject"],
                body=contact["draft"]["body"],
                model_used=contact["draft"]["model_used"],
            )

    db.update_run(
        run_id,
        status=DEMO_STATUS,
        contacts_found=metrics["contacts_discovered"],
        evaluations_completed=metrics["contacts_evaluated"],
        drafts_generated=metrics["drafts_generated"],
        evaluation_mode=DEMO_EVALUATION_MODE,
        stage=DEMO_STAGE,
        stage_detail=DEMO_STAGE_DETAIL,
        average_confidence=metrics["avg_confidence"],
        metrics=metrics,
        run_insight=_build_run_insight(metrics),
    )
    return run_id
