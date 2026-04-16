"""
FastAPI routes for Evident.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, HttpUrl

from agent.pipeline import AgentPipeline
from ai.evaluate import compare_ranked_contacts
from db import database as db
from demo.example_run import ensure_demo_run
from extractor.compatibility import assess_site_compatibility
from ranking.prefilter import score_contact_deterministically
from scraper.access import normalize_public_url


FRONTEND_PATH = Path(__file__).resolve().parent.parent / "frontend" / "index.html"
FRONTEND_REDESIGN_PATH = Path(__file__).resolve().parent.parent / "frontend" / "redesign.js"
FRONTEND_ASSETS_PATH = (Path(__file__).resolve().parent.parent / "frontend" / "assets").resolve()
RUN_EVENT_QUEUES: dict[int, Queue] = {}
RUN_REQUEST_TIMES: dict[str, list[float]] = {}
APP_ENV = os.getenv("APP_ENV", "development").lower()
APP_MODE = os.getenv("APP_MODE", "demo" if APP_ENV == "production" else "local").strip().lower()
if APP_MODE not in {"demo", "local"}:
    APP_MODE = "local"
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "200000"))
CORS_ALLOW_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "*" if APP_ENV == "development" else "").split(",") if origin.strip()]
DEMO_API_KEY = os.getenv("DEMO_API_KEY", "").strip()
RUN_REQUEST_DAILY_LIMIT = int(os.getenv("RUN_REQUEST_DAILY_LIMIT", "3" if APP_MODE == "demo" else "0"))
RUN_REQUESTS_PER_MINUTE = int(os.getenv("RUN_REQUESTS_PER_MINUTE", "3" if APP_MODE == "demo" else "0"))
APP_BASIC_AUTH_USER = os.getenv("APP_BASIC_AUTH_USER", "").strip()
APP_BASIC_AUTH_PASSWORD = os.getenv("APP_BASIC_AUTH_PASSWORD", "").strip()
MAX_DRAFTS_PER_RUN = int(os.getenv("MAX_DRAFTS_PER_RUN", "2" if APP_MODE == "demo" else "5"))
MAX_EVALUATIONS_PER_RUN = int(os.getenv("MAX_EVALUATIONS_PER_RUN", "8" if APP_MODE == "demo" else "12"))


app = FastAPI(
    title="Evident",
    description="Evidence-Grounded AI Decision System for Research Outreach",
    version="1.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _basic_auth_enabled() -> bool:
    return bool(APP_BASIC_AUTH_USER and APP_BASIC_AUTH_PASSWORD)


def _unauthorized_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required."},
        headers={"WWW-Authenticate": 'Basic realm="Evident"'},
    )


def _request_is_authenticated(request: Request) -> bool:
    if not _basic_auth_enabled():
        return True
    auth_header = request.headers.get("authorization", "").strip()
    if not auth_header.lower().startswith("basic "):
        return False
    try:
        encoded = auth_header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False
    return (
        secrets.compare_digest(username, APP_BASIC_AUTH_USER)
        and secrets.compare_digest(password, APP_BASIC_AUTH_PASSWORD)
    )


@app.middleware("http")
async def require_basic_auth(request: Request, call_next):
    # Keep health checks open so App Runner can verify the container without credentials.
    if request.url.path == "/health" or not _basic_auth_enabled():
        return await call_next(request)
    if not _request_is_authenticated(request):
        return _unauthorized_response()
    return await call_next(request)


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Request body is too large."})
    return await call_next(request)


class RunAgentRequest(BaseModel):
    target_url: HttpUrl | None = Field(default=None, description="Public page to analyze")
    interest_area: Optional[str] = Field(default=None, description="User's core research or outreach interest", max_length=240)
    goal_description: Optional[str] = Field(default=None, max_length=1200)
    student_profile: Optional[str] = Field(default=None, max_length=4000)
    sender_name: Optional[str] = Field(default=None, max_length=120)
    sender_email: Optional[str] = Field(default=None, max_length=160)
    sender_phone: Optional[str] = Field(default=None, max_length=60)
    top_n: int = Field(default=5, ge=1, le=max(1, MAX_DRAFTS_PER_RUN))
    url: HttpUrl | None = None
    interest: Optional[str] = Field(default=None, max_length=240)

    def resolved_url(self) -> str:
        return str(self.target_url or self.url or "")

    def resolved_interest(self) -> str:
        return (self.interest_area or self.interest or "").strip()


class SiteCheckRequest(BaseModel):
    target_url: HttpUrl


def build_user_goal(request: RunAgentRequest) -> tuple[str, str]:
    target_url = request.resolved_url()
    interest_area = request.resolved_interest()
    if not target_url:
        raise HTTPException(status_code=422, detail="target_url is required")
    if not interest_area:
        raise HTTPException(status_code=422, detail="interest_area is required")

    try:
        target_url = normalize_public_url(target_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    user_goal = interest_area
    if request.goal_description:
        user_goal += f"\n\nAdditional context: {request.goal_description}"
    return target_url, user_goal


def enforce_run_rate_limit(request: Request) -> None:
    if APP_MODE != "demo":
        return
    client_ip = (request.client.host if request.client else "local") or "local"
    now = time.time()
    # Keep a short rolling window and a daily window so a private deployment cannot rack up runaway model spend.
    recent = [stamp for stamp in RUN_REQUEST_TIMES.get(client_ip, []) if now - stamp < 60]
    daily = [stamp for stamp in RUN_REQUEST_TIMES.get(client_ip, []) if now - stamp < 86400]
    if RUN_REQUESTS_PER_MINUTE > 0 and len(recent) >= RUN_REQUESTS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Too many run requests. Please wait a minute before starting another pass.")
    if RUN_REQUEST_DAILY_LIMIT > 0 and len(daily) >= RUN_REQUEST_DAILY_LIMIT:
        raise HTTPException(status_code=429, detail="Daily run limit reached for this IP.")
    recent.append(now)
    daily.append(now)
    RUN_REQUEST_TIMES[client_ip] = daily


def require_demo_key_if_configured(request: Request) -> None:
    # GET endpoints stay viewable, but run-triggering endpoints can be gated separately for private cloud demos.
    if APP_MODE != "demo":
        return
    if not DEMO_API_KEY:
        return
    provided = request.headers.get("X-Demo-Key", "").strip()
    if provided != DEMO_API_KEY:
        raise HTTPException(status_code=403, detail="Missing or invalid demo key.")


def enqueue_progress(run_id: int, stage: str, detail: str) -> None:
    queue = RUN_EVENT_QUEUES.get(run_id)
    if queue is not None:
        queue.put({"stage": stage, "detail": detail})


def build_pipeline(request: RunAgentRequest, run_id: int | None = None):
    target_url, user_goal = build_user_goal(request)
    pipeline = AgentPipeline(
        user_goal=user_goal,
        student_profile=request.student_profile,
        sender_name=request.sender_name,
        sender_email=request.sender_email,
        sender_phone=request.sender_phone,
        top_n_emails=request.top_n,
        max_eval_contacts=max(1, MAX_EVALUATIONS_PER_RUN),
        progress_callback=(lambda stage, detail: enqueue_progress(run_id, stage, detail)) if run_id is not None else None,
    )
    return target_url, user_goal, pipeline


def start_background_run(request: RunAgentRequest, background_tasks: BackgroundTasks, *, use_sent_exclusions: bool) -> dict:
    target_url, user_goal = build_user_goal(request)
    db.init_db()
    run_id = db.create_run(target_url=target_url, interest_area=user_goal, status="running")
    RUN_EVENT_QUEUES[run_id] = Queue()
    pipeline = AgentPipeline(
        user_goal=user_goal,
        student_profile=request.student_profile,
        sender_name=request.sender_name,
        sender_email=request.sender_email,
        sender_phone=request.sender_phone,
        top_n_emails=request.top_n,
        max_eval_contacts=max(1, MAX_EVALUATIONS_PER_RUN),
        progress_callback=lambda stage, detail: enqueue_progress(run_id, stage, detail),
    )
    exclusions = db.get_outreach_contact_exclusions() if use_sent_exclusions else None
    background_tasks.add_task(pipeline.run, target_url, run_id, exclusions)
    return {"run_id": run_id, "status": "started"}


@app.post("/run-agent")
def run_agent(request: RunAgentRequest, fastapi_request: Request):
    require_demo_key_if_configured(fastapi_request)
    enforce_run_rate_limit(fastapi_request)
    target_url, _, pipeline = build_pipeline(request)
    try:
        return pipeline.run(url=target_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")


@app.post("/check-site")
def check_site(request: SiteCheckRequest):
    try:
        return assess_site_compatibility(str(request.target_url))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/demo-run")
def load_demo_run():
    if APP_MODE != "demo":
        raise HTTPException(status_code=404, detail="Not found")
    db.init_db()
    run_id = ensure_demo_run()
    return {"run_id": run_id, "status": "ready"}


@app.post("/run-next")
def run_next(request: RunAgentRequest, fastapi_request: Request):
    require_demo_key_if_configured(fastapi_request)
    enforce_run_rate_limit(fastapi_request)
    target_url, _, pipeline = build_pipeline(request)
    exclusions = db.get_outreach_contact_exclusions()
    try:
        return pipeline.run(url=target_url, exclusion_list=exclusions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")


@app.post("/run-agent/start")
def start_run_agent(request: RunAgentRequest, background_tasks: BackgroundTasks, fastapi_request: Request):
    require_demo_key_if_configured(fastapi_request)
    enforce_run_rate_limit(fastapi_request)
    return start_background_run(request, background_tasks, use_sent_exclusions=False)


@app.post("/run-next/start")
def start_run_next(request: RunAgentRequest, background_tasks: BackgroundTasks, fastapi_request: Request):
    require_demo_key_if_configured(fastapi_request)
    enforce_run_rate_limit(fastapi_request)
    return start_background_run(request, background_tasks, use_sent_exclusions=True)


@app.get("/run-stream/{run_id}")
def run_stream(run_id: int):
    db.init_db()
    if run_id not in RUN_EVENT_QUEUES:
        RUN_EVENT_QUEUES[run_id] = Queue()

    def event_generator():
        queue = RUN_EVENT_QUEUES[run_id]
        while True:
            try:
                event = queue.get(timeout=15)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("stage") in {"complete", "failed"}:
                    break
            except Empty:
                run = db.get_run(run_id)
                if run is None:
                    yield f"data: {json.dumps({'stage': 'failed', 'detail': 'Run not found'})}\n\n"
                    break
                if run.get("status") in {"completed", "failed", "no_contacts", "no_evaluations"}:
                    final_stage = "complete" if run.get("status") != "failed" else "failed"
                    yield f"data: {json.dumps({'stage': final_stage, 'detail': run.get('stage_detail', 'Run finished')})}\n\n"
                    break
                yield ": keepalive\n\n"
        RUN_EVENT_QUEUES.pop(run_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/", include_in_schema=False)
def serve_frontend():
    version = str(int(FRONTEND_REDESIGN_PATH.stat().st_mtime))
    html = FRONTEND_PATH.read_text(encoding="utf-8").replace(
        '<script src="/redesign.js"></script>',
        f'<script src="/redesign.js?v={version}"></script>',
    )
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/redesign.js", include_in_schema=False)
def serve_frontend_redesign():
    return FileResponse(
        FRONTEND_REDESIGN_PATH,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/assets/{asset_name:path}", include_in_schema=False)
def serve_frontend_asset(asset_name: str):
    asset_path = (FRONTEND_ASSETS_PATH / asset_name).resolve()
    if FRONTEND_ASSETS_PATH not in asset_path.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not asset_path.exists() or not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    media_type = "image/svg+xml" if asset_path.suffix.lower() == ".svg" else None
    return FileResponse(asset_path, media_type=media_type)


@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon():
    return FileResponse(FRONTEND_ASSETS_PATH / "logo-mark.svg", media_type="image/svg+xml")


@app.get("/contacts")
def get_contacts(run_id: Optional[int] = Query(default=None)):
    db.init_db()
    contacts = db.get_ranked_contacts(run_id=run_id)
    latest_run_id = run_id or db.get_latest_run_id()
    return {"run_id": latest_run_id, "contacts": contacts, "total": len(contacts)}


@app.get("/drafts")
def get_drafts(run_id: Optional[int] = Query(default=None)):
    db.init_db()
    drafts = db.get_all_drafts(run_id=run_id)
    latest_run_id = run_id or db.get_latest_run_id()
    return {"run_id": latest_run_id, "drafts": drafts, "total": len(drafts)}


@app.get("/history")
def get_history(
    limit: int = Query(default=100, ge=1, le=500),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
):
    db.init_db()
    history = db.get_outreach_history(limit=limit, status_filter=status, search=search)
    return {"history": history, "total": len(history)}


@app.get("/metrics")
def get_metrics():
    db.init_db()
    return db.get_resume_metrics()


@app.get("/runs/{run_id}")
def get_run(run_id: int):
    db.init_db()
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    run["contacts"] = db.get_ranked_contacts(run_id=run_id)
    run["drafts"] = db.get_all_drafts(run_id=run_id)
    return run


@app.get("/compare-top")
def compare_top(run_id: int = Query(...)):
    db.init_db()
    contacts = db.get_ranked_contacts(run_id=run_id)
    if len(contacts) < 2:
        raise HTTPException(status_code=400, detail="Need at least two ranked contacts to compare")
    rank_1 = contacts[0]
    rank_2 = contacts[1]
    comparison = compare_ranked_contacts(rank_1, rank_2)
    return {
        "rank_1": {"contact_name": rank_1["name"], "score": rank_1.get("ranking_score", rank_1["final_score"])},
        "rank_2": {"contact_name": rank_2["name"], "score": rank_2.get("ranking_score", rank_2["final_score"])},
        "comparison_explanation": comparison,
    }


@app.get("/audit/{contact_id}")
def audit_contact(contact_id: int, run_id: int = Query(...)):
    db.init_db()
    audit = db.get_contact_audit(contact_id, run_id)
    if audit is None:
        raise HTTPException(status_code=404, detail="Contact audit not found")

    run = db.get_run(run_id)
    interest_area = (run or {}).get("interest_area", "")
    pre_filter_score = score_contact_deterministically(
        type("AuditContact", (), {
            "title": audit.get("title", ""),
            "email": audit.get("email", ""),
            "research_text": audit.get("research_text", ""),
            "identity_verified": audit.get("identity_verified", False),
        })(),
        interest_area,
    )
    pre_filter_passed = audit.get("status") != "pre_filtered"

    suggestions = []
    if not audit.get("email"):
        suggestions.append("A direct contact email would increase confidence.")
    if float(audit.get("evidence_strength_score") or 0) < 4:
        suggestions.append("Additional public sources or a lab page would strengthen this recommendation.")
    if not audit.get("identity_verified"):
        suggestions.append("A verified faculty profile page would increase identity confidence.")
    if audit.get("conflicts_detected"):
        suggestions.append("Clearer alignment between sources would resolve conflicting signals.")

    return {
        "contact": {
            "name": audit.get("name"),
            "title": audit.get("title"),
            "email": audit.get("email"),
            "url": audit.get("url"),
        },
        "pre_filter_score": round(pre_filter_score, 2),
        "pre_filter_passed": pre_filter_passed,
        "evaluation_status": audit.get("evaluation_status"),
        "relevance_score": audit.get("relevance_score"),
        "evidence_strength_score": audit.get("evidence_strength_score"),
        "confidence_label": audit.get("confidence_label"),
        "confidence_justification": audit.get("confidence_justification"),
        "evidence_agreement": audit.get("evidence_agreement", {}),
        "conflicts_detected": audit.get("conflicts_detected"),
        "conflict_note": audit.get("conflict_note"),
        "cited_evidence": audit.get("cited_evidence", []),
        "reasoning_trace": audit.get("reason_trace", {}),
        "score_breakdown": audit.get("score_breakdown", {}),
        "decision_revision": audit.get("decision_revision", {"revised": False}),
        "what_would_increase_confidence": " ".join(suggestions).strip(),
    }


@app.post("/mark-sent/{draft_id}")
@app.post("/drafts/{draft_id}/mark-sent")
def mark_draft_sent(draft_id: int):
    db.init_db()
    draft = db.mark_draft_sent(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {"success": True, "draft_id": draft_id, "sent_at": draft.get("sent_at"), **draft}


@app.post("/drafts/{draft_id}/mark-skipped")
def mark_draft_skipped(draft_id: int):
    db.init_db()
    draft = db.mark_draft_status(draft_id, "skipped")
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@app.post("/drafts/{draft_id}/restore")
def restore_draft(draft_id: int):
    db.init_db()
    draft = db.mark_draft_status(draft_id, "draft")
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@app.get("/health")
def health():
    db_status = "error"
    latest_run_id = None
    latest_completed_run_id = None
    latest_run_status = None
    try:
        db.init_db()
        db_status = db.check_db_health()
        latest_run_id = db.get_latest_run_id()
        latest_completed_run_id = db.get_latest_completed_run_id()
        latest_run = db.get_run(latest_run_id) if latest_run_id is not None else None
        latest_run_status = latest_run.get("status") if latest_run else None
    except Exception:
        db_status = "error"
    return {
        "status": "healthy",
        "version": app.version,
        "db": db_status,
        "env": APP_ENV,
        "mode": APP_MODE,
        "private_mode": _basic_auth_enabled(),
        "latest_run_id": latest_run_id,
        "latest_completed_run_id": latest_completed_run_id,
        "latest_run_status": latest_run_status,
    }
