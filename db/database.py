"""
This file owns the durable state for runs, contacts, evaluations, drafts, and evidence chunks.
The schema is kept explicit here because the app leans on raw SQL and direct hydration instead of hiding behavior behind an ORM.
"""

from __future__ import annotations

import json
import os
import sqlite3
from urllib.parse import urlparse
from typing import Any, Iterable

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:  # pragma: no cover - optional dependency until DATABASE_URL uses postgres
    psycopg2 = None
    RealDictCursor = None

from ai.signals import (
    build_support_snapshot,
    cap_confidence_for_model,
    compute_confidence_justification,
    compute_confidence_label,
    compute_evidence_strength_score,
    evidence_strength_label,
    maybe_degrade_for_agreement,
    support_summary,
)
from extractor.extract import clean_display_text

MIN_RECOMMENDATION_EVIDENCE_STRENGTH = 3.5
HIGH_CONFIDENCE_MIN_EVIDENCE = 5.5


def resolve_db_path() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        parsed = urlparse(database_url)
        if parsed.scheme == "sqlite":
            if parsed.netloc and parsed.path:
                return f"{parsed.netloc}{parsed.path}"
            return parsed.path.lstrip("/") or "outreach_agent.db"
    return os.getenv("DB_PATH", "outreach_agent.db")


DB_PATH = resolve_db_path()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_DIALECT = "postgres" if DATABASE_URL.startswith("postgresql") else "sqlite"


CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_url TEXT NOT NULL,
    interest_area TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    stage_detail TEXT,
    contacts_found INTEGER DEFAULT 0,
    evaluations_completed INTEGER DEFAULT 0,
    drafts_generated INTEGER DEFAULT 0,
    evaluation_mode TEXT,
    average_confidence REAL DEFAULT 0,
    metrics_json TEXT,
    run_insight TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_CONTACTS = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    name TEXT NOT NULL,
    title TEXT,
    role_category TEXT,
    email TEXT,
    url TEXT,
    research_text TEXT,
    source_page TEXT,
    identity_verified INTEGER DEFAULT 0,
    identity_confidence REAL DEFAULT 0,
    evidence_json TEXT,
    status TEXT DEFAULT 'active',
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
"""

CREATE_EVALUATIONS = """
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    contact_id INTEGER NOT NULL,
    relevance_score REAL,
    recommended INTEGER,
    evaluation_status TEXT,
    research_summary TEXT,
    reason_match TEXT,
    reason_gap TEXT,
    reason_evidence TEXT,
    confidence_label TEXT,
    confidence_score REAL DEFAULT 0,
    confidence_justification TEXT,
    evidence_strength_score REAL DEFAULT 0,
    cited_evidence_json TEXT,
    not_recommended_reason TEXT,
    insufficient_reason TEXT,
    evidence_agreement_json TEXT,
    conflicts_detected INTEGER DEFAULT 0,
    conflict_note TEXT,
    original_score REAL,
    original_status TEXT,
    second_pass_triggered INTEGER DEFAULT 0,
    revised_score REAL,
    revised_status TEXT,
    revision_reason TEXT,
    confidence_changed INTEGER DEFAULT 0,
    final_status TEXT,
    tokens_used INTEGER DEFAULT 0,
    model_used TEXT,
    final_score REAL,
    ranking_score REAL,
    score_breakdown TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);
"""

CREATE_EVIDENCE_CHUNKS = """
CREATE TABLE IF NOT EXISTS evidence_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    source_url TEXT,
    source_type TEXT,
    chunk_text TEXT NOT NULL,
    relevance_to_goal REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);
"""

CREATE_DRAFTS = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    contact_id INTEGER NOT NULL,
    subject TEXT,
    body TEXT,
    model_used TEXT,
    status TEXT DEFAULT 'draft',
    sent_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);
"""


def _translate_query(query: str, dialect: str) -> str:
    if dialect != "postgres":
        return query
    translated = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    translated = translated.replace("?", "%s")
    return translated


class DatabaseCursor:
    def __init__(self, cursor: Any, dialect: str, connection: "DatabaseConnection"):
        self.cursor = cursor
        self.dialect = dialect
        self.connection = connection

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        return dict(row) if self.dialect == "postgres" else row

    def fetchall(self):
        rows = self.cursor.fetchall()
        if self.dialect == "postgres":
            return [dict(row) for row in rows]
        return rows

    @property
    def lastrowid(self):
        if self.dialect == "sqlite":
            return self.cursor.lastrowid
        probe = self.connection.raw.cursor(cursor_factory=RealDictCursor)
        try:
            probe.execute("SELECT LASTVAL() AS id")
            row = probe.fetchone()
            return row["id"] if row else None
        finally:
            probe.close()


class DatabaseConnection:
    def __init__(self, raw: Any, dialect: str):
        self.raw = raw
        self.dialect = dialect

    def execute(self, query: str, params: Iterable[Any] | None = None) -> DatabaseCursor:
        cursor = self.raw.cursor(cursor_factory=RealDictCursor) if self.dialect == "postgres" else self.raw.cursor()
        cursor.execute(_translate_query(query, self.dialect), tuple(params or ()))
        return DatabaseCursor(cursor, self.dialect, self)

    def commit(self) -> None:
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()


def get_connection() -> DatabaseConnection:
    if DB_DIALECT == "postgres":
        if psycopg2 is None:
            raise RuntimeError("psycopg2-binary is required for PostgreSQL DATABASE_URL values.")
        raw = psycopg2.connect(DATABASE_URL)
        raw.autocommit = False
        return DatabaseConnection(raw, "postgres")

    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return DatabaseConnection(connection, "sqlite")


def table_columns(connection: DatabaseConnection, table_name: str) -> set[str]:
    if connection.dialect == "postgres":
        rows = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table_name,),
        ).fetchall()
        return {row["column_name"] for row in rows}
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


# This keeps lightweight schema upgrades alive without dragging in a full migration framework for a single-app codebase.
def ensure_column(connection: DatabaseConnection, table_name: str, column_name: str, definition: str) -> None:
    if connection.dialect == "postgres":
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {definition}")
        return
    if column_name not in table_columns(connection, table_name):
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def init_db() -> None:
    connection = get_connection()
    connection.execute(CREATE_RUNS)
    connection.execute(CREATE_CONTACTS)
    connection.execute(CREATE_EVALUATIONS)
    connection.execute(CREATE_DRAFTS)
    connection.execute(CREATE_EVIDENCE_CHUNKS)

    ensure_column(connection, "runs", "stage", "TEXT")
    ensure_column(connection, "runs", "stage_detail", "TEXT")
    ensure_column(connection, "runs", "contacts_found", "INTEGER DEFAULT 0")
    ensure_column(connection, "runs", "evaluations_completed", "INTEGER DEFAULT 0")
    ensure_column(connection, "runs", "drafts_generated", "INTEGER DEFAULT 0")
    ensure_column(connection, "runs", "evaluation_mode", "TEXT")
    ensure_column(connection, "runs", "average_confidence", "REAL DEFAULT 0")
    ensure_column(connection, "runs", "metrics_json", "TEXT")
    ensure_column(connection, "runs", "run_insight", "TEXT")

    ensure_column(connection, "contacts", "role_category", "TEXT")
    ensure_column(connection, "contacts", "identity_verified", "INTEGER DEFAULT 0")
    ensure_column(connection, "contacts", "identity_confidence", "REAL DEFAULT 0")
    ensure_column(connection, "contacts", "evidence_json", "TEXT")
    ensure_column(connection, "contacts", "status", "TEXT DEFAULT 'active'")
    ensure_column(connection, "contacts", "reason", "TEXT")

    ensure_column(connection, "evaluations", "run_id", "INTEGER")
    ensure_column(connection, "evaluations", "model_used", "TEXT")
    ensure_column(connection, "evaluations", "reason_match", "TEXT")
    ensure_column(connection, "evaluations", "reason_gap", "TEXT")
    ensure_column(connection, "evaluations", "reason_evidence", "TEXT")
    ensure_column(connection, "evaluations", "confidence_label", "TEXT")
    ensure_column(connection, "evaluations", "confidence_score", "REAL DEFAULT 0")
    ensure_column(connection, "evaluations", "confidence_justification", "TEXT")
    ensure_column(connection, "evaluations", "evaluation_status", "TEXT")
    ensure_column(connection, "evaluations", "evidence_strength_score", "REAL DEFAULT 0")
    ensure_column(connection, "evaluations", "cited_evidence_json", "TEXT")
    ensure_column(connection, "evaluations", "not_recommended_reason", "TEXT")
    ensure_column(connection, "evaluations", "insufficient_reason", "TEXT")
    ensure_column(connection, "evaluations", "evidence_agreement_json", "TEXT")
    ensure_column(connection, "evaluations", "conflicts_detected", "INTEGER DEFAULT 0")
    ensure_column(connection, "evaluations", "conflict_note", "TEXT")
    ensure_column(connection, "evaluations", "original_score", "REAL")
    ensure_column(connection, "evaluations", "original_status", "TEXT")
    ensure_column(connection, "evaluations", "second_pass_triggered", "INTEGER DEFAULT 0")
    ensure_column(connection, "evaluations", "revised_score", "REAL")
    ensure_column(connection, "evaluations", "revised_status", "TEXT")
    ensure_column(connection, "evaluations", "revision_reason", "TEXT")
    ensure_column(connection, "evaluations", "confidence_changed", "INTEGER DEFAULT 0")
    ensure_column(connection, "evaluations", "final_status", "TEXT")
    ensure_column(connection, "evaluations", "tokens_used", "INTEGER DEFAULT 0")
    ensure_column(connection, "evaluations", "ranking_score", "REAL")

    ensure_column(connection, "drafts", "run_id", "INTEGER")
    ensure_column(connection, "drafts", "model_used", "TEXT")
    ensure_column(connection, "drafts", "status", "TEXT DEFAULT 'draft'")
    ensure_column(connection, "drafts", "sent_at", "TIMESTAMP")

    connection.commit()
    connection.close()
    print(f"[db] Initialized database at: {DB_PATH}")


def create_run(target_url: str, interest_area: str, status: str = "running") -> int:
    connection = get_connection()
    cursor = connection.execute(
        """
        INSERT INTO runs (target_url, interest_area, status, stage, stage_detail, metrics_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (target_url, interest_area, status, "queued", "Waiting to start", json.dumps({})),
    )
    run_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return run_id


def update_run(
    run_id: int,
    *,
    status: str,
    contacts_found: int | None = None,
    evaluations_completed: int | None = None,
    drafts_generated: int | None = None,
    evaluation_mode: str | None = None,
    stage: str | None = None,
    stage_detail: str | None = None,
    average_confidence: float | None = None,
    metrics: dict | None = None,
    run_insight: str | None = None,
) -> None:
    fields: list[str] = ["status = ?"]
    values: list[Any] = [status]

    if contacts_found is not None:
        fields.append("contacts_found = ?")
        values.append(contacts_found)
    if evaluations_completed is not None:
        fields.append("evaluations_completed = ?")
        values.append(evaluations_completed)
    if drafts_generated is not None:
        fields.append("drafts_generated = ?")
        values.append(drafts_generated)
    if evaluation_mode is not None:
        fields.append("evaluation_mode = ?")
        values.append(evaluation_mode)
    if stage is not None:
        fields.append("stage = ?")
        values.append(stage)
    if stage_detail is not None:
        fields.append("stage_detail = ?")
        values.append(stage_detail)
    if average_confidence is not None:
        fields.append("average_confidence = ?")
        values.append(average_confidence)
    if metrics is not None:
        fields.append("metrics_json = ?")
        values.append(json.dumps(metrics))
    if run_insight is not None:
        fields.append("run_insight = ?")
        values.append(run_insight)

    values.append(run_id)
    connection = get_connection()
    connection.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", values)
    connection.commit()
    connection.close()


def _hydrate_run(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["metrics"] = json.loads(item["metrics_json"]) if item.get("metrics_json") else {}
    item["run_insight"] = (item.get("run_insight") or "").strip() or default_run_insight(item["metrics"], item.get("status", "completed"))
    item.pop("metrics_json", None)
    return item


def get_run(run_id: int) -> dict | None:
    connection = get_connection()
    row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    connection.close()
    return _hydrate_run(row)


def get_latest_run_id() -> int | None:
    connection = get_connection()
    row = connection.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    return row["id"] if row else None


def get_latest_completed_run_id() -> int | None:
    connection = get_connection()
    row = connection.execute(
        """
        SELECT id
        FROM runs
        WHERE status = 'completed'
          AND contacts_found > 0
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    connection.close()
    return row["id"] if row else None


def default_run_insight(metrics: dict | None = None, status: str = "completed") -> str:
    metrics = metrics or {}
    discovered = int(metrics.get("contacts_discovered", 0) or 0)
    evaluated = int(metrics.get("contacts_evaluated", 0) or 0)
    recommended = int(metrics.get("recommended_count", 0) or 0)
    insufficient = int(metrics.get("insufficient_evidence_count", 0) or 0)
    emails = int(metrics.get("direct_emails_found", 0) or 0)
    excluded = int(metrics.get("contacts_excluded_outreach", metrics.get("contacts_excluded_sent", 0)) or 0)
    blocked = int(metrics.get("blocked_responses_count", 0) or 0)

    if status == "failed":
        return "The run did not complete cleanly, so the safest takeaway is that site access, extraction quality, or evidence coverage needs another pass."
    if status in {"no_contacts", "no_contacts_found"}:
        return "The run finished without keeping any contacts, which usually means the page structure was too thin or the candidates were removed during cleaning."
    if status == "no_evaluations":
        return "The run collected candidates but none reached a confident evaluation stage, so evidence quality or fit signals were too weak to move forward."

    parts = []
    if evaluated > 0:
        parts.append(f"The run evaluated {evaluated} contacts and recommended {recommended}.")
    elif discovered > 0:
        parts.append(f"The run surfaced {discovered} contacts but did not reach a strong evaluation set.")
    else:
        parts.append("The run completed, but the resulting evidence set was limited.")
    if insufficient:
        parts.append(f"{insufficient} contacts were held back for insufficient evidence.")
    if emails:
        parts.append(f"{emails} direct email paths were found.")
    if excluded:
        parts.append(f"{excluded} previously contacted or skipped contacts were excluded from reuse.")
    if blocked:
        parts.append(f"{blocked} access-boundary signals limited retrieval depth.")
    return " ".join(parts).strip()


def get_recent_run_impact_notes(limit: int = 10) -> list[dict]:
    connection = get_connection()
    rows = connection.execute(
        """
        SELECT id, created_at, target_url, status, evaluation_mode, run_insight, metrics_json
        FROM runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    connection.close()

    notes: list[dict] = []
    for row in rows:
        item = dict(row)
        metrics = json.loads(item["metrics_json"]) if item.get("metrics_json") else {}
        insight = (item.get("run_insight") or "").strip() or default_run_insight(metrics, item.get("status", "completed"))
        notes.append(
            {
                "run_id": item["id"],
                "created_at": item.get("created_at"),
                "target_url": item.get("target_url"),
                "status": item.get("status"),
                "evaluation_mode": item.get("evaluation_mode") or "",
                "run_insight": insight,
                "recommended_count": int(metrics.get("recommended_count", 0) or 0),
                "drafts_generated": int(metrics.get("drafts_generated", 0) or 0),
                "contacts_evaluated": int(metrics.get("contacts_evaluated", 0) or 0),
                "insufficient_evidence_count": int(metrics.get("insufficient_evidence_count", 0) or 0),
            }
        )
    return notes


def save_contact(
    run_id: int,
    name: str,
    title: str,
    role_category: str,
    email: str,
    url: str,
    research_text: str,
    source_page: str,
    identity_verified: bool = False,
    identity_confidence: float = 0.0,
    evidence_json: str = "[]",
    status: str = "active",
    reason: str | None = None,
) -> int:
    connection = get_connection()
    cursor = connection.execute(
        """
        INSERT INTO contacts (
            run_id, name, title, role_category, email, url, research_text, source_page,
            identity_verified, identity_confidence, evidence_json, status, reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            name,
            title,
            role_category,
            email,
            url,
            research_text,
            source_page,
            int(identity_verified),
            identity_confidence,
            evidence_json,
            status,
            reason,
        ),
    )
    contact_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return contact_id


def save_evaluation(
    run_id: int,
    contact_id: int,
    relevance_score: float,
    recommended: bool,
    evaluation_status: str,
    research_summary: str,
    reason_match: str,
    reason_gap: str,
    reason_evidence: str,
    confidence_label: str,
    confidence_score: float,
    confidence_justification: str,
    evidence_strength_score: float,
    cited_evidence_json: str,
    not_recommended_reason: str | None,
    insufficient_reason: str | None,
    evidence_agreement_json: str,
    conflicts_detected: bool,
    conflict_note: str,
    original_score: float,
    original_status: str,
    second_pass_triggered: bool,
    revised_score: float | None,
    revised_status: str | None,
    revision_reason: str | None,
    confidence_changed: bool,
    final_status: str,
    tokens_used: int,
    model_used: str,
    final_score: float,
    ranking_score: float,
    score_breakdown: str,
) -> int:
    connection = get_connection()
    cursor = connection.execute(
        """
        INSERT INTO evaluations (
            run_id, contact_id, relevance_score, recommended, evaluation_status, research_summary,
            reason_match, reason_gap, reason_evidence, confidence_label, confidence_score,
            confidence_justification, evidence_strength_score, cited_evidence_json, not_recommended_reason,
            insufficient_reason, evidence_agreement_json, conflicts_detected, conflict_note,
            original_score, original_status, second_pass_triggered, revised_score, revised_status,
            revision_reason, confidence_changed, final_status, tokens_used, model_used,
            final_score, ranking_score, score_breakdown
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            contact_id,
            relevance_score,
            int(recommended),
            evaluation_status,
            research_summary,
            reason_match,
            reason_gap,
            reason_evidence,
            confidence_label,
            confidence_score,
            confidence_justification,
            evidence_strength_score,
            cited_evidence_json,
            not_recommended_reason,
            insufficient_reason,
            evidence_agreement_json,
            int(conflicts_detected),
            conflict_note,
            original_score,
            original_status,
            int(second_pass_triggered),
            revised_score,
            revised_status,
            revision_reason,
            int(confidence_changed),
            final_status,
            tokens_used,
            model_used,
            final_score,
            ranking_score,
            score_breakdown,
        ),
    )
    evaluation_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return evaluation_id


def save_evidence_chunk(
    contact_id: int,
    run_id: int,
    source_url: str,
    source_type: str,
    chunk_text: str,
) -> int:
    connection = get_connection()
    cursor = connection.execute(
        """
        INSERT INTO evidence_chunks (contact_id, run_id, source_url, source_type, chunk_text)
        VALUES (?, ?, ?, ?, ?)
        """,
        (contact_id, run_id, source_url, source_type, chunk_text),
    )
    chunk_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return chunk_id


def get_chunks_for_contact(contact_id: int, run_id: int, top_n: int = 3) -> list[dict]:
    connection = get_connection()
    rows = connection.execute(
        """
        SELECT id, contact_id, run_id, source_url, source_type, chunk_text, relevance_to_goal, created_at
        FROM evidence_chunks
        WHERE contact_id = ? AND run_id = ?
        ORDER BY relevance_to_goal DESC, LENGTH(chunk_text) DESC, id ASC
        LIMIT ?
        """,
        (contact_id, run_id, top_n),
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def save_draft(
    run_id: int,
    contact_id: int,
    subject: str,
    body: str,
    model_used: str,
    status: str = "draft",
) -> int:
    connection = get_connection()
    cursor = connection.execute(
        """
        INSERT INTO drafts (run_id, contact_id, subject, body, model_used, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, contact_id, subject, body, model_used, status),
    )
    draft_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return draft_id


def _reason_trace_dict(item: dict) -> dict:
    if not any(item.get(key) for key in ("reason_match", "reason_gap", "reason_evidence")) and item.get("legacy_reason_trace"):
        text = str(item.get("legacy_reason_trace") or "").strip()
        for line in text.splitlines():
            lowered = line.lower()
            if lowered.startswith("match:"):
                item["reason_match"] = line.split(":", 1)[1].strip()
            elif lowered.startswith("gap:"):
                item["reason_gap"] = line.split(":", 1)[1].strip()
            elif lowered.startswith("evidence:"):
                item["reason_evidence"] = line.split(":", 1)[1].strip()
    return {
        "match": clean_display_text(item.get("reason_match", "")),
        "gap": clean_display_text(item.get("reason_gap", "")),
        "evidence": clean_display_text(item.get("reason_evidence", "")),
    }


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_score_fields(item: dict) -> dict:
    breakdown = item.get("score_breakdown")
    breakdown_final = _coerce_float(breakdown.get("final_score")) if isinstance(breakdown, dict) else 0.0
    relevance_score = _coerce_float(item.get("relevance_score"))
    revised_score = _coerce_float(item.get("revised_score"))
    final_score = _coerce_float(item.get("final_score"))
    ranking_score = _coerce_float(item.get("ranking_score"))

    if ranking_score <= 0 and breakdown_final > 0:
        ranking_score = breakdown_final
    if final_score <= 0 and relevance_score > 0:
        final_score = revised_score or relevance_score
    if final_score > 10 and relevance_score > 0:
        if ranking_score <= 0:
            ranking_score = final_score
        final_score = revised_score or relevance_score
    if ranking_score <= 0 and breakdown_final > final_score:
        ranking_score = breakdown_final

    item["final_score"] = round(final_score, 2) if final_score > 0 else 0.0
    item["ranking_score"] = round(ranking_score, 2) if ranking_score > 0 else 0.0
    return item


def _hydrate_support_fields(item: dict) -> dict:
    chunk_items: list[dict] = []
    contact_id = item.get("contact_id") or item.get("id")
    run_id = item.get("run_id")
    if contact_id and run_id:
        try:
            chunk_items = get_chunks_for_contact(int(contact_id), int(run_id), top_n=5)
        except Exception:
            chunk_items = []
    support_snapshot = build_support_snapshot(
        research_text=item.get("research_text", ""),
        email=item.get("email") or item.get("contact_email", ""),
        identity_verified=bool(item.get("identity_verified")),
        evidence=item.get("evidence", []),
        chunks=chunk_items,
        cited_evidence=item.get("cited_evidence", []),
        user_goal="",
    )
    stored_evidence_strength = _coerce_float(item.get("evidence_strength_score"))
    recomputed_evidence_strength = compute_evidence_strength_score(
        research_text=item.get("research_text", ""),
        email=item.get("email") or item.get("contact_email", ""),
        identity_verified=bool(item.get("identity_verified")),
        evidence=item.get("evidence", []),
        chunks=chunk_items,
        cited_evidence=item.get("cited_evidence", []),
        user_goal="",
    )
    if stored_evidence_strength <= 0 and recomputed_evidence_strength > 0:
        item["evidence_strength_score"] = recomputed_evidence_strength
    else:
        item["evidence_strength_score"] = round(max(stored_evidence_strength, recomputed_evidence_strength), 1)
    item["evidence_strength_label"] = evidence_strength_label(item["evidence_strength_score"])
    item["support_summary"] = support_summary(
        evidence_strength_score=item["evidence_strength_score"],
        support_snapshot=support_snapshot,
    )
    return support_snapshot


def _default_evaluation_status(item: dict) -> str:
    if item.get("final_status"):
        return item["final_status"]
    if item.get("evaluation_status"):
        return item["evaluation_status"]
    if bool(item.get("recommended")):
        return "recommended"
    return "not_recommended"


def _apply_trust_safeguards(item: dict) -> dict:
    status = str(item.get("evaluation_status") or "").strip().lower()
    evidence_strength = _coerce_float(item.get("evidence_strength_score"))
    fit_score = _effective_fit_score(item)
    ranking_score = _coerce_float(item.get("ranking_score"))
    trust_notes: list[str] = []

    if status == "insufficient_evidence":
        item["recommended"] = False
        if not item.get("insufficient_reason"):
            item["insufficient_reason"] = "Public evidence was too weak for a confident recommendation."

    if status == "recommended" and evidence_strength < MIN_RECOMMENDATION_EVIDENCE_STRENGTH:
        item["evaluation_status"] = "not_recommended"
        item["final_status"] = "not_recommended"
        item["recommended"] = False
        if not item.get("not_recommended_reason"):
            item["not_recommended_reason"] = (
                f"Support level was below the recommendation floor ({MIN_RECOMMENDATION_EVIDENCE_STRENGTH}/10)."
            )
        trust_notes.append("Recommendation was downgraded because support level was below the required floor.")

    confidence_label = str(item.get("confidence_label") or "").strip()
    if confidence_label == "High Confidence" and evidence_strength < HIGH_CONFIDENCE_MIN_EVIDENCE:
        item["confidence_label"] = "Moderate Confidence"
        trust_notes.append("Confidence was capped to moderate because support depth was mixed.")
    if item.get("confidence_label") == "Moderate Confidence" and evidence_strength < 3.0:
        item["confidence_label"] = "Low Confidence"
        trust_notes.append("Confidence was lowered because support depth remained thin.")

    if ranking_score >= fit_score + 3.0 and evidence_strength < 3.5:
        trust_notes.append("Rank score was lifted mostly by deterministic outreach-readiness boosts, not fit alone.")

    if trust_notes:
        existing = str(item.get("confidence_justification") or "").strip()
        note_text = " ".join(trust_notes)
        item["confidence_justification"] = f"{existing} {note_text}".strip()
    return item


def _decision_revision_dict(item: dict) -> dict:
    if not bool(item.get("second_pass_triggered")):
        return {"revised": False}
    return {
        "revised": True,
        "original_score": float(item.get("original_score") or item.get("relevance_score") or 0),
        "original_status": item.get("original_status") or item.get("evaluation_status") or "",
        "final_score": float(item.get("final_score") or item.get("revised_score") or item.get("relevance_score") or 0),
        "final_status": item.get("final_status") or item.get("evaluation_status") or "",
        "reason": item.get("revision_reason") or "",
    }


def _effective_fit_score(item: dict) -> float:
    return float(item.get("final_score") or item.get("revised_score") or item.get("relevance_score") or 0)


def _hydrate_evaluation_item(item: dict) -> dict:
    item["recommended"] = bool(item.get("recommended"))
    item["identity_verified"] = bool(item.get("identity_verified"))
    item["conflicts_detected"] = bool(item.get("conflicts_detected"))
    item["second_pass_triggered"] = bool(item.get("second_pass_triggered"))
    item["confidence_changed"] = bool(item.get("confidence_changed"))
    item["evidence"] = json.loads(item["evidence_json"]) if item.get("evidence_json") else []
    item.pop("evidence_json", None)
    item["score_breakdown"] = json.loads(item["score_breakdown"]) if item.get("score_breakdown") else {}
    item["reason_trace"] = _reason_trace_dict(item)
    item["cited_evidence"] = json.loads(item["cited_evidence_json"]) if item.get("cited_evidence_json") else []
    item["evidence_agreement"] = json.loads(item["evidence_agreement_json"]) if item.get("evidence_agreement_json") else {}
    item["evaluation_status"] = _default_evaluation_status(item)
    item["research_summary"] = clean_display_text(
        item.get("research_summary", ""),
        (item.get("research_text", "") or "Limited public research detail was available for this contact.")[:220],
    )

    if item.get("original_score") in (None, 0, 0.0) and float(item.get("relevance_score") or 0) > 0:
        item["original_score"] = float(item.get("relevance_score") or 0)
    if not item.get("original_status"):
        item["original_status"] = item["evaluation_status"]
    if not item.get("final_status"):
        item["final_status"] = item["evaluation_status"]
    if item.get("final_score") in (None, 0, 0.0) and float(item.get("relevance_score") or 0) > 0:
        item["final_score"] = float(item.get("relevance_score") or 0)

    item = _normalize_score_fields(item)
    support_snapshot = _hydrate_support_fields(item)
    item["decision_revision"] = _decision_revision_dict(item)
    normalized_confidence_label, normalized_confidence_score = compute_confidence_label(
        relevance_score=_effective_fit_score(item),
        evidence_strength_score=item["evidence_strength_score"],
        identity_verified=item["identity_verified"],
        source_count=support_snapshot["source_count"],
        evaluation_status=item["evaluation_status"],
    )
    normalized_confidence_label, normalized_confidence_score = maybe_degrade_for_agreement(
        normalized_confidence_label,
        item["evidence_agreement"],
    )
    normalized_confidence_label, normalized_confidence_score = cap_confidence_for_model(
        normalized_confidence_label,
        item.get("model_used", ""),
    )
    item["confidence_label"] = normalized_confidence_label
    item["confidence_score"] = normalized_confidence_score
    item["confidence_justification"] = compute_confidence_justification(
        relevance_score=_effective_fit_score(item),
        confidence_label=item["confidence_label"],
        evidence_strength_score=item["evidence_strength_score"],
        support_snapshot=support_snapshot,
    )
    if not item.get("not_recommended_reason") and item["evaluation_status"] == "not_recommended":
        item["not_recommended_reason"] = item["reason_trace"].get("gap", "")
    if item["evaluation_status"] == "insufficient_evidence" and not item.get("insufficient_reason"):
        item["insufficient_reason"] = "Public evidence was too weak for a confident recommendation."
    item = _apply_trust_safeguards(item)
    return item


def check_db_health() -> str:
    try:
        connection = get_connection()
        connection.execute("SELECT 1").fetchone()
        connection.close()
        return "ok"
    except Exception:
        return "error"


def get_ranked_contacts(run_id: int | None = None) -> list[dict]:
    run_id = run_id or get_latest_run_id()
    if run_id is None:
        return []

    connection = get_connection()
    rows = connection.execute(
        """
        SELECT
            c.id,
            c.run_id,
            c.name,
            c.title,
            c.role_category,
            c.email,
            c.url,
            c.research_text,
            c.source_page,
            c.identity_verified,
            c.identity_confidence,
            c.evidence_json,
            c.status,
            c.reason,
            e.relevance_score,
            e.recommended,
            e.evaluation_status,
            e.research_summary,
            e.reason_match,
            e.reason_gap,
            e.reason_evidence,
            e.confidence_label,
            e.confidence_score,
            e.confidence_justification,
            e.evidence_strength_score,
            e.cited_evidence_json,
            e.not_recommended_reason,
            e.insufficient_reason,
            e.evidence_agreement_json,
            e.conflicts_detected,
            e.conflict_note,
            e.original_score,
            e.original_status,
            e.second_pass_triggered,
            e.revised_score,
            e.revised_status,
            e.revision_reason,
            e.confidence_changed,
            e.final_status,
            e.tokens_used,
            e.model_used,
            e.final_score,
            e.ranking_score,
            e.score_breakdown
        FROM contacts c
        JOIN evaluations e ON e.contact_id = c.id
        WHERE e.run_id = ?
        ORDER BY COALESCE(e.ranking_score, e.final_score) DESC, e.final_score DESC
        """,
        (run_id,),
    ).fetchall()
    connection.close()

    contacts: list[dict] = []
    for row in rows:
        contacts.append(_hydrate_evaluation_item(dict(row)))
    return contacts


def get_all_drafts(run_id: int | None = None) -> list[dict]:
    run_id = run_id or get_latest_run_id()
    if run_id is None:
        return []

    connection = get_connection()
    rows = connection.execute(
        """
        SELECT
            d.id,
            d.run_id,
            d.subject,
            d.body,
            d.model_used,
            d.status,
            d.sent_at,
            d.created_at,
            d.contact_id,
            c.name AS contact_name,
            c.title AS contact_title,
            c.email AS contact_email,
            c.identity_verified,
            c.research_text,
            c.evidence_json,
            e.relevance_score,
            e.final_score,
            e.final_status,
            e.recommended,
            e.confidence_label,
            e.confidence_justification,
            e.evidence_strength_score,
            e.research_summary
        FROM drafts d
        JOIN contacts c ON c.id = d.contact_id
        LEFT JOIN evaluations e ON e.contact_id = c.id AND e.run_id = d.run_id
        WHERE d.run_id = ?
        ORDER BY d.id ASC
        """,
        (run_id,),
    ).fetchall()
    connection.close()

    drafts = [dict(row) for row in rows]
    for draft in drafts:
        draft["recommended"] = bool(draft.get("recommended"))
        draft["identity_verified"] = bool(draft.get("identity_verified"))
        draft["evidence"] = json.loads(draft["evidence_json"]) if draft.get("evidence_json") else []
        draft.pop("evidence_json", None)
        draft = _normalize_score_fields(draft)
        _hydrate_support_fields(draft)
    return drafts


def mark_draft_status(draft_id: int, status: str) -> dict | None:
    connection = get_connection()
    connection.execute(
        """
        UPDATE drafts
        SET status = ?,
            sent_at = CASE WHEN ? = 'sent' THEN CURRENT_TIMESTAMP ELSE NULL END
        WHERE id = ?
        """,
        (status, status, draft_id),
    )
    connection.commit()
    row = connection.execute(
        """
        SELECT
            d.id,
            d.run_id,
            d.contact_id,
            d.subject,
            d.body,
            d.model_used,
            d.status,
            d.sent_at,
            d.created_at,
            c.name AS contact_name,
            c.title AS contact_title,
            c.email AS contact_email,
            c.identity_verified,
            c.research_text,
            c.evidence_json,
            e.relevance_score,
            e.final_score,
            e.final_status,
            e.recommended,
            e.confidence_label,
            e.confidence_justification,
            e.evidence_strength_score,
            e.research_summary
        FROM drafts d
        JOIN contacts c ON c.id = d.contact_id
        LEFT JOIN evaluations e ON e.contact_id = c.id AND e.run_id = d.run_id
        WHERE d.id = ?
        """,
        (draft_id,),
    ).fetchone()
    connection.close()
    if row is None:
        return None
    item = dict(row)
    item["recommended"] = bool(item.get("recommended"))
    item["identity_verified"] = bool(item.get("identity_verified"))
    item["evidence"] = json.loads(item["evidence_json"]) if item.get("evidence_json") else []
    item.pop("evidence_json", None)
    item = _normalize_score_fields(item)
    _hydrate_support_fields(item)
    return item


def mark_draft_sent(draft_id: int) -> dict | None:
    return mark_draft_status(draft_id, "sent")


def get_outreach_contact_exclusions(statuses: Iterable[str] = ("sent", "skipped")) -> dict[str, list[str]]:
    placeholders = ", ".join("?" for _ in statuses)
    connection = get_connection()
    rows = connection.execute(
        f"""
        SELECT DISTINCT c.name, c.email, c.url
        FROM drafts d
        JOIN contacts c ON c.id = d.contact_id
        WHERE d.status IN ({placeholders})
        """,
        tuple(statuses),
    ).fetchall()
    connection.close()
    names = sorted({row["name"].strip().lower() for row in rows if row["name"]})
    emails = sorted({row["email"].strip().lower() for row in rows if row["email"]})
    urls = sorted({row["url"].strip().lower() for row in rows if row["url"]})
    return {"names": names, "emails": emails, "urls": urls}


def get_sent_contact_exclusions() -> dict[str, list[str]]:
    return get_outreach_contact_exclusions(("sent",))


def get_outreach_history(limit: int = 100, status_filter: str | None = None, search: str | None = None) -> list[dict]:
    connection = get_connection()
    query = """
        SELECT
            d.id,
            d.run_id,
            d.contact_id,
            d.subject,
            d.status,
            d.sent_at,
            d.created_at,
            c.name AS contact_name,
            c.title AS contact_title,
            c.role_category,
            c.email AS contact_email,
            c.url AS contact_url,
            c.identity_verified,
            c.identity_confidence,
            c.evidence_json,
            e.relevance_score,
            e.final_score,
            e.final_status,
            e.evidence_strength_score,
            e.confidence_label
            ,e.confidence_justification
        FROM drafts d
        JOIN contacts c ON c.id = d.contact_id
        LEFT JOIN evaluations e ON e.contact_id = c.id AND e.run_id = d.run_id
        WHERE d.status != 'draft'
    """
    params: list[Any] = []
    if status_filter:
        query += " AND d.status = ?"
        params.append(status_filter)
    if search:
        query += " AND (LOWER(c.name) LIKE ? OR LOWER(COALESCE(c.email, '')) LIKE ? OR LOWER(COALESCE(c.title, '')) LIKE ?)"
        like = f"%{search.strip().lower()}%"
        params.extend([like, like, like])
    query += " ORDER BY COALESCE(d.sent_at, d.created_at) DESC, d.id DESC LIMIT ?"
    params.append(limit)
    rows = connection.execute(query, params).fetchall()
    connection.close()

    history: list[dict] = []
    for row in rows:
        item = dict(row)
        item["identity_verified"] = bool(item.get("identity_verified"))
        evidence = json.loads(item["evidence_json"]) if item.get("evidence_json") else []
        item["evidence_count"] = len(evidence)
        item["evidence"] = evidence
        item = _normalize_score_fields(item)
        _hydrate_support_fields(item)
        item.pop("evidence_json", None)
        history.append(item)
    return history


def get_resume_metrics() -> dict:
    connection = get_connection()
    run_rows = connection.execute(
        """
        SELECT metrics_json
        FROM runs
        WHERE status = 'completed'
        """
    ).fetchall()
    sent = connection.execute(
        """
        SELECT
            COUNT(CASE WHEN status = 'sent' THEN 1 END) AS sent_count,
            COUNT(CASE WHEN status = 'skipped' THEN 1 END) AS skipped_count
        FROM drafts
        """
    ).fetchone()
    recent_runs = connection.execute(
        """
        SELECT id, created_at, target_url, status, evaluation_mode, run_insight, metrics_json
        FROM runs
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    connection.close()

    totals = {
        "total_runs": len(run_rows),
        "contacts_discovered": 0,
        "contacts_after_clean": 0,
        "contacts_pre_filtered": 0,
        "contacts_evaluated": 0,
        "identities_verified": 0,
        "direct_emails_found": 0,
        "recommended_count": 0,
        "insufficient_evidence_count": 0,
        "drafts_generated": 0,
        "avg_relevance_score": 0.0,
        "avg_confidence": 0.0,
        "avg_evidence_strength": 0.0,
        "api_calls_made": 0,
        "contacts_excluded_sent": 0,
        "conflicts_detected_count": 0,
        "second_pass_count": 0,
        "deep_retrieval_triggered_count": 0,
        "deep_retrieval_chunks_added": 0,
        "avg_tokens_per_evaluation": 0.0,
        "evidence_coverage": 0.0,
        "confidence_distribution": {"high": 0, "moderate": 0, "low": 0, "insufficient": 0},
        "requests_attempted": 0,
        "blocked_responses_count": 0,
        "throttled_delays_count": 0,
        "domains_skipped_due_policy_or_block": 0,
        "requests_blocked_by_policy": 0,
    }
    avg_rel_values: list[float] = []
    avg_conf_values: list[float] = []
    avg_evidence_strength_values: list[float] = []
    avg_tokens_values: list[float] = []
    evidence_coverage_values: list[float] = []
    for row in run_rows:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        for key in (
            "contacts_discovered",
            "contacts_after_clean",
            "contacts_pre_filtered",
            "contacts_evaluated",
            "identities_verified",
            "direct_emails_found",
            "recommended_count",
            "insufficient_evidence_count",
            "drafts_generated",
            "api_calls_made",
            "contacts_excluded_sent",
            "conflicts_detected_count",
            "second_pass_count",
            "deep_retrieval_triggered_count",
            "deep_retrieval_chunks_added",
            "requests_attempted",
            "blocked_responses_count",
            "throttled_delays_count",
            "domains_skipped_due_policy_or_block",
            "requests_blocked_by_policy",
        ):
            totals[key] += int(metrics.get(key, 0) or 0)
        if metrics.get("avg_relevance_score") is not None:
            avg_rel_values.append(float(metrics.get("avg_relevance_score", 0) or 0))
        if metrics.get("avg_confidence") is not None:
            avg_conf_values.append(float(metrics.get("avg_confidence", 0) or 0))
        if metrics.get("avg_evidence_strength") is not None:
            avg_evidence_strength_values.append(float(metrics.get("avg_evidence_strength", 0) or 0))
        if metrics.get("avg_tokens_per_evaluation") is not None:
            avg_tokens_values.append(float(metrics.get("avg_tokens_per_evaluation", 0) or 0))
        if metrics.get("evidence_coverage") is not None:
            evidence_coverage_values.append(float(metrics.get("evidence_coverage", 0) or 0))
        distribution = metrics.get("confidence_distribution") or {}
        for key in ("high", "moderate", "low", "insufficient"):
            totals["confidence_distribution"][key] += int(distribution.get(key, 0) or 0)

    totals["avg_relevance_score"] = round(sum(avg_rel_values) / len(avg_rel_values), 2) if avg_rel_values else 0.0
    totals["avg_confidence"] = round(sum(avg_conf_values) / len(avg_conf_values), 2) if avg_conf_values else 0.0
    totals["avg_evidence_strength"] = round(sum(avg_evidence_strength_values) / len(avg_evidence_strength_values), 1) if avg_evidence_strength_values else 0.0
    totals["avg_tokens_per_evaluation"] = round(sum(avg_tokens_values) / len(avg_tokens_values), 1) if avg_tokens_values else 0.0
    totals["evidence_coverage"] = round(sum(evidence_coverage_values) / len(evidence_coverage_values), 1) if evidence_coverage_values else 0.0
    totals["sent_count"] = int(sent["sent_count"] or 0)
    totals["skipped_count"] = int(sent["skipped_count"] or 0)
    totals["api_calls_avoided"] = max(0, totals["contacts_after_clean"] - totals["api_calls_made"])
    totals["estimated_minutes_saved"] = totals["contacts_after_clean"] * 6
    totals["latest_run_id"] = recent_runs[0]["id"] if recent_runs else None
    totals["latest_completed_run_id"] = get_latest_completed_run_id()
    totals["recent_impact_notes"] = []
    for row in recent_runs:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        totals["recent_impact_notes"].append(
            {
                "run_id": row["id"],
                "created_at": row["created_at"],
                "target_url": row["target_url"],
                "status": row["status"],
                "evaluation_mode": row["evaluation_mode"] or "",
                "run_insight": (row["run_insight"] or "").strip() or default_run_insight(metrics, row["status"]),
                "recommended_count": int(metrics.get("recommended_count", 0) or 0),
                "drafts_generated": int(metrics.get("drafts_generated", 0) or 0),
                "contacts_evaluated": int(metrics.get("contacts_evaluated", 0) or 0),
                "insufficient_evidence_count": int(metrics.get("insufficient_evidence_count", 0) or 0),
            }
        )
    return totals


# Cache hits exist so repeat demos and reruns can reuse a prior decision when the interest area plus contact identity still match.
def get_cached_evaluation(
    *,
    interest_area: str,
    name: str,
    email: str = "",
    url: str = "",
    research_text: str = "",
) -> dict | None:
    connection = get_connection()
    row = connection.execute(
        """
        SELECT
            e.relevance_score,
            e.recommended,
            e.evaluation_status,
            e.research_summary,
            e.reason_match,
            e.reason_gap,
            e.reason_evidence,
            e.confidence_label,
            e.confidence_score,
            e.confidence_justification,
            e.evidence_strength_score,
            e.cited_evidence_json,
            e.not_recommended_reason,
            e.insufficient_reason,
            e.evidence_agreement_json,
            e.conflicts_detected,
            e.conflict_note,
            e.original_score,
            e.original_status,
            e.second_pass_triggered,
            e.revised_score,
            e.revised_status,
            e.revision_reason,
            e.confidence_changed,
            e.final_status,
            e.tokens_used,
            e.final_score,
            e.ranking_score,
            e.score_breakdown,
            c.name,
            c.title,
            c.role_category,
            c.email,
            c.url,
            c.research_text,
            c.source_page,
            c.identity_verified,
            c.identity_confidence,
            c.evidence_json
        FROM evaluations e
        JOIN contacts c ON c.id = e.contact_id
        JOIN runs r ON r.id = e.run_id
        WHERE r.interest_area = ?
          AND LOWER(c.name) = LOWER(?)
          AND (
            (? != '' AND LOWER(COALESCE(c.email, '')) = LOWER(?))
            OR (? != '' AND LOWER(COALESCE(c.url, '')) = LOWER(?))
            OR LOWER(COALESCE(c.research_text, '')) = LOWER(?)
          )
        ORDER BY e.id DESC
        LIMIT 1
        """,
        (interest_area, name, email, email, url, url, research_text.lower()),
    ).fetchone()
    connection.close()
    if row is None:
        return None

    return _hydrate_evaluation_item(dict(row))


def get_contact_audit(contact_id: int, run_id: int) -> dict | None:
    connection = get_connection()
    row = connection.execute(
        """
        SELECT
            c.id,
            c.run_id,
            c.name,
            c.title,
            c.role_category,
            c.email,
            c.url,
            c.research_text,
            c.source_page,
            c.identity_verified,
            c.identity_confidence,
            c.evidence_json,
            c.status,
            c.reason,
            e.relevance_score,
            e.recommended,
            e.evaluation_status,
            e.research_summary,
            e.reason_match,
            e.reason_gap,
            e.reason_evidence,
            e.confidence_label,
            e.confidence_score,
            e.confidence_justification,
            e.evidence_strength_score,
            e.cited_evidence_json,
            e.not_recommended_reason,
            e.insufficient_reason,
            e.evidence_agreement_json,
            e.conflicts_detected,
            e.conflict_note,
            e.original_score,
            e.original_status,
            e.second_pass_triggered,
            e.revised_score,
            e.revised_status,
            e.revision_reason,
            e.confidence_changed,
            e.final_status,
            e.tokens_used,
            e.model_used,
            e.final_score,
            e.ranking_score,
            e.score_breakdown
        FROM contacts c
        LEFT JOIN evaluations e ON e.contact_id = c.id AND e.run_id = c.run_id
        WHERE c.id = ? AND c.run_id = ?
        LIMIT 1
        """,
        (contact_id, run_id),
    ).fetchone()
    connection.close()
    if row is None:
        return None

    return _hydrate_evaluation_item(dict(row))


