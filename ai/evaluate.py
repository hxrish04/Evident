"""
This file turns retrieved public evidence into contact decisions, confidence labels, and outreach drafts.
It also contains the guardrails that keep model output from overruling the system's trust thresholds.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from statistics import mean
from typing import Optional

import anthropic

from ai.signals import (
    build_support_snapshot,
    cap_confidence_for_model,
    compute_confidence_justification,
    compute_confidence_label,
    compute_evidence_strength_score,
    maybe_degrade_for_agreement,
)
from extractor.extract import (
    RawContact,
    clean_display_text,
    detect_conflicts,
    detect_evidence_agreement,
    goal_keywords,
)
from prompts.templates import (
    COMPARE_TOP_PROMPT,
    EVALUATE_CONTACT_PROMPT,
    GENERATE_EMAIL_PROMPT,
    REEVAL_CONTACT_PROMPT,
    RUN_INSIGHT_PROMPT,
)


DEFAULT_MODEL = os.getenv("ANTHROPIC_EVAL_MODEL", "claude-sonnet-4-5")
DRAFTING_MODEL = os.getenv("ANTHROPIC_DRAFT_MODEL", DEFAULT_MODEL)
RECOMMENDATION_THRESHOLD = 6.5
MIN_RECOMMENDATION_EVIDENCE_STRENGTH = 3.5


@dataclass
class ContactEvaluation:
    contact_id: int | None
    contact_name: str
    relevance_score: float
    recommended: bool
    evaluation_status: str
    research_summary: str
    reason_trace: dict[str, str]
    confidence_label: str
    confidence_score: float
    confidence_justification: str
    evidence_strength_score: float
    cited_evidence: list[dict]
    not_recommended_reason: str | None
    insufficient_reason: str | None
    evidence_agreement: dict
    conflicts_detected: bool
    conflict_note: str
    original_score: float
    original_status: str
    second_pass_triggered: bool
    revised_score: float | None
    revised_status: str | None
    revision_reason: str | None
    confidence_changed: bool
    final_status: str
    final_score: float
    tokens_used: int
    model_used: str
    raw_contact: Optional[RawContact] = None


@dataclass
class OutreachDraft:
    contact_name: str
    contact_email: str
    subject: str
    body: str
    model_used: str


def get_client() -> anthropic.Anthropic | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


def parse_json_payload(raw_text: str) -> dict:
    payload = (raw_text or "").strip()
    if payload.startswith("```"):
        parts = payload.split("```")
        if len(parts) >= 2:
            payload = parts[1]
        if payload.startswith("json"):
            payload = payload[4:]
    return json.loads(payload.strip())


def format_evidence(contact: RawContact | None) -> str:
    if contact is None or not contact.evidence:
        return "No corroborating sources were gathered."
    lines = []
    for item in contact.evidence[:3]:
        lines.append(
            f"- {item.get('source_type', 'source')}: {item.get('title', '')} | "
            f"{item.get('source_url', '')} | snippet: {item.get('snippet', '')}"
        )
    return "\n".join(lines)


def format_signature(sender_name: str = "", sender_email: str = "", sender_phone: str = "") -> str:
    lines = [line for line in [sender_name, sender_email, sender_phone] if line]
    return "\n".join(lines) if lines else "[Your Name]"


def _preserve_case(source: str, replacement: str) -> str:
    if not source:
        return replacement
    if source.isupper():
        return replacement.upper()
    if source[0].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


_HER_OBJECT_FOLLOWERS = {
    "about",
    "again",
    "after",
    "around",
    "as",
    "at",
    "before",
    "because",
    "besides",
    "between",
    "by",
    "during",
    "directly",
    "for",
    "from",
    "further",
    "if",
    "in",
    "instead",
    "into",
    "later",
    "near",
    "of",
    "on",
    "over",
    "personally",
    "privately",
    "promptly",
    "shortly",
    "soon",
    "then",
    "thereafter",
    "today",
    "tomorrow",
    "regarding",
    "since",
    "than",
    "that",
    "through",
    "to",
    "toward",
    "under",
    "until",
    "when",
    "where",
    "whether",
    "while",
    "with",
    "without",
}


def _replace_her_token(match: re.Match[str]) -> str:
    text = match.string
    token = match.group(0)
    before = text[: match.start()]
    after = text[match.end() :]

    next_match = re.match(r"\s*([A-Za-z]+)", after)
    next_word = next_match.group(1).lower() if next_match else ""

    if not next_word:
        replacement = "them"
    elif next_word in _HER_OBJECT_FOLLOWERS:
        replacement = "them"
    else:
        replacement = "their"
    return _preserve_case(token, replacement)


def neutralize_gendered_language(text: str) -> str:
    normalized = str(text or "")
    replacements: list[tuple[str, str]] = [
        (r"\b(she|he)'s\b", "they're"),
        (r"\b(she|he)'ll\b", "they'll"),
        (r"\b(she|he)'d\b", "they'd"),
        (r"\b(she|he)\s+is\b", "they are"),
        (r"\b(she|he)\s+was\b", "they were"),
        (r"\b(she|he)\s+has\b", "they have"),
        (r"\b(she|he)\s+does\b", "they do"),
        (r"\b(she|he)\s+studies\b", "they study"),
        (r"\b(she|he)\s+focuses\b", "they focus"),
        (r"\b(she|he)\s+works\b", "they work"),
        (r"\b(she|he)\s+leads\b", "they lead"),
        (r"\b(she|he)\s+directs\b", "they direct"),
        (r"\b(she|he)\s+serves\b", "they serve"),
        (r"\b(she|he)\s+appears\b", "they appear"),
        (r"\b(himself|herself)\b", "themself"),
        (r"\bhers\b", "theirs"),
        (r"\bhim\b", "them"),
        (r"\bhis(?=\s+[A-Za-z])\b", "their"),
        (r"\bhis\b", "theirs"),
        (r"\b(she|he)\b", "they"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(
            pattern,
            lambda match, repl=replacement: _preserve_case(match.group(0), repl),
            normalized,
            flags=re.IGNORECASE,
        )
    normalized = re.sub(r"\bher\b", _replace_her_token, normalized, flags=re.IGNORECASE)
    return normalized


def remove_dash_clause_breaks(text: str) -> str:
    """
    Drafts read more naturally when clause breaks use commas or full sentences
    instead of AI-looking dash asides. This preserves normal hyphenated words.
    """
    cleaned_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        line = re.sub(r"^\s*[—–-]\s+", "", line)
        line = re.sub(r"(?<=\S)\s+[—–-]\s+(?=\S)", ", ", line)
        line = re.sub(r",\s*,+", ", ", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def finalize_draft_text(text: str) -> str:
    return remove_dash_clause_breaks(neutralize_gendered_language(text))


def humanize_draft_language(text: str) -> str:
    normalized = str(text or "")
    replacements: list[tuple[str, str]] = [
        (r"\bclosely aligned with\b", "like a strong fit for"),
        (r"\bat your convenience\b", "when you have time"),
        (r"\bI would be grateful for the chance to learn more\b", "I'd love to learn more"),
        (r"\bI would appreciate\b", "I'd appreciate"),
        (r"\bI would love the opportunity to\b", "I'd love the chance to"),
        (r"\bI am currently looking to\b", "I'm currently looking to"),
        (r"\bI am especially interested in\b", "I'm especially interested in"),
        (r"\bI am\b", "I'm"),
        (r"\bI came across your (?:UAB )?profile while looking for research opportunities\b", "I was looking through UAB research pages"),
        (r"\bcontribute where helpful\b", "get involved and learn"),
        (r"\bI'm very passionate about\b", "I'm really interested in"),
        (r"\bexactly the kind of research I want to do\b", "work I'd be excited to learn from"),
        (r"\bIf there is any opportunity to get involved in your lab or learn more about your work, I would really appreciate the chance to connect\.\b", "If there may be space in your lab, I'd really appreciate the chance to learn more."),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in normalized.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def normalize_email_layout(text: str) -> str:
    normalized = str(text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return ""

    normalized = re.sub(
        r"^((?:Hi|Hello|Dear)\b[^\n,]*,)\s+",
        r"\1\n\n",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"([.!?])\s+((?:Best regards|Regards|Thanks|Thank you|Sincerely|Best),)",
        r"\1\n\n\2",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"((?:Best regards|Regards|Thanks|Thank you|Sincerely|Best),)\s+([^\n])",
        r"\1\n\2",
        normalized,
        flags=re.IGNORECASE,
    )

    lines = [line.strip() for line in normalized.split("\n")]
    cleaned_lines: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line
        if blank and previous_blank:
            continue
        cleaned_lines.append(line)
        previous_blank = blank

    return "\n".join(cleaned_lines).strip()


def finalize_email_output(text: str) -> str:
    return normalize_email_layout(humanize_draft_language(finalize_draft_text(text)))


def normalize_reason_trace(reason_trace: dict | str | None) -> dict[str, str]:
    if isinstance(reason_trace, dict):
        return {
            "match": clean_display_text(reason_trace.get("match", "")),
            "gap": clean_display_text(reason_trace.get("gap", "")),
            "evidence": clean_display_text(reason_trace.get("evidence", "")),
        }
    text = str(reason_trace or "").strip()
    if not text:
        return {"match": "", "gap": "", "evidence": ""}
    parts = {"match": "", "gap": "", "evidence": ""}
    for line in text.splitlines():
        lowered = line.lower()
        if lowered.startswith("match:"):
            parts["match"] = line.split(":", 1)[1].strip()
        elif lowered.startswith("gap:"):
            parts["gap"] = line.split(":", 1)[1].strip()
        elif lowered.startswith("evidence:"):
            parts["evidence"] = line.split(":", 1)[1].strip()
    return {
        "match": clean_display_text(parts["match"]),
        "gap": clean_display_text(parts["gap"]),
        "evidence": clean_display_text(parts["evidence"]),
    }


def first_sentence_text(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return ""
    match = re.match(r".+?[.!?](?=\s|$)", normalized)
    return (match.group(0) if match else normalized).strip()


def compact_text(text: str) -> str:
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split()).strip()


def build_salutation(contact_name: str, title: str = "") -> str:
    cleaned_name = clean_display_text(contact_name, "").strip()
    last_name = cleaned_name.split()[-1] if cleaned_name else ""
    lower_title = str(title or "").lower()
    if last_name and any(token in lower_title for token in ("professor", "chair", "director")):
        return f"Hi Dr. {last_name},"
    if cleaned_name:
        return f"Hi {cleaned_name},"
    return "Hi,"


def parse_student_identity(student_profile: str, sender_name: str = "") -> str:
    profile_text = str(student_profile or "")
    lowered = profile_text.lower()
    grad_match = re.search(r"\b(May|August|December|Spring|Summer|Fall)\s+\d{4}\b", profile_text, flags=re.IGNORECASE)
    grad_text = grad_match.group(0) if grad_match else ""
    school = "UAB" if ("uab" in lowered or "university of alabama at birmingham" in lowered) else ""
    major = "neuroscience honors student" if "neuroscience" in lowered and "honors" in lowered else ""
    if not major and "neuroscience" in lowered:
        major = "neuroscience student"
    name = clean_display_text(sender_name, "")
    if not name:
        for raw_line in profile_text.splitlines():
            candidate = raw_line.strip()
            if candidate:
                name = candidate.title() if candidate.isupper() else candidate
                break

    detail_bits: list[str] = []
    if major:
        detail_bits.append(major)
    if school:
        detail_bits.append(f"at {school}")
    if grad_text:
        detail_bits.append(f"graduating in {grad_text}")

    if name and detail_bits:
        return f"I'm {name}, a {' '.join(detail_bits)}"
    if name:
        return f"I'm {name}"
    if detail_bits:
        return f"I'm a {' '.join(detail_bits)}"
    return "I'm a student"


def infer_student_goal(student_profile: str, user_goal: str) -> str:
    lowered = str(student_profile or "").lower()
    if any(token in lowered for token in ("dental school", "dentist", "dat")):
        return "before applying to dental school"
    if user_goal.strip():
        return f"while building experience in {user_goal.strip()}"
    return "while building research experience"


def extract_research_focus_text(evaluation: ContactEvaluation) -> str:
    source_text = clean_display_text(
        evaluation.raw_contact.research_text if evaluation.raw_contact else "",
        fallback=evaluation.research_summary,
    )
    source_text = compact_text(source_text)
    source_text = re.sub(r"^research interests?:\s*", "", source_text, flags=re.IGNORECASE)
    source_text = first_sentence_text(source_text).rstrip(".")
    if not source_text:
        return ""
    comma_parts = [part.strip() for part in source_text.split(",") if part.strip()]
    if len(comma_parts) >= 2:
        source_text = f"{comma_parts[0]} and {comma_parts[1]}"
    words = source_text.split()
    if len(words) > 12:
        source_text = " ".join(words[:12]).rstrip(",;:")
    return source_text


def detect_undergraduate_signal(evaluation: ContactEvaluation) -> bool:
    signal_text = " ".join(
        [
            evaluation.research_summary,
            reason_trace_text(evaluation.reason_trace),
            evaluation.raw_contact.research_text if evaluation.raw_contact else "",
        ]
    ).lower()
    return any(token in signal_text for token in ("undergraduate", "undergrad", "cure", "mentor", "mentorship"))


def infer_student_strengths(student_profile: str) -> str:
    lowered = str(student_profile or "").lower()
    strengths: list[str] = []

    if any(token in lowered for token in ("data collection", "documentation", "medical records", "excel")):
        strengths.append("data collection and careful documentation")
    if "coursework" in lowered and "lab" in lowered:
        strengths.append("lab exposure through coursework")
    if any(token in lowered for token in ("pediatrics", "patient", "medical terminology")):
        strengths.append("experience handling clinical information carefully")
    if any(token in lowered for token in ("mathnasium", "analytical", "problem-solving", "instructor")):
        strengths.append("strong analytical and communication skills")

    unique_strengths: list[str] = []
    for strength in strengths:
        if strength not in unique_strengths:
            unique_strengths.append(strength)

    if not unique_strengths:
        return "a careful approach to learning, documentation, and follow-through"
    if len(unique_strengths) == 1:
        return unique_strengths[0]
    return f"{unique_strengths[0]}, plus {unique_strengths[1]}"


def apply_recommendation_threshold(
    relevance_score: float,
    evidence_strength_score: float,
    evaluation_status: str,
    recommended: bool,
    reason_trace: dict[str, str],
) -> tuple[str, bool, str | None]:
    # The system should be able to say "not yet" when fit looks decent but the public support is thin.
    if evaluation_status == "insufficient_evidence":
        return evaluation_status, False, None
    if evidence_strength_score < MIN_RECOMMENDATION_EVIDENCE_STRENGTH:
        override_reason = f"Evidence support below recommendation threshold ({MIN_RECOMMENDATION_EVIDENCE_STRENGTH}/10)"
        if not clean_display_text(reason_trace.get("gap", "")):
            reason_trace["gap"] = override_reason
        return "not_recommended", False, override_reason
    if relevance_score < RECOMMENDATION_THRESHOLD:
        override_reason = f"Score below recommendation threshold ({RECOMMENDATION_THRESHOLD})"
        if not clean_display_text(reason_trace.get("gap", "")):
            reason_trace["gap"] = override_reason
        return "not_recommended", False, override_reason
    return evaluation_status, recommended, (reason_trace.get("gap", "") if evaluation_status == "not_recommended" else None)


def is_uncertain_evaluation(evaluation: ContactEvaluation) -> bool:
    return (
        evaluation.confidence_label == "Low Confidence"
        or evaluation.conflicts_detected
        or evaluation.evaluation_status == "insufficient_evidence"
        or 4.5 <= float(evaluation.relevance_score or 0) <= 6.5
    )


def decision_revision_payload(evaluation: ContactEvaluation) -> dict:
    if not evaluation.second_pass_triggered:
        return {"revised": False}
    return {
        "revised": True,
        "original_score": evaluation.original_score,
        "original_status": evaluation.original_status,
        "final_score": evaluation.final_score,
        "final_status": evaluation.final_status,
        "reason": evaluation.revision_reason or "",
    }


def choose_top_chunks(chunks: list[dict], user_goal: str, top_n: int = 3) -> list[dict]:
    keywords = {
        token.lower()
        for token in user_goal.replace("\n", " ").split()
        if len(token.strip(" ,.;:()[]{}")) > 3
    }
    scored = []
    for chunk in chunks:
        text = (chunk.get("chunk_text") or "").lower()
        keyword_hits = sum(1 for keyword in keywords if keyword in text)
        score = keyword_hits * 10 + len(chunk.get("chunk_text") or "")
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored[:top_n]]


def format_supporting_evidence(chunks: list[dict]) -> str:
    if not chunks:
        return "[1] (no_supporting_chunks - n/a): No supporting evidence chunks were retrieved."
    lines = []
    for index, chunk in enumerate(chunks, start=1):
        lines.append(
            f"[{index}] ({chunk.get('source_type', 'source')} - {chunk.get('source_url', 'n/a')}): {chunk.get('chunk_text', '')}"
        )
    return "\n".join(lines)


def summarize_evidence_agreement(agreement: dict) -> str:
    return (
        f"verdict={agreement.get('verdict', 'unknown')}, "
        f"agreement_count={agreement.get('agreement_count', 0)}, "
        f"conflict_count={agreement.get('conflict_count', 0)}, "
        f"impact={agreement.get('impact', '')}"
    )


def shorten_quote(text: str, max_words: int = 20) -> str:
    words = str(text or "").split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).strip() + "..."


def heuristic_keyword_score(contact: RawContact, user_goal: str, student_profile: str = "") -> tuple[float, int]:
    goal_terms = {
        token.strip(" ,.;:()[]{}").lower()
        for token in f"{user_goal} {student_profile}".split()
        if len(token.strip(" ,.;:()[]{}")) > 3
    }
    research_text = (contact.research_text or "").lower()
    hits = sum(1 for term in goal_terms if term and term in research_text)
    return min(9.5, 3.0 + hits * 1.3), hits


def fallback_evaluation(contact: RawContact, user_goal: str, student_profile: str = "") -> ContactEvaluation:
    base_score, hits = heuristic_keyword_score(contact, user_goal, student_profile)
    has_email = bool(contact.email)
    evidence_strength_score = compute_evidence_strength_score(
        research_text=contact.research_text,
        email=contact.email,
        identity_verified=contact.identity_verified,
        evidence=contact.evidence,
        chunks=contact.evidence_chunks or [],
        user_goal=user_goal,
    )
    insufficient_reason = None
    if len(contact.evidence or []) < 2 or len((contact.research_text or "").strip()) < 80 or not contact.identity_verified:
        evaluation_status = "insufficient_evidence"
        recommended = False
        insufficient_reason = "Public evidence is too weak to make a confident recommendation."
    else:
        recommended = base_score >= RECOMMENDATION_THRESHOLD and contact.identity_verified and (has_email or hits >= 4)
        evaluation_status = "recommended" if recommended else "not_recommended"

    research_summary = (
        contact.research_text[:220].strip() if contact.research_text else "Limited public research detail was available."
    )
    if research_summary and not research_summary.endswith("."):
        research_summary += "."
    research_summary = clean_display_text(
        research_summary,
        "Limited public research detail was available for this contact.",
    )

    reason_trace = {
        "match": (
            f"Public research text overlaps with the goal through {hits} matching topic signal{'s' if hits != 1 else ''}."
            if hits
            else "Public research text shows limited explicit overlap with the stated interests."
        ),
        "gap": (
            "No direct email was detected, so outreach is less actionable right now."
            if not has_email
            else "The remaining unknown is whether this lab is actively taking undergraduate researchers."
        ),
        "evidence": (
            f"Identity {'was' if contact.identity_verified else 'was not fully'} corroborated across gathered sources "
            f"with confidence {contact.identity_confidence}, and {'a direct email was found' if has_email else 'no direct email was found'}."
        ),
    }
    reason_trace = normalize_reason_trace(reason_trace)
    evidence_agreement = detect_evidence_agreement(contact.evidence_chunks or [], contact)
    support_snapshot = build_support_snapshot(
        research_text=contact.research_text,
        email=contact.email,
        identity_verified=contact.identity_verified,
        evidence=contact.evidence,
        chunks=contact.evidence_chunks or [],
        user_goal=user_goal,
    )
    confidence_label, confidence_score = compute_confidence_label(
        relevance_score=round(base_score, 2),
        evidence_strength_score=evidence_strength_score,
        identity_verified=contact.identity_verified,
        source_count=support_snapshot["source_count"],
        evaluation_status=evaluation_status,
    )
    conflicts_detected, conflict_note = detect_conflicts(contact.evidence_chunks or [], contact)
    confidence_label, confidence_score = maybe_degrade_for_agreement(confidence_label, evidence_agreement)
    confidence_label, confidence_score = cap_confidence_for_model(confidence_label, "heuristic-fallback")
    confidence_justification = compute_confidence_justification(
        relevance_score=round(base_score, 2),
        confidence_label=confidence_label,
        evidence_strength_score=evidence_strength_score,
        support_snapshot=support_snapshot,
    )
    evaluation_status, recommended, threshold_reason = apply_recommendation_threshold(
        round(base_score, 2),
        evidence_strength_score,
        evaluation_status,
        recommended,
        reason_trace,
    )

    return ContactEvaluation(
        contact_id=None,
        contact_name=contact.name,
        relevance_score=round(base_score, 2),
        recommended=recommended,
        evaluation_status=evaluation_status,
        research_summary=research_summary,
        reason_trace=reason_trace,
        confidence_label=confidence_label,
        confidence_score=confidence_score,
        confidence_justification=confidence_justification,
        evidence_strength_score=evidence_strength_score,
        cited_evidence=[],
        not_recommended_reason=threshold_reason or (reason_trace["gap"] if evaluation_status == "not_recommended" else None),
        insufficient_reason=insufficient_reason,
        evidence_agreement=evidence_agreement,
        conflicts_detected=conflicts_detected,
        conflict_note=conflict_note,
        original_score=round(base_score, 2),
        original_status=evaluation_status,
        second_pass_triggered=False,
        revised_score=None,
        revised_status=None,
        revision_reason=None,
        confidence_changed=False,
        final_status=evaluation_status,
        final_score=round(base_score, 2),
        tokens_used=0,
        model_used="heuristic-fallback",
        raw_contact=contact,
    )


# This stage is deliberately three-way: recommend, not recommend, or refuse to decide when the evidence is too thin.
def evaluate_contact(
    contact: RawContact,
    user_goal: str,
    student_profile: str = "",
    supporting_chunks: list[dict] | None = None,
    contact_id: int | None = None,
) -> ContactEvaluation:
    selected_chunks = choose_top_chunks(supporting_chunks or [], user_goal, top_n=3)
    evidence_strength_score = compute_evidence_strength_score(
        research_text=contact.research_text,
        email=contact.email,
        identity_verified=contact.identity_verified,
        evidence=contact.evidence,
        chunks=supporting_chunks or [],
        user_goal=user_goal,
    )
    conflicts_detected, conflict_note = detect_conflicts(supporting_chunks or [], contact)
    prompt = EVALUATE_CONTACT_PROMPT.format(
        user_goal=user_goal,
        student_profile=student_profile or "No student profile provided.",
        verification_status=f"identity_verified={contact.identity_verified}, identity_confidence={contact.identity_confidence}",
        evidence_sources=format_evidence(contact),
        evidence_chunks=format_supporting_evidence(selected_chunks),
        conflict_note=conflict_note or "No conflicting signals were detected in public sources for this contact.",
        evidence_agreement=summarize_evidence_agreement(detect_evidence_agreement(supporting_chunks or [], contact)),
        name=contact.name,
        title=contact.title,
        email=contact.email or "Not found",
        url=contact.url or contact.source_page or "Not available",
        research_text=contact.research_text or "No research description available.",
    )

    client = get_client()
    if client is None:
        fallback = fallback_evaluation(contact, user_goal, student_profile)
        fallback.contact_id = contact_id
        return fallback

    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        data = parse_json_payload(response.content[0].text)
        relevance_score = float(data.get("relevance_score", 0.0))
        reason_trace = normalize_reason_trace(data.get("reason_trace"))
        returned_status = str(data.get("status", "")).strip().lower()
        insufficient_reason = None
        if returned_status == "insufficient_evidence":
            recommended = False
            evaluation_status = "insufficient_evidence"
            insufficient_reason = str(data.get("reason", "")).strip() or "Public evidence was too weak for a confident recommendation."
        else:
            recommended = bool(data.get("recommended", False))
            evaluation_status = "recommended" if recommended else "not_recommended"
        evidence_agreement = detect_evidence_agreement(supporting_chunks or [], contact)
        # Claude can sound more certain than the evidence deserves, so the hard threshold gets the last word here.
        evaluation_status, recommended, threshold_reason = apply_recommendation_threshold(
            relevance_score,
            evidence_strength_score,
            evaluation_status,
            recommended,
            reason_trace,
        )
        support_snapshot = build_support_snapshot(
            research_text=contact.research_text,
            email=contact.email,
            identity_verified=contact.identity_verified,
            evidence=contact.evidence,
            chunks=supporting_chunks or [],
            cited_evidence=data.get("cited_evidence", []) if isinstance(data.get("cited_evidence", []), list) else [],
            user_goal=user_goal,
        )
        confidence_label, confidence_score = compute_confidence_label(
            relevance_score=relevance_score,
            evidence_strength_score=evidence_strength_score,
            identity_verified=contact.identity_verified,
            source_count=support_snapshot["source_count"],
            evaluation_status=evaluation_status,
        )
        confidence_label, confidence_score = maybe_degrade_for_agreement(confidence_label, evidence_agreement)
        confidence_label, confidence_score = cap_confidence_for_model(confidence_label, DEFAULT_MODEL)
        confidence_justification = compute_confidence_justification(
            relevance_score=relevance_score,
            confidence_label=confidence_label,
            evidence_strength_score=evidence_strength_score,
            support_snapshot=support_snapshot,
        )
        cited_evidence = []
        for item in data.get("cited_evidence", []) if isinstance(data.get("cited_evidence", []), list) else []:
            idx = int(item.get("index", 0) or 0)
            chunk = selected_chunks[idx - 1] if 1 <= idx <= len(selected_chunks) else {}
            cited_evidence.append(
                {
                    "index": idx,
                    "quote": shorten_quote(str(item.get("quote", "")).strip()),
                    "why_relevant": str(item.get("why_relevant", "")).strip(),
                    "source_type": chunk.get("source_type", ""),
                    "source_url": chunk.get("source_url", ""),
                }
            )
        return ContactEvaluation(
            contact_id=contact_id,
            contact_name=contact.name,
            relevance_score=relevance_score,
            recommended=recommended,
            evaluation_status=evaluation_status,
            research_summary=clean_display_text(
                data.get("research_summary", ""),
                (contact.research_text or "Limited public research detail was available.")[:220].strip(),
            ),
            reason_trace=reason_trace,
            confidence_label=confidence_label,
            confidence_score=confidence_score,
            confidence_justification=confidence_justification,
            evidence_strength_score=evidence_strength_score,
            cited_evidence=cited_evidence,
            not_recommended_reason=threshold_reason or (reason_trace.get("gap", "") if evaluation_status == "not_recommended" else None),
            insufficient_reason=insufficient_reason,
            evidence_agreement=evidence_agreement,
            conflicts_detected=conflicts_detected,
            conflict_note=conflict_note,
            original_score=relevance_score,
            original_status=evaluation_status,
            second_pass_triggered=False,
            revised_score=None,
            revised_status=None,
            revision_reason=None,
            confidence_changed=False,
            final_status=evaluation_status,
            final_score=relevance_score,
            tokens_used=(
                int(getattr(response.usage, "input_tokens", 0) or 0) +
                int(getattr(response.usage, "output_tokens", 0) or 0)
            ) if getattr(response, "usage", None) else 0,
            model_used=DEFAULT_MODEL,
            raw_contact=contact,
        )
    except Exception as exc:
        print(f"[ai/evaluate] Falling back for {contact.name}: {exc}")
        fallback = fallback_evaluation(contact, user_goal, student_profile)
        # Persistence depends on contact_id; keep it when falling back so evaluations are still stored and queryable per run.
        fallback.contact_id = contact_id
        return fallback


def reevaluate_contact(
    evaluation: ContactEvaluation,
    user_goal: str,
    student_profile: str = "",
    additional_chunks: list[dict] | None = None,
) -> ContactEvaluation:
    client = get_client()
    if client is None:
        return evaluation

    agreement = evaluation.evidence_agreement or detect_evidence_agreement(additional_chunks or [], evaluation.raw_contact or RawContact(name=evaluation.contact_name))
    prompt = REEVAL_CONTACT_PROMPT.format(
        initial_status=evaluation.original_status,
        initial_score=evaluation.original_score,
        initial_confidence=evaluation.confidence_label,
        initial_reasoning=reason_trace_text(evaluation.reason_trace),
        conflict_note=evaluation.conflict_note or "No conflict note recorded.",
        evidence_agreement=summarize_evidence_agreement(agreement),
        additional_chunks=format_supporting_evidence(choose_top_chunks(additional_chunks or [], user_goal, top_n=4)),
    )
    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}],
        )
        data = parse_json_payload(response.content[0].text)
        revised_status = str(data.get("revised_status", evaluation.evaluation_status)).strip().lower()
        if revised_status not in {"recommended", "not_recommended", "insufficient_evidence"}:
            revised_status = evaluation.evaluation_status
        revised_score = round(float(data.get("revised_score", evaluation.relevance_score) or evaluation.relevance_score), 2)
        revision_reason = str(data.get("revision_reason", "")).strip() or "Second-pass review confirmed the initial decision."
        confidence_changed = bool(data.get("confidence_changed", False))
        if revised_status != "insufficient_evidence":
            if evaluation.evidence_strength_score < MIN_RECOMMENDATION_EVIDENCE_STRENGTH:
                revised_status = "not_recommended"
                revision_reason = f"Evidence support below recommendation threshold ({MIN_RECOMMENDATION_EVIDENCE_STRENGTH}/10)"
            elif revised_score < RECOMMENDATION_THRESHOLD:
                revised_status = "not_recommended"
                revision_reason = f"Score below recommendation threshold ({RECOMMENDATION_THRESHOLD})"

        evaluation.second_pass_triggered = True
        evaluation.revised_score = revised_score
        evaluation.revised_status = revised_status
        evaluation.revision_reason = revision_reason
        evaluation.confidence_changed = confidence_changed
        evaluation.final_status = revised_status
        evaluation.final_score = revised_score
        evaluation.evaluation_status = revised_status
        evaluation.relevance_score = revised_score
        evaluation.recommended = revised_status == "recommended"
        support_snapshot = build_support_snapshot(
            research_text=evaluation.raw_contact.research_text if evaluation.raw_contact else "",
            email=evaluation.raw_contact.email if evaluation.raw_contact else "",
            identity_verified=evaluation.raw_contact.identity_verified if evaluation.raw_contact else False,
            evidence=evaluation.raw_contact.evidence if evaluation.raw_contact else [],
            chunks=evaluation.raw_contact.evidence_chunks if evaluation.raw_contact else [],
            cited_evidence=evaluation.cited_evidence,
            user_goal=user_goal,
        )
        updated_confidence_label, updated_confidence_score = compute_confidence_label(
            relevance_score=revised_score,
            evidence_strength_score=evaluation.evidence_strength_score,
            identity_verified=evaluation.raw_contact.identity_verified if evaluation.raw_contact else False,
            source_count=support_snapshot["source_count"],
            evaluation_status=revised_status,
        )
        updated_confidence_label, updated_confidence_score = maybe_degrade_for_agreement(updated_confidence_label, agreement)
        updated_confidence_label, updated_confidence_score = cap_confidence_for_model(updated_confidence_label, DEFAULT_MODEL)
        evaluation.confidence_label = updated_confidence_label
        evaluation.confidence_score = updated_confidence_score
        evaluation.confidence_justification = compute_confidence_justification(
            relevance_score=revised_score,
            confidence_label=updated_confidence_label,
            evidence_strength_score=evaluation.evidence_strength_score,
            support_snapshot=support_snapshot,
        )
        if revised_status == "not_recommended":
            evaluation.not_recommended_reason = revision_reason
            evaluation.insufficient_reason = None
        elif revised_status == "insufficient_evidence":
            evaluation.insufficient_reason = revision_reason
            evaluation.not_recommended_reason = None
        else:
            evaluation.not_recommended_reason = None
            evaluation.insufficient_reason = None
        evaluation.tokens_used += (
            int(getattr(response.usage, "input_tokens", 0) or 0) +
            int(getattr(response.usage, "output_tokens", 0) or 0)
        ) if getattr(response, "usage", None) else 0
        return evaluation
    except Exception as exc:
        print(f"[ai/reeval] Keeping first-pass decision for {evaluation.contact_name}: {exc}")
        return evaluation


def evaluate_all(
    contacts: list[tuple[int, RawContact, list[dict]]],
    user_goal: str,
    student_profile: str = "",
    min_research_length: int = 40,
    progress_callback=None,
) -> list[ContactEvaluation]:
    results: list[ContactEvaluation] = []

    for contact_id, contact, supporting_chunks in contacts:
        if progress_callback:
            progress_callback("evaluating", f"Evaluating fit for {contact.name}")
        if len((contact.research_text or "").strip()) < min_research_length:
            contact = RawContact(
                name=contact.name,
                title=contact.title,
                role_category=contact.role_category,
                email=contact.email,
                url=contact.url,
                research_text=contact.research_text or "Research description was sparse on the source page.",
                source_page=contact.source_page,
                identity_verified=contact.identity_verified,
                identity_confidence=contact.identity_confidence,
                evidence=contact.evidence,
                evidence_chunks=contact.evidence_chunks,
            )
        print(f"[ai/evaluate] Evaluating: {contact.name}")
        results.append(evaluate_contact(contact, user_goal, student_profile, supporting_chunks, contact_id))

    return results


# Second pass is for the gray area: uncertain contacts where one more retrieval cycle might legitimately change the decision.
def run_second_pass(
    evaluations: list[ContactEvaluation],
    user_goal: str,
    student_profile: str = "",
    progress_callback=None,
) -> list[ContactEvaluation]:
    for evaluation in evaluations:
        if not is_uncertain_evaluation(evaluation):
            continue
        if progress_callback:
            progress_callback("evaluating", f"Re-evaluating {evaluation.contact_name}")
        evaluation = reevaluate_contact(
            evaluation,
            user_goal,
            student_profile,
            evaluation.raw_contact.evidence_chunks if evaluation.raw_contact else [],
        )
    return evaluations


def generate_run_insight(metrics: dict) -> str:
    # Keep the stored system assessment deterministic enough that the same run
    # cannot claim both "strong email discovery" and "0 direct emails".
    def split_sentences(text: str) -> list[str]:
        normalized = " ".join(str(text or "").split()).strip()
        if not normalized:
            return []
        sentences = [part.strip() for part in re.findall(r"[^.!?]+[.!?]?", normalized) if part.strip()]
        return sentences

    def direct_email_sentence() -> str:
        direct_emails = int(metrics.get("direct_emails_found", 0) or 0)
        cleaned_contacts = int(metrics.get("contacts_after_clean", 0) or metrics.get("contacts_discovered", 0) or 0)
        if direct_emails <= 0:
            return "No direct emails were extracted from public sources."
        if cleaned_contacts > 0:
            return f"Direct emails were extracted for {direct_emails} of {cleaned_contacts} cleaned contacts."
        return f"Direct emails were extracted for {direct_emails} contacts."

    def normalize_run_insight(text: str) -> str:
        non_email_sentences = [sentence for sentence in split_sentences(text) if "email" not in sentence.lower()]
        trimmed = non_email_sentences[:2]
        trimmed.append(direct_email_sentence())
        run_insight = " ".join(trimmed[:3]).strip()
        # Keep assessment concise and deterministic for UI readability.
        sentences = run_insight.split(". ")
        run_insight = ". ".join(sentences[:3]).strip()
        if run_insight and not run_insight.endswith("."):
            run_insight += "."
        return run_insight

    def fallback_run_insight() -> str:
        recommended = int(metrics.get("recommended_count", 0) or 0)
        insufficient = int(metrics.get("insufficient_evidence_count", 0) or 0)
        conflicts = int(metrics.get("conflicts_detected_count", 0) or 0)
        limitation = "Confidence was limited most by weak or missing public evidence."
        if conflicts:
            limitation = "Confidence was limited by weak public evidence and conflicting signals between sources."
        return normalize_run_insight(
            f"The run produced {recommended} recommended contacts with {insufficient} marked as insufficient evidence. "
            f"{limitation}"
        )

    client = get_client()
    if client is None:
        return fallback_run_insight()
    prompt = RUN_INSIGHT_PROMPT.format(metrics_json=json.dumps(metrics, indent=2))
    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        return normalize_run_insight(text)
    except Exception as exc:
        print(f"[ai/run_insight] Falling back: {exc}")
        return fallback_run_insight()


def reason_trace_text(reason_trace: dict[str, str]) -> str:
    normalized = normalize_reason_trace(reason_trace)
    return (
        f"Match: {normalized['match']}\n"
        f"Gap: {normalized['gap']}\n"
        f"Evidence: {normalized['evidence']}"
    )


def fallback_email(
    evaluation: ContactEvaluation,
    user_goal: str,
    student_profile: str = "",
    sender_name: str = "",
    sender_email: str = "",
    sender_phone: str = "",
) -> OutreachDraft:
    title = evaluation.raw_contact.title if evaluation.raw_contact else "Researcher"
    research_focus = extract_research_focus_text(evaluation)
    focus_label = " ".join(research_focus.split()[:5]).strip(",.;:") if research_focus else ""
    subject = (
        f"Question about your {focus_label} research"
        if focus_label
        else f"Question about research opportunities in your lab"
    )
    signature = format_signature(sender_name, sender_email, sender_phone)
    intro = build_salutation(evaluation.contact_name, title)
    student_intro = parse_student_identity(student_profile, sender_name).rstrip(".")
    goal_clause = infer_student_goal(student_profile, user_goal)
    strengths = infer_student_strengths(student_profile)
    first_paragraph = f"{student_intro}."
    if research_focus:
        first_paragraph += f" I was especially interested in your work on {research_focus}."
    else:
        first_paragraph += f" I was especially interested in the research direction of your lab."

    second_paragraph = f"I'm hoping to get involved in research {goal_clause}."
    if detect_undergraduate_signal(evaluation):
        second_paragraph += " I also noticed signs that your group supports undergraduate involvement, which made me want to reach out directly."
    else:
        second_paragraph += " Your lab stood out to me as a place where I could learn the research process in a more hands-on way."

    third_paragraph = (
        f"My background includes {strengths}. I'm still early in my research experience, "
        "but I'm eager to learn and contribute in a steady, thoughtful way."
    )
    ask_paragraph = (
        "If you have space for an undergraduate in your lab, I'd really appreciate the chance "
        "to talk briefly or learn how students typically get involved."
    )
    body = (
        f"{intro}\n\n"
        f"{first_paragraph}\n\n"
        f"{second_paragraph}\n\n"
        f"{third_paragraph}\n\n"
        f"{ask_paragraph}\n\n"
        f"Best regards,\n{signature}"
    )
    return OutreachDraft(
        contact_name=evaluation.contact_name,
        contact_email=evaluation.raw_contact.email if evaluation.raw_contact else "",
        subject=finalize_email_output(subject),
        body=finalize_email_output(body),
        model_used="heuristic-fallback",
    )


def generate_email(
    evaluation: ContactEvaluation,
    user_goal: str,
    student_profile: str = "",
    sender_name: str = "",
    sender_email: str = "",
    sender_phone: str = "",
) -> OutreachDraft:
    prompt = GENERATE_EMAIL_PROMPT.format(
        user_goal=user_goal,
        student_profile=student_profile or "No student profile provided.",
        evidence_sources=format_evidence(evaluation.raw_contact),
        sender_signature=format_signature(sender_name, sender_email, sender_phone),
        name=evaluation.contact_name,
        title=evaluation.raw_contact.title if evaluation.raw_contact else "Researcher",
        research_summary=evaluation.research_summary,
        reason_trace=reason_trace_text(evaluation.reason_trace),
    )

    client = get_client()
    if client is None:
        return fallback_email(evaluation, user_goal, student_profile, sender_name, sender_email, sender_phone)

    try:
        response = client.messages.create(
            model=DRAFTING_MODEL,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        data = parse_json_payload(response.content[0].text)
        return OutreachDraft(
            contact_name=evaluation.contact_name,
            contact_email=evaluation.raw_contact.email if evaluation.raw_contact else "",
            subject=finalize_email_output(data.get("subject", "Research Opportunity").strip()),
            body=finalize_email_output(data.get("body", "").strip()),
            model_used=DRAFTING_MODEL,
        )
    except Exception as exc:
        print(f"[ai/draft] Falling back for {evaluation.contact_name}: {exc}")
        return fallback_email(evaluation, user_goal, student_profile, sender_name, sender_email, sender_phone)


def generate_emails_for_top(
    evaluations: list[ContactEvaluation],
    user_goal: str,
    student_profile: str = "",
    sender_name: str = "",
    sender_email: str = "",
    sender_phone: str = "",
    top_n: int = 5,
    progress_callback=None,
) -> list[OutreachDraft]:
    top_contacts = [evaluation for evaluation in evaluations if evaluation.recommended][:top_n]
    drafts: list[OutreachDraft] = []

    for evaluation in top_contacts:
        if progress_callback:
            progress_callback("drafting", f"Drafting email for {evaluation.contact_name}")
        print(f"[ai/draft] Drafting email for: {evaluation.contact_name}")
        drafts.append(
            generate_email(
                evaluation,
                user_goal,
                student_profile,
                sender_name,
                sender_email,
                sender_phone,
            )
        )

    return drafts


def compare_ranked_contacts(contact_a: dict, contact_b: dict) -> str:
    def clamp_single_sentence(text: str, max_words: int = 20) -> str:
        explanation = str(text or "").strip().split("\n", 1)[0].strip()
        if not explanation:
            return ""
        words = explanation.split()
        trimmed = " ".join(words[:max_words]).strip()
        if len(words) > max_words:
            trimmed = trimmed.rstrip(".,;:!?") + "..."
        if trimmed and trimmed[-1] not in ".!?":
            trimmed += "."
        return trimmed

    client = get_client()
    if client is None:
        return clamp_single_sentence(
            f"{contact_a.get('name')} ranked above {contact_b.get('name')} because the system gave them a stronger overall fit "
            f"based on research relevance, evidence quality, and confidence signals."
        )

    prompt = COMPARE_TOP_PROMPT.format(
        score_a=contact_a.get("ranking_score", contact_a.get("final_score", 0)),
        score_b=contact_b.get("ranking_score", contact_b.get("final_score", 0)),
        contact_a=json.dumps(contact_a, ensure_ascii=True),
        contact_b=json.dumps(contact_b, ensure_ascii=True),
    )
    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        return clamp_single_sentence(response.content[0].text)
    except Exception as exc:
        print(f"[ai/compare] Falling back: {exc}")
        return clamp_single_sentence(
            f"{contact_a.get('name')} ranked above {contact_b.get('name')} because their profile showed a stronger match "
            f"to the stated goal and better supporting evidence."
        )
