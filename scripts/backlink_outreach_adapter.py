#!/usr/bin/env python3
"""Command adapter for danpoletaev/backlink-outreach-js (Apify Actor).

This script is designed to be called from:
`geo outreach run --provider command --command-template ...`
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote
from urllib.request import Request, urlopen


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def normalize_domain(value: str) -> str:
    v = value.strip().lower()
    if v.startswith("http://"):
        v = v[len("http://") :]
    if v.startswith("https://"):
        v = v[len("https://") :]
    v = v.split("/", 1)[0]
    if v.startswith("www."):
        v = v[4:]
    return v


def build_actor_url(actor_id: str, token: str) -> str:
    actor_slug = actor_id.strip().replace("/", "~")
    if not actor_slug:
        raise ValueError("actor-id is required")
    return f"https://api.apify.com/v2/acts/{quote(actor_slug, safe='')}/run-sync-get-dataset-items?token={quote(token, safe='')}"


def post_json(url: str, payload: Dict[str, object], timeout: int) -> object:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def extract_items(data: object) -> List[Dict[str, object]]:
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return [i for i in data["items"] if isinstance(i, dict)]
        return [data]
    return []


def pick_items_for_domain(items: List[Dict[str, object]], domain: str) -> List[Dict[str, object]]:
    target = normalize_domain(domain)
    picked: List[Dict[str, object]] = []
    for item in items:
        url = str(item.get("articleUrl") or "")
        article_domain = normalize_domain(url)
        if not article_domain:
            continue
        if article_domain == target or article_domain.endswith(f".{target}"):
            picked.append(item)
    return picked


def build_input(args: argparse.Namespace) -> Dict[str, object]:
    business_name = args.business_name or args.site_name or os.getenv("GEO_BUSINESS_NAME", "")
    short_desc = args.business_description or os.getenv("GEO_BUSINESS_DESCRIPTION", "")
    contact_name = args.contact_name or os.getenv("GEO_CONTACT_NAME", "")
    if not business_name:
        business_name = normalize_domain(args.pitch_url)
    payload: Dict[str, object] = {
        "keywords": [args.keyword],
        "businessName": business_name,
        "shortBusinessDescription": short_desc,
        "name": contact_name,
        "excludeDomains": args.exclude_domain or [],
    }
    return payload


def write_output(path: Path, content: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="backlink_outreach_adapter.py",
        description="Adapter to run backlink-outreach-js actor and filter result by target domain.",
    )
    parser.add_argument("--domain", required=True, help="Target outreach domain from geo campaign.")
    parser.add_argument("--keyword", required=True, help="Gap keyword.")
    parser.add_argument("--pitch-url", required=True, help="Your URL to promote.")
    parser.add_argument("--site-name", default="", help="Your site/sender name.")
    parser.add_argument("--contact-email", default="", help="Contact email discovered by geo outreach plan.")
    parser.add_argument("--contact-page", default="", help="Contact page discovered by geo outreach plan.")
    parser.add_argument("--business-name", default="", help="Override businessName passed to actor.")
    parser.add_argument("--business-description", default="", help="Override shortBusinessDescription passed to actor.")
    parser.add_argument("--contact-name", default="", help="Override contact name passed to actor.")
    parser.add_argument("--exclude-domain", action="append", help="Extra exclude domains for actor input.")
    parser.add_argument("--actor-id", default="daniil.poletaev/backlink-building-agent")
    parser.add_argument("--apify-token", default=os.getenv("APIFY_TOKEN", ""))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output-dir", default="./outreach-output/apify-adapter")
    parser.add_argument("--allow-fallback-first", action="store_true", help="Use first item if target-domain item not found.")
    parser.add_argument("--dry-run", action="store_true", help="Print payload and exit without API call.")
    args = parser.parse_args()

    payload = build_input(args)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "generated_at_utc": now_utc(),
                    "domain": args.domain,
                    "keyword": args.keyword,
                    "payload": payload,
                },
                ensure_ascii=False,
            )
        )
        return 0

    token = (args.apify_token or "").strip()
    if not token:
        print("missing APIFY_TOKEN (set env or --apify-token)", file=sys.stderr)
        return 2

    actor_url = build_actor_url(args.actor_id, token)
    try:
        raw_data = post_json(actor_url, payload, timeout=args.timeout)
    except Exception as e:
        print(f"actor call failed: {e}", file=sys.stderr)
        return 3

    items = extract_items(raw_data)
    matched = pick_items_for_domain(items, args.domain)
    selected = matched
    used_fallback = False
    if not selected and args.allow_fallback_first and items:
        selected = [items[0]]
        used_fallback = True

    result = {
        "generated_at_utc": now_utc(),
        "domain": normalize_domain(args.domain),
        "keyword": args.keyword,
        "pitch_url": args.pitch_url,
        "site_name": args.site_name,
        "contact_email": args.contact_email,
        "contact_page": args.contact_page,
        "actor_id": args.actor_id,
        "input_payload": payload,
        "raw_items_count": len(items),
        "matched_items_count": len(matched),
        "used_fallback_first": used_fallback,
        "selected_items": selected,
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out_name = f"apify-outreach-{normalize_domain(args.domain)}-{stamp}.json"
    out_path = Path(args.output_dir).expanduser().resolve() / out_name
    write_output(out_path, result)

    if selected:
        print(f"adapter_ok domain={normalize_domain(args.domain)} selected={len(selected)} output={out_path}")
        return 0

    print(f"adapter_no_match domain={normalize_domain(args.domain)} raw_items={len(items)} output={out_path}", file=sys.stderr)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
