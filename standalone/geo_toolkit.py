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
import ssl
import sys
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT = 12
DEFAULT_UA = (
    "geo-llms-toolkit/0.2 standalone-cli (+https://github.com/aronhy/geo-llms-toolkit)"
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
            "version": "0.2.0",
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


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render_scan_output(report: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        return to_csv_report(report)
    return to_markdown_report(report)


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
        description="GEO LLMs Toolkit standalone CLI (scan + llms generation).",
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
