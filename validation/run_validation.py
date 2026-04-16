"""
Runs deterministic compatibility validation across a small set of public faculty pages.

This is intentionally model-free so we can sanity-check multi-site generalization without
spending Anthropic credits during smoke tests.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from extractor.compatibility import assess_site_compatibility  # noqa: E402


def load_sites(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    normalized: list[dict] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        clone = dict(item)
        clone["label"] = clone.get("label") or clone.get("name") or clone.get("url")
        normalized.append(clone)
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Evident against a small site matrix.")
    parser.add_argument(
        "--sites",
        default=str(Path(__file__).resolve().parent / "sites.json"),
        help="Path to a JSON list of labeled site URLs.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / "latest_report.json"),
        help="Path to write the JSON report.",
    )
    args = parser.parse_args()

    sites_path = Path(args.sites).resolve()
    output_path = Path(args.output).resolve()
    sites = load_sites(sites_path)

    results: list[dict] = []
    status_counts: Counter[str] = Counter()
    for site in sites:
        label = str(site.get("label") or site.get("name") or site.get("url"))
        url = str(site["url"])
        expected_min_contacts = int(site.get("expected_min_contacts") or 0)
        try:
            report = assess_site_compatibility(url)
            report["label"] = label
            report["expected_min_contacts"] = expected_min_contacts
            report["meets_expected_min_contacts"] = (
                report.get("cleaned_contacts_found", 0) >= expected_min_contacts if expected_min_contacts else True
            )
            report["ok"] = True
            status_counts[str(report.get("compatibility_status", "unknown"))] += 1
        except Exception as exc:  # pragma: no cover - deterministic smoke path
            report = {
                "label": label,
                "target_url": url,
                "ok": False,
                "compatibility_status": "error",
                "error": str(exc),
            }
            status_counts["error"] += 1
        results.append(report)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "site_count": len(results),
        "status_counts": dict(status_counts),
    }
    payload = {"summary": summary, "results": results}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
