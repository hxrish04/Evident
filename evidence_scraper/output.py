"""Writers for item records, failures, and the index."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from .records import ItemRecord

log = logging.getLogger(__name__)


def write_item(record: ItemRecord, items_dir: Path) -> Path:
    items_dir.mkdir(parents=True, exist_ok=True)
    path = items_dir / f"item-{record.site_slug}-{record.item_slug}.json"
    path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return path


def write_failure(failures_path: Path, site: str, url: str, reason: str) -> None:
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"site": site, "url": url, "reason": reason}
    data: List[dict] = []
    if failures_path.exists():
        try:
            loaded = json.loads(failures_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                data = loaded
        except Exception:
            pass
    data.append(record)
    failures_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def collect_index(items_dir: Path, out_path: Path) -> int:
    """Read every item-*.json into a single index file. Returns count."""
    if not items_dir.exists():
        out_path.write_text("[]", encoding="utf-8")
        return 0
    records: List[dict] = []
    for p in sorted(items_dir.glob("item-*.json")):
        try:
            records.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:
            log.warning("could not read %s: %s", p, e)
    out_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return len(records)


def record_url(json_path: Path) -> str:
    try:
        return json.loads(json_path.read_text(encoding="utf-8")).get("url", "")
    except Exception:
        return ""
