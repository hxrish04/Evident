# Evident

**Evidence-grounded AI decision system for research outreach.**

Not a scraper. Not a chatbot. A system that determines who is worth
contacting, explains why, shows its evidence, and refuses to
recommend when confidence is too low.

Live demo: on-demand AWS ECS deployment

---

## Evident: AI system that ranks outreach targets while minimizing hallucination and API cost

Evident is a bounded AI decision system that ranks research contacts with evidence-backed scoring, explicit uncertainty refusal, and cost-safe run limits.

### System architecture

```mermaid
flowchart TD
    A[Input: Faculty URL + Research Focus] --> B[Extract + structure evidence]
    B --> C[LLM evaluation + confidence scoring]
    C --> D{**Sufficient confidence?**}

    D -- Yes --> E[Rank contacts]
    D -- No --> F[Bounded loop: 1 retrieval + 1 re-check]
    F --> E

    E --> G[Outreach drafts (top-ranked only)]
    E --> H[Audit trail (citations + confidence + decisions)]

    subgraph Controls [Reliability + Cost Controls (Supporting)]
      I[Run caps on evals, drafts, retries]
      J[Pre-filter cuts model calls]
      K[**Refusal state: "insufficient evidence"**]
    end

    Controls -.-> C

    style K fill:#2b2b2b,stroke:#60a5fa,stroke-width:2px,color:#ffffff
```

### Why this is different

- Deterministic pre-filter reduces unnecessary model calls before LLM evaluation.
- Bounded uncertainty loop runs at most one extra retrieval and one re-check.
- Explicit refusal state returns `insufficient_evidence` instead of forcing a low-confidence decision.
- Full audit trail stores citations, confidence, and decision history per contact.

---

## Demo

This project is model-backed and not kept publicly hosted full-time to avoid unnecessary API and infrastructure cost.

Sample run shown in screenshots:
- Input: faculty/research directory URL + interest area
- Output:
  - ranked contacts
  - recommended / not recommended / insufficient evidence
  - reasoning + cited evidence
  - outreach drafts for top recommendations

---

## Modes (demo vs local)

Evident supports two operating modes using environment variables:

- `APP_MODE=demo`: public-facing, lower caps, optional `DEMO_API_KEY`, optional rate limits.
- `APP_MODE=local`: personal daily-use, no demo key, practical defaults for generating drafts.

Local mode does not depend on the cloud deployment being up.

---

## What it does

A user provides a faculty page URL and a research interest.
Evident runs a multi-stage pipeline and returns a ranked shortlist
of researchers worth contacting, with reasoning, evidence, and a
personalized draft email for each recommendation.

Pipeline stages:
1. Loads the target page safely (respects robots.txt, rate limits)
2. Extracts contacts deterministically, no AI used here
3. Enriches each contact with evidence from public profile pages
4. Pre-filters to the top candidates before spending model budget
5. Evaluates each shortlisted contact with Claude (retrieval-backed)
6. Runs a bounded agent loop on uncertain cases: one adaptive retrieval pass, then one second-pass re-evaluation
7. Ranks using a hybrid score: AI fit + evidence strength + seniority
8. Drafts personalized outreach emails for recommended contacts only

Three possible outcomes per contact:
- **Recommended** - strong fit, sufficient evidence
- **Not recommended** - evaluated but does not meet threshold
- **Insufficient evidence** - system refuses to decide

---

## What makes this different

- Deterministic pre-filter before any model call (cuts API cost by avoiding obvious weak contacts)
- Explicit refusal state, the system can say "I don't know"
- Bounded agentic loop for uncertainty: evaluate -> adaptive retrieval -> second pass -> finalize
- Second-pass reevaluation for uncertain contacts
- Decision revision tracking, shows when and why the decision changed
- Evidence agreement modeling, detects conflicting signals
- Full audit trail per contact: score breakdown, cited evidence,
  confidence justification, revision history

---

## Bounded agent loop

Evident uses a constrained uncertainty loop, not open-ended autonomy:

1. Evaluate shortlisted contacts
2. If uncertainty is high, trigger at most one adaptive retrieval pass (`ADAPTIVE_RETRIEVAL_MAX_CONTACTS`, default `1`)
3. Re-evaluate once
4. Finalize a decision (`recommended`, `not_recommended`, or `insufficient_evidence`)

This keeps behavior explainable, cost-aware, and reproducible in demos.

---

## Architecture

extractor -> enrichment -> prefilter -> evaluate -> rank -> draft
^                         ^
evidence chunks          second pass
identity signals         adaptive retrieval

Tech stack: Python · FastAPI · Playwright · SQLite/Postgres ·
Anthropic Claude · Server-Sent Events · Docker · AWS ECS/Fargate

---

## Product screens

![Overview](docs/screenshots/01-overview.png)
Launch panel + live run controls for a full pass.

![Evidence](docs/screenshots/02-evidence-view.png)
Case-file proof view with cited evidence and confidence context.

![Drafts](docs/screenshots/03-draft-view.png)
Draft workspace showing recommendation-linked outreach email generation.

![Insights](docs/screenshots/04-insights-view.png)
Run-level quality, confidence mix, and efficiency metrics.

![Agentic loop proof](docs/screenshots/05-agentic-loop-proof.png)
Bounded loop proof: second-pass counts plus adaptive retrieval trigger/chunk metrics.

---

## Validated sites

Tested against faculty-style directory pages:
- UAB Neurobiology
- Johns Hopkins Neuroscience
- NYU Langone Neuroscience

Sites outside this family return a compatibility report explaining
why they are not supported, rather than silently failing.

---

## Setup

```bash
git clone <repo>
cd evident
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
uvicorn main:app --reload
```

Open http://localhost:8000

---

## How I use Evident locally every day

1. Set `APP_MODE=local` and `ANTHROPIC_API_KEY` in `.env`
2. Start the server: `uvicorn main:app --reload`
3. Open http://localhost:8000
4. Paste a faculty page URL + interest area
5. Generate up to **5 drafts per run** (default), then copy/export and mark sent/skipped

Local persistence (SQLite by default) stores:
runs, contacts, evaluations, drafts, outreach history, and evidence chunks.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| ANTHROPIC_API_KEY | Yes (for live runs) | Claude API key |
| DATABASE_URL | No | Postgres URL (SQLite default) |
| DEMO_API_KEY | No | Restricts run endpoints to key holders |
| APP_MODE | No | `local` (daily use) or `demo` (public) |
| APP_BASIC_AUTH_USER | No | Basic auth for private deployment |
| APP_BASIC_AUTH_PASSWORD | No | Basic auth password |
| MAX_REQUESTS_PER_RUN | No | Cap on outbound fetches per run |
| MAX_DRAFTS_PER_RUN | No | Draft cap per run (defaults: local=5, demo=2) |
| MAX_EVALUATIONS_PER_RUN | No | Eval cap per run (defaults: local=12, demo=8) |
| ADAPTIVE_RETRIEVAL_MAX_CONTACTS | No | Max uncertain contacts to deep-retrieve per run (default: 1) |

---

## Cloud deployment

Docker image + AWS ECS/Fargate.
See `Dockerfile` and `apprunner.yaml` for config.
Anthropic key is injected via AWS Secrets Manager.

---

## What this is not

- Not a universal web scraper
- Not a bulk email sender
- Not a CRM
- Does not auto-send anything
- Does not bypass site restrictions or CAPTCHAs
