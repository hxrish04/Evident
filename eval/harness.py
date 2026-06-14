"""
Offline evaluation harness.

Runs each labeled case through the *real* decision path (ai.evaluate.evaluate_contact)
with a stubbed Claude client, so no API is called and results are deterministic.
The stub returns each case's scripted model response; Evident's own guardrails
then decide the final outcome. This measures the system, not a live model.

Metrics produced:
  - 3-class confusion matrix + per-class precision / recall / F1
  - overall accuracy
  - false-recommend rate on insufficient-evidence cases (the core safety metric;
    must be 0 - the system must never recommend when evidence is too thin)
  - over-refusal rate (cases wrongly sent to insufficient_evidence)
  - confidence calibration: accuracy per confidence bucket + Expected
    Calibration Error (ECE)
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import ai.evaluate as evaluate_mod
from ai.evaluate import evaluate_contact
from eval.dataset import CASES
from extractor.extract import RawContact

LABELS = ["recommended", "not_recommended", "insufficient_evidence"]


# --- Stub Anthropic client -------------------------------------------------

class _Block:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, text: str, input_tokens: int, output_tokens: int):
        self.content = [_Block(text)]
        self.usage = _Usage(input_tokens, output_tokens)


class StubClient:
    """Returns whatever JSON the active case scripted. Mimics the subset of the
    Anthropic client surface that ai.evaluate touches (client.messages.create)."""

    def __init__(self, holder: dict):
        self._holder = holder

    @property
    def messages(self):
        return self

    def create(self, model, max_tokens, messages):  # noqa: D401 - matches SDK signature loosely
        payload = json.dumps(self._holder["response"])
        # Plausible token counts so cost/observability has something to chew on.
        return _Response(payload, input_tokens=900, output_tokens=120)


@contextmanager
def stubbed_client(holder: dict):
    original = evaluate_mod.get_client
    evaluate_mod.get_client = lambda: StubClient(holder)
    try:
        yield
    finally:
        evaluate_mod.get_client = original


# --- Runner ----------------------------------------------------------------

def _contact_from(case: dict) -> RawContact:
    c = dict(case["contact"])
    c.setdefault("role_category", "faculty")
    c.setdefault("evidence_chunks", case.get("chunks", []))
    return RawContact(**c)


def run_cases(cases: list[dict] | None = None) -> list[dict]:
    cases = cases or CASES
    holder: dict = {"response": {}}
    results: list[dict] = []
    with stubbed_client(holder):
        for case in cases:
            holder["response"] = case["model_response"]
            contact = _contact_from(case)
            evaluation = evaluate_contact(
                contact,
                case["interest_area"],
                supporting_chunks=case.get("chunks", []),
                model="claude-haiku-4-5",
            )
            results.append(
                {
                    "id": case["id"],
                    "expected": case["expected"],
                    "predicted": evaluation.evaluation_status,
                    "correct": evaluation.evaluation_status == case["expected"],
                    "confidence_score": round(float(evaluation.confidence_score or 0), 3),
                    "confidence_label": evaluation.confidence_label,
                    "relevance_score": evaluation.relevance_score,
                    "evidence_strength": evaluation.evidence_strength_score,
                    "injection_flagged": evaluation.injection_flagged,
                    "recommended": evaluation.recommended,
                }
            )
    return results


# --- Metrics ---------------------------------------------------------------

def confusion_matrix(results: list[dict]) -> dict:
    matrix = {actual: {pred: 0 for pred in LABELS} for actual in LABELS}
    for row in results:
        if row["expected"] in matrix and row["predicted"] in LABELS:
            matrix[row["expected"]][row["predicted"]] += 1
    return matrix


def per_class_prf(matrix: dict) -> dict:
    out: dict = {}
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[other][label] for other in LABELS if other != label)
        fn = sum(matrix[label][other] for other in LABELS if other != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        out[label] = {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3), "support": tp + fn}
    return out


def calibration(results: list[dict], n_buckets: int = 5) -> dict:
    # Bucket by predicted confidence_score; compare bucket confidence to accuracy.
    buckets: list[dict] = []
    ece = 0.0
    total = len(results) or 1
    edges = [i / n_buckets for i in range(n_buckets + 1)]
    for lo, hi in zip(edges, edges[1:]):
        members = [r for r in results if (lo < r["confidence_score"] <= hi) or (lo == 0.0 and r["confidence_score"] == 0.0)]
        if not members:
            continue
        avg_conf = sum(r["confidence_score"] for r in members) / len(members)
        accuracy = sum(1 for r in members if r["correct"]) / len(members)
        gap = abs(avg_conf - accuracy)
        ece += (len(members) / total) * gap
        buckets.append({"range": f"({lo:.1f}, {hi:.1f}]", "count": len(members), "avg_confidence": round(avg_conf, 3), "accuracy": round(accuracy, 3), "gap": round(gap, 3)})
    # Direction of miscalibration matters more than its magnitude for a
    # trust-first system: under-confidence (the system is right more often than
    # it claims) is the safe failure mode; over-confidence is the dangerous one.
    signed = sum((len(_members_in(results, lo, hi)) / total) * (_avg_conf(results, lo, hi) - _accuracy(results, lo, hi))
                 for lo, hi in zip(edges, edges[1:]) if _members_in(results, lo, hi))
    if signed <= -0.05:
        tendency = "under_confident (conservative - safe direction)"
    elif signed >= 0.05:
        tendency = "over_confident (risky direction)"
    else:
        tendency = "well_calibrated"
    return {"buckets": buckets, "ece": round(ece, 4), "signed_calibration_error": round(signed, 4), "tendency": tendency}


def _members_in(results: list[dict], lo: float, hi: float) -> list[dict]:
    return [r for r in results if (lo < r["confidence_score"] <= hi) or (lo == 0.0 and r["confidence_score"] == 0.0)]


def _avg_conf(results: list[dict], lo: float, hi: float) -> float:
    members = _members_in(results, lo, hi)
    return sum(r["confidence_score"] for r in members) / len(members) if members else 0.0


def _accuracy(results: list[dict], lo: float, hi: float) -> float:
    members = _members_in(results, lo, hi)
    return sum(1 for r in members if r["correct"]) / len(members) if members else 0.0


def evaluate_metrics(results: list[dict]) -> dict:
    matrix = confusion_matrix(results)
    total = len(results) or 1
    correct = sum(1 for r in results if r["correct"])

    insuf_cases = [r for r in results if r["expected"] == "insufficient_evidence"]
    false_recommend_on_insufficient = sum(1 for r in insuf_cases if r["recommended"])
    decisive_cases = [r for r in results if r["expected"] != "insufficient_evidence"]
    over_refusals = sum(1 for r in decisive_cases if r["predicted"] == "insufficient_evidence")
    injection_cases = [r for r in results if r["id"].startswith("inject")]
    injection_detected = sum(1 for r in injection_cases if r["injection_flagged"])
    injection_never_recommended = all(not r["recommended"] for r in injection_cases)

    return {
        "total_cases": len(results),
        "accuracy": round(correct / total, 3),
        "confusion_matrix": matrix,
        "per_class": per_class_prf(matrix),
        "safety": {
            "false_recommend_on_insufficient": false_recommend_on_insufficient,
            "false_recommend_rate": round(false_recommend_on_insufficient / (len(insuf_cases) or 1), 3),
            "over_refusal_count": over_refusals,
            "over_refusal_rate": round(over_refusals / (len(decisive_cases) or 1), 3),
            "injection_cases": len(injection_cases),
            "injection_detected": injection_detected,
            "injection_never_recommended": injection_never_recommended,
        },
        # Calibrate only over decisive decisions (recommend / not-recommend).
        # On a refusal the confidence score reflects *contact quality*, not the
        # correctness of the decision, so including refusals would conflate two
        # different quantities and inflate ECE. The refusal path is graded
        # separately by the safety metrics above.
        "calibration": calibration([r for r in results if r["predicted"] != "insufficient_evidence"]),
    }


def run() -> dict:
    results = run_cases()
    metrics = evaluate_metrics(results)
    return {"metrics": metrics, "results": results}
