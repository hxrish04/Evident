"""
CLI for the Evident evaluation harness.

    python -m eval.run            # run, print a report, write eval/reports/latest.json
    python -m eval.run --quiet    # write the report only

No API key required: the harness stubs the model client, so this is safe to run
in CI as a regression gate on the decision layer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.harness import run

REPORT_DIR = Path(__file__).resolve().parent / "reports"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_markdown(report: dict) -> str:
    m = report["metrics"]
    lines: list[str] = []
    lines.append("# Evident decision-layer evaluation\n")
    lines.append(f"- Cases: **{m['total_cases']}**")
    lines.append(f"- Overall accuracy: **{_fmt_pct(m['accuracy'])}**")
    safety = m["safety"]
    lines.append(f"- False-recommend on insufficient evidence: **{safety['false_recommend_on_insufficient']}** (rate {_fmt_pct(safety['false_recommend_rate'])}) - target 0")
    lines.append(f"- Over-refusal rate: **{_fmt_pct(safety['over_refusal_rate'])}**")
    lines.append(f"- Injection cases: {safety['injection_cases']} | detected {safety['injection_detected']} | never recommended: {safety['injection_never_recommended']}")
    lines.append(f"- Confidence calibration ECE: **{m['calibration']['ece']}** ({m['calibration']['tendency']}, decisive decisions only)\n")

    lines.append("## Per-class precision / recall / F1\n")
    lines.append("| class | precision | recall | f1 | support |")
    lines.append("|---|---|---|---|---|")
    for label, vals in m["per_class"].items():
        lines.append(f"| {label} | {vals['precision']} | {vals['recall']} | {vals['f1']} | {vals['support']} |")

    lines.append("\n## Confusion matrix (rows = actual, cols = predicted)\n")
    labels = list(m["confusion_matrix"].keys())
    lines.append("| actual \\ predicted | " + " | ".join(labels) + " |")
    lines.append("|" + "---|" * (len(labels) + 1))
    for actual in labels:
        row = m["confusion_matrix"][actual]
        lines.append(f"| {actual} | " + " | ".join(str(row[p]) for p in labels) + " |")

    lines.append("\n## Calibration buckets\n")
    lines.append("| confidence range | count | avg confidence | accuracy | gap |")
    lines.append("|---|---|---|---|---|")
    for bucket in m["calibration"]["buckets"]:
        lines.append(f"| {bucket['range']} | {bucket['count']} | {bucket['avg_confidence']} | {bucket['accuracy']} | {bucket['gap']} |")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Evident decision-layer evaluation.")
    parser.add_argument("--quiet", action="store_true", help="Write report files without printing the table.")
    args = parser.parse_args()

    report = run()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown = render_markdown(report)
    (REPORT_DIR / "latest.md").write_text(markdown, encoding="utf-8")

    if not args.quiet:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
