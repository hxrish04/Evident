"""
This file stitches the whole Evident run together from page load to saved drafts.
It keeps the stages explicit so the system stays debuggable and we can see exactly where a run went sideways.
"""

from __future__ import annotations

import json
import os
from statistics import mean

from ai.evaluate import (
    ContactEvaluation,
    apply_recommendation_threshold,
    evaluate_all,
    generate_emails_for_top,
    generate_run_insight,
    is_uncertain_evaluation,
    normalize_reason_trace,
    run_second_pass,
)
from db import database as db
from extractor.adapters import detect_site_adapter
from extractor.extract import RawContact, clean_contacts, detect_evidence_agreement, parse_faculty_page
from ranking.prefilter import score_contact_deterministically
from ranking.rank import RankedContact, rank_contacts
from research.enrich import chunk_text, enrich_contacts
from scraper.access import RunAccessTracker, check_robots_policy, domain_from_url
from scraper.browser import load_page_result_sync


NON_API_MODELS = {"cache-reuse", "heuristic-fallback"}


class AgentPipeline:
    # Keep request-level state on the pipeline so every stage can share access limits, adapter selection, and progress events.
    def __init__(
        self,
        user_goal: str,
        student_profile: str | None = None,
        sender_name: str | None = None,
        sender_email: str | None = None,
        sender_phone: str | None = None,
        top_n_emails: int = 5,
        max_eval_contacts: int = 8,
        progress_callback=None,
    ):
        self.user_goal = user_goal
        self.student_profile = (student_profile or "").strip()
        self.sender_name = (sender_name or "").strip()
        self.sender_email = (sender_email or "").strip()
        self.sender_phone = (sender_phone or "").strip()
        self.top_n_emails = max(1, top_n_emails)
        self.max_eval_contacts = max(1, max_eval_contacts)
        self.progress_callback = progress_callback
        self.target_domain: str | None = None
        self.access_tracker: RunAccessTracker | None = None
        self.robots_policy: dict | None = None
        self.site_adapter: dict | None = None

    # Runs update the UI live, so this writes the same stage info to both SSE and the database.
    def emit_progress(
        self,
        run_id: int,
        stage: str,
        detail: str,
        *,
        status: str = "running",
        contacts_found: int | None = None,
        evaluations_completed: int | None = None,
        drafts_generated: int | None = None,
        evaluation_mode: str | None = None,
        average_confidence: float | None = None,
        metrics: dict | None = None,
        run_insight: str | None = None,
    ) -> None:
        if self.progress_callback:
            self.progress_callback(stage, detail)
        db.update_run(
            run_id,
            status=status,
            stage=stage,
            stage_detail=detail,
            contacts_found=contacts_found,
            evaluations_completed=evaluations_completed,
            drafts_generated=drafts_generated,
            evaluation_mode=evaluation_mode,
            average_confidence=average_confidence,
            metrics=metrics,
            run_insight=run_insight,
        )

    # Centralize page loading so every fetch goes through the same access policy and error handling.
    def load_page(self, url: str) -> tuple[str, str]:
        result = load_page_result_sync(
            url,
            tracker=self.access_tracker,
            allowed_domain=self.target_domain,
            prefer_browser=False,
            enforce_robots=True,
        )
        self.robots_policy = result.robots_policy or self.robots_policy
        if not result.ok or not result.text or not result.html:
            raise ValueError(f"Failed to load page: {url} ({result.block_reason or result.error or 'unknown'})")
        return result.text, result.html

    # Keep extraction isolated so parsing bugs are easy to pin on the deterministic layer, not later AI steps.
    def extract_raw_contacts(self, text: str, html: str, url: str) -> list[RawContact]:
        return parse_faculty_page(text, html, source_url=url)

    # Cleaning happens before scoring so obvious junk never pollutes downstream ranking or caching.
    def clean_contact_list(self, contacts: list[RawContact]) -> list[RawContact]:
        return clean_contacts(contacts, max_contacts=20)

    # Next-batch runs should skip already-contacted people without changing the original extraction logic.
    def exclude_contacts(
        self,
        contacts: list[RawContact],
        exclusion_list: dict[str, list[str]] | None = None,
    ) -> tuple[list[RawContact], int]:
        if not exclusion_list:
            return contacts, 0
        excluded_names = {name.strip().lower() for name in exclusion_list.get("names", []) if name}
        excluded_emails = {email.strip().lower() for email in exclusion_list.get("emails", []) if email}
        excluded_urls = {url.strip().lower() for url in exclusion_list.get("urls", []) if url}
        filtered: list[RawContact] = []
        skipped = 0
        for contact in contacts:
            if contact.email and contact.email.strip().lower() in excluded_emails:
                skipped += 1
                continue
            if contact.url and contact.url.strip().lower() in excluded_urls:
                skipped += 1
                continue
            if contact.name and contact.name.strip().lower() in excluded_names:
                skipped += 1
                continue
            filtered.append(contact)
        return filtered, skipped

    # Pre-filter runs before Claude so we do not burn API credits on obviously weak contacts.
    def prefilter_contacts(self, contacts: list[RawContact]) -> tuple[list[RawContact], list[tuple[RawContact, float]], list[dict]]:
        # Keep the model-facing shortlist intentionally small so token spend stays predictable.
        scored = [(score_contact_deterministically(contact, self.user_goal), contact) for contact in contacts]
        scored.sort(key=lambda item: item[0], reverse=True)
        shortlisted = [contact for score, contact in scored[: self.max_eval_contacts]]
        shortlisted_ids = {id(contact) for contact in shortlisted}
        filtered_out = [(contact, score) for score, contact in scored if id(contact) not in shortlisted_ids]
        debug = [
            {
                "name": contact.name,
                "pre_score": score,
                "has_email": bool(contact.email),
                "identity_verified": contact.identity_verified,
                "research_length": len((contact.research_text or "").strip()),
            }
            for score, contact in scored
        ]
        return shortlisted, filtered_out, debug

    # Evidence chunks need their own persistence path because later audit views and cache hits depend on them.
    def save_contact_chunks(self, contact_id: int, run_id: int, contact: RawContact) -> None:
        for chunk in contact.evidence_chunks or []:
            db.save_evidence_chunk(
                contact_id=contact_id,
                run_id=run_id,
                source_url=chunk.get("source_url", ""),
                source_type=chunk.get("source_type", ""),
                chunk_text=chunk.get("chunk_text", ""),
            )

    # We still save filtered-out contacts so the run can explain who got dropped and why.
    def save_prefiltered_contacts(self, run_id: int, contacts: list[tuple[RawContact, float]]) -> None:
        for contact, _score in contacts:
            contact_id = db.save_contact(
                run_id=run_id,
                name=contact.name,
                title=contact.title,
                role_category=contact.role_category,
                email=contact.email,
                url=contact.url,
                research_text=contact.research_text,
                source_page=contact.source_page,
                identity_verified=contact.identity_verified,
                identity_confidence=contact.identity_confidence,
                evidence_json=json.dumps(contact.evidence or []),
                status="pre_filtered",
                reason="below pre-filter threshold",
            )
            self.save_contact_chunks(contact_id, run_id, contact)

    # Shortlisted contacts get IDs early so evaluation, chunks, drafts, and cache reuse all line up on the same record.
    def save_shortlisted_contacts(self, run_id: int, contacts: list[RawContact]) -> dict[str, int]:
        id_map: dict[str, int] = {}
        for contact in contacts:
            contact_id = db.save_contact(
                run_id=run_id,
                name=contact.name,
                title=contact.title,
                role_category=contact.role_category,
                email=contact.email,
                url=contact.url,
                research_text=contact.research_text,
                source_page=contact.source_page,
                identity_verified=contact.identity_verified,
                identity_confidence=contact.identity_confidence,
                evidence_json=json.dumps(contact.evidence or []),
                status="active",
                reason=None,
            )
            self.save_contact_chunks(contact_id, run_id, contact)
            id_map[self.contact_identity_key(contact)] = contact_id
        return id_map

    # Cache reuse gets messy fast, so we normalize identity matching in one place.
    def contact_identity_key(self, contact: RawContact) -> str:
        if contact.email:
            return f"email:{contact.email.strip().lower()}"
        if contact.url:
            return f"url:{contact.url.strip().lower()}"
        return f"name:{contact.name.strip().lower()}|research:{(contact.research_text or '').strip().lower()[:160]}"

    # Second-pass retrieval is intentionally narrow: one more shot at evidence for borderline contacts, not a full crawl.
    def deep_retrieve_uncertain_contacts(
        self,
        evaluations: list[ContactEvaluation],
        contact_id_map: dict[str, int],
        run_id: int,
    ) -> tuple[int, int]:
        # Adaptive retrieval stays narrow on purpose: one extra public source per uncertain contact.
        # Demo/interview note: keeping this at 1 shows controlled autonomy, not runaway crawling.
        max_triggers = max(0, int(os.getenv("ADAPTIVE_RETRIEVAL_MAX_CONTACTS", "1") or 1))
        triggered = 0
        chunks_added = 0
        for evaluation in evaluations:
            if max_triggers and triggered >= max_triggers:
                break
            if not is_uncertain_evaluation(evaluation):
                continue
            contact = evaluation.raw_contact
            if contact is None or not contact.url:
                continue
            if len(contact.evidence_chunks or []) >= 3:
                continue

            result = load_page_result_sync(
                contact.url,
                tracker=self.access_tracker,
                allowed_domain=self.target_domain,
                prefer_browser=False,
                enforce_robots=False,
            )
            if not result.ok or not result.text:
                continue

            existing_chunk_texts = {
                str(chunk.get("chunk_text", "")).strip()
                for chunk in (contact.evidence_chunks or [])
                if chunk.get("chunk_text")
            }
            # Deduping by chunk text keeps this "one extra look" useful without inflating evidence counts.
            new_chunks = []
            for chunk in chunk_text(result.text):
                cleaned_chunk = str(chunk or "").strip()
                if not cleaned_chunk or cleaned_chunk in existing_chunk_texts:
                    continue
                new_chunks.append({"source_url": contact.url, "source_type": "deep_retrieval", "chunk_text": cleaned_chunk})
            if not new_chunks:
                continue

            triggered += 1
            chunks_added += len(new_chunks)
            contact.evidence_chunks = (contact.evidence_chunks or []) + new_chunks
            contact_id = evaluation.contact_id or contact_id_map.get(self.contact_identity_key(contact))
            if contact_id is None:
                continue
            for chunk in new_chunks:
                db.save_evidence_chunk(
                    contact_id=contact_id,
                    run_id=run_id,
                    source_url=chunk["source_url"],
                    source_type=chunk["source_type"],
                    chunk_text=chunk["chunk_text"],
                )
        return triggered, chunks_added

    # Evaluation owns cache reuse, live model calls, and second-pass escalation so ranking only sees final decisions.
    def evaluate_contacts(self, contacts: list[RawContact], contact_id_map: dict[str, int], run_id: int) -> list[ContactEvaluation]:
        results: list[ContactEvaluation] = []
        cached_keys: set[str] = set()
        for contact in contacts:
            contact_key = self.contact_identity_key(contact)
            contact_id = contact_id_map[contact_key]
            cached = db.get_cached_evaluation(
                interest_area=self.user_goal,
                name=contact.name,
                email=contact.email,
                url=contact.url,
                research_text=contact.research_text,
            )
            has_new_eval_shape = bool(
                cached
                and (cached["reason_trace"].get("match") or cached["reason_trace"].get("gap") or cached["reason_trace"].get("evidence"))
                and cached.get("confidence_justification") is not None
                and cached.get("evaluation_status")
            )
            if cached and has_new_eval_shape:
                cached_contact = RawContact(
                    name=cached["name"],
                    title=cached["title"],
                    role_category=cached.get("role_category") or contact.role_category,
                    email=cached["email"],
                    url=cached["url"],
                    research_text=cached["research_text"],
                    source_page=cached["source_page"],
                    identity_verified=cached["identity_verified"],
                    identity_confidence=cached["identity_confidence"],
                    evidence=cached["evidence"],
                    evidence_chunks=contact.evidence_chunks,
                )
                cached_relevance = float(cached.get("relevance_score", 0) or 0)
                cached_status = cached.get("final_status") or cached.get("evaluation_status") or ("recommended" if cached.get("recommended") else "not_recommended")
                cached_reason_trace = normalize_reason_trace(cached.get("reason_trace"))
                cached_status, cached_recommended, cached_threshold_reason = apply_recommendation_threshold(
                    cached_relevance,
                    float(cached.get("evidence_strength_score", 0.0) or 0.0),
                    cached_status,
                    bool(cached.get("recommended")),
                    cached_reason_trace,
                )
                cached_original_score = cached.get("original_score")
                if cached_original_score is None or (float(cached_original_score or 0) == 0 and cached_relevance > 0):
                    cached_original_score = cached_relevance
                results.append(
                    ContactEvaluation(
                        contact_id=contact_id,
                        contact_name=cached["name"],
                        relevance_score=cached_relevance,
                        recommended=cached_recommended,
                        evaluation_status=cached_status,
                        research_summary=cached["research_summary"],
                        reason_trace=cached_reason_trace,
                        confidence_label=cached.get("confidence_label", "Moderate Confidence"),
                        confidence_score=float(cached.get("confidence_score", 0.65)),
                        confidence_justification=cached.get("confidence_justification", ""),
                        evidence_strength_score=float(cached.get("evidence_strength_score", 0.0) or 0.0),
                        cited_evidence=cached.get("cited_evidence", []),
                        not_recommended_reason=cached_threshold_reason or cached.get("not_recommended_reason"),
                        insufficient_reason=cached.get("insufficient_reason"),
                        evidence_agreement=cached.get("evidence_agreement") or detect_evidence_agreement(contact.evidence_chunks or [], cached_contact),
                        conflicts_detected=bool(cached.get("conflicts_detected", False)),
                        conflict_note=cached.get("conflict_note", ""),
                        original_score=float(cached_original_score or 0),
                        original_status=cached.get("original_status") or cached_status,
                        second_pass_triggered=bool(cached.get("second_pass_triggered", False)),
                        revised_score=float(cached.get("revised_score", 0) or 0) if cached.get("revised_score") is not None else None,
                        revised_status=cached.get("revised_status"),
                        revision_reason=cached.get("revision_reason"),
                        confidence_changed=bool(cached.get("confidence_changed", False)),
                        final_status=cached_status,
                        final_score=float(cached.get("revised_score", cached_relevance) if cached.get("revised_score") is not None else cached_relevance),
                        tokens_used=int(cached.get("tokens_used", 0) or 0),
                        model_used="cache-reuse",
                        raw_contact=cached_contact,
                    )
                )
                cached_keys.add(contact_key)

        uncached_contacts = [
            (
                contact_id_map[self.contact_identity_key(contact)],
                contact,
                db.get_chunks_for_contact(contact_id_map[self.contact_identity_key(contact)], run_id, top_n=12),
            )
            for contact in contacts
            if self.contact_identity_key(contact) not in cached_keys
        ]
        results.extend(
            evaluate_all(
                uncached_contacts,
                self.user_goal,
                self.student_profile,
                progress_callback=lambda stage, detail: self.emit_progress(run_id, stage, detail),
            )
        )
        return results

    # Enrichment fills the evidence gaps extraction cannot solve from the directory page alone.
    def research_contacts(self, contacts: list[RawContact], run_id: int) -> list[RawContact]:
        return enrich_contacts(
            contacts,
            progress_callback=lambda stage, detail: self.emit_progress(run_id, stage, detail),
            tracker=self.access_tracker,
            allowed_domain=self.target_domain,
        )

    # Ranking is separate from evaluation so we can explain ordering without rewriting the contact decision itself.
    def rank(self, evaluations: list[ContactEvaluation], run_id: int) -> list[RankedContact]:
        self.emit_progress(run_id, "ranking", f"Ranking {len(evaluations)} evaluated contacts", evaluations_completed=len(evaluations))
        return rank_contacts(evaluations)

    # Persist the final scored state here so the UI never has to recompute ranking math on read.
    def save_results(self, run_id: int, ranked: list[RankedContact]) -> dict[str, int]:
        id_map: dict[str, int] = {}
        for ranked_contact in ranked:
            contact = ranked_contact.evaluation.raw_contact
            if contact is None:
                continue
            contact_id = ranked_contact.evaluation.contact_id
            if contact_id is None:
                continue
            db.save_evaluation(
                run_id=run_id,
                contact_id=contact_id,
                relevance_score=ranked_contact.evaluation.relevance_score,
                recommended=ranked_contact.evaluation.recommended,
                evaluation_status=ranked_contact.evaluation.evaluation_status,
                research_summary=ranked_contact.evaluation.research_summary,
                reason_match=ranked_contact.evaluation.reason_trace.get("match", ""),
                reason_gap=ranked_contact.evaluation.reason_trace.get("gap", ""),
                reason_evidence=ranked_contact.evaluation.reason_trace.get("evidence", ""),
                confidence_label=ranked_contact.evaluation.confidence_label,
                confidence_score=ranked_contact.evaluation.confidence_score,
                confidence_justification=ranked_contact.evaluation.confidence_justification,
                evidence_strength_score=ranked_contact.evaluation.evidence_strength_score,
                cited_evidence_json=json.dumps(ranked_contact.evaluation.cited_evidence),
                not_recommended_reason=ranked_contact.evaluation.not_recommended_reason,
                insufficient_reason=ranked_contact.evaluation.insufficient_reason,
                evidence_agreement_json=json.dumps(ranked_contact.evaluation.evidence_agreement),
                conflicts_detected=ranked_contact.evaluation.conflicts_detected,
                conflict_note=ranked_contact.evaluation.conflict_note,
                original_score=ranked_contact.evaluation.original_score,
                original_status=ranked_contact.evaluation.original_status,
                second_pass_triggered=ranked_contact.evaluation.second_pass_triggered,
                revised_score=ranked_contact.evaluation.revised_score,
                revised_status=ranked_contact.evaluation.revised_status,
                revision_reason=ranked_contact.evaluation.revision_reason,
                confidence_changed=ranked_contact.evaluation.confidence_changed,
                final_status=ranked_contact.evaluation.final_status,
                tokens_used=ranked_contact.evaluation.tokens_used,
                model_used=ranked_contact.evaluation.model_used,
                final_score=ranked_contact.evaluation.final_score,
                ranking_score=ranked_contact.final_score,
                score_breakdown=json.dumps(ranked_contact.score_breakdown),
            )
            id_map[contact.name] = contact_id
        return id_map

    # Drafts only happen after ranking because we only want to spend effort on the strongest contacts.
    def generate_drafts(self, run_id: int, ranked: list[RankedContact], id_map: dict[str, int]) -> list[dict]:
        ranked_lookup = {ranked_contact.evaluation.contact_name: ranked_contact for ranked_contact in ranked}
        selected_evaluations = [ranked_contact.evaluation for ranked_contact in ranked if ranked_contact.evaluation.recommended]
        drafts = generate_emails_for_top(
            selected_evaluations,
            self.user_goal,
            self.student_profile,
            self.sender_name,
            self.sender_email,
            self.sender_phone,
            self.top_n_emails,
            progress_callback=lambda stage, detail: self.emit_progress(run_id, stage, detail),
        )
        stored_drafts: list[dict] = []
        for draft in drafts:
            contact_id = id_map.get(draft.contact_name)
            if contact_id is None:
                continue
            ranked_contact = ranked_lookup.get(draft.contact_name)
            db.save_draft(run_id=run_id, contact_id=contact_id, subject=draft.subject, body=draft.body, model_used=draft.model_used)
            stored_drafts.append(
                {
                    "contact_name": draft.contact_name,
                    "contact_email": draft.contact_email,
                    "relevance_score": ranked_contact.evaluation.relevance_score if ranked_contact else None,
                    "recommended": ranked_contact.evaluation.recommended if ranked_contact else False,
                    "confidence_label": ranked_contact.evaluation.confidence_label if ranked_contact else "",
                    "subject": draft.subject,
                    "body": draft.body,
                    "model_used": draft.model_used,
                }
            )
        return stored_drafts

    # Run metrics live here so API responses, insights, and history views all read the same summary.
    def build_run_metrics(
        self,
        *,
        contacts_discovered: int,
        contacts_after_clean: int,
        contacts_pre_filtered: int,
        identities_verified: int,
        direct_emails_found: int,
        recommended_count: int,
        drafts_generated: int,
        evaluations: list[ContactEvaluation],
        drafts: list[dict],
        contacts_excluded_sent: int,
        deep_retrieval_triggered_count: int = 0,
        deep_retrieval_chunks_added: int = 0,
        extraction_audit: dict | None = None,
    ) -> dict:
        avg_relevance = round(mean([item.relevance_score for item in evaluations]), 2) if evaluations else 0.0
        avg_confidence = round(mean([item.confidence_score for item in evaluations]), 2) if evaluations else 0.0
        avg_evidence_strength = round(mean([item.evidence_strength_score for item in evaluations]), 1) if evaluations else 0.0
        evidence_coverage = round(mean([len(item.raw_contact.evidence_chunks or []) for item in evaluations if item.raw_contact]), 1) if evaluations else 0.0
        avg_tokens_per_evaluation = round(mean([item.tokens_used for item in evaluations]), 1) if evaluations else 0.0
        api_calls_made = sum(1 for item in evaluations if item.model_used not in NON_API_MODELS) + sum(1 for draft in drafts if draft["model_used"] not in NON_API_MODELS)
        insufficient_count = sum(1 for item in evaluations if item.evaluation_status == "insufficient_evidence")
        metrics = {
            "contacts_discovered": contacts_discovered,
            "contacts_after_clean": contacts_after_clean,
            "contacts_pre_filtered": contacts_pre_filtered,
            "contacts_evaluated": len(evaluations),
            "identities_verified": identities_verified,
            "direct_emails_found": direct_emails_found,
            "recommended_count": recommended_count,
            "insufficient_evidence_count": insufficient_count,
            "drafts_generated": drafts_generated,
            "avg_relevance_score": avg_relevance,
            "avg_confidence": avg_confidence,
            "avg_evidence_strength": avg_evidence_strength,
            "evidence_coverage": evidence_coverage,
            "confidence_distribution": {
                "high": sum(1 for item in evaluations if item.evaluation_status != "insufficient_evidence" and item.confidence_label == "High Confidence"),
                "moderate": sum(1 for item in evaluations if item.evaluation_status != "insufficient_evidence" and item.confidence_label == "Moderate Confidence"),
                "low": sum(1 for item in evaluations if item.evaluation_status != "insufficient_evidence" and item.confidence_label == "Low Confidence"),
                "insufficient": insufficient_count,
            },
            "conflicts_detected_count": sum(1 for item in evaluations if item.conflicts_detected),
            "avg_tokens_per_evaluation": avg_tokens_per_evaluation,
            "api_calls_made": api_calls_made,
            "contacts_excluded_sent": contacts_excluded_sent,
            "contacts_excluded_outreach": contacts_excluded_sent,
            "model_calls_saved": max(0, contacts_after_clean - len(evaluations)) + contacts_excluded_sent,
            "estimated_minutes_saved": contacts_after_clean * 6,
            "second_pass_count": sum(1 for item in evaluations if item.second_pass_triggered),
            "deep_retrieval_triggered_count": deep_retrieval_triggered_count,
            "deep_retrieval_chunks_added": deep_retrieval_chunks_added,
        }
        if extraction_audit:
            metrics.update(extraction_audit)
        if self.robots_policy:
            metrics["robots_policy"] = self.robots_policy
        if self.site_adapter:
            metrics.update(self.site_adapter)
        if self.access_tracker:
            metrics.update(self.access_tracker.snapshot())
        return metrics

    # This is the top-level orchestrator: one call in, full saved run out.
    def run(self, url: str, run_id: int | None = None, exclusion_list: dict[str, list[str]] | None = None) -> dict:
        db.init_db()
        run_id = run_id or db.create_run(target_url=url, interest_area=self.user_goal)
        self.target_domain = domain_from_url(url)
        self.access_tracker = RunAccessTracker(target_domain=self.target_domain or "")
        self.robots_policy = check_robots_policy(url)
        try:
            if self.robots_policy.get("path_allowed") is False:
                self.access_tracker.note_policy_skip(self.target_domain or "", "robots_disallow")
                metrics = self.build_run_metrics(
                    contacts_discovered=0,
                    contacts_after_clean=0,
                    contacts_pre_filtered=0,
                    identities_verified=0,
                    direct_emails_found=0,
                    recommended_count=0,
                    drafts_generated=0,
                    evaluations=[],
                    drafts=[],
                    contacts_excluded_sent=0,
                    extraction_audit={"compatibility_status": "blocked_or_restricted"},
                )
                run_insight = generate_run_insight(metrics)
                self.emit_progress(run_id, "failed", "Run stopped. robots.txt appears to disallow this path.", status="failed", metrics=metrics, run_insight=run_insight)
                return {"status": "blocked_or_restricted", "run_id": run_id, "contacts": [], "drafts": [], "metrics": metrics, "run_insight": run_insight}

            self.emit_progress(run_id, "loading_page", f"Loading {url}")
            text, html = self.load_page(url)

            self.emit_progress(run_id, "extracting_contacts", "Extracting contacts from faculty page")
            raw_contacts = self.extract_raw_contacts(text, html, url)
            self.emit_progress(run_id, "extracting_contacts", f"Found {len(raw_contacts)} raw contacts", contacts_found=len(raw_contacts))

            cleaned_contacts = self.clean_contact_list(raw_contacts)
            self.site_adapter = detect_site_adapter(text, html, cleaned_contacts)
            extraction_audit = {
                "candidates_with_title": sum(1 for item in cleaned_contacts if item.title and item.title != "Unknown"),
                "candidates_with_profile_url": sum(1 for item in cleaned_contacts if item.url),
                "candidates_with_direct_email": sum(1 for item in cleaned_contacts if item.email),
                "candidates_with_research_text": sum(1 for item in cleaned_contacts if (item.research_text or "").strip()),
                "extraction_loss_rate": round(max(0.0, 1 - (len(cleaned_contacts) / max(1, len(raw_contacts)))), 2),
            }
            extraction_audit["compatibility_status"] = (
                "supported"
                if extraction_audit["candidates_with_profile_url"] >= 3 and extraction_audit["candidates_with_research_text"] >= 3
                else "partially_supported"
            )
            self.emit_progress(run_id, "cleaning_contacts", f"{len(cleaned_contacts)} contacts after cleaning", contacts_found=len(cleaned_contacts))

            cleaned_contacts, sent_skipped = self.exclude_contacts(cleaned_contacts, exclusion_list)
            if not cleaned_contacts:
                metrics = self.build_run_metrics(
                    contacts_discovered=len(raw_contacts),
                    contacts_after_clean=0,
                    contacts_pre_filtered=0,
                    identities_verified=0,
                    direct_emails_found=0,
                    recommended_count=0,
                    drafts_generated=0,
                    evaluations=[],
                    drafts=[],
                    contacts_excluded_sent=sent_skipped,
                    extraction_audit=extraction_audit,
                )
                run_insight = generate_run_insight(metrics)
                self.emit_progress(run_id, "complete", "Run complete. 0 contacts ranked, 0 drafts generated.", status="no_contacts", contacts_found=0, evaluations_completed=0, drafts_generated=0, average_confidence=0.0, metrics=metrics, run_insight=run_insight)
                return {"status": "no_contacts_found", "run_id": run_id, "contacts": [], "drafts": [], "metrics": metrics, "run_insight": run_insight}

            researched_contacts = self.research_contacts(cleaned_contacts, run_id)
            shortlisted, filtered_out, prefilter_debug = self.prefilter_contacts(researched_contacts)
            self.save_prefiltered_contacts(run_id, filtered_out)
            self.emit_progress(run_id, "pre_filtering", f"Top {len(shortlisted)} contacts selected for evaluation", contacts_found=len(cleaned_contacts))
            shortlisted_contact_ids = self.save_shortlisted_contacts(run_id, shortlisted)

            evaluations = self.evaluate_contacts(shortlisted, shortlisted_contact_ids, run_id)
            deep_retrieval_triggered_count, deep_retrieval_chunks_added = self.deep_retrieve_uncertain_contacts(evaluations, shortlisted_contact_ids, run_id)
            evaluations = run_second_pass(evaluations, self.user_goal, self.student_profile, progress_callback=lambda stage, detail: self.emit_progress(run_id, stage, detail))
            if not evaluations:
                metrics = self.build_run_metrics(
                    contacts_discovered=len(raw_contacts),
                    contacts_after_clean=len(cleaned_contacts),
                    contacts_pre_filtered=len(shortlisted),
                    identities_verified=sum(1 for item in researched_contacts if item.identity_verified),
                    direct_emails_found=sum(1 for item in researched_contacts if item.email),
                    recommended_count=0,
                    drafts_generated=0,
                    evaluations=[],
                    drafts=[],
                    contacts_excluded_sent=sent_skipped,
                    deep_retrieval_triggered_count=deep_retrieval_triggered_count,
                    deep_retrieval_chunks_added=deep_retrieval_chunks_added,
                    extraction_audit=extraction_audit,
                )
                run_insight = generate_run_insight(metrics)
                self.emit_progress(run_id, "complete", "Run complete. 0 contacts ranked, 0 drafts generated.", status="no_evaluations", contacts_found=len(cleaned_contacts), evaluations_completed=0, drafts_generated=0, average_confidence=0.0, metrics=metrics, run_insight=run_insight)
                return {"status": "no_evaluations", "run_id": run_id, "contacts": [], "drafts": [], "metrics": metrics, "run_insight": run_insight}

            ranked = self.rank(evaluations, run_id)
            id_map = self.save_results(run_id, ranked)
            drafts = self.generate_drafts(run_id, ranked, id_map)

            recommended_count = sum(1 for item in ranked if item.evaluation.recommended)
            identities_verified = sum(1 for item in researched_contacts if item.identity_verified)
            direct_emails_found = sum(1 for item in researched_contacts if item.email)
            metrics = self.build_run_metrics(
                contacts_discovered=len(raw_contacts),
                contacts_after_clean=len(cleaned_contacts),
                contacts_pre_filtered=len(shortlisted),
                identities_verified=identities_verified,
                direct_emails_found=direct_emails_found,
                recommended_count=recommended_count,
                drafts_generated=len(drafts),
                evaluations=evaluations,
                drafts=drafts,
                contacts_excluded_sent=sent_skipped,
                deep_retrieval_triggered_count=deep_retrieval_triggered_count,
                deep_retrieval_chunks_added=deep_retrieval_chunks_added,
                extraction_audit=extraction_audit,
            )
            average_confidence = metrics["avg_confidence"]
            evaluation_mode = next((item.evaluation.model_used for item in ranked if item.evaluation.model_used not in NON_API_MODELS), ranked[0].evaluation.model_used if ranked else None)
            run_insight = generate_run_insight(metrics)
            self.emit_progress(run_id, "complete", f"Run complete. {len(ranked)} contacts ranked, {len(drafts)} drafts generated.", status="completed", contacts_found=len(cleaned_contacts), evaluations_completed=len(shortlisted), drafts_generated=len(drafts), evaluation_mode=evaluation_mode, average_confidence=average_confidence, metrics=metrics, run_insight=run_insight)

            top_contacts = []
            for ranked_contact in ranked[:10]:
                evaluation = ranked_contact.evaluation
                raw_contact = evaluation.raw_contact
                top_contacts.append(
                    {
                        "name": evaluation.contact_name,
                        "title": raw_contact.title if raw_contact else "",
                        "email": raw_contact.email if raw_contact else "",
                        "role_category": raw_contact.role_category if raw_contact else "unknown",
                        "url": raw_contact.url if raw_contact else "",
                        "identity_verified": raw_contact.identity_verified if raw_contact else False,
                        "identity_confidence": raw_contact.identity_confidence if raw_contact else 0.0,
                        "evidence": raw_contact.evidence if raw_contact else [],
                        "recommended": evaluation.recommended,
                        "evaluation_status": evaluation.evaluation_status,
                        "final_status": evaluation.final_status,
                        "relevance_score": evaluation.relevance_score,
                        "final_score": evaluation.final_score,
                        "ranking_score": ranked_contact.final_score,
                        "research_summary": evaluation.research_summary,
                        "reason_trace": evaluation.reason_trace,
                        "reason_match": evaluation.reason_trace.get("match", ""),
                        "reason_gap": evaluation.reason_trace.get("gap", ""),
                        "reason_evidence": evaluation.reason_trace.get("evidence", ""),
                        "confidence_label": evaluation.confidence_label,
                        "confidence_score": evaluation.confidence_score,
                        "confidence_justification": evaluation.confidence_justification,
                        "evidence_strength_score": evaluation.evidence_strength_score,
                        "evidence_agreement": evaluation.evidence_agreement,
                        "cited_evidence": evaluation.cited_evidence,
                        "not_recommended_reason": evaluation.not_recommended_reason,
                        "insufficient_reason": evaluation.insufficient_reason,
                        "conflicts_detected": evaluation.conflicts_detected,
                        "conflict_note": evaluation.conflict_note,
                        "decision_revision": {"revised": True, "original_score": evaluation.original_score, "original_status": evaluation.original_status, "final_score": evaluation.final_score, "final_status": evaluation.final_status, "reason": evaluation.revision_reason or ""} if evaluation.second_pass_triggered else {"revised": False},
                        "tokens_used": evaluation.tokens_used,
                        "model_used": evaluation.model_used,
                        "score_breakdown": ranked_contact.score_breakdown,
                    }
                )

            return {
                "status": "success",
                "run_id": run_id,
                "contacts_found": len(cleaned_contacts),
                "evaluations_completed": len(shortlisted),
                "drafts_generated": len(drafts),
                "recommended_count": recommended_count,
                "verified_count": identities_verified,
                "email_found_count": direct_emails_found,
                "sent_skipped_count": sent_skipped,
                "prefiltered_count": len(shortlisted),
                "prefilter_debug": prefilter_debug[:10],
                "average_confidence": average_confidence,
                "run_insight": run_insight,
                "top_contacts": top_contacts,
                "drafts": drafts,
                "metrics": metrics,
            }
        except Exception:
            failed_metrics = self.build_run_metrics(
                contacts_discovered=0,
                contacts_after_clean=0,
                contacts_pre_filtered=0,
                identities_verified=0,
                direct_emails_found=0,
                recommended_count=0,
                drafts_generated=0,
                evaluations=[],
                drafts=[],
                contacts_excluded_sent=0,
                extraction_audit={"compatibility_status": "blocked_or_restricted" if self.access_tracker and self.access_tracker.stop_reason else "unsupported"},
            )
            run_insight = generate_run_insight(failed_metrics)
            self.emit_progress(run_id, "failed", "Run failed", status="failed", metrics=failed_metrics, run_insight=run_insight)
            raise
