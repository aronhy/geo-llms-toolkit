#!/usr/bin/env python3
"""Standalone GEO + LLMS toolkit CLI.

This script is platform-agnostic and can run on any website that exposes
public pages and (preferably) at least one sitemap endpoint.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import ssl
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT = 12
TOOL_VERSION = "0.9.0"
DEFAULT_UA = (
    "geo-llms-toolkit/0.9 standalone-cli (+https://github.com/aronhy/geo-llms-toolkit)"
)
GOOGLEBOT_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)

LOW_VALUE_PATTERNS = [
    r"/wp-admin/?",
    r"/wp-login\.php",
    r"/login/?",
    r"/register/?",
    r"/signup/?",
    r"/checkout/?",
    r"/cart/?",
    r"/my-account/?",
    r"/account/?",
    r"/password",
    r"/lost-password",
    r"/preview",
    r"/feed/?",
]

NON_OUTREACH_DOMAINS = {
    "google.com",
    "bing.com",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "linkedin.com",
    "wikipedia.org",
    "reddit.com",
    "pinterest.com",
    "tiktok.com",
}

DEFAULT_MONITOR_WEIGHTS = {
    "keyword_overlap": 0.45,
    "serp_coappear": 0.35,
    "rank_pressure": 0.20,
}

OUTREACH_STATUSES = {
    "queued",
    "sent",
    "failed",
    "skipped",
    "followup_due",
    "replied",
    "won",
    "lost",
}


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: Dict[str, str]
    body: str
    error: Optional[str] = None


@dataclass
class CheckResult:
    key: str
    category: str
    status: str
    message: str
    details: Dict[str, object]


@dataclass
class KeywordItem:
    keyword: str
    group: str
    value: float
    is_brand: bool


@dataclass
class ActionItem:
    keyword: str
    group: str
    priority: str
    priority_score: float
    impact_score: float
    effort_score: float
    target_rank: int
    best_competitor: str
    best_competitor_rank: int
    recommendation: str


@dataclass
class OutreachProspect:
    domain: str
    prospect_score: float
    opportunities: int
    average_serp_rank: float
    top_gap_keyword: str
    top_gap_group: str
    best_competitor: str
    best_competitor_rank: int
    keywords: List[str]
    outreach_angle: str
    contact_email: str
    contact_page: str
    contact_confidence: float
    email_subject: str
    email_body: str


class PageSignals(HTMLParser):
    """Extract basic GEO/SEO signals from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.h1_count = 0
        self.canonical = ""
        self.llms_link = ""
        self.meta_robots: List[str] = []
        self.meta_description = ""
        self.og: Dict[str, str] = {}
        self.twitter: Dict[str, str] = {}
        self.json_ld_blocks: List[str] = []
        self.body_excerpt = ""

        self._capture_title = False
        self._capture_jsonld = False
        self._capture_p = False
        self._buf_title: List[str] = []
        self._buf_jsonld: List[str] = []
        self._buf_p: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()

        if tag == "title":
            self._capture_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "p" and not self.body_excerpt:
            self._capture_p = True
            self._buf_p = []
        elif tag == "link":
            rel = attr.get("rel", "").lower()
            href = attr.get("href", "")
            rel_parts = {part.strip() for part in rel.split()}
            if "canonical" in rel_parts and href and not self.canonical:
                self.canonical = href
            if "llms" in rel_parts and href and not self.llms_link:
                self.llms_link = href
        elif tag == "meta":
            name = attr.get("name", "").lower()
            prop = attr.get("property", "").lower()
            content = attr.get("content", "").strip()
            if not content:
                return
            if name == "robots":
                self.meta_robots.append(content.lower())
            if name == "description" and not self.meta_description:
                self.meta_description = content
            if prop.startswith("og:"):
                self.og[prop] = content
            if name.startswith("twitter:"):
                self.twitter[name] = content
        elif tag == "script":
            script_type = attr.get("type", "").lower().strip()
            if script_type == "application/ld+json":
                self._capture_jsonld = True
                self._buf_jsonld = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._capture_title = False
            if not self.title:
                self.title = unescape("".join(self._buf_title)).strip()
            self._buf_title = []
        elif tag == "script" and self._capture_jsonld:
            block = "".join(self._buf_jsonld).strip()
            if block:
                self.json_ld_blocks.append(block)
            self._capture_jsonld = False
            self._buf_jsonld = []
        elif tag == "p" and self._capture_p:
            excerpt = " ".join("".join(self._buf_p).split())
            if excerpt and not self.body_excerpt:
                self.body_excerpt = unescape(excerpt)[:280]
            self._capture_p = False
            self._buf_p = []

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._buf_title.append(data)
        if self._capture_jsonld:
            self._buf_jsonld.append(data)
        if self._capture_p:
            self._buf_p.append(data)


class BingResultParser(HTMLParser):
    """Parse basic organic result URLs from Bing SERP HTML."""

    def __init__(self, max_results: int) -> None:
        super().__init__()
        self.max_results = max_results
        self.urls: List[str] = []
        self._in_algo = False
        self._algo_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "li":
            cls = attr.get("class", "")
            cls_parts = {p.strip().lower() for p in cls.split()}
            if "b_algo" in cls_parts:
                self._in_algo = True
                self._algo_depth = 1
                return
        if self._in_algo:
            self._algo_depth += 1
            if tag == "a":
                href = attr.get("href", "").strip()
                if href.startswith("http://") or href.startswith("https://"):
                    if href not in self.urls:
                        self.urls.append(href)
                        if len(self.urls) >= self.max_results:
                            self._in_algo = False

    def handle_endtag(self, tag: str) -> None:
        if self._in_algo:
            self._algo_depth -= 1
            if self._algo_depth <= 0:
                self._in_algo = False


def normalize_base_url(target: str) -> str:
    value = target.strip()
    if not value:
        raise ValueError("target is required")
    if not re.match(r"^https?://", value, flags=re.I):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError(f"invalid target: {target}")
    base = f"{parsed.scheme}://{parsed.netloc}"
    return base.rstrip("/")


def safe_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path or "/"


def localname(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def parse_content_type(headers: Dict[str, str]) -> str:
    return headers.get("content-type", "").lower()


def fetch_url(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_UA,
    max_bytes: int = 1_000_000,
) -> FetchResult:
    req = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml,text/plain,*/*",
        },
    )
    ctx = ssl.create_default_context()

    try:
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
            ctype = headers.get("content-type", "")
            charset_match = re.search(r"charset=([^\s;]+)", ctype, flags=re.I)
            charset = charset_match.group(1).strip("\"'") if charset_match else "utf-8"
            body = raw.decode(charset, errors="replace")
            final_url = getattr(resp, "geturl", lambda: url)()
            return FetchResult(url=url, final_url=final_url, status=code, headers=headers, body=body)
    except HTTPError as e:
        headers = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        body = ""
        try:
            body = e.read(120_000).decode("utf-8", errors="replace")
        except Exception:
            pass
        return FetchResult(
            url=url,
            final_url=url,
            status=e.code,
            headers=headers,
            body=body,
            error=f"HTTPError {e.code}",
        )
    except URLError as e:
        return FetchResult(
            url=url,
            final_url=url,
            status=0,
            headers={},
            body="",
            error=f"URLError {e.reason}",
        )
    except Exception as e:  # pragma: no cover - safety net
        return FetchResult(
            url=url,
            final_url=url,
            status=0,
            headers={},
            body="",
            error=str(e),
        )


def parse_html_signals(html: str) -> PageSignals:
    parser = PageSignals()
    parser.feed(html or "")
    parser.close()
    return parser


def normalize_domain(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.netloc or parsed.path).lower().strip()
    host = re.sub(r":\d+$", "", host)
    if host.startswith("www."):
        host = host[4:]
    return host


def read_keywords_file(path: Path, brand_tokens: Sequence[str], max_keywords: int) -> List[KeywordItem]:
    if not path.exists():
        raise ValueError(f"keywords file does not exist: {path}")

    rows: List[KeywordItem] = []
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        delimiter = "," if suffix == ".csv" else "\t"
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            if not reader.fieldnames or "keyword" not in [h.lower() for h in reader.fieldnames]:
                raise ValueError("keywords csv/tsv requires a 'keyword' column")
            normalized_map = {h.lower(): h for h in reader.fieldnames}
            k_col = normalized_map["keyword"]
            g_col = normalized_map.get("group")
            v_col = normalized_map.get("value")
            for row in reader:
                kw = (row.get(k_col) or "").strip()
                if not kw:
                    continue
                grp = (row.get(g_col) or "default").strip() if g_col else "default"
                raw_value = (row.get(v_col) or "").strip() if v_col else ""
                try:
                    value = float(raw_value) if raw_value else 1.0
                except ValueError:
                    value = 1.0
                rows.append(
                    KeywordItem(
                        keyword=kw,
                        group=grp or "default",
                        value=value,
                        is_brand=is_brand_keyword(kw, brand_tokens),
                    )
                )
                if len(rows) >= max_keywords:
                    break
    else:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                kw = line.strip()
                if not kw or kw.startswith("#"):
                    continue
                rows.append(
                    KeywordItem(
                        keyword=kw,
                        group="default",
                        value=1.0,
                        is_brand=is_brand_keyword(kw, brand_tokens),
                    )
                )
                if len(rows) >= max_keywords:
                    break

    if not rows:
        raise ValueError("no keywords loaded from file")
    return rows


def is_brand_keyword(keyword: str, brand_tokens: Sequence[str]) -> bool:
    kw = keyword.lower()
    return any(token and token in kw for token in brand_tokens)


def fetch_bing_results(keyword: str, depth: int, timeout: int, user_agent: str) -> List[str]:
    url = f"https://www.bing.com/search?q={quote_plus(keyword)}&count={max(10, min(depth, 50))}"
    res = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=2_000_000)
    if res.status != 200:
        return []
    if "html" not in parse_content_type(res.headers):
        return []
    parser = BingResultParser(max_results=depth)
    parser.feed(res.body or "")
    parser.close()
    cleaned: List[str] = []
    for raw in parser.urls:
        p = urlparse(raw)
        host = p.netloc.lower()
        if not host or host.endswith("bing.com"):
            continue
        cleaned.append(raw)
        if len(cleaned) >= depth:
            break
    return cleaned


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def classify_competitor_tier(score: float, p50: float, p80: float) -> str:
    if score <= 0:
        return "peripheral"
    if score >= p80:
        return "direct"
    if score >= p50:
        return "potential"
    return "peripheral"


def calc_priority(impact_score: float, effort_score: float) -> Tuple[str, float]:
    priority_score = (impact_score * 0.65) + ((100.0 - effort_score) * 0.35)
    if priority_score >= 75:
        label = "P0"
    elif priority_score >= 55:
        label = "P1"
    else:
        label = "P2"
    return label, round(priority_score, 2)


def load_monitor_weights(path: Optional[Path]) -> Dict[str, float]:
    if not path:
        return dict(DEFAULT_MONITOR_WEIGHTS)
    if not path.exists():
        raise ValueError(f"weights file not found: {path}")
    data = read_json_file(path)
    weights = dict(DEFAULT_MONITOR_WEIGHTS)
    for key in list(weights.keys()):
        raw = data.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        weights[key] = value

    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_MONITOR_WEIGHTS)
    normalized = {k: v / total for k, v in weights.items()}
    return normalized


def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    pattern = r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}"
    found = re.findall(pattern, text, flags=re.I)
    cleaned = []
    for email in found:
        val = email.strip().strip(".,;:()[]{}<>").lower()
        if val.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg")):
            continue
        if val not in cleaned:
            cleaned.append(val)
    return cleaned


def discover_contact_info(domain: str, timeout: int, user_agent: str) -> Dict[str, object]:
    base = f"https://{domain}"
    pages_to_check = [f"{base}/", f"{base}/contact", f"{base}/about", f"{base}/write-for-us"]
    checked: List[str] = []
    emails: List[str] = []
    contact_page = ""

    for url in pages_to_check:
        res = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=1_000_000)
        checked.append(url)
        if res.status != 200:
            continue
        emails_found = extract_emails(res.body)
        for e in emails_found:
            if e not in emails:
                emails.append(e)
        if not contact_page and re.search(r"/contact|/write", safe_path(url), flags=re.I):
            contact_page = url
        if emails:
            break

    confidence = 0.0
    if emails:
        confidence = 0.8
    elif len(checked) > 0:
        confidence = 0.2
    return {
        "email": emails[0] if emails else "",
        "contact_page": contact_page,
        "confidence": round(confidence, 2),
        "checked_urls": checked,
    }


def verify_backlink_presence(domain: str, pitch_url: str, timeout: int, user_agent: str) -> Dict[str, object]:
    base = f"https://{domain}"
    pitch_host = normalize_domain(pitch_url)
    pages = [f"{base}/", f"{base}/resources", f"{base}/links", f"{base}/blog", f"{base}/sitemap.xml"]
    checked = []
    matched_urls: List[str] = []
    for url in pages:
        res = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=1_500_000)
        checked.append({"url": url, "status": res.status})
        if res.status != 200:
            continue
        body = (res.body or "").lower()
        if pitch_url.lower() in body or (pitch_host and pitch_host in body):
            matched_urls.append(url)

    return {
        "found": len(matched_urls) > 0,
        "matched_urls": matched_urls,
        "checked": checked,
    }


def load_monitor_diff(current_path: Path, previous_path: Path) -> Dict[str, object]:
    current = load_monitor_report(current_path)
    previous = load_monitor_report(previous_path)

    cur_summary = current.get("summary", {})
    prev_summary = previous.get("summary", {})
    cur_competitors = {c["domain"]: c for c in current.get("competitors", []) if isinstance(c, dict) and c.get("domain")}
    prev_competitors = {c["domain"]: c for c in previous.get("competitors", []) if isinstance(c, dict) and c.get("domain")}

    domains_union = sorted(set(cur_competitors.keys()) | set(prev_competitors.keys()))
    competitor_changes = []
    for d in domains_union:
        cur = cur_competitors.get(d, {})
        prev = prev_competitors.get(d, {})
        cur_score = float(cur.get("score") or 0.0)
        prev_score = float(prev.get("score") or 0.0)
        competitor_changes.append(
            {
                "domain": d,
                "current_score": round(cur_score, 2),
                "previous_score": round(prev_score, 2),
                "delta_score": round(cur_score - prev_score, 2),
                "current_tier": cur.get("tier", "none"),
                "previous_tier": prev.get("tier", "none"),
            }
        )

    cur_actions = {a["keyword"]: a for a in current.get("actions", []) if isinstance(a, dict) and a.get("keyword")}
    prev_actions = {a["keyword"]: a for a in previous.get("actions", []) if isinstance(a, dict) and a.get("keyword")}
    added_action_keywords = sorted(set(cur_actions.keys()) - set(prev_actions.keys()))
    removed_action_keywords = sorted(set(prev_actions.keys()) - set(cur_actions.keys()))

    return {
        "meta": {
            "generated_at_utc": now_utc(),
            "current_report": str(current_path),
            "previous_report": str(previous_path),
            "target": current.get("meta", {}).get("target"),
        },
        "summary": {
            "keywords_total_delta": int(cur_summary.get("keywords_total", 0)) - int(prev_summary.get("keywords_total", 0)),
            "keywords_with_serp_results_delta": int(cur_summary.get("keywords_with_serp_results", 0))
            - int(prev_summary.get("keywords_with_serp_results", 0)),
            "competitors_tracked_delta": int(cur_summary.get("competitors_tracked", 0))
            - int(prev_summary.get("competitors_tracked", 0)),
            "actions_generated_delta": int(cur_summary.get("actions_generated", 0))
            - int(prev_summary.get("actions_generated", 0)),
        },
        "competitor_changes": sorted(competitor_changes, key=lambda x: x["delta_score"], reverse=True),
        "actions": {
            "added_keywords": added_action_keywords,
            "removed_keywords": removed_action_keywords,
        },
    }


def domain_matches_any(domain: str, patterns: Sequence[str]) -> bool:
    for p in patterns:
        normalized = normalize_domain(p)
        if not normalized:
            continue
        if domain == normalized or domain.endswith(f".{normalized}"):
            return True
    return False


def load_monitor_report(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise ValueError(f"monitor report file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid monitor report: expected JSON object")
    required = ["meta", "summary", "competitors", "actions", "keywords"]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"invalid monitor report: missing keys {', '.join(missing)}")
    return data


def build_outreach_plan(
    monitor_report: Dict[str, object],
    pitch_url: str,
    site_name: str,
    offer: str,
    max_prospects: int,
    min_prospect_score: float,
    min_opportunities: int,
    exclude_domains: Sequence[str],
    enrich_contacts: bool,
    timeout: int,
    user_agent: str,
) -> Dict[str, object]:
    meta = monitor_report.get("meta", {})
    target_domain = normalize_domain(str(meta.get("target_domain") or meta.get("target") or ""))
    if not target_domain:
        raise ValueError("monitor report does not contain target domain")

    competitors = monitor_report.get("competitors", [])
    actions = monitor_report.get("actions", [])
    keywords = monitor_report.get("keywords", [])
    if not isinstance(competitors, list) or not isinstance(actions, list) or not isinstance(keywords, list):
        raise ValueError("monitor report fields have unexpected format")

    competitor_set = {normalize_domain(str(c.get("domain", ""))) for c in competitors if isinstance(c, dict)}
    competitor_set = {d for d in competitor_set if d}
    blocked_patterns = list(NON_OUTREACH_DOMAINS) + list(exclude_domains)

    action_map: Dict[str, Dict[str, object]] = {}
    for action in actions:
        if isinstance(action, dict) and action.get("keyword"):
            action_map[str(action["keyword"])] = action

    domain_stats: Dict[str, Dict[str, object]] = {}
    for kw in keywords:
        if not isinstance(kw, dict):
            continue
        if bool(kw.get("is_brand")):
            continue

        target_rank = int(kw.get("target_rank") or 0)
        if target_rank and target_rank <= 3:
            continue

        top_domains = kw.get("top_domains", [])
        if not isinstance(top_domains, list):
            continue
        keyword = str(kw.get("keyword") or "")
        group = str(kw.get("group") or "default")
        value = float(kw.get("value") or 1.0)
        action = action_map.get(keyword, {})
        best_comp = str(action.get("best_competitor") or "")
        best_comp_rank = int(action.get("best_competitor_rank") or 0)

        for rank, raw_domain in enumerate(top_domains, start=1):
            domain = normalize_domain(str(raw_domain))
            if not domain:
                continue
            if domain == target_domain:
                continue
            if domain in competitor_set:
                continue
            if domain_matches_any(domain, blocked_patterns):
                continue

            gap_bonus = 6.0 if target_rank == 0 else min(6.0, max(0.0, (target_rank - 3) * 0.8))
            rank_score = max(0.5, (11.0 - min(rank, 10)))
            score = (rank_score + gap_bonus) * value

            info = domain_stats.get(domain)
            if not info:
                info = {
                    "score": 0.0,
                    "hits": 0,
                    "rank_sum": 0.0,
                    "keywords": [],
                    "top_gap_keyword": keyword,
                    "top_gap_group": group,
                    "top_gap_weight": score,
                    "best_competitor": best_comp,
                    "best_competitor_rank": best_comp_rank,
                }
                domain_stats[domain] = info

            info["score"] = float(info["score"]) + score
            info["hits"] = int(info["hits"]) + 1
            info["rank_sum"] = float(info["rank_sum"]) + rank
            info["keywords"].append(keyword)

            if score > float(info["top_gap_weight"]):
                info["top_gap_weight"] = score
                info["top_gap_keyword"] = keyword
                info["top_gap_group"] = group
                info["best_competitor"] = best_comp
                info["best_competitor_rank"] = best_comp_rank

    prospects: List[OutreachProspect] = []
    for domain, info in domain_stats.items():
        hits = int(info["hits"])
        score = round(float(info["score"]), 2)
        if hits < min_opportunities or score < min_prospect_score:
            continue

        unique_keywords = sorted(set([str(k) for k in info["keywords"]]))[:12]
        gap_keyword = str(info["top_gap_keyword"])
        gap_group = str(info["top_gap_group"])
        best_comp = str(info["best_competitor"])
        best_comp_rank = int(info["best_competitor_rank"])

        angle = f"Gap keyword '{gap_keyword}' where your site underperforms."
        subject = f"Resource suggestion for {gap_keyword}"
        contact_email = ""
        contact_page = ""
        contact_confidence = 0.0
        if enrich_contacts:
            contact_info = discover_contact_info(domain, timeout=timeout, user_agent=user_agent)
            contact_email = str(contact_info.get("email") or "")
            contact_page = str(contact_info.get("contact_page") or "")
            contact_confidence = float(contact_info.get("confidence") or 0.0)

        body = textwrap.dedent(
            f"""\
            Hi [First Name],

            I was reading your content on {gap_keyword} and found it very useful.
            We recently published a practical resource that may complement your page:
            {pitch_url}

            Offer: {offer}
            If useful for your readers, feel free to include it as a reference.

            Best,
            {site_name}
            """
        ).strip()

        prospects.append(
            OutreachProspect(
                domain=domain,
                prospect_score=score,
                opportunities=hits,
                average_serp_rank=round(float(info["rank_sum"]) / max(1, hits), 2),
                top_gap_keyword=gap_keyword,
                top_gap_group=gap_group,
                best_competitor=best_comp,
                best_competitor_rank=best_comp_rank,
                keywords=unique_keywords,
                outreach_angle=angle,
                contact_email=contact_email,
                contact_page=contact_page,
                contact_confidence=round(contact_confidence, 2),
                email_subject=subject,
                email_body=body,
            )
        )

    prospects.sort(key=lambda p: p.prospect_score, reverse=True)
    prospects = prospects[:max_prospects]

    return {
        "meta": {
            "generated_at_utc": now_utc(),
            "target_domain": target_domain,
            "pitch_url": pitch_url,
            "site_name": site_name,
            "offer": offer,
            "source_provider": meta.get("provider"),
            "source_report_generated_at_utc": meta.get("generated_at_utc"),
        },
        "summary": {
            "prospects_total": len(prospects),
            "min_prospect_score": min_prospect_score,
            "min_opportunities": min_opportunities,
        },
        "prospects": [asdict(p) for p in prospects],
    }


def build_campaign_from_plan(plan: Dict[str, object]) -> Dict[str, object]:
    campaign_id = datetime.now(timezone.utc).strftime("cmp-%Y%m%dT%H%M%S%fZ")
    prospects = []
    for p in plan.get("prospects", []):
        if not isinstance(p, dict):
            continue
        item = dict(p)
        item["status"] = "queued"
        item["attempts"] = 0
        item["last_attempt_at_utc"] = ""
        item["sent_at_utc"] = ""
        item["followup_due_at_utc"] = ""
        item["followup_subject"] = ""
        item["followup_body"] = ""
        item["followup_count"] = 0
        item["reply_at_utc"] = ""
        item["won_at_utc"] = ""
        item["lost_at_utc"] = ""
        item["verified_link_url"] = ""
        item["last_error"] = ""
        prospects.append(item)

    return {
        "meta": {
            "campaign_id": campaign_id,
            "created_at_utc": now_utc(),
            "last_run_at_utc": "",
            "target_domain": plan.get("meta", {}).get("target_domain"),
            "pitch_url": plan.get("meta", {}).get("pitch_url"),
            "site_name": plan.get("meta", {}).get("site_name"),
            "offer": plan.get("meta", {}).get("offer"),
            "source_provider": plan.get("meta", {}).get("source_provider"),
            "source_report_generated_at_utc": plan.get("meta", {}).get("source_report_generated_at_utc"),
        },
        "summary": {
            "prospects_total": len(prospects),
            "queued": len(prospects),
            "sent": 0,
            "failed": 0,
            "skipped": 0,
        },
        "prospects": prospects,
        "runs": [],
    }


def load_campaign(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise ValueError(f"campaign file not found: {path}")
    data = read_json_file(path)
    if not isinstance(data.get("prospects"), list):
        raise ValueError("invalid campaign file: prospects is missing")
    if not isinstance(data.get("runs"), list):
        data["runs"] = []
    return data


def refresh_campaign_summary(campaign: Dict[str, object]) -> None:
    prospects = campaign.get("prospects", [])
    queued = sent = failed = skipped = followup_due = replied = won = lost = 0
    for p in prospects:
        if not isinstance(p, dict):
            continue
        status = str(p.get("status") or "queued")
        if status == "sent":
            sent += 1
        elif status == "followup_due":
            followup_due += 1
        elif status == "replied":
            replied += 1
        elif status == "won":
            won += 1
        elif status == "lost":
            lost += 1
        elif status == "failed":
            failed += 1
        elif status == "skipped":
            skipped += 1
        else:
            queued += 1
    campaign["summary"] = {
        "prospects_total": len([p for p in prospects if isinstance(p, dict)]),
        "queued": queued,
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "followup_due": followup_due,
        "replied": replied,
        "won": won,
        "lost": lost,
    }


def update_state_sent(
    state: Dict[str, object],
    target_domain: str,
    domain: str,
    campaign_id: str,
    pitch_url: str,
) -> None:
    records = state.setdefault("records", [])
    if not isinstance(records, list):
        state["records"] = []
        records = state["records"]
    now = now_utc()
    for item in records:
        if not isinstance(item, dict):
            continue
        if item.get("target_domain") == target_domain and item.get("domain") == domain:
            item["last_sent_at_utc"] = now
            item["campaign_id"] = campaign_id
            item["pitch_url"] = pitch_url
            return
    records.append(
        {
            "target_domain": target_domain,
            "domain": domain,
            "last_sent_at_utc": now,
            "campaign_id": campaign_id,
            "pitch_url": pitch_url,
        }
    )


def was_sent_recently(
    state: Dict[str, object],
    target_domain: str,
    domain: str,
    cooldown_days: int,
) -> bool:
    records = state.get("records", [])
    if not isinstance(records, list):
        return False
    now = datetime.now(timezone.utc)
    for item in records:
        if not isinstance(item, dict):
            continue
        if item.get("target_domain") != target_domain or item.get("domain") != domain:
            continue
        sent_at = parse_utc(str(item.get("last_sent_at_utc") or ""))
        if not sent_at:
            continue
        age_days = (now - sent_at).total_seconds() / 86400.0
        if age_days < cooldown_days:
            return True
    return False


def execute_webhook(
    webhook_url: str,
    webhook_token: str,
    payload: Dict[str, object],
    timeout: int,
) -> Tuple[bool, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if webhook_token:
        headers["Authorization"] = f"Bearer {webhook_token}"
    req = Request(webhook_url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(4000).decode("utf-8", errors="replace")
            ok = 200 <= int(code) < 300
            return ok, f"HTTP {code}: {raw[:240]}"
    except Exception as e:
        return False, str(e)


def execute_command(command_template: str, payload: Dict[str, object], timeout: int) -> Tuple[bool, str]:
    raw_values = {
        "domain": str(payload.get("domain") or ""),
        "keyword": str(payload.get("top_gap_keyword") or ""),
        "pitch_url": str(payload.get("pitch_url") or ""),
        "site_name": str(payload.get("site_name") or ""),
        "email_subject": str(payload.get("email_subject") or ""),
        "contact_email": str(payload.get("contact_email") or ""),
        "contact_page": str(payload.get("contact_page") or ""),
    }
    values = dict(raw_values)
    for key, value in raw_values.items():
        values[f"{key}_q"] = shlex.quote(value)
    try:
        command = command_template.format_map(values)
    except KeyError as e:
        return False, f"missing template variable: {e}"
    parts = shlex.split(command)
    if not parts:
        return False, "empty command"
    try:
        proc = subprocess.run(parts, capture_output=True, text=True, timeout=timeout, check=False)
        out = (proc.stdout or proc.stderr or "").strip()
        ok = proc.returncode == 0
        return ok, out[:240]
    except Exception as e:
        return False, str(e)


def execute_apify_adapter(
    payload: Dict[str, object],
    timeout: int,
    apify_token: str,
    actor_id: str,
    adapter_path: str,
    output_dir: str,
    allow_fallback_first: bool,
) -> Tuple[bool, str]:
    adapter = Path(adapter_path).expanduser().resolve()
    if not adapter.exists():
        return False, f"adapter script not found: {adapter}"

    cmd = [
        sys.executable,
        str(adapter),
        "--domain",
        str(payload.get("domain") or ""),
        "--keyword",
        str(payload.get("top_gap_keyword") or ""),
        "--pitch-url",
        str(payload.get("pitch_url") or ""),
        "--site-name",
        str(payload.get("site_name") or ""),
        "--contact-email",
        str(payload.get("contact_email") or ""),
        "--contact-page",
        str(payload.get("contact_page") or ""),
        "--actor-id",
        actor_id,
        "--timeout",
        str(max(10, timeout)),
        "--output-dir",
        output_dir,
    ]
    if apify_token:
        cmd.extend(["--apify-token", apify_token])
    if allow_fallback_first:
        cmd.append("--allow-fallback-first")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(20, timeout + 10), check=False)
        out = (proc.stdout or proc.stderr or "").strip()
        ok = proc.returncode == 0
        return ok, out[:280]
    except Exception as e:
        return False, str(e)


def run_outreach_campaign(
    campaign: Dict[str, object],
    provider: str,
    only_new: bool,
    cooldown_days: int,
    state: Dict[str, object],
    webhook_url: str,
    webhook_token: str,
    command_template: str,
    timeout: int,
    followup_days: int,
    apify_token: str,
    apify_actor_id: str,
    apify_adapter_path: str,
    apify_output_dir: str,
    apify_allow_fallback_first: bool,
    run_followup_due: bool,
) -> Dict[str, object]:
    prospects = campaign.get("prospects", [])
    if not isinstance(prospects, list):
        raise ValueError("invalid campaign: prospects should be list")

    target_domain = str(campaign.get("meta", {}).get("target_domain") or "")
    pitch_url = str(campaign.get("meta", {}).get("pitch_url") or "")
    site_name = str(campaign.get("meta", {}).get("site_name") or target_domain)
    campaign_id = str(campaign.get("meta", {}).get("campaign_id") or "")
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S%fZ")
    started_at = now_utc()

    sent = failed = skipped = 0
    for p in prospects:
        if not isinstance(p, dict):
            continue

        domain = normalize_domain(str(p.get("domain") or ""))
        if not domain:
            continue
        current_status = str(p.get("status") or "queued")
        is_followup_item = current_status == "followup_due"

        if is_followup_item and not run_followup_due:
            skipped += 1
            continue

        if only_new and (not is_followup_item) and was_sent_recently(state, target_domain, domain, cooldown_days):
            skipped += 1
            if str(p.get("status") or "") != "sent":
                p["status"] = "skipped"
                p["last_error"] = f"cooldown<{cooldown_days}d"
            continue

        payload = {
            "campaign_id": campaign_id,
            "target_domain": target_domain,
            "pitch_url": pitch_url,
            "site_name": site_name,
            "domain": domain,
            "top_gap_keyword": p.get("top_gap_keyword"),
            "top_gap_group": p.get("top_gap_group"),
            "email_subject": p.get("followup_subject") if is_followup_item and p.get("followup_subject") else p.get("email_subject"),
            "email_body": p.get("followup_body") if is_followup_item and p.get("followup_body") else p.get("email_body"),
            "contact_email": p.get("contact_email"),
            "contact_page": p.get("contact_page"),
            "keywords": p.get("keywords"),
            "prospect_score": p.get("prospect_score"),
            "opportunities": p.get("opportunities"),
        }

        ok = False
        detail = ""
        if provider == "dry-run":
            ok = True
            detail = "dry-run"
        elif provider == "webhook":
            if not webhook_url:
                raise ValueError("webhook-url is required when provider=webhook")
            ok, detail = execute_webhook(webhook_url, webhook_token, payload, timeout)
        elif provider == "command":
            if not command_template:
                raise ValueError("command-template is required when provider=command")
            ok, detail = execute_command(command_template, payload, timeout)
        elif provider == "apify":
            ok, detail = execute_apify_adapter(
                payload=payload,
                timeout=timeout,
                apify_token=apify_token,
                actor_id=apify_actor_id,
                adapter_path=apify_adapter_path,
                output_dir=apify_output_dir,
                allow_fallback_first=apify_allow_fallback_first,
            )
        else:
            raise ValueError(f"unsupported provider: {provider}")

        p["attempts"] = int(p.get("attempts") or 0) + 1
        p["last_attempt_at_utc"] = now_utc()
        if ok:
            p["status"] = "sent"
            p["sent_at_utc"] = now_utc()
            followup_dt = datetime.now(timezone.utc).timestamp() + (max(1, followup_days) * 86400)
            p["followup_due_at_utc"] = datetime.fromtimestamp(followup_dt, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
            if is_followup_item:
                p["followup_count"] = int(p.get("followup_count") or 0) + 1
            p["last_error"] = ""
            sent += 1
            update_state_sent(state, target_domain, domain, campaign_id, pitch_url)
        else:
            p["status"] = "failed"
            p["last_error"] = detail
            failed += 1

    run = {
        "run_id": run_id,
        "provider": provider,
        "started_at_utc": started_at,
        "finished_at_utc": now_utc(),
        "only_new": only_new,
        "cooldown_days": cooldown_days,
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
    }
    runs = campaign.setdefault("runs", [])
    if isinstance(runs, list):
        runs.append(run)
    campaign_meta = campaign.setdefault("meta", {})
    if isinstance(campaign_meta, dict):
        campaign_meta["last_run_at_utc"] = run["finished_at_utc"]
    refresh_campaign_summary(campaign)
    return run


def update_campaign_prospect_status(
    campaign: Dict[str, object],
    domain: str,
    new_status: str,
    note: str,
) -> bool:
    status = new_status.strip().lower()
    if status not in OUTREACH_STATUSES:
        raise ValueError(f"invalid status: {new_status}")
    target_domain = normalize_domain(domain)
    prospects = campaign.get("prospects", [])
    if not isinstance(prospects, list):
        return False
    now = now_utc()
    for p in prospects:
        if not isinstance(p, dict):
            continue
        if normalize_domain(str(p.get("domain") or "")) != target_domain:
            continue
        p["status"] = status
        if status == "replied":
            p["reply_at_utc"] = now
        elif status == "won":
            p["won_at_utc"] = now
        elif status == "lost":
            p["lost_at_utc"] = now
        p["last_error"] = note
        refresh_campaign_summary(campaign)
        runs = campaign.setdefault("runs", [])
        if isinstance(runs, list):
            runs.append(
                {
                    "run_id": datetime.now(timezone.utc).strftime("run-update-%Y%m%dT%H%M%S%fZ"),
                    "provider": "manual-update",
                    "started_at_utc": now,
                    "finished_at_utc": now,
                    "sent": 0,
                    "failed": 0,
                    "skipped": 0,
                    "updated_domain": target_domain,
                    "new_status": status,
                }
            )
        meta = campaign.setdefault("meta", {})
        if isinstance(meta, dict):
            meta["last_run_at_utc"] = now
        return True
    return False


def verify_campaign_backlinks(
    campaign: Dict[str, object],
    timeout: int,
    user_agent: str,
    followup_days: int,
) -> Dict[str, int]:
    prospects = campaign.get("prospects", [])
    if not isinstance(prospects, list):
        raise ValueError("invalid campaign: prospects list missing")
    campaign_meta = campaign.get("meta", {})
    pitch_url = str(campaign_meta.get("pitch_url") or "")
    now = datetime.now(timezone.utc)

    checked = won = followup_due = unchanged = 0
    for p in prospects:
        if not isinstance(p, dict):
            continue
        domain = normalize_domain(str(p.get("domain") or ""))
        status = str(p.get("status") or "queued")
        if not domain or status in {"won", "lost"}:
            continue

        checked += 1
        res = verify_backlink_presence(domain, pitch_url, timeout=timeout, user_agent=user_agent)
        if bool(res.get("found")):
            p["status"] = "won"
            p["won_at_utc"] = now_utc()
            matched = res.get("matched_urls") or []
            p["verified_link_url"] = matched[0] if isinstance(matched, list) and matched else ""
            won += 1
            continue

        sent_at = parse_utc(str(p.get("sent_at_utc") or ""))
        if sent_at and status in {"sent", "followup_due"}:
            age_days = (now - sent_at).total_seconds() / 86400.0
            if age_days >= max(1, followup_days):
                p["status"] = "followup_due"
                subject, body = build_followup_content(p, campaign_meta if isinstance(campaign_meta, dict) else {})
                p["followup_subject"] = subject
                p["followup_body"] = body
                if not p.get("followup_due_at_utc"):
                    p["followup_due_at_utc"] = now_utc()
                followup_due += 1
                continue
        unchanged += 1

    refresh_campaign_summary(campaign)
    runs = campaign.setdefault("runs", [])
    now_s = now_utc()
    if isinstance(runs, list):
        runs.append(
            {
                "run_id": datetime.now(timezone.utc).strftime("run-verify-%Y%m%dT%H%M%S%fZ"),
                "provider": "verify",
                "started_at_utc": now_s,
                "finished_at_utc": now_s,
                "checked": checked,
                "won": won,
                "followup_due": followup_due,
                "unchanged": unchanged,
            }
        )
    meta = campaign.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["last_run_at_utc"] = now_s
    return {"checked": checked, "won": won, "followup_due": followup_due, "unchanged": unchanged}


def parse_jsonld_blocks(blocks: Iterable[str]) -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    for raw in blocks:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    items.append(item)
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                for item in data["@graph"]:
                    if isinstance(item, dict):
                        items.append(item)
            items.append(data)
    return items


def type_matches(schema: Dict[str, object], target: str) -> bool:
    value = schema.get("@type")
    if isinstance(value, str):
        return value.lower() == target.lower()
    if isinstance(value, list):
        return any(isinstance(v, str) and v.lower() == target.lower() for v in value)
    return False


def parse_sitemap_xml(xml_text: str) -> Tuple[str, List[str]]:
    root = ET.fromstring(xml_text.strip())
    root_name = localname(root.tag).lower()
    locs: List[str] = []

    if root_name == "urlset":
        for node in root.iter():
            if localname(node.tag).lower() == "loc" and node.text:
                locs.append(node.text.strip())
        return "urlset", locs

    if root_name == "sitemapindex":
        for node in root.iter():
            if localname(node.tag).lower() == "loc" and node.text:
                locs.append(node.text.strip())
        return "sitemapindex", locs

    raise ValueError("unsupported sitemap format")


def discover_sitemaps(base_url: str, timeout: int, user_agent: str) -> Tuple[List[str], List[str]]:
    candidates = [
        f"{base_url}/sitemap.xml",
        f"{base_url}/sitemap_index.xml",
        f"{base_url}/wp-sitemap.xml",
    ]
    alive: List[str] = []
    inspected: List[str] = []
    for url in candidates:
        res = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=800_000)
        inspected.append(url)
        if res.status == 200 and "xml" in parse_content_type(res.headers):
            alive.append(url)
    return alive, inspected


def collect_urls_from_sitemaps(
    base_url: str,
    timeout: int,
    user_agent: str,
    max_sitemaps: int = 30,
    max_urls: int = 600,
) -> List[str]:
    sitemap_urls, _ = discover_sitemaps(base_url, timeout, user_agent)
    if not sitemap_urls:
        return []

    queue = list(sitemap_urls)
    visited = set()
    urls: List[str] = []
    base_host = urlparse(base_url).netloc.lower()

    while queue and len(visited) < max_sitemaps and len(urls) < max_urls:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        res = fetch_url(current, timeout=timeout, user_agent=user_agent, max_bytes=2_000_000)
        if res.status != 200:
            continue
        ctype = parse_content_type(res.headers)
        if "xml" not in ctype:
            continue
        try:
            kind, locs = parse_sitemap_xml(res.body)
        except Exception:
            continue
        if kind == "sitemapindex":
            for loc in locs:
                if loc not in visited and loc not in queue:
                    queue.append(loc)
        else:
            for loc in locs:
                p = urlparse(loc)
                if p.scheme not in {"http", "https"}:
                    continue
                if p.netloc.lower() != base_host:
                    continue
                if loc not in urls:
                    urls.append(loc)
                    if len(urls) >= max_urls:
                        break
    return urls


def is_low_value_url(url: str, extra_patterns: Optional[List[str]] = None) -> bool:
    path = safe_path(url).lower()
    full = url.lower()
    patterns = LOW_VALUE_PATTERNS + (extra_patterns or [])
    for pattern in patterns:
        if re.search(pattern, path) or re.search(pattern, full):
            return True
    return False


def pick_first_article_url(urls: List[str], base_url: str) -> Optional[str]:
    for u in urls:
        if u.rstrip("/") == base_url.rstrip("/"):
            continue
        if is_low_value_url(u):
            continue
        if len((urlparse(u).path or "").strip("/")) < 2:
            continue
        return u
    return None


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def parse_utc(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_json_file(path: Path) -> Dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return data


def load_or_create_state(path: Path) -> Dict[str, object]:
    if path.exists():
        state = read_json_file(path)
        records = state.get("records")
        if isinstance(records, list):
            return {"records": records}
    return {"records": []}


def save_state(path: Path, state: Dict[str, object]) -> None:
    write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def endpoint_check(base_url: str, path: str, timeout: int, user_agent: str) -> Tuple[FetchResult, bool]:
    url = f"{base_url}{path}"
    res = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=120_000)
    ok = res.status == 200
    return res, ok


def run_scan(base_url: str, timeout: int, user_agent: str, max_urls: int) -> Dict[str, object]:
    checks: List[CheckResult] = []

    robots_res, robots_ok = endpoint_check(base_url, "/robots.txt", timeout, user_agent)
    robots_ct = parse_content_type(robots_res.headers)
    if robots_ok:
        if "text/plain" in robots_ct:
            status = "pass"
            msg = "robots.txt returns 200 and text/plain."
        else:
            status = "warn"
            msg = "robots.txt returns 200 but Content-Type is not text/plain."
    else:
        status = "fail"
        msg = "robots.txt is not reachable with status 200."
    checks.append(
        CheckResult(
            key="robots_endpoint",
            category="endpoint",
            status=status,
            message=msg,
            details={"url": f"{base_url}/robots.txt", "status": robots_res.status, "content_type": robots_ct},
        )
    )

    sitemap_paths = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"]
    for path in sitemap_paths:
        res, ok = endpoint_check(base_url, path, timeout, user_agent)
        ctype = parse_content_type(res.headers)
        if ok and "xml" in ctype:
            state = "pass"
            msg = f"{path} returns 200 XML."
        elif ok:
            state = "warn"
            msg = f"{path} returns 200 but Content-Type is not XML."
        else:
            state = "fail"
            msg = f"{path} is not reachable with status 200."
        checks.append(
            CheckResult(
                key=f"sitemap_{path.strip('/').replace('.', '_')}",
                category="endpoint",
                status=state,
                message=msg,
                details={"url": f"{base_url}{path}", "status": res.status, "content_type": ctype},
            )
        )

    llms_paths = ["/llms.txt", "/llms-full.txt"]
    for path in llms_paths:
        res, ok = endpoint_check(base_url, path, timeout, user_agent)
        ctype = parse_content_type(res.headers)
        if ok and "text/plain" in ctype:
            state = "pass"
            msg = f"{path} returns 200 text/plain."
        elif ok:
            state = "warn"
            msg = f"{path} returns 200 but Content-Type is not text/plain."
        else:
            state = "fail"
            msg = f"{path} is missing or not 200."
        checks.append(
            CheckResult(
                key=f"llms_{path.strip('/').replace('.', '_')}",
                category="endpoint",
                status=state,
                message=msg,
                details={"url": f"{base_url}{path}", "status": res.status, "content_type": ctype},
            )
        )

    home = fetch_url(base_url + "/", timeout=timeout, user_agent=user_agent, max_bytes=1_500_000)
    if home.status != 200 or "html" not in parse_content_type(home.headers):
        checks.append(
            CheckResult(
                key="homepage_fetch",
                category="signal",
                status="fail",
                message="Homepage is not fetchable as HTML.",
                details={"url": base_url + "/", "status": home.status, "content_type": parse_content_type(home.headers)},
            )
        )
    else:
        signals = parse_html_signals(home.body)
        schemas = parse_jsonld_blocks(signals.json_ld_blocks)

        checks.append(
            CheckResult(
                key="homepage_h1",
                category="signal",
                status="pass" if signals.h1_count > 0 else "fail",
                message="Homepage has H1." if signals.h1_count > 0 else "Homepage has no H1.",
                details={"count": signals.h1_count},
            )
        )
        checks.append(
            CheckResult(
                key="homepage_canonical",
                category="signal",
                status="pass" if bool(signals.canonical) else "warn",
                message="Homepage canonical detected." if signals.canonical else "Homepage canonical missing.",
                details={"canonical": signals.canonical},
            )
        )

        missing_og = [k for k in ["og:title", "og:description", "og:image"] if k not in signals.og]
        checks.append(
            CheckResult(
                key="homepage_og",
                category="signal",
                status="pass" if not missing_og else "warn",
                message="Homepage og tags look complete."
                if not missing_og
                else f"Homepage missing og tags: {', '.join(missing_og)}",
                details={"missing": missing_og},
            )
        )

        missing_twitter = [k for k in ["twitter:card", "twitter:title", "twitter:description"] if k not in signals.twitter]
        checks.append(
            CheckResult(
                key="homepage_twitter",
                category="signal",
                status="pass" if not missing_twitter else "warn",
                message="Homepage twitter tags look complete."
                if not missing_twitter
                else f"Homepage missing twitter tags: {', '.join(missing_twitter)}",
                details={"missing": missing_twitter},
            )
        )

        has_llms_link = bool(signals.llms_link)
        checks.append(
            CheckResult(
                key="homepage_llms_link",
                category="signal",
                status="pass" if has_llms_link else "warn",
                message="Homepage has <link rel=\"llms\">."
                if has_llms_link
                else "Homepage missing <link rel=\"llms\" href=\"/llms.txt\">.",
                details={"value": signals.llms_link},
            )
        )

        robots_blob = ",".join(signals.meta_robots).lower()
        noindex_home = "noindex" in robots_blob
        checks.append(
            CheckResult(
                key="homepage_noindex_conflict",
                category="signal",
                status="fail" if noindex_home else "pass",
                message="Homepage is noindex." if noindex_home else "Homepage is indexable.",
                details={"meta_robots": signals.meta_robots},
            )
        )

        title_lower = (signals.title or "").lower()
        body_lower = (home.body[:12000] or "").lower()
        soft404 = home.status == 200 and ("404" in title_lower or "not found" in body_lower[:3000])
        checks.append(
            CheckResult(
                key="homepage_soft404",
                category="signal",
                status="warn" if soft404 else "pass",
                message="Homepage may look like soft-404 content." if soft404 else "No homepage soft-404 pattern detected.",
                details={"title": signals.title[:120]},
            )
        )

        org_schema = None
        for schema in schemas:
            if type_matches(schema, "Organization"):
                org_schema = schema
                break
        same_as = org_schema.get("sameAs") if isinstance(org_schema, dict) else None
        has_same_as = isinstance(same_as, list) and len(same_as) > 0
        checks.append(
            CheckResult(
                key="organization_sameas",
                category="schema",
                status="pass" if has_same_as else "warn",
                message="Organization sameAs detected."
                if has_same_as
                else "Organization sameAs missing.",
                details={"same_as_count": len(same_as) if isinstance(same_as, list) else 0},
            )
        )

    bot_home = fetch_url(base_url + "/", timeout=timeout, user_agent=GOOGLEBOT_UA, max_bytes=350_000)
    if home.status == 0 or bot_home.status == 0:
        checks.append(
            CheckResult(
                key="fetch_consistency",
                category="crawl",
                status="warn",
                message="Could not fully compare normal UA vs Googlebot UA fetch.",
                details={"normal_status": home.status, "googlebot_status": bot_home.status},
            )
        )
    else:
        status_match = home.status == bot_home.status
        checks.append(
            CheckResult(
                key="fetch_consistency",
                category="crawl",
                status="pass" if status_match else "warn",
                message="Normal UA and Googlebot get same homepage status."
                if status_match
                else "Homepage status differs between normal UA and Googlebot.",
                details={"normal_status": home.status, "googlebot_status": bot_home.status},
            )
        )

    urls = collect_urls_from_sitemaps(base_url, timeout, user_agent, max_urls=max_urls)
    article_url = pick_first_article_url(urls, base_url)
    if not article_url:
        checks.append(
            CheckResult(
                key="article_schema",
                category="schema",
                status="warn",
                message="No article-like URL discovered from sitemap for schema checks.",
                details={"sitemap_urls": len(urls)},
            )
        )
    else:
        article = fetch_url(article_url, timeout=timeout, user_agent=user_agent, max_bytes=1_200_000)
        if article.status != 200:
            checks.append(
                CheckResult(
                    key="article_schema",
                    category="schema",
                    status="warn",
                    message="Article URL discovered but not fetchable for schema checks.",
                    details={"url": article_url, "status": article.status},
                )
            )
        else:
            signals = parse_html_signals(article.body)
            schemas = parse_jsonld_blocks(signals.json_ld_blocks)
            article_schema = None
            for schema in schemas:
                if type_matches(schema, "Article") or type_matches(schema, "BlogPosting"):
                    article_schema = schema
                    break
            if not article_schema:
                checks.append(
                    CheckResult(
                        key="article_schema",
                        category="schema",
                        status="warn",
                        message="Article schema not found on sampled article page.",
                        details={"url": article_url},
                    )
                )
            else:
                need = ["author", "datePublished", "dateModified", "publisher"]
                missing = [k for k in need if not article_schema.get(k)]
                checks.append(
                    CheckResult(
                        key="article_schema",
                        category="schema",
                        status="pass" if not missing else "warn",
                        message="Sampled article schema has key fields."
                        if not missing
                        else f"Sampled article schema missing fields: {', '.join(missing)}",
                        details={"url": article_url, "missing": missing},
                    )
                )

    low_value_candidates = [
        "/wp-login.php",
        "/login",
        "/register",
        "/lost-password",
        "/sample-page",
    ]
    checked_count = 0
    issue_count = 0
    for path in low_value_candidates:
        res = fetch_url(base_url + path, timeout=timeout, user_agent=user_agent, max_bytes=200_000)
        if res.status != 200:
            continue
        checked_count += 1
        x_robots = (res.headers.get("x-robots-tag") or "").lower()
        html_noindex = False
        if "html" in parse_content_type(res.headers):
            s = parse_html_signals(res.body)
            html_noindex = "noindex" in ",".join(s.meta_robots).lower()
        if "noindex" not in x_robots and not html_noindex:
            issue_count += 1
    if checked_count == 0:
        checks.append(
            CheckResult(
                key="low_value_noindex",
                category="signal",
                status="pass",
                message="No low-value 200 pages found from default probes.",
                details={},
            )
        )
    elif issue_count > 0:
        checks.append(
            CheckResult(
                key="low_value_noindex",
                category="signal",
                status="warn",
                message="Some low-value pages are indexable; consider noindex.",
                details={"checked_pages": checked_count, "indexable_pages": issue_count},
            )
        )
    else:
        checks.append(
            CheckResult(
                key="low_value_noindex",
                category="signal",
                status="pass",
                message="Checked low-value pages are noindex-protected.",
                details={"checked_pages": checked_count},
            )
        )

    counts = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        counts[check.status] += 1

    overall = "pass"
    if counts["fail"] > 0:
        overall = "fail"
    elif counts["warn"] > 0:
        overall = "warn"

    return {
        "meta": {
            "target": base_url,
            "generated_at_utc": now_utc(),
            "tool": "geo-llms-toolkit standalone-cli",
            "version": TOOL_VERSION,
        },
        "summary": {
            "overall": overall,
            "pass": counts["pass"],
            "warn": counts["warn"],
            "fail": counts["fail"],
            "total": len(checks),
        },
        "checks": [asdict(c) for c in checks],
    }


def run_monitor(
    base_url: str,
    keywords: List[KeywordItem],
    competitors: List[str],
    timeout: int,
    user_agent: str,
    serp_depth: int,
    auto_discover: bool,
    max_discovered: int,
    weights: Dict[str, float],
) -> Dict[str, object]:
    target_domain = normalize_domain(base_url)
    provided_competitors: List[str] = []
    for raw in competitors:
        d = normalize_domain(raw)
        if d:
            provided_competitors.append(d)
    provided_set = {c for c in provided_competitors if c and c != target_domain}

    rows: List[Dict[str, object]] = []
    keyword_hits_target = 0
    keywords_with_serp_results = 0
    discovered_counter: Dict[str, int] = {}

    for item in keywords:
        urls = fetch_bing_results(item.keyword, serp_depth, timeout, user_agent)
        domains: List[str] = []
        rank_by_domain: Dict[str, int] = {}
        for idx, u in enumerate(urls, start=1):
            d = normalize_domain(u)
            if not d:
                continue
            domains.append(d)
            if d not in rank_by_domain:
                rank_by_domain[d] = idx
                if d != target_domain:
                    discovered_counter[d] = discovered_counter.get(d, 0) + 1

        if domains:
            keywords_with_serp_results += 1

        target_rank = rank_by_domain.get(target_domain, 0)
        if target_rank:
            keyword_hits_target += 1
        serp_confidence = round(min(1.0, len(domains) / max(1, serp_depth)), 2)
        rows.append(
            {
                "keyword": item.keyword,
                "group": item.group,
                "value": item.value,
                "is_brand": item.is_brand,
                "target_rank": target_rank,
                "domains": domains,
                "rank_by_domain": rank_by_domain,
                "serp_confidence": serp_confidence,
            }
        )

    if auto_discover:
        discovered_sorted = sorted(
            [d for d in discovered_counter.items() if d[0] not in provided_set and d[0] != target_domain],
            key=lambda x: x[1],
            reverse=True,
        )
        for domain, _count in discovered_sorted[:max_discovered]:
            provided_set.add(domain)

    competitor_list = sorted(provided_set)
    total_keywords = len(rows)
    brand_keywords = sum(1 for r in rows if r["is_brand"])
    non_brand_keywords = total_keywords - brand_keywords

    competitor_profiles: List[Dict[str, object]] = []
    for comp in competitor_list:
        matched = 0
        matched_brand = 0
        matched_non_brand = 0
        coappear = 0
        weighted_presence = 0.0
        rank_gaps: List[float] = []
        avg_rank_sum = 0.0

        for row in rows:
            comp_rank = row["rank_by_domain"].get(comp, 0)
            if not comp_rank:
                continue
            matched += 1
            avg_rank_sum += comp_rank
            weighted_presence += float(row["value"])
            if row["is_brand"]:
                matched_brand += 1
            else:
                matched_non_brand += 1

            target_rank = row["target_rank"]
            if target_rank:
                coappear += 1
                gap = target_rank - comp_rank
                if gap > 0:
                    rank_gaps.append(float(gap))

        keyword_overlap = (matched / total_keywords * 100.0) if total_keywords else 0.0
        serp_coappear = (coappear / keyword_hits_target * 100.0) if keyword_hits_target else 0.0
        avg_rank_gap = (sum(rank_gaps) / len(rank_gaps)) if rank_gaps else 0.0
        rank_pressure = min(100.0, (avg_rank_gap / max(1, serp_depth)) * 100.0)
        non_brand_share = (matched_non_brand / max(1, non_brand_keywords) * 100.0) if non_brand_keywords else 0.0
        brand_share = (matched_brand / max(1, brand_keywords) * 100.0) if brand_keywords else 0.0

        score = (
            keyword_overlap * float(weights.get("keyword_overlap", DEFAULT_MONITOR_WEIGHTS["keyword_overlap"]))
            + serp_coappear * float(weights.get("serp_coappear", DEFAULT_MONITOR_WEIGHTS["serp_coappear"]))
            + rank_pressure * float(weights.get("rank_pressure", DEFAULT_MONITOR_WEIGHTS["rank_pressure"]))
        )
        data_coverage = (matched / max(1, total_keywords)) * 100.0
        confidence = round(min(1.0, data_coverage / 100.0) * 100.0, 2)
        competitor_profiles.append(
            {
                "domain": comp,
                "score": round(score, 2),
                "confidence_pct": confidence,
                "keyword_overlap_pct": round(keyword_overlap, 2),
                "serp_coappear_pct": round(serp_coappear, 2),
                "rank_pressure_pct": round(rank_pressure, 2),
                "brand_share_pct": round(brand_share, 2),
                "non_brand_share_pct": round(non_brand_share, 2),
                "matched_keywords": matched,
                "average_rank": round(avg_rank_sum / matched, 2) if matched else 0.0,
                "weighted_presence": round(weighted_presence, 2),
            }
        )

    score_values = [float(p["score"]) for p in competitor_profiles]
    p50 = percentile(score_values, 0.5)
    p80 = percentile(score_values, 0.8)
    for p in competitor_profiles:
        p["tier"] = classify_competitor_tier(float(p["score"]), p50, p80)

    priority_actions: List[ActionItem] = []
    high_competitors = [p for p in competitor_profiles if p["tier"] in {"direct", "potential"}]
    tracked_domains = [p["domain"] for p in high_competitors]

    for row in rows:
        if row["is_brand"]:
            continue
        target_rank = int(row["target_rank"])
        best_comp_domain = ""
        best_comp_rank = 0
        hit_count = 0
        for comp in tracked_domains:
            comp_rank = int(row["rank_by_domain"].get(comp, 0))
            if not comp_rank:
                continue
            hit_count += 1
            if best_comp_rank == 0 or comp_rank < best_comp_rank:
                best_comp_rank = comp_rank
                best_comp_domain = comp
        if best_comp_rank == 0:
            continue

        if target_rank == 0:
            gap = serp_depth + 1 - best_comp_rank
        else:
            gap = target_rank - best_comp_rank
        if gap < 3:
            continue

        impact_score = min(100.0, (gap * 8.0) + (hit_count * 6.0) + (float(row["value"]) * 5.0))
        effort_score = 70.0 if target_rank == 0 else 45.0
        priority, priority_score = calc_priority(impact_score, effort_score)
        rec = (
            "Create net-new high-value page and add to llms pin list."
            if target_rank == 0
            else "Refresh existing page title/meta/schema and re-submit in sitemap/llms."
        )
        priority_actions.append(
            ActionItem(
                keyword=str(row["keyword"]),
                group=str(row["group"]),
                priority=priority,
                priority_score=priority_score,
                impact_score=round(impact_score, 2),
                effort_score=round(effort_score, 2),
                target_rank=target_rank,
                best_competitor=best_comp_domain,
                best_competitor_rank=best_comp_rank,
                recommendation=rec,
            )
        )

    priority_actions.sort(key=lambda a: a.priority_score, reverse=True)

    top_actions = [asdict(a) for a in priority_actions[:20]]
    competitor_profiles.sort(key=lambda x: float(x["score"]), reverse=True)

    return {
        "meta": {
            "target": base_url,
            "target_domain": target_domain,
            "generated_at_utc": now_utc(),
            "tool": "geo-llms-toolkit standalone-cli",
            "version": TOOL_VERSION,
            "provider": "bing-serp",
            "serp_depth": serp_depth,
            "weights": {
                "keyword_overlap": round(float(weights.get("keyword_overlap", DEFAULT_MONITOR_WEIGHTS["keyword_overlap"])), 4),
                "serp_coappear": round(float(weights.get("serp_coappear", DEFAULT_MONITOR_WEIGHTS["serp_coappear"])), 4),
                "rank_pressure": round(float(weights.get("rank_pressure", DEFAULT_MONITOR_WEIGHTS["rank_pressure"])), 4),
            },
        },
        "summary": {
            "keywords_total": total_keywords,
            "keywords_brand": brand_keywords,
            "keywords_non_brand": non_brand_keywords,
            "keywords_target_ranked": keyword_hits_target,
            "keywords_with_serp_results": keywords_with_serp_results,
            "data_coverage_pct": round((keywords_with_serp_results / max(1, total_keywords)) * 100.0, 2),
            "competitors_tracked": len(competitor_profiles),
            "actions_generated": len(top_actions),
        },
        "competitors": competitor_profiles,
        "actions": top_actions,
        "keywords": [
            {
                "keyword": r["keyword"],
                "group": r["group"],
                "value": r["value"],
                "is_brand": r["is_brand"],
                "target_rank": r["target_rank"],
                "serp_confidence": r["serp_confidence"],
                "top_domains": r["domains"][:10],
            }
            for r in rows
        ],
    }


def to_markdown_report(report: Dict[str, object]) -> str:
    meta = report["meta"]
    summary = report["summary"]
    checks = report["checks"]

    lines = [
        f"# GEO Scan Report - {meta['target']}",
        "",
        f"- Generated (UTC): {meta['generated_at_utc']}",
        f"- Tool: {meta['tool']} {meta['version']}",
        f"- Overall: **{summary['overall'].upper()}**",
        f"- Pass/Warn/Fail: {summary['pass']}/{summary['warn']}/{summary['fail']}",
        "",
        "## Checks",
        "",
        "| Key | Category | Status | Message |",
        "| --- | --- | --- | --- |",
    ]
    for c in checks:
        message = str(c["message"]).replace("|", "\\|")
        lines.append(f"| {c['key']} | {c['category']} | {str(c['status']).upper()} | {message} |")
    return "\n".join(lines) + "\n"


def to_csv_report(report: Dict[str, object]) -> str:
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["key", "category", "status", "message", "details_json"])
    for c in report["checks"]:
        writer.writerow([c["key"], c["category"], c["status"], c["message"], json.dumps(c["details"], ensure_ascii=False)])
    return buf.getvalue()


def to_monitor_markdown(report: Dict[str, object]) -> str:
    meta = report["meta"]
    summary = report["summary"]
    competitors = report["competitors"]
    actions = report["actions"]

    lines = [
        f"# GEO Competitor Monitor - {meta['target']}",
        "",
        f"- Generated (UTC): {meta['generated_at_utc']}",
        f"- Provider: {meta['provider']}",
        f"- Weights: overlap={meta.get('weights', {}).get('keyword_overlap', '-')}, coappear={meta.get('weights', {}).get('serp_coappear', '-')}, pressure={meta.get('weights', {}).get('rank_pressure', '-')}",
        f"- Keywords: {summary['keywords_total']} (brand={summary['keywords_brand']}, non-brand={summary['keywords_non_brand']})",
        f"- Keywords with SERP data: {summary['keywords_with_serp_results']}",
        f"- Data coverage: {summary.get('data_coverage_pct', 0)}%",
        f"- Competitors tracked: {summary['competitors_tracked']}",
        f"- Priority actions: {summary['actions_generated']}",
        "",
        "## Competitor Scores",
        "",
        "| Domain | Tier | Score | Confidence % | Keyword Overlap % | Co-appear % | Rank Pressure % | Non-Brand Share % | Avg Rank |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for c in competitors:
        lines.append(
            f"| {c['domain']} | {str(c['tier']).upper()} | {c['score']} | {c.get('confidence_pct', 0)} | {c['keyword_overlap_pct']} | "
            f"{c['serp_coappear_pct']} | {c['rank_pressure_pct']} | {c['non_brand_share_pct']} | {c['average_rank']} |"
        )

    if summary["keywords_with_serp_results"] == 0:
        lines.extend(
            [
                "",
                "> Warning: no SERP data was captured. Check network, provider accessibility, or try a different region/proxy.",
            ]
        )

    lines.extend(["", "## Priority Actions", "", "| Priority | Keyword | Group | Target Rank | Best Competitor | Gap Action |",
                  "| --- | --- | --- | ---: | --- | --- |"])

    for action in actions:
        target_rank = action["target_rank"] if action["target_rank"] else "-"
        lines.append(
            f"| {action['priority']} ({action['priority_score']}) | {action['keyword']} | {action['group']} | "
            f"{target_rank} | {action['best_competitor']} #{action['best_competitor_rank']} | {action['recommendation']} |"
        )
    return "\n".join(lines) + "\n"


def to_monitor_csv(report: Dict[str, object]) -> str:
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "domain",
            "tier",
            "score",
            "confidence_pct",
            "keyword_overlap_pct",
            "serp_coappear_pct",
            "rank_pressure_pct",
            "brand_share_pct",
            "non_brand_share_pct",
            "matched_keywords",
            "average_rank",
        ]
    )
    for c in report["competitors"]:
        writer.writerow(
            [
                c["domain"],
                c["tier"],
                c["score"],
                c.get("confidence_pct", 0),
                c["keyword_overlap_pct"],
                c["serp_coappear_pct"],
                c["rank_pressure_pct"],
                c["brand_share_pct"],
                c["non_brand_share_pct"],
                c["matched_keywords"],
                c["average_rank"],
            ]
        )
    return buf.getvalue()


def to_outreach_markdown(plan: Dict[str, object]) -> str:
    meta = plan["meta"]
    summary = plan["summary"]
    prospects = plan["prospects"]

    lines = [
        f"# GEO Outreach Plan - {meta['target_domain']}",
        "",
        f"- Generated (UTC): {meta['generated_at_utc']}",
        f"- Pitch URL: {meta['pitch_url']}",
        f"- Offer: {meta['offer']}",
        f"- Prospects: {summary['prospects_total']}",
        "",
        "## Prospect List",
        "",
        "| Domain | Score | Opportunities | Avg SERP Rank | Top Gap Keyword | Contact | Best Competitor |",
        "| --- | ---: | ---: | ---: | --- | --- | --- |",
    ]

    for p in prospects:
        best_comp = f"{p['best_competitor']} #{p['best_competitor_rank']}" if p["best_competitor"] else "-"
        contact = p.get("contact_email") or p.get("contact_page") or "-"
        lines.append(
            f"| {p['domain']} | {p['prospect_score']} | {p['opportunities']} | {p['average_serp_rank']} | "
            f"{p['top_gap_keyword']} | {contact} | {best_comp} |"
        )
    return "\n".join(lines) + "\n"


def to_outreach_csv(plan: Dict[str, object]) -> str:
    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "domain",
            "prospect_score",
            "opportunities",
            "average_serp_rank",
            "top_gap_keyword",
            "top_gap_group",
            "best_competitor",
            "best_competitor_rank",
            "contact_email",
            "contact_page",
            "contact_confidence",
            "keywords",
            "outreach_angle",
            "email_subject",
            "email_body",
        ]
    )
    for p in plan["prospects"]:
        writer.writerow(
            [
                p["domain"],
                p["prospect_score"],
                p["opportunities"],
                p["average_serp_rank"],
                p["top_gap_keyword"],
                p["top_gap_group"],
                p["best_competitor"],
                p["best_competitor_rank"],
                p.get("contact_email", ""),
                p.get("contact_page", ""),
                p.get("contact_confidence", 0),
                "; ".join(p["keywords"]),
                p["outreach_angle"],
                p["email_subject"],
                p["email_body"],
            ]
        )
    return buf.getvalue()


def to_outreach_sequences_markdown(plan: Dict[str, object]) -> str:
    lines = ["# GEO Outreach Email Sequences", ""]
    for idx, p in enumerate(plan["prospects"], start=1):
        lines.append(f"## {idx}. {p['domain']}")
        lines.append(f"- Score: {p['prospect_score']}")
        lines.append(f"- Top keyword gap: {p['top_gap_keyword']}")
        if p.get("contact_email"):
            lines.append(f"- Contact email: {p['contact_email']}")
        elif p.get("contact_page"):
            lines.append(f"- Contact page: {p['contact_page']}")
        lines.append(f"- Subject: {p['email_subject']}")
        lines.append("")
        lines.append("```text")
        lines.append(p["email_body"])
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_followup_content(prospect: Dict[str, object], campaign_meta: Dict[str, object]) -> Tuple[str, str]:
    keyword = str(prospect.get("top_gap_keyword") or "this topic")
    pitch_url = str(campaign_meta.get("pitch_url") or "")
    site_name = str(campaign_meta.get("site_name") or campaign_meta.get("target_domain") or "")
    subject = f"Quick follow-up: resource for {keyword}"
    body = textwrap.dedent(
        f"""\
        Hi [First Name],

        Quick follow-up on my previous note about your {keyword} page.
        In case it helps your readers, here is the resource again:
        {pitch_url}

        If you'd like, I can also share a short summary version for easier inclusion.

        Best,
        {site_name}
        """
    ).strip()
    return subject, body


def to_followup_sequences_markdown(campaign: Dict[str, object], limit: int = 200) -> str:
    meta = campaign.get("meta", {})
    prospects = campaign.get("prospects", [])
    lines = [
        f"# GEO Outreach Follow-up Sequences - {meta.get('campaign_id', '-')}",
        "",
    ]
    count = 0
    for p in prospects:
        if not isinstance(p, dict):
            continue
        if str(p.get("status") or "") != "followup_due":
            continue
        count += 1
        if count > limit:
            break
        subject = str(p.get("followup_subject") or "")
        body = str(p.get("followup_body") or "")
        if not subject or not body:
            subject, body = build_followup_content(p, meta if isinstance(meta, dict) else {})
        lines.append(f"## {count}. {p.get('domain', '-')}")
        lines.append(f"- Keyword: {p.get('top_gap_keyword', '-')}")
        lines.append(f"- Contact: {p.get('contact_email') or p.get('contact_page') or '-'}")
        lines.append(f"- Subject: {subject}")
        lines.append("")
        lines.append("```text")
        lines.append(body)
        lines.append("```")
        lines.append("")
    if count == 0:
        lines.append("No `followup_due` prospects currently.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_followup_csv(campaign: Dict[str, object], limit: int = 1000) -> str:
    from io import StringIO

    meta = campaign.get("meta", {})
    prospects = campaign.get("prospects", [])
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "campaign_id",
            "domain",
            "top_gap_keyword",
            "contact_email",
            "contact_page",
            "followup_count",
            "followup_due_at_utc",
            "subject",
            "body",
        ]
    )
    count = 0
    for p in prospects:
        if not isinstance(p, dict):
            continue
        if str(p.get("status") or "") != "followup_due":
            continue
        count += 1
        if count > limit:
            break
        subject = str(p.get("followup_subject") or "")
        body = str(p.get("followup_body") or "")
        if not subject or not body:
            subject, body = build_followup_content(p, meta if isinstance(meta, dict) else {})
        writer.writerow(
            [
                meta.get("campaign_id", ""),
                p.get("domain", ""),
                p.get("top_gap_keyword", ""),
                p.get("contact_email", ""),
                p.get("contact_page", ""),
                p.get("followup_count", 0),
                p.get("followup_due_at_utc", ""),
                subject,
                body,
            ]
        )
    return buf.getvalue()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render_scan_output(report: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        return to_csv_report(report)
    return to_markdown_report(report)


def render_monitor_output(report: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        return to_monitor_csv(report)
    return to_monitor_markdown(report)


def render_outreach_output(plan: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        return to_outreach_csv(plan)
    return to_outreach_markdown(plan)


def render_campaign_status_markdown(campaign: Dict[str, object]) -> str:
    meta = campaign.get("meta", {})
    summary = campaign.get("summary", {})
    runs = campaign.get("runs", [])
    lines = [
        f"# Outreach Campaign Status - {meta.get('campaign_id', '-')}",
        "",
        f"- Target: {meta.get('target_domain', '-')}",
        f"- Pitch URL: {meta.get('pitch_url', '-')}",
        f"- Created (UTC): {meta.get('created_at_utc', '-')}",
        f"- Last run (UTC): {meta.get('last_run_at_utc', '-') or '-'}",
        f"- Prospects total: {summary.get('prospects_total', 0)}",
        f"- Sent / Followup / Replied / Won / Lost: {summary.get('sent', 0)} / {summary.get('followup_due', 0)} / {summary.get('replied', 0)} / {summary.get('won', 0)} / {summary.get('lost', 0)}",
        f"- Failed / Skipped / Queued: {summary.get('failed', 0)} / {summary.get('skipped', 0)} / {summary.get('queued', 0)}",
        "",
    ]
    if isinstance(runs, list) and runs:
        lines.extend(
            [
                "## Recent Runs",
                "",
                "| Run ID | Provider | Sent | Failed | Skipped | Finished (UTC) |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for run in list(runs)[-10:]:
            if not isinstance(run, dict):
                continue
            lines.append(
                f"| {run.get('run_id', '-')} | {run.get('provider', '-')} | {run.get('sent', 0)} | {run.get('failed', 0)} | "
                f"{run.get('skipped', 0)} | {run.get('finished_at_utc', '-')} |"
            )
    return "\n".join(lines) + "\n"


def render_monitor_diff(diff: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(diff, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        from io import StringIO

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["domain", "current_score", "previous_score", "delta_score", "current_tier", "previous_tier"])
        for row in diff.get("competitor_changes", []):
            writer.writerow(
                [
                    row.get("domain"),
                    row.get("current_score"),
                    row.get("previous_score"),
                    row.get("delta_score"),
                    row.get("current_tier"),
                    row.get("previous_tier"),
                ]
            )
        return buf.getvalue()

    lines = [
        f"# GEO Monitor Diff - {diff.get('meta', {}).get('target', '-')}",
        "",
        f"- Generated (UTC): {diff.get('meta', {}).get('generated_at_utc', '-')}",
        f"- Current report: {diff.get('meta', {}).get('current_report', '-')}",
        f"- Previous report: {diff.get('meta', {}).get('previous_report', '-')}",
        "",
        "## Summary Delta",
        "",
        f"- Keywords total delta: {diff.get('summary', {}).get('keywords_total_delta', 0)}",
        f"- Keywords with SERP results delta: {diff.get('summary', {}).get('keywords_with_serp_results_delta', 0)}",
        f"- Competitors tracked delta: {diff.get('summary', {}).get('competitors_tracked_delta', 0)}",
        f"- Actions generated delta: {diff.get('summary', {}).get('actions_generated_delta', 0)}",
        "",
        "## Competitor Score Delta",
        "",
        "| Domain | Current | Previous | Delta | Current Tier | Previous Tier |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in diff.get("competitor_changes", [])[:30]:
        lines.append(
            f"| {row.get('domain')} | {row.get('current_score')} | {row.get('previous_score')} | {row.get('delta_score')} | "
            f"{row.get('current_tier')} | {row.get('previous_tier')} |"
        )
    added = diff.get("actions", {}).get("added_keywords", [])
    removed = diff.get("actions", {}).get("removed_keywords", [])
    lines.extend(
        [
            "",
            "## Action Keyword Changes",
            "",
            f"- Added: {', '.join(added[:20]) if added else '-'}",
            f"- Removed: {', '.join(removed[:20]) if removed else '-'}",
            "",
        ]
    )
    return "\n".join(lines)


def choose_title(url: str, page: PageSignals) -> str:
    if page.title:
        return page.title.strip()
    path = safe_path(url).strip("/")
    if not path:
        return "Homepage"
    return path.replace("-", " ")


def build_llms_files(
    base_url: str,
    timeout: int,
    user_agent: str,
    output_dir: Path,
    max_items: int,
    extra_exclude: List[str],
) -> Dict[str, object]:
    sitemap_urls = collect_urls_from_sitemaps(base_url, timeout, user_agent, max_urls=max_items * 4)
    if not sitemap_urls:
        sitemap_urls = [f"{base_url}/"]

    filtered: List[str] = []
    for url in sitemap_urls:
        if is_low_value_url(url, extra_patterns=extra_exclude):
            continue
        if url not in filtered:
            filtered.append(url)
        if len(filtered) >= max_items:
            break

    if not filtered:
        filtered = [f"{base_url}/"]

    entries: List[Dict[str, str]] = []
    for url in filtered:
        fetched = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=900_000)
        if fetched.status != 200:
            continue
        content_type = parse_content_type(fetched.headers)
        if "html" not in content_type and "text/" not in content_type:
            continue
        page = parse_html_signals(fetched.body)
        title = choose_title(url, page)
        summary = page.meta_description or page.body_excerpt or ""
        entries.append({"url": url, "title": title, "summary": summary[:360]})

    if not entries:
        entries.append({"url": f"{base_url}/", "title": "Homepage", "summary": ""})

    host = urlparse(base_url).netloc
    generated = now_utc()

    llms_lines = [
        f"# {host} | LLMS Content Index",
        "",
        f"Generated (UTC): {generated}",
        f"Base URL: {base_url}",
        f"Source URLs discovered: {len(sitemap_urls)}",
        f"Included high-value URLs: {len(entries)}",
        "",
        "## High-value pages",
    ]
    for item in entries:
        llms_lines.append(f"- {item['title']} - {item['url']}")

    full_lines = [
        f"# {host} | LLMS Extended Index",
        "",
        f"Generated (UTC): {generated}",
        f"Base URL: {base_url}",
        "",
    ]
    for idx, item in enumerate(entries, start=1):
        full_lines.append(f"## {idx}. {item['title']}")
        full_lines.append(f"URL: {item['url']}")
        if item["summary"]:
            full_lines.append(f"Summary: {item['summary']}")
        full_lines.append("")

    llms_path = output_dir / "llms.txt"
    llms_full_path = output_dir / "llms-full.txt"
    write_text(llms_path, "\n".join(llms_lines).rstrip() + "\n")
    write_text(llms_full_path, "\n".join(full_lines).rstrip() + "\n")

    return {
        "base_url": base_url,
        "generated_at_utc": generated,
        "sitemap_discovered": len(sitemap_urls),
        "included_entries": len(entries),
        "llms_path": str(llms_path),
        "llms_full_path": str(llms_full_path),
    }


def handle_scan(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.target)
    report = run_scan(base_url, timeout=args.timeout, user_agent=args.user_agent, max_urls=args.max_urls)
    content = render_scan_output(report, args.format)

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        write_text(out_path, content)
        print(f"Scan report saved: {out_path}")
    else:
        sys.stdout.write(content)

    return 0 if report["summary"]["fail"] == 0 else 2


def handle_monitor(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.target)
    target_domain = normalize_domain(base_url)
    inferred_brand = [p for p in re.split(r"[-_.]", target_domain.split(".")[0]) if len(p) >= 2]
    brand_tokens = sorted({t.lower() for t in (args.brand_token or []) + inferred_brand})

    keywords_path = Path(args.keywords_file).expanduser().resolve()
    weights_file = Path(args.weights_file).expanduser().resolve() if args.weights_file else None
    weights = load_monitor_weights(weights_file)
    keywords = read_keywords_file(keywords_path, brand_tokens=brand_tokens, max_keywords=args.max_keywords)
    report = run_monitor(
        base_url=base_url,
        keywords=keywords,
        competitors=args.competitor or [],
        timeout=args.timeout,
        user_agent=args.user_agent,
        serp_depth=args.serp_depth,
        auto_discover=args.discover_competitors,
        max_discovered=args.max_discovered,
        weights=weights,
    )
    content = render_monitor_output(report, args.format)

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        write_text(out_path, content)
        print(f"Monitor report saved: {out_path}")
    else:
        sys.stdout.write(content)

    if args.history_dir:
        history_dir = Path(args.history_dir).expanduser().resolve()
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot_path = history_dir / f"monitor-{normalize_domain(base_url)}-{stamp}.json"
        write_text(snapshot_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Snapshot saved: {snapshot_path}")
    return 0


def handle_monitor_diff(args: argparse.Namespace) -> int:
    current_path = Path(args.current_report).expanduser().resolve()
    previous_path = Path(args.previous_report).expanduser().resolve()
    diff = load_monitor_diff(current_path, previous_path)
    body = render_monitor_diff(diff, args.format)
    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        write_text(out_path, body)
        print(f"Monitor diff saved: {out_path}")
    else:
        sys.stdout.write(body)
    return 0


def handle_outreach(args: argparse.Namespace) -> int:
    action = (args.action or "plan").strip().lower()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    campaign_path = Path(args.campaign_file).expanduser().resolve()
    state_path = Path(args.state_file).expanduser().resolve()

    if action == "plan":
        if not args.monitor_report:
            raise ValueError("monitor-report is required for outreach plan")
        if not args.pitch_url:
            raise ValueError("pitch-url is required for outreach plan")

        monitor_path = Path(args.monitor_report).expanduser().resolve()
        monitor_report = load_monitor_report(monitor_path)
        target_domain = normalize_domain(str(monitor_report.get("meta", {}).get("target_domain") or ""))
        site_name = args.site_name or target_domain

        pitch_url = args.pitch_url.strip()
        if not re.match(r"^https?://", pitch_url, flags=re.I):
            raise ValueError("pitch-url must be a full URL (http/https)")

        plan = build_outreach_plan(
            monitor_report=monitor_report,
            pitch_url=pitch_url,
            site_name=site_name,
            offer=args.offer,
            max_prospects=args.max_prospects,
            min_prospect_score=args.min_prospect_score,
            min_opportunities=args.min_opportunities,
            exclude_domains=args.exclude_domain or [],
            enrich_contacts=bool(args.enrich_contacts),
            timeout=args.timeout,
            user_agent=args.user_agent,
        )

        plan_json_path = output_dir / "outreach-plan.json"
        prospects_csv_path = output_dir / "outreach-prospects.csv"
        report_md_path = output_dir / "outreach-report.md"
        sequences_md_path = output_dir / "outreach-sequences.md"

        write_text(plan_json_path, render_outreach_output(plan, "json"))
        write_text(prospects_csv_path, render_outreach_output(plan, "csv"))
        write_text(report_md_path, render_outreach_output(plan, "markdown"))
        write_text(sequences_md_path, to_outreach_sequences_markdown(plan))

        campaign = build_campaign_from_plan(plan)
        write_text(campaign_path, json.dumps(campaign, ensure_ascii=False, indent=2) + "\n")

        print(
            textwrap.dedent(
                f"""\
                Outreach plan generated.
                - Prospects: {plan['summary']['prospects_total']}
                - JSON: {plan_json_path}
                - CSV: {prospects_csv_path}
                - Report: {report_md_path}
                - Sequences: {sequences_md_path}
                - Campaign: {campaign_path}
                """
            ).rstrip()
        )
        return 0

    if action == "run":
        campaign = load_campaign(campaign_path)
        state = load_or_create_state(state_path)
        run = run_outreach_campaign(
            campaign=campaign,
            provider=args.provider,
            only_new=not bool(args.include_existing),
            cooldown_days=args.cooldown_days,
            state=state,
            webhook_url=args.webhook_url or "",
            webhook_token=args.webhook_token or "",
            command_template=args.command_template or "",
            timeout=args.timeout,
            followup_days=args.followup_days,
            apify_token=args.apify_token or "",
            apify_actor_id=args.apify_actor_id,
            apify_adapter_path=args.apify_adapter_path,
            apify_output_dir=args.apify_output_dir,
            apify_allow_fallback_first=bool(args.apify_allow_fallback_first),
            run_followup_due=bool(args.run_followup_due),
        )
        write_text(campaign_path, json.dumps(campaign, ensure_ascii=False, indent=2) + "\n")
        save_state(state_path, state)
        status_md_path = output_dir / "outreach-status.md"
        write_text(status_md_path, render_campaign_status_markdown(campaign))
        print(
            textwrap.dedent(
                f"""\
                Outreach run completed.
                - Campaign: {campaign_path}
                - Provider: {run['provider']}
                - Sent: {run['sent']}
                - Failed: {run['failed']}
                - Skipped: {run['skipped']}
                - State: {state_path}
                - Status report: {status_md_path}
                """
            ).rstrip()
        )
        return 0 if int(run["failed"]) == 0 else 3

    if action == "verify":
        campaign = load_campaign(campaign_path)
        summary = verify_campaign_backlinks(
            campaign=campaign,
            timeout=args.timeout,
            user_agent=args.user_agent,
            followup_days=args.followup_days,
        )
        write_text(campaign_path, json.dumps(campaign, ensure_ascii=False, indent=2) + "\n")
        status_md_path = output_dir / "outreach-status.md"
        write_text(status_md_path, render_campaign_status_markdown(campaign))
        followup_md_path = output_dir / "outreach-followup-sequences.md"
        followup_csv_path = output_dir / "outreach-followup.csv"
        write_text(followup_md_path, to_followup_sequences_markdown(campaign))
        write_text(followup_csv_path, to_followup_csv(campaign))
        print(
            textwrap.dedent(
                f"""\
                Outreach verify completed.
                - Campaign: {campaign_path}
                - Checked: {summary['checked']}
                - Won: {summary['won']}
                - Followup due: {summary['followup_due']}
                - Unchanged: {summary['unchanged']}
                - Status report: {status_md_path}
                - Followup sequences: {followup_md_path}
                - Followup CSV: {followup_csv_path}
                """
            ).rstrip()
        )
        return 0

    if action == "update":
        if not args.domain or not args.new_status:
            raise ValueError("domain and new-status are required for outreach update")
        campaign = load_campaign(campaign_path)
        ok = update_campaign_prospect_status(
            campaign=campaign,
            domain=args.domain,
            new_status=args.new_status,
            note=args.note or "",
        )
        if not ok:
            raise ValueError(f"domain not found in campaign: {args.domain}")
        write_text(campaign_path, json.dumps(campaign, ensure_ascii=False, indent=2) + "\n")
        print(f"Campaign updated: {args.domain} -> {args.new_status}")
        return 0

    if action == "status":
        campaign = load_campaign(campaign_path)
        body = render_campaign_status_markdown(campaign)
        if args.output:
            out_path = Path(args.output).expanduser().resolve()
            write_text(out_path, body)
            print(f"Campaign status saved: {out_path}")
        else:
            sys.stdout.write(body)
        return 0

    raise ValueError(f"unsupported outreach action: {action}")


def handle_llms(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.target)
    output_dir = Path(args.output_dir).expanduser().resolve()
    result = build_llms_files(
        base_url=base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        output_dir=output_dir,
        max_items=args.max_items,
        extra_exclude=args.exclude_pattern or [],
    )
    print(
        textwrap.dedent(
            f"""\
            LLMS files generated.
            - llms.txt: {result['llms_path']}
            - llms-full.txt: {result['llms_full_path']}
            - Included entries: {result['included_entries']}
            """
        ).rstrip()
    )
    return 0


def handle_all(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.target)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_scan(base_url, timeout=args.timeout, user_agent=args.user_agent, max_urls=args.max_urls)
    report_body = render_scan_output(report, args.report_format)
    ext = "md" if args.report_format == "markdown" else args.report_format
    report_path = output_dir / f"geo-scan-report.{ext}"
    write_text(report_path, report_body)

    llms_result = build_llms_files(
        base_url=base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        output_dir=output_dir,
        max_items=args.max_items,
        extra_exclude=args.exclude_pattern or [],
    )

    print(
        textwrap.dedent(
            f"""\
            Completed.
            - Scan report: {report_path}
            - Overall: {report['summary']['overall']} (pass={report['summary']['pass']}, warn={report['summary']['warn']}, fail={report['summary']['fail']})
            - llms.txt: {llms_result['llms_path']}
            - llms-full.txt: {llms_result['llms_full_path']}
            """
        ).rstrip()
    )
    return 0 if report["summary"]["fail"] == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geo",
        description="GEO LLMs Toolkit standalone CLI (scan + llms + monitor + monitor-diff + outreach).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Run GEO scan checks on a website.")
    scan_p.add_argument("target", help="Domain or URL, e.g. aronhouyu.com")
    scan_p.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    scan_p.add_argument("--output", help="Write report to a file path.")
    scan_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    scan_p.add_argument("--max-urls", type=int, default=220, help="Max sitemap URLs for schema sampling.")
    scan_p.add_argument("--user-agent", default=DEFAULT_UA)
    scan_p.set_defaults(func=handle_scan)

    llms_p = sub.add_parser("llms", help="Generate llms.txt and llms-full.txt from sitemap URLs.")
    llms_p.add_argument("target", help="Domain or URL, e.g. aronhouyu.com")
    llms_p.add_argument("--output-dir", default=".")
    llms_p.add_argument("--max-items", type=int, default=120)
    llms_p.add_argument("--exclude-pattern", action="append", help="Extra regex pattern(s) to exclude URL.")
    llms_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    llms_p.add_argument("--user-agent", default=DEFAULT_UA)
    llms_p.set_defaults(func=handle_llms)

    all_p = sub.add_parser("all", help="Run scan and generate LLMS files in one command.")
    all_p.add_argument("target", help="Domain or URL, e.g. aronhouyu.com")
    all_p.add_argument("--output-dir", default=".")
    all_p.add_argument("--report-format", choices=["markdown", "json", "csv"], default="markdown")
    all_p.add_argument("--max-items", type=int, default=120)
    all_p.add_argument("--max-urls", type=int, default=220)
    all_p.add_argument("--exclude-pattern", action="append", help="Extra regex pattern(s) to exclude URL.")
    all_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    all_p.add_argument("--user-agent", default=DEFAULT_UA)
    all_p.set_defaults(func=handle_all)

    monitor_p = sub.add_parser(
        "monitor",
        help="Monitor competitors by keyword SERP overlap and generate prioritized actions.",
    )
    monitor_p.add_argument("target", help="Domain or URL, e.g. aronhouyu.com")
    monitor_p.add_argument("--keywords-file", required=True, help="TXT/CSV/TSV file of keywords.")
    monitor_p.add_argument("--competitor", action="append", help="Competitor domain. Can be repeated.")
    monitor_p.add_argument("--discover-competitors", action="store_true", help="Auto-discover competitors from SERP.")
    monitor_p.add_argument("--max-discovered", type=int, default=8, help="Max discovered competitors to include.")
    monitor_p.add_argument("--brand-token", action="append", help="Extra brand token(s) for brand keyword isolation.")
    monitor_p.add_argument("--serp-depth", type=int, default=10, help="SERP depth per keyword (max 50).")
    monitor_p.add_argument("--max-keywords", type=int, default=100, help="Max keywords loaded from file.")
    monitor_p.add_argument("--weights-file", help="JSON weights file: keyword_overlap/serp_coappear/rank_pressure.")
    monitor_p.add_argument("--history-dir", default=".geo-history", help="Directory to store JSON snapshots.")
    monitor_p.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    monitor_p.add_argument("--output", help="Write report to a file path.")
    monitor_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    monitor_p.add_argument("--user-agent", default=DEFAULT_UA)
    monitor_p.set_defaults(func=handle_monitor)

    monitor_diff_p = sub.add_parser("monitor-diff", help="Compare two monitor JSON reports.")
    monitor_diff_p.add_argument("--current-report", required=True, help="Current monitor JSON report path.")
    monitor_diff_p.add_argument("--previous-report", required=True, help="Previous monitor JSON report path.")
    monitor_diff_p.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    monitor_diff_p.add_argument("--output", help="Write diff report to a file path.")
    monitor_diff_p.set_defaults(func=handle_monitor_diff)

    outreach_p = sub.add_parser(
        "outreach",
        help="Outreach workflow: plan / run / status / verify / update.",
    )
    outreach_p.add_argument(
        "action",
        nargs="?",
        choices=["plan", "run", "status", "verify", "update"],
        default="plan",
        help="Outreach action. Default: plan.",
    )
    outreach_p.add_argument("--monitor-report", help="Path to `geo monitor --format json` output.")
    outreach_p.add_argument("--pitch-url", help="Your page URL to promote in outreach.")
    outreach_p.add_argument("--site-name", help="Sender/site name used in email templates.")
    outreach_p.add_argument("--offer", default="Resource inclusion request", help="Offer text in email template.")
    outreach_p.add_argument("--max-prospects", type=int, default=30, help="Max prospects to keep.")
    outreach_p.add_argument("--min-prospect-score", type=float, default=8.0, help="Min prospect score threshold.")
    outreach_p.add_argument("--min-opportunities", type=int, default=1, help="Min keyword opportunities per domain.")
    outreach_p.add_argument("--exclude-domain", action="append", help="Domain pattern to exclude (repeatable).")
    outreach_p.add_argument("--enrich-contacts", action="store_true", help="Try discovering contact email/page per domain.")
    outreach_p.add_argument("--output-dir", default="./outreach-output", help="Output directory for generated files.")
    outreach_p.add_argument(
        "--campaign-file",
        default="./outreach-output/outreach-campaign.json",
        help="Campaign JSON path (written by plan, read by run/status).",
    )
    outreach_p.add_argument(
        "--state-file",
        default=".geo-history/outreach-state.json",
        help="Global state file for dedupe/cooldown between runs.",
    )
    outreach_p.add_argument("--provider", choices=["dry-run", "webhook", "command", "apify"], default="dry-run")
    outreach_p.add_argument("--webhook-url", help="Webhook endpoint when provider=webhook.")
    outreach_p.add_argument("--webhook-token", help="Bearer token for webhook authentication.")
    outreach_p.add_argument(
        "--command-template",
        help="Command template when provider=command. Vars: {domain} {keyword} {pitch_url} {site_name} {email_subject} {contact_email} {contact_page} and shell-safe *_q variants.",
    )
    outreach_p.add_argument("--apify-token", help="APIFY token when provider=apify (or set APIFY_TOKEN env).")
    outreach_p.add_argument("--apify-actor-id", default="daniil.poletaev/backlink-building-agent")
    outreach_p.add_argument(
        "--apify-adapter-path",
        default="./scripts/backlink_outreach_adapter.py",
        help="Adapter script path for provider=apify.",
    )
    outreach_p.add_argument(
        "--apify-output-dir",
        default="./outreach-output/apify-adapter",
        help="Where apify adapter writes per-prospect artifacts.",
    )
    outreach_p.add_argument("--apify-allow-fallback-first", action="store_true")
    outreach_p.add_argument(
        "--include-existing",
        action="store_true",
        help="Also run prospects that were contacted within cooldown window (default is only new).",
    )
    outreach_p.add_argument(
        "--run-followup-due",
        action="store_true",
        help="Allow sending prospects currently in followup_due status during outreach run.",
    )
    outreach_p.add_argument("--cooldown-days", type=int, default=21, help="Skip domains contacted within this window.")
    outreach_p.add_argument("--followup-days", type=int, default=7, help="Mark sent prospects as followup_due after this many days.")
    outreach_p.add_argument("--domain", help="Prospect domain for `outreach update`.")
    outreach_p.add_argument("--new-status", choices=sorted(OUTREACH_STATUSES), help="New status for `outreach update`.")
    outreach_p.add_argument("--note", help="Optional note for `outreach update`.")
    outreach_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    outreach_p.add_argument("--user-agent", default=DEFAULT_UA)
    outreach_p.add_argument("--output", help="Write status markdown to file (used by `outreach status`).")
    outreach_p.set_defaults(func=handle_outreach)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
