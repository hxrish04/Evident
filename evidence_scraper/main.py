"""CLI entry point.

Every command takes --profile pointing at a profile YAML:

  python -m evidence_scraper --profile profiles/my-project.yaml discover
  python -m evidence_scraper --profile profiles/my-project.yaml extract --site acme
  python -m evidence_scraper --profile profiles/my-project.yaml run --limit 3
  python -m evidence_scraper --profile profiles/my-project.yaml index
  python -m evidence_scraper --profile profiles/my-project.yaml summarize --format wide
  python -m evidence_scraper init my-new-project       # scaffold a new profile

Data lands in data/<profile-slug>/{urls,items,cache,...} so multiple
projects coexist without stepping on each other.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, List, Optional

import yaml

from .ai_discovery import discover_via_ai
from .discovery import discover_for_site, write_site_urls
from .extractor import Extractor
from .fetcher import Fetcher
from .output import collect_index, record_url, write_failure, write_item
from .profile import Profile, load_profile
from .records import ItemRecord, SiteUrls, UrlRecord
from .url_filter import UrlFilter, slug_from_url

PKG_DIR = Path(__file__).resolve().parent
ROOT_DIR = PKG_DIR.parent


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def load_engine_config(path: Path) -> dict:
    """Engine settings (fetch/discovery/extraction limits) from config.yaml."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    with env_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def require_api_key(cfg: dict) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key", "")
    if not api_key or api_key.startswith("REPLACE_"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Add it to .env.local next to config.yaml "
            "or set the environment variable."
        )
    return api_key


def data_dir_for(profile: Profile, cfg: dict) -> Path:
    base = Path(cfg.get("data_dir", "data"))
    return base / profile.slug


def filter_sites(profile: Profile, name: Optional[str]):
    if not name:
        if not profile.sites:
            raise SystemExit("Profile has no sites. Add at least one under `sites:`.")
        return profile.sites
    site = profile.site(name)
    if not site:
        raise SystemExit(
            f"No site named {name!r} in profile. Known: "
            + ", ".join(s.slug for s in profile.sites)
        )
    return [site]


def parse_item_filters(values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    items: List[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                items.append(part)
    return items


def filter_url_records(records: List[UrlRecord], item_filters: List[str]) -> List[UrlRecord]:
    todo = [r for r in records if not r.skip]
    if not item_filters:
        return todo
    wanted = {m.lower() for m in item_filters}
    return [
        r for r in todo
        if r.candidate_id and r.candidate_id.strip().lower() in wanted
    ]


# ---------------------------------------------------------------------------
# summarize helpers
# ---------------------------------------------------------------------------

def parse_confidence_filter(expr: Optional[str]) -> Optional[Callable[[Optional[float]], bool]]:
    if not expr:
        return None
    raw = expr.strip()
    op, value_text = ">=", raw
    for candidate in (">=", "<=", ">", "<"):
        if raw.startswith(candidate):
            op, value_text = candidate, raw[len(candidate):].strip()
            break
    try:
        threshold = float(value_text)
    except ValueError as e:
        raise SystemExit("--confidence must be a number or comparison like '>=0.8'") from e
    if not 0.0 <= threshold <= 1.0:
        raise SystemExit("--confidence threshold must be between 0.0 and 1.0")

    def keep(c: Optional[float]) -> bool:
        if c is None:
            return False
        if op == ">=":
            return c >= threshold
        if op == "<=":
            return c <= threshold
        if op == ">":
            return c > threshold
        return c < threshold

    return keep


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def iter_item_records(items_dir: Path, site: Optional[str] = None):
    site_filter = site.strip().lower() if site else None
    if not items_dir.exists():
        return
    for path in sorted(items_dir.glob("item-*.json")):
        try:
            rec = ItemRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"warning: skip invalid item file {path}: {e}", file=sys.stderr)
            continue
        if site_filter and site_filter not in {rec.site.lower(), rec.site_slug.lower()}:
            continue
        yield rec


def summarize_rows(profile: Profile, items_dir: Path,
                   site: Optional[str], confidence: Optional[str]) -> List[dict]:
    conf_filter = parse_confidence_filter(confidence)
    rows: List[dict] = []
    for rec in iter_item_records(items_dir, site):
        for attr in profile.attributes:
            obs = rec.attributes.get(attr.name) or {}
            conf = obs.get("confidence")
            if conf_filter and not conf_filter(conf):
                continue
            rows.append({
                "Site": rec.site,
                "Item": rec.item_name,
                "Attribute": attr.name,
                "value": _stringify(obs.get("value")),
                "unit": obs.get("unit") or "",
                "confidence": "" if conf is None else str(conf),
            })
    return rows


def summarize_wide_rows(profile: Profile, items_dir: Path, site: Optional[str]) -> List[dict]:
    rows: List[dict] = []
    for rec in iter_item_records(items_dir, site):
        row = {"Site": rec.site, "Item": rec.item_name, "URL": rec.url}
        for attr in profile.attributes:
            obs = rec.attributes.get(attr.name) or {}
            row[attr.name] = _stringify(obs.get("value"))
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args, _profile, _cfg) -> None:
    """Scaffold a new profile from the template."""
    name = args.name
    slug = name.lower().replace(" ", "-")
    dest = ROOT_DIR / "profiles" / f"{slug}.yaml"
    if dest.exists():
        raise SystemExit(f"{dest} already exists.")
    template = ROOT_DIR / "profiles" / "_template.yaml"
    if not template.exists():
        raise SystemExit(f"Template not found at {template}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(template, dest)
    print(f"Created {dest}")
    print("Open it, fill in the blanks, then run:")
    print(f"  python -m evidence_scraper --profile profiles/{slug}.yaml run --limit 3")


def cmd_validate(args, profile: Profile, cfg: dict) -> None:
    """Validate the profile and show what the LLM will be asked to do."""
    from .schema_gen import build_extraction_system_prompt

    print(f"Profile OK: {profile.name} (slug: {profile.slug})")
    print(f"  target: {profile.target.description}")
    print(f"  attributes: {len(profile.attributes)}")
    for a in profile.attributes:
        unit = f" [{a.unit}]" if a.unit else ""
        print(f"    - {a.name} ({a.type}){unit}")
    print(f"  sites: {len(profile.sites)}")
    for s in profile.sites:
        print(f"    - {s.name} ({s.slug}): {len(s.start_urls)} start url(s)")
    if args.show_prompt:
        print("\n--- extraction system prompt ---\n")
        print(build_extraction_system_prompt(profile))


def cmd_discover(args, profile: Profile, cfg: dict) -> None:
    log = logging.getLogger("discover")
    data_dir = data_dir_for(profile, cfg)
    disc_cfg = dict(cfg.get("discovery", {}))
    disc_cfg.setdefault(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    )
    disc_cfg.setdefault("request_timeout_sec", 30)
    disc_cfg.setdefault("max_pages_per_site", 400)
    disc_cfg.setdefault("max_crawl_depth", 3)
    sites = filter_sites(profile, args.site)
    method = getattr(args, "discovery_method", "ai")

    api_key = require_api_key(cfg) if method == "ai" else ""
    url_filter = UrlFilter(profile)

    for site in sites:
        try:
            if method == "ai":
                records = discover_via_ai(
                    site,
                    profile,
                    api_key=api_key,
                    model=cfg.get("model", "claude-sonnet-4-6"),
                    disc_cfg=disc_cfg,
                    fetch_cfg=cfg.get("fetch"),
                    cache_dir=data_dir / "cache",
                )
                merged = write_site_urls(site, data_dir, records, force=args.force)
                log.info("[%s] AI discovery wrote %d urls", site.name, len(merged.urls))
            else:
                discover_for_site(site, url_filter, disc_cfg, data_dir, force=args.force)
            log.info("OK %s", site.name)
        except Exception as e:
            log.exception("FAIL %s: %s", site.name, e)


def cmd_extract(args, profile: Profile, cfg: dict) -> None:
    log = logging.getLogger("extract")
    api_key = require_api_key(cfg)
    data_dir = data_dir_for(profile, cfg)
    urls_dir = data_dir / "urls"
    items_dir = data_dir / "items"
    cache_dir = data_dir / "cache"
    failures_path = data_dir / "failures.json"

    ext_cfg = cfg.get("extraction", {})
    extractor = Extractor(
        profile,
        api_key=api_key,
        model=cfg.get("model", "claude-sonnet-4-6"),
        max_chars=ext_cfg.get("max_chars_per_page", 60000),
    )
    min_chars = ext_cfg.get("min_chars_per_page", 200)

    sites = filter_sites(profile, args.site)
    item_filters = parse_item_filters(args.item)

    with Fetcher(cfg.get("fetch", {}), cache_dir) as fetcher:
        for site in sites:
            urls_file = urls_dir / f"urls-{site.slug}.json"
            if not urls_file.exists():
                log.warning("[%s] no urls file at %s — run discover first, or "
                            "write one by hand", site.name, urls_file)
                continue
            su = SiteUrls.model_validate_json(urls_file.read_text(encoding="utf-8"))
            todo = filter_url_records(su.urls, item_filters)
            if args.limit:
                todo = todo[: args.limit]
            log.info("[%s] extracting from %d urls", site.name, len(todo))

            existing = list(items_dir.glob(f"item-{site.slug}-*.json")) if items_dir.exists() else []
            done_urls = {record_url(p) for p in existing}

            for rec in todo:
                url = rec.url
                if url in done_urls and not args.force:
                    log.info("[%s] skip (already extracted) %s", site.name, url)
                    continue

                try:
                    fr = fetcher.fetch(url, use_cache=not args.force_fetch)
                except Exception as e:
                    log.warning("[%s] fetch error %s: %s", site.name, url, e)
                    write_failure(failures_path, site.name, url, f"fetch: {e}")
                    continue
                if not fr or len(fr.visible_text) < min_chars:
                    log.warning("[%s] too little text on %s", site.name, url)
                    write_failure(failures_path, site.name, url, "empty page")
                    continue

                try:
                    tool_input = extractor.classify_and_extract(
                        url=url,
                        page_title=fr.title,
                        page_text=fr.visible_text,
                        site_name=site.name,
                    )
                except Exception as e:
                    log.exception("[%s] LLM error %s", site.name, url)
                    write_failure(failures_path, site.name, url, f"llm: {e}")
                    continue
                if tool_input is None:
                    write_failure(failures_path, site.name, url, "llm_no_tool_use")
                    continue

                ir = extractor.to_item_record(
                    tool_input=tool_input,
                    site_name=site.name,
                    site_slug=site.slug,
                    url=url,
                    page_title=fr.title,
                    raw_text_chars=len(fr.visible_text),
                    fallback_slug=rec.candidate_id or slug_from_url(url) or "unknown",
                )
                if not ir.is_target:
                    log.info("[%s] not a target page: %s (%s)",
                             site.name, url, ir.classification_reason)
                    write_item(ir, items_dir / "_rejected")
                    continue

                path = write_item(ir, items_dir)
                done_urls.add(url)
                log.info("[%s] wrote %s", site.name, path)


def cmd_run(args, profile: Profile, cfg: dict) -> None:
    cmd_discover(args, profile, cfg)
    cmd_extract(args, profile, cfg)


def cmd_index(args, profile: Profile, cfg: dict) -> None:
    data_dir = data_dir_for(profile, cfg)
    n = collect_index(data_dir / "items", data_dir / "index.json")
    print(f"Indexed {n} items into {data_dir / 'index.json'}")


def cmd_summarize(args, profile: Profile, cfg: dict) -> None:
    data_dir = data_dir_for(profile, cfg)
    items_dir = data_dir / "items"

    out_path = getattr(args, "out", None)
    handle = open(out_path, "w", newline="", encoding="utf-8") if out_path else sys.stdout
    try:
        if args.format == "wide":
            rows = summarize_wide_rows(profile, items_dir, args.site)
            fieldnames = ["Site", "Item", "URL"] + [a.name for a in profile.attributes]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            return

        rows = summarize_rows(profile, items_dir, args.site, args.confidence)
        fieldnames = ["Site", "Item", "Attribute", "value", "unit", "confidence"]
        if args.format == "csv":
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            return

        print(" | ".join(fieldnames), file=handle)
        for row in rows:
            print(" | ".join(row[f] for f in fieldnames), file=handle)
    finally:
        if handle is not sys.stdout:
            handle.close()
            print(f"Wrote {out_path}")


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="evidence_scraper",
        description="Profile-driven universal web scraper (discover URLs, "
                    "extract structured attributes with an LLM).",
    )
    p.add_argument("--profile", help="Path to a profile YAML (required for most commands).")
    p.add_argument("--config", default=str(ROOT_DIR / "config.yaml"),
                   help="Engine config file (fetch/discovery settings).")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="Scaffold a new profile YAML from the template.")
    pi.add_argument("name", help="Project name, e.g. 'espresso-machines'.")
    pi.set_defaults(func=cmd_init, needs_profile=False)

    pv = sub.add_parser("validate", help="Validate a profile and preview the LLM prompt.")
    pv.add_argument("--show-prompt", action="store_true",
                    help="Print the generated extraction system prompt.")
    pv.set_defaults(func=cmd_validate, needs_profile=True)

    pd = sub.add_parser("discover", help="Find candidate target URLs per site.")
    pd.add_argument("--site", help="Only this site (name or slug).")
    pd.add_argument("--discovery-method", choices=["ai", "crawl"], default="ai",
                    help="ai = LLM selects from gathered links (default); "
                         "crawl = sitemap/BFS + keyword filter only.")
    pd.add_argument("--force", action="store_true",
                    help="Overwrite existing urls-*.json instead of merging.")
    pd.set_defaults(func=cmd_discover, needs_profile=True)

    pe = sub.add_parser("extract", help="Fetch pages + LLM extraction.")
    pe.add_argument("--site", help="Only this site (name or slug).")
    pe.add_argument("--item", action="append",
                    help="Only URL records with this candidate_id. May be repeated "
                         "or comma-separated.")
    pe.add_argument("--limit", type=int, default=0)
    pe.add_argument("--force", action="store_true",
                    help="Re-extract even if an item record already exists.")
    pe.add_argument("--force-fetch", action="store_true", help="Bypass the HTML cache.")
    pe.set_defaults(func=cmd_extract, needs_profile=True)

    pr = sub.add_parser("run", help="discover then extract.")
    pr.add_argument("--site")
    pr.add_argument("--discovery-method", choices=["ai", "crawl"], default="ai")
    pr.add_argument("--item", action="append")
    pr.add_argument("--limit", type=int, default=0)
    pr.add_argument("--force", action="store_true")
    pr.add_argument("--force-fetch", action="store_true")
    pr.set_defaults(func=cmd_run, needs_profile=True)

    px = sub.add_parser("index", help="Build index.json from item files.")
    px.set_defaults(func=cmd_index, needs_profile=True)

    ps = sub.add_parser("summarize", help="Print extracted attributes.")
    ps.add_argument("--site", help="Only items from this site (name or slug).")
    ps.add_argument("--confidence",
                    help="Confidence comparison, e.g. '>=0.8' or '<0.5'. "
                         "Bare number means >=.")
    ps.add_argument("--format", choices=["console", "csv", "wide"], default="console",
                    help="'wide' = CSV, one row per item, one column per attribute.")
    ps.add_argument("--out", help="Write to this file instead of stdout.")
    ps.set_defaults(func=cmd_summarize, needs_profile=True)

    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)

    cfg_path = Path(args.config)
    _load_env_file(cfg_path.parent / ".env.local")
    cfg = load_engine_config(cfg_path)

    if not getattr(args, "needs_profile", True):
        args.func(args, None, cfg)
        return

    if not args.profile:
        raise SystemExit(
            "This command needs --profile. Example:\n"
            "  python -m evidence_scraper --profile profiles/my-project.yaml "
            f"{args.cmd}"
        )
    profile = load_profile(Path(args.profile))
    log_path = data_dir_for(profile, cfg) / "run.log"
    setup_logging(args.verbose, log_path)
    args.func(args, profile, cfg)


if __name__ == "__main__":
    main()