#!/usr/bin/env python3
"""Standalone GEO + LLMS toolkit CLI.

This script is platform-agnostic and can run on any website that exposes
public pages and (preferably) at least one sitemap endpoint.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
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
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.python.adapter_contract import (  # noqa: E402
    AdapterActionResult,
    AdapterFetchOptions,
    AdapterHttpResponse,
    AdapterPage,
    AdapterSiteIdentity,
    GeoAdapterContract,
)

DEFAULT_TIMEOUT = 12
TOOL_VERSION = "0.13.0"
DEFAULT_UA = (
    "geo-llms-toolkit/0.13 standalone-cli (+https://github.com/aronhy/geo-llms-toolkit)"
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

INDEX_STATUSES = {"indexed", "not_indexed", "unknown"}
INDEX_GROUPS = {"core", "blog", "low_value", "other"}

INDEX_AUDIT_ISSUES = {
    "crawl_failed": {
        "priority": "P0",
        "message": "URL 抓取失败（5xx/网络错误）。",
        "fix": "先修可用性与稳定性（源站、WAF、CDN 回源），确保 URL 对搜索引擎 200 可访问。",
    },
    "not_found": {
        "priority": "P0",
        "message": "URL 返回 404/410。",
        "fix": "确认页面是否应存在；应存在则恢复 200 内容，不应存在则从收录池移除。",
    },
    "noindex": {
        "priority": "P0",
        "message": "检测到 noindex（meta 或 x-robots-tag）。",
        "fix": "移除 noindex 或仅对低价值页保留 noindex。",
    },
    "canonical_conflict": {
        "priority": "P1",
        "message": "canonical 指向了不同 URL。",
        "fix": "将 canonical 改为页面自身规范 URL，避免跨模板错误指向。",
    },
    "soft_404": {
        "priority": "P1",
        "message": "疑似软 404（内容空薄或 404 语义）。",
        "fix": "补全主体内容与唯一价值，避免“未找到/404”语义文案。",
    },
    "thin_content": {
        "priority": "P2",
        "message": "页面内容过薄。",
        "fix": "补充核心段落、FAQ、示例，提升正文与信息密度。",
    },
    "weak_internal_links": {
        "priority": "P2",
        "message": "内链信号偏弱（首页未发现该 URL）。",
        "fix": "从首页/目录页/相关文章添加可抓取文本链接。",
    },
    "missing_in_llms": {
        "priority": "P2",
        "message": "该 URL 未出现在 llms 池。",
        "fix": "重建 llms，并确保高价值页被纳入。",
    },
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


class StandaloneWebAdapter(GeoAdapterContract):
    """Default web adapter implementation for standalone CLI."""

    def __init__(
        self,
        base_url: str,
        timeout: int,
        user_agent: str,
        output_dir: Optional[Path] = None,
        extra_low_patterns: Optional[List[str]] = None,
        webhook_url: str = "",
        webhook_token: str = "",
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout = max(1, int(timeout))
        self.user_agent = user_agent or DEFAULT_UA
        self.output_dir = output_dir
        self.extra_low_patterns = extra_low_patterns or []
        self.webhook_url = webhook_url.strip()
        self.webhook_token = webhook_token.strip()

    def get_site_identity(self) -> AdapterSiteIdentity:
        return AdapterSiteIdentity(
            name=normalize_domain(self.base_url),
            url=self.base_url,
            locale="",
        )

    def fetch(self, url: str, options: Optional[AdapterFetchOptions] = None) -> AdapterHttpResponse:
        opts = options or AdapterFetchOptions()
        timeout = max(1, int(opts.timeout or self.timeout))
        user_agent = opts.user_agent or self.user_agent
        max_bytes = max(1024, int(opts.max_bytes or 1_000_000))
        res = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=max_bytes)
        return AdapterHttpResponse(
            url=res.url,
            final_url=res.final_url,
            status=res.status,
            headers=res.headers,
            body=res.body,
            error=res.error or "",
        )

    def list_high_value_pages(self, limit: int) -> List[AdapterPage]:
        max_items = max(1, int(limit))
        urls = collect_urls_from_sitemaps(
            self.base_url,
            timeout=self.timeout,
            user_agent=self.user_agent,
            max_urls=max(max_items * 4, 120),
        )
        if not urls:
            urls = [f"{self.base_url}/"]

        pages: List[AdapterPage] = []
        seen = set()
        for url in urls:
            normalized = normalize_url_for_compare(url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if is_low_value_url(url, extra_patterns=self.extra_low_patterns):
                continue
            pages.append(
                AdapterPage(
                    url=url,
                    group=classify_index_group(url, self.base_url, extra_low_patterns=self.extra_low_patterns),
                )
            )
            if len(pages) >= max_items:
                break

        if not pages:
            homepage = f"{self.base_url}/"
            pages.append(AdapterPage(url=homepage, group="core"))
        return pages

    def list_low_value_pages(self, limit: int) -> List[AdapterPage]:
        max_items = max(1, int(limit))
        urls = collect_urls_from_sitemaps(
            self.base_url,
            timeout=self.timeout,
            user_agent=self.user_agent,
            max_urls=max(max_items * 4, 120),
        )
        pages: List[AdapterPage] = []
        seen = set()
        for url in urls:
            normalized = normalize_url_for_compare(url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if not is_low_value_url(url, extra_patterns=self.extra_low_patterns):
                continue
            pages.append(AdapterPage(url=url, group="low_value"))
            if len(pages) >= max_items:
                break
        return pages

    def write_index_files(self, llms_text: str, llms_full_text: str) -> AdapterActionResult:
        if self.output_dir is None:
            return AdapterActionResult(ok=False, detail="missing_output_dir")
        out = self.output_dir.resolve()
        out.mkdir(parents=True, exist_ok=True)
        llms_path = out / "llms.txt"
        llms_full_path = out / "llms-full.txt"
        llms_path.write_text(llms_text, encoding="utf-8")
        llms_full_path.write_text(llms_full_text, encoding="utf-8")
        return AdapterActionResult(
            ok=True,
            detail="written",
            meta={
                "llms_path": str(llms_path),
                "llms_full_path": str(llms_full_path),
            },
        )

    def send_notification(self, payload: Dict[str, object]) -> AdapterActionResult:
        if not self.webhook_url:
            return AdapterActionResult(ok=False, detail="missing_webhook_url")
        ok, detail = execute_webhook(
            self.webhook_url,
            self.webhook_token,
            payload,
            timeout=self.timeout,
        )
        return AdapterActionResult(ok=bool(ok), detail=detail)

    def purge_cache(self, context: Dict[str, object]) -> AdapterActionResult:
        _ = context
        return AdapterActionResult(ok=False, detail="not_supported_in_standalone")


def parse_html_signals(html: str) -> PageSignals:
    parser = PageSignals()
    parser.feed(html or "")
    parser.close()
    return parser


def normalize_url_for_compare(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = parsed.query.strip()
    if query:
        return f"{parsed.scheme.lower()}://{host}{path}?{query}"
    return f"{parsed.scheme.lower()}://{host}{path}"


def clean_found_url(value: str) -> str:
    return value.strip().strip(".,;:()[]{}<>\"'")


def extract_urls_from_text(text: str, host: str) -> List[str]:
    if not text:
        return []
    found = re.findall(r"https?://[^\s<>\"]+", text, flags=re.I)
    urls: List[str] = []
    for raw in found:
        url = clean_found_url(raw)
        parsed = urlparse(url)
        h = parsed.netloc.lower()
        if h.startswith("www."):
            h = h[4:]
        if h != host:
            continue
        if url not in urls:
            urls.append(url)
    return urls


def extract_links_from_html(html: str, base_url: str, host: str, limit: int = 300) -> List[str]:
    if not html:
        return []
    links = re.findall(r"""<a[^>]+href=["']([^"'#]+)["']""", html, flags=re.I)
    urls: List[str] = []
    for raw in links:
        abs_url = urljoin(base_url + "/", raw.strip())
        parsed = urlparse(abs_url)
        if parsed.scheme not in {"http", "https"}:
            continue
        h = parsed.netloc.lower()
        if h.startswith("www."):
            h = h[4:]
        if h != host:
            continue
        if abs_url not in urls:
            urls.append(abs_url)
        if len(urls) >= limit:
            break
    return urls


def classify_index_group(url: str, base_url: str, extra_low_patterns: Optional[List[str]] = None) -> str:
    if is_low_value_url(url, extra_patterns=extra_low_patterns or []):
        return "low_value"

    normalized_base = normalize_url_for_compare(base_url + "/")
    normalized_url = normalize_url_for_compare(url)
    if normalized_url == normalized_base:
        return "core"

    path = safe_path(url).lower()
    if re.search(r"/(blog|posts?|article|articles|news|insights)/", path):
        return "blog"
    if re.search(r"/20\d{2}/\d{1,2}/", path):
        return "blog"

    depth = len([p for p in path.split("/") if p])
    if depth <= 1:
        return "core"
    if depth >= 2:
        return "blog"
    return "other"


def parse_url_column_file(path: Path) -> List[str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        values: List[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    values.append(item.strip())
                elif isinstance(item, dict) and item.get("url"):
                    values.append(str(item["url"]).strip())
        elif isinstance(data, dict):
            for key in ("urls", "records", "items"):
                arr = data.get(key)
                if not isinstance(arr, list):
                    continue
                for item in arr:
                    if isinstance(item, str):
                        values.append(item.strip())
                    elif isinstance(item, dict) and item.get("url"):
                        values.append(str(item["url"]).strip())
                if values:
                    break
        return [v for v in values if v]

    if suffix in {".csv", ".tsv"}:
        delimiter = "," if suffix == ".csv" else "\t"
        rows: List[str] = []
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            if not reader.fieldnames:
                return rows
            normalized = {name.lower(): name for name in reader.fieldnames}
            col = normalized.get("url") or normalized.get("loc")
            if not col:
                return rows
            for row in reader:
                val = (row.get(col) or "").strip()
                if val:
                    rows.append(val)
        return rows

    values: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            values.append(raw)
    return values


def discover_index_url_pool(
    base_url: str,
    timeout: int,
    user_agent: str,
    max_urls: int,
    extra_low_patterns: Optional[List[str]] = None,
) -> Dict[str, object]:
    base_host = normalize_domain(base_url)
    source_map: Dict[str, List[str]] = {
        "sitemap": [],
        "llms": [],
        "homepage_links": [],
    }

    sitemap_urls = collect_urls_from_sitemaps(
        base_url,
        timeout=timeout,
        user_agent=user_agent,
        max_urls=max(max_urls * 3, 300),
    )
    source_map["sitemap"] = sitemap_urls[:]

    for llms_path in ["/llms.txt", "/llms-full.txt"]:
        res = fetch_url(base_url + llms_path, timeout=timeout, user_agent=user_agent, max_bytes=1_500_000)
        if res.status == 200 and "text/" in parse_content_type(res.headers):
            urls = extract_urls_from_text(res.body, base_host)
            for u in urls:
                if u not in source_map["llms"]:
                    source_map["llms"].append(u)

    home = fetch_url(base_url + "/", timeout=timeout, user_agent=user_agent, max_bytes=1_500_000)
    if home.status == 200 and "html" in parse_content_type(home.headers):
        source_map["homepage_links"] = extract_links_from_html(home.body, base_url, base_host, limit=max_urls * 3)

    union = {}
    homepage_url = f"{base_url}/"
    for src, urls in source_map.items():
        for u in urls:
            normalized = normalize_url_for_compare(u)
            if not normalized:
                continue
            if normalized not in union:
                union[normalized] = {"url": u, "sources": set()}
            union[normalized]["sources"].add(src)

    normalized_homepage = normalize_url_for_compare(homepage_url)
    if normalized_homepage not in union:
        union[normalized_homepage] = {"url": homepage_url, "sources": {"seed"}}

    urls: List[Dict[str, object]] = []
    for info in union.values():
        url = str(info["url"])
        group = classify_index_group(url, base_url, extra_low_patterns=extra_low_patterns or [])
        urls.append(
            {
                "url": url,
                "group": group,
                "sources": sorted(list(info["sources"])),
            }
        )
    urls.sort(key=lambda x: (str(x["group"]), str(x["url"])))
    if len(urls) > max_urls:
        urls = urls[:max_urls]

    group_counts: Dict[str, int] = {g: 0 for g in sorted(INDEX_GROUPS)}
    for item in urls:
        group_counts[str(item["group"])] = group_counts.get(str(item["group"]), 0) + 1

    return {
        "meta": {
            "target": base_url,
            "target_domain": base_host,
            "generated_at_utc": now_utc(),
            "tool": "geo-llms-toolkit standalone-cli",
            "version": TOOL_VERSION,
        },
        "summary": {
            "urls_total": len(urls),
            "source_counts": {k: len(v) for k, v in source_map.items()},
            "groups": group_counts,
        },
        "urls": urls,
    }


def load_index_pool_from_file(path: Path, base_url: str, extra_low_patterns: Optional[List[str]] = None) -> List[Dict[str, object]]:
    raw_urls = parse_url_column_file(path)
    base_host = normalize_domain(base_url)
    seen = set()
    pool: List[Dict[str, object]] = []
    for raw in raw_urls:
        url = raw.strip()
        if not re.match(r"^https?://", url, flags=re.I):
            url = urljoin(base_url + "/", url.lstrip("/"))
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host != base_host:
            continue
        normalized = normalize_url_for_compare(url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        pool.append(
            {
                "url": url,
                "group": classify_index_group(url, base_url, extra_low_patterns=extra_low_patterns or []),
                "sources": ["input"],
            }
        )
    return pool


def list_index_track_snapshots(history_dir: Path, domain: str) -> List[Path]:
    if not history_dir.exists():
        return []
    files = sorted(history_dir.glob(f"index-track-{domain}-*.json"))
    return [f for f in files if f.is_file()]


def load_track_records(path: Path) -> Dict[str, Dict[str, object]]:
    data = read_json_file(path)
    records = data.get("records", [])
    out: Dict[str, Dict[str, object]] = {}
    if not isinstance(records, list):
        return out
    for item in records:
        if not isinstance(item, dict):
            continue
        u = str(item.get("url") or "")
        key = normalize_url_for_compare(u)
        if not key:
            continue
        out[key] = item
    return out

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


def probe_index_status(
    url: str,
    timeout: int,
    user_agent: str,
    search_depth: int,
    strict_search: bool,
) -> Dict[str, object]:
    checked_at = now_utc()
    res = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=1_500_000)
    ctype = parse_content_type(res.headers)
    x_robots = (res.headers.get("x-robots-tag") or "").lower()

    status = "unknown"
    reason = "unclassified"
    indexable = False
    canonical = ""
    meta_robots: List[str] = []
    search_hit = False
    search_results = 0

    if res.status in {404, 410}:
        status = "not_indexed"
        reason = f"http_{res.status}"
    elif res.status == 0:
        status = "unknown"
        reason = "fetch_error"
    elif res.status >= 500:
        status = "unknown"
        reason = f"http_{res.status}"
    elif res.status >= 300 and res.status < 400:
        status = "unknown"
        reason = f"http_{res.status}"
    elif res.status == 200:
        if "html" in ctype:
            page = parse_html_signals(res.body)
            canonical = page.canonical
            meta_robots = page.meta_robots[:]
            robots_blob = ",".join(page.meta_robots).lower()
            if "noindex" in robots_blob or "noindex" in x_robots:
                status = "not_indexed"
                reason = "noindex"
            else:
                indexable = True
        else:
            if "noindex" in x_robots:
                status = "not_indexed"
                reason = "noindex"
            else:
                indexable = True

        if indexable:
            query = f"\"{url}\""
            serp_urls = fetch_bing_results(query, max(5, min(search_depth, 20)), timeout, user_agent)
            search_results = len(serp_urls)
            target_norm = normalize_url_for_compare(url)
            for u in serp_urls:
                if normalize_url_for_compare(u) == target_norm:
                    search_hit = True
                    break
            if search_hit:
                status = "indexed"
                reason = "search_exact_match"
            else:
                if search_results == 0:
                    status = "unknown"
                    reason = "search_empty"
                else:
                    status = "not_indexed" if strict_search else "unknown"
                    reason = "search_no_match"

    return {
        "url": url,
        "status": status if status in INDEX_STATUSES else "unknown",
        "reason": reason,
        "checked_at_utc": checked_at,
        "http_status": res.status,
        "content_type": ctype,
        "indexable": bool(indexable),
        "canonical": canonical,
        "meta_robots": meta_robots,
        "x_robots_tag": x_robots,
        "search_hit": bool(search_hit),
        "search_results": search_results,
        "error": res.error or "",
    }


def merge_index_track_records(
    current_records: List[Dict[str, object]],
    previous_records: Dict[str, Dict[str, object]],
) -> List[Dict[str, object]]:
    merged: List[Dict[str, object]] = []
    for item in current_records:
        url = str(item.get("url") or "")
        key = normalize_url_for_compare(url)
        prev = previous_records.get(key, {}) if key else {}
        checked_at = str(item.get("checked_at_utc") or now_utc())
        current_status = str(item.get("status") or "unknown")
        prev_status = str(prev.get("status") or "")

        item["first_seen_utc"] = str(prev.get("first_seen_utc") or checked_at)

        prev_first_indexed = str(prev.get("first_indexed_utc") or "")
        if current_status == "indexed":
            item["first_indexed_utc"] = prev_first_indexed or checked_at
        else:
            item["first_indexed_utc"] = prev_first_indexed

        prev_first_not_indexed = str(prev.get("first_not_indexed_utc") or "")
        if current_status == "not_indexed":
            item["first_not_indexed_utc"] = prev_first_not_indexed or checked_at
        else:
            item["first_not_indexed_utc"] = prev_first_not_indexed

        if prev_status and prev_status != current_status:
            item["last_status_change_utc"] = checked_at
        else:
            item["last_status_change_utc"] = str(prev.get("last_status_change_utc") or checked_at)

        if item.get("first_not_indexed_utc") and current_status == "not_indexed":
            dt = parse_utc(str(item["first_not_indexed_utc"]))
            if dt:
                age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
                item["not_indexed_age_days"] = round(age_days, 2)
            else:
                item["not_indexed_age_days"] = 0.0
        else:
            item["not_indexed_age_days"] = 0.0

        merged.append(item)

    merged.sort(key=lambda x: str(x.get("url") or ""))
    return merged


def compute_index_track_changes(
    records: List[Dict[str, object]],
    previous_records: Dict[str, Dict[str, object]],
    long_unindexed_days: int,
) -> Dict[str, List[Dict[str, object]]]:
    newly_indexed: List[Dict[str, object]] = []
    dropped_indexed: List[Dict[str, object]] = []
    status_changed: List[Dict[str, object]] = []
    long_unindexed: List[Dict[str, object]] = []

    for item in records:
        url = str(item.get("url") or "")
        key = normalize_url_for_compare(url)
        prev = previous_records.get(key, {}) if key else {}
        prev_status = str(prev.get("status") or "")
        cur_status = str(item.get("status") or "unknown")

        if prev_status and prev_status != cur_status:
            status_changed.append(
                {
                    "url": url,
                    "from": prev_status,
                    "to": cur_status,
                }
            )
        if cur_status == "indexed" and prev_status in {"not_indexed", "unknown"}:
            newly_indexed.append(
                {
                    "url": url,
                    "from": prev_status,
                    "to": "indexed",
                }
            )
        if prev_status == "indexed" and cur_status in {"not_indexed", "unknown"}:
            dropped_indexed.append(
                {
                    "url": url,
                    "from": "indexed",
                    "to": cur_status,
                }
            )

        if cur_status == "not_indexed":
            age_days = float(item.get("not_indexed_age_days") or 0.0)
            if age_days >= float(max(1, long_unindexed_days)):
                long_unindexed.append(
                    {
                        "url": url,
                        "group": item.get("group", "other"),
                        "age_days": age_days,
                        "reason": item.get("reason", ""),
                    }
                )

    newly_indexed.sort(key=lambda x: str(x["url"]))
    dropped_indexed.sort(key=lambda x: str(x["url"]))
    status_changed.sort(key=lambda x: str(x["url"]))
    long_unindexed.sort(key=lambda x: float(x.get("age_days", 0.0)), reverse=True)

    return {
        "newly_indexed": newly_indexed,
        "dropped_indexed": dropped_indexed,
        "status_changed": status_changed,
        "long_unindexed": long_unindexed,
    }


def summarize_index_track_records(records: List[Dict[str, object]]) -> Dict[str, object]:
    by_status = {"indexed": 0, "not_indexed": 0, "unknown": 0}
    by_group: Dict[str, Dict[str, int]] = {g: {"total": 0, "indexed": 0} for g in sorted(INDEX_GROUPS)}
    for row in records:
        status = str(row.get("status") or "unknown")
        group = str(row.get("group") or "other")
        by_status[status if status in by_status else "unknown"] += 1
        if group not in by_group:
            by_group[group] = {"total": 0, "indexed": 0}
        by_group[group]["total"] += 1
        if status == "indexed":
            by_group[group]["indexed"] += 1

    total = len(records)
    indexed = by_status["indexed"]
    index_rate = round((indexed / max(1, total)) * 100.0, 2)
    return {
        "total": total,
        "indexed": indexed,
        "not_indexed": by_status["not_indexed"],
        "unknown": by_status["unknown"],
        "index_rate_pct": index_rate,
        "groups": by_group,
    }


def normalize_status_filter(raw: str) -> List[str]:
    values = [s.strip().lower() for s in raw.split(",") if s.strip()]
    out = [s for s in values if s in INDEX_STATUSES]
    return out or ["not_indexed", "unknown"]


def load_index_pool_from_discover_report(
    path: Path,
    base_url: str,
    extra_low_patterns: Optional[List[str]] = None,
) -> List[Dict[str, object]]:
    data = read_json_file(path)
    items = data.get("urls", [])
    if not isinstance(items, list):
        return []
    pool: List[Dict[str, object]] = []
    seen = set()
    for item in items:
        if isinstance(item, str):
            url = item
            group = classify_index_group(url, base_url, extra_low_patterns=extra_low_patterns or [])
            sources = ["discover"]
        elif isinstance(item, dict):
            url = str(item.get("url") or "")
            group = str(item.get("group") or classify_index_group(url, base_url, extra_low_patterns=extra_low_patterns or []))
            sources = item.get("sources") if isinstance(item.get("sources"), list) else ["discover"]
        else:
            continue
        key = normalize_url_for_compare(url)
        if not key or key in seen:
            continue
        seen.add(key)
        pool.append({"url": url, "group": group, "sources": sources})
    return pool


def load_index_pool_from_track_report(path: Path, statuses: Sequence[str]) -> List[Dict[str, object]]:
    data = read_json_file(path)
    records = data.get("records", [])
    pool: List[Dict[str, object]] = []
    seen = set()
    status_set = {s for s in statuses if s in INDEX_STATUSES}
    for row in records if isinstance(records, list) else []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").lower()
        if status_set and status not in status_set:
            continue
        url = str(row.get("url") or "")
        key = normalize_url_for_compare(url)
        if not key or key in seen:
            continue
        seen.add(key)
        pool.append(
            {
                "url": url,
                "group": str(row.get("group") or "other"),
                "sources": ["track-report"],
            }
        )
    return pool


def resolve_index_pool(
    base_url: str,
    timeout: int,
    user_agent: str,
    max_urls: int,
    extra_low_patterns: Optional[List[str]],
    urls_file: str,
    discover_report_file: str,
) -> List[Dict[str, object]]:
    if urls_file:
        path = Path(urls_file).expanduser().resolve()
        return load_index_pool_from_file(path, base_url, extra_low_patterns=extra_low_patterns or [])[:max_urls]
    if discover_report_file:
        path = Path(discover_report_file).expanduser().resolve()
        return load_index_pool_from_discover_report(path, base_url, extra_low_patterns=extra_low_patterns or [])[:max_urls]
    auto = discover_index_url_pool(
        base_url=base_url,
        timeout=timeout,
        user_agent=user_agent,
        max_urls=max_urls,
        extra_low_patterns=extra_low_patterns or [],
    )
    return list(auto.get("urls", []))[:max_urls]


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
    adapter = StandaloneWebAdapter(
        base_url=base_url,
        timeout=timeout,
        user_agent=user_agent,
        output_dir=output_dir,
        extra_low_patterns=extra_exclude,
    )
    sitemap_urls = collect_urls_from_sitemaps(base_url, timeout, user_agent, max_urls=max_items * 4)
    high_value_pages = adapter.list_high_value_pages(max_items)
    filtered = [page.url for page in high_value_pages]

    entries: List[Dict[str, str]] = []
    for url in filtered:
        fetched = adapter.fetch(
            url,
            AdapterFetchOptions(timeout=timeout, user_agent=user_agent, max_bytes=900_000),
        )
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
    llms_text = "\n".join(llms_lines).rstrip() + "\n"
    llms_full_text = "\n".join(full_lines).rstrip() + "\n"
    write_result = adapter.write_index_files(llms_text, llms_full_text)
    if not write_result.ok:
        raise ValueError(f"failed to write llms files via adapter: {write_result.detail}")
    llms_path = Path(str(write_result.meta.get("llms_path") or output_dir / "llms.txt"))
    llms_full_path = Path(str(write_result.meta.get("llms_full_path") or output_dir / "llms-full.txt"))

    return {
        "base_url": base_url,
        "generated_at_utc": generated,
        "sitemap_discovered": len(sitemap_urls),
        "included_entries": len(entries),
        "llms_path": str(llms_path),
        "llms_full_path": str(llms_full_path),
    }


def render_index_discover_output(report: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        from io import StringIO

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["url", "group", "sources"])
        for row in report.get("urls", []):
            writer.writerow([row.get("url", ""), row.get("group", ""), ";".join(row.get("sources", []))])
        return buf.getvalue()

    summary = report.get("summary", {})
    lines = [
        f"# GEO Index Discover - {report.get('meta', {}).get('target', '-')}",
        "",
        f"- Generated (UTC): {report.get('meta', {}).get('generated_at_utc', '-')}",
        f"- URL pool total: {summary.get('urls_total', 0)}",
        f"- Source counts: {json.dumps(summary.get('source_counts', {}), ensure_ascii=False)}",
        f"- Group counts: {json.dumps(summary.get('groups', {}), ensure_ascii=False)}",
        "",
        "## URL Pool",
        "",
        "| URL | Group | Sources |",
        "| --- | --- | --- |",
    ]
    for row in report.get("urls", []):
        lines.append(f"| {row.get('url', '')} | {row.get('group', '')} | {', '.join(row.get('sources', []))} |")
    return "\n".join(lines) + "\n"


def render_index_track_output(report: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        from io import StringIO

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "url",
                "group",
                "status",
                "reason",
                "http_status",
                "indexable",
                "search_hit",
                "not_indexed_age_days",
                "first_seen_utc",
                "first_indexed_utc",
                "first_not_indexed_utc",
                "last_status_change_utc",
            ]
        )
        for row in report.get("records", []):
            writer.writerow(
                [
                    row.get("url", ""),
                    row.get("group", ""),
                    row.get("status", ""),
                    row.get("reason", ""),
                    row.get("http_status", ""),
                    row.get("indexable", False),
                    row.get("search_hit", False),
                    row.get("not_indexed_age_days", 0),
                    row.get("first_seen_utc", ""),
                    row.get("first_indexed_utc", ""),
                    row.get("first_not_indexed_utc", ""),
                    row.get("last_status_change_utc", ""),
                ]
            )
        return buf.getvalue()

    summary = report.get("summary", {})
    changes = report.get("changes", {})
    lines = [
        f"# GEO Index Track - {report.get('meta', {}).get('target', '-')}",
        "",
        f"- Generated (UTC): {report.get('meta', {}).get('generated_at_utc', '-')}",
        f"- Index rate: {summary.get('index_rate_pct', 0)}%",
        f"- Indexed / Not indexed / Unknown: {summary.get('indexed', 0)} / {summary.get('not_indexed', 0)} / {summary.get('unknown', 0)}",
        f"- Newly indexed: {len(changes.get('newly_indexed', []))}",
        f"- Dropped indexed: {len(changes.get('dropped_indexed', []))}",
        f"- Long unindexed: {len(changes.get('long_unindexed', []))}",
        "",
        "## Priority Lists",
        "",
        f"- 新增收录: {', '.join([r.get('url', '') for r in changes.get('newly_indexed', [])[:8]]) or '-'}",
        f"- 掉索引: {', '.join([r.get('url', '') for r in changes.get('dropped_indexed', [])[:8]]) or '-'}",
        f"- 长期未收录: {', '.join([r.get('url', '') for r in changes.get('long_unindexed', [])[:8]]) or '-'}",
        "",
    ]
    return "\n".join(lines)


def render_index_submit_output(report: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        from io import StringIO

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["url", "group", "status", "provider", "detail"])
        for row in report.get("items", []):
            writer.writerow([row.get("url", ""), row.get("group", ""), row.get("status", ""), row.get("provider", ""), row.get("detail", "")])
        return buf.getvalue()

    summary = report.get("summary", {})
    lines = [
        f"# GEO Index Submit - {report.get('meta', {}).get('target', '-')}",
        "",
        f"- Generated (UTC): {report.get('meta', {}).get('generated_at_utc', '-')}",
        f"- Provider: {report.get('meta', {}).get('provider', '-')}",
        f"- Total: {summary.get('total', 0)}",
        f"- Submitted: {summary.get('submitted', 0)}",
        f"- Skipped: {summary.get('skipped', 0)}",
        f"- Failed: {summary.get('failed', 0)}",
        "",
        "## Result",
        "",
        "| URL | Group | Status | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for row in report.get("items", []):
        detail = str(row.get("detail", "")).replace("|", "\\|")
        lines.append(f"| {row.get('url', '')} | {row.get('group', '')} | {row.get('status', '')} | {detail} |")
    return "\n".join(lines) + "\n"


def render_index_audit_output(report: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        from io import StringIO

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["url", "group", "status", "max_priority", "issues", "fixes"])
        for row in report.get("records", []):
            issue_codes = ";".join([str(x.get("code", "")) for x in row.get("issues", []) if isinstance(x, dict)])
            fixes = ";".join([str(x.get("fix", "")) for x in row.get("issues", []) if isinstance(x, dict)])
            writer.writerow([row.get("url", ""), row.get("group", ""), row.get("status", ""), row.get("max_priority", ""), issue_codes, fixes])
        return buf.getvalue()

    summary = report.get("summary", {})
    lines = [
        f"# GEO Index Audit - {report.get('meta', {}).get('target', '-')}",
        "",
        f"- Generated (UTC): {report.get('meta', {}).get('generated_at_utc', '-')}",
        f"- URL total: {summary.get('total', 0)}",
        f"- PASS/WARN/FAIL: {summary.get('pass', 0)}/{summary.get('warn', 0)}/{summary.get('fail', 0)}",
        f"- P0/P1/P2: {summary.get('p0', 0)}/{summary.get('p1', 0)}/{summary.get('p2', 0)}",
        "",
        "## Top Fixes",
        "",
        "| Issue | Priority | Count | Fix |",
        "| --- | --- | ---: | --- |",
    ]
    for item in report.get("issues_summary", [])[:12]:
        fix = str(item.get("fix", "")).replace("|", "\\|")
        lines.append(f"| {item.get('code', '')} | {item.get('priority', '')} | {item.get('count', 0)} | {fix} |")
    lines.extend(["", "## Problem URLs", "", "| URL | Group | Status | Max Priority | Issues |", "| --- | --- | --- | --- | --- |"])
    for row in report.get("records", []):
        if row.get("status") == "pass":
            continue
        issue_codes = ", ".join([str(x.get("code", "")) for x in row.get("issues", []) if isinstance(x, dict)])
        lines.append(
            f"| {row.get('url', '')} | {row.get('group', '')} | {row.get('status', '')} | {row.get('max_priority', '')} | {issue_codes} |"
        )
    return "\n".join(lines) + "\n"


def render_index_report_output(report: Dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output_format == "csv":
        from io import StringIO

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["generated_at_utc", "index_rate_pct", "indexed", "total", "newly_indexed", "dropped_indexed"])
        for row in report.get("trend", []):
            writer.writerow(
                [
                    row.get("generated_at_utc", ""),
                    row.get("index_rate_pct", 0),
                    row.get("indexed", 0),
                    row.get("total", 0),
                    row.get("newly_indexed", 0),
                    row.get("dropped_indexed", 0),
                ]
            )
        return buf.getvalue()

    summary = report.get("summary", {})
    lines = [
        f"# GEO Index Weekly Report - {report.get('meta', {}).get('target', '-')}",
        "",
        f"- Window: {report.get('meta', {}).get('window_days', 0)} days",
        f"- Snapshots: {report.get('meta', {}).get('snapshots', 0)}",
        f"- Current index rate: {summary.get('current_index_rate_pct', 0)}%",
        f"- Avg indexing latency: {summary.get('avg_indexing_days', 0)} days",
        f"- Deindex rate: {summary.get('deindex_rate_pct', 0)}%",
        f"- Recovery rate: {summary.get('recovery_rate_pct', 0)}%",
        "",
        "## Template Performance",
        "",
        "| Group | Indexed | Total | Index Rate % |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in report.get("template_performance", []):
        lines.append(
            f"| {row.get('group', '')} | {row.get('indexed', 0)} | {row.get('total', 0)} | {row.get('index_rate_pct', 0)} |"
        )
    lines.extend(["", "## Trend", "", "| Time (UTC) | Index Rate % | Indexed | Total | New | Dropped |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in report.get("trend", []):
        lines.append(
            f"| {row.get('generated_at_utc', '')} | {row.get('index_rate_pct', 0)} | {row.get('indexed', 0)} | {row.get('total', 0)} | {row.get('newly_indexed', 0)} | {row.get('dropped_indexed', 0)} |"
        )
    focus = report.get("focus_lists", {})
    lines.extend(
        [
            "",
            "## Focus Lists",
            "",
            f"- 新增收录: {', '.join([x.get('url', '') for x in focus.get('newly_indexed', [])[:8]]) or '-'}",
            f"- 掉索引: {', '.join([x.get('url', '') for x in focus.get('dropped_indexed', [])[:8]]) or '-'}",
            f"- 长期未收录: {', '.join([x.get('url', '') for x in focus.get('long_unindexed', [])[:8]]) or '-'}",
            "",
        ]
    )
    return "\n".join(lines)


def execute_index_submit_command(command_template: str, payload: Dict[str, object], timeout: int) -> Tuple[bool, str]:
    raw_values = {
        "url": str(payload.get("url") or ""),
        "type": str(payload.get("type") or ""),
        "target_domain": str(payload.get("target_domain") or ""),
        "provider": str(payload.get("provider") or ""),
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
        return proc.returncode == 0, out[:240]
    except Exception as e:
        return False, str(e)


def is_google_indexing_supported_url(url: str) -> bool:
    path = safe_path(url).lower()
    return bool(re.search(r"/(job|jobs|career|careers|hiring|live|stream|broadcast|event|events)/", path))


def submit_to_google_indexing_api(
    url: str,
    token: str,
    timeout: int,
    notification_type: str = "URL_UPDATED",
) -> Tuple[bool, str]:
    if not token:
        return False, "missing_google_token"
    body = json.dumps({"url": url, "type": notification_type}, ensure_ascii=False).encode("utf-8")
    req = Request(
        "https://indexing.googleapis.com/v3/urlNotifications:publish",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(1200).decode("utf-8", errors="replace")
            return (200 <= int(code) < 300), f"HTTP {code}: {raw[:220]}"
    except Exception as e:
        return False, str(e)


def extract_visible_text_length(html: str) -> int:
    if not html:
        return 0
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return len(text)


def audit_index_url(
    url: str,
    group: str,
    timeout: int,
    user_agent: str,
    thin_threshold_chars: int,
    homepage_body: str,
    llms_url_set: set,
) -> Dict[str, object]:
    issues: List[Dict[str, object]] = []
    res = fetch_url(url, timeout=timeout, user_agent=user_agent, max_bytes=1_500_000)
    ctype = parse_content_type(res.headers)
    x_robots = (res.headers.get("x-robots-tag") or "").lower()

    page = PageSignals()
    if res.status == 200 and "html" in ctype:
        page = parse_html_signals(res.body)

    if res.status == 0 or res.status >= 500:
        cfg = INDEX_AUDIT_ISSUES["crawl_failed"]
        issues.append({"code": "crawl_failed", "priority": cfg["priority"], "message": cfg["message"], "fix": cfg["fix"]})
    elif res.status in {404, 410}:
        cfg = INDEX_AUDIT_ISSUES["not_found"]
        issues.append({"code": "not_found", "priority": cfg["priority"], "message": cfg["message"], "fix": cfg["fix"]})
    else:
        robots_blob = ",".join(page.meta_robots).lower() if page.meta_robots else ""
        if "noindex" in x_robots or "noindex" in robots_blob:
            cfg = INDEX_AUDIT_ISSUES["noindex"]
            issues.append({"code": "noindex", "priority": cfg["priority"], "message": cfg["message"], "fix": cfg["fix"]})

        canonical = normalize_url_for_compare(page.canonical) if page.canonical else ""
        self_url = normalize_url_for_compare(url)
        if canonical and self_url and canonical != self_url:
            cfg = INDEX_AUDIT_ISSUES["canonical_conflict"]
            issues.append(
                {
                    "code": "canonical_conflict",
                    "priority": cfg["priority"],
                    "message": cfg["message"],
                    "fix": cfg["fix"],
                    "canonical": page.canonical,
                }
            )

        title_lower = (page.title or "").lower()
        body_lower = (res.body[:12000] or "").lower()
        if "404" in title_lower or "not found" in body_lower[:3000] or "页面不存在" in body_lower[:3000]:
            cfg = INDEX_AUDIT_ISSUES["soft_404"]
            issues.append({"code": "soft_404", "priority": cfg["priority"], "message": cfg["message"], "fix": cfg["fix"]})

        text_len = extract_visible_text_length(res.body)
        if group in {"blog", "core"} and text_len < max(120, thin_threshold_chars):
            cfg = INDEX_AUDIT_ISSUES["thin_content"]
            issues.append(
                {
                    "code": "thin_content",
                    "priority": cfg["priority"],
                    "message": cfg["message"],
                    "fix": cfg["fix"],
                    "chars": text_len,
                }
            )

        if group in {"blog", "core"}:
            key = normalize_url_for_compare(url)
            if key and key not in llms_url_set:
                cfg = INDEX_AUDIT_ISSUES["missing_in_llms"]
                issues.append({"code": "missing_in_llms", "priority": cfg["priority"], "message": cfg["message"], "fix": cfg["fix"]})
            if homepage_body and url.lower() not in homepage_body:
                cfg = INDEX_AUDIT_ISSUES["weak_internal_links"]
                issues.append({"code": "weak_internal_links", "priority": cfg["priority"], "message": cfg["message"], "fix": cfg["fix"]})

    max_priority = "PASS"
    if any(i.get("priority") == "P0" for i in issues):
        max_priority = "P0"
    elif any(i.get("priority") == "P1" for i in issues):
        max_priority = "P1"
    elif any(i.get("priority") == "P2" for i in issues):
        max_priority = "P2"

    status = "pass"
    if max_priority == "P0":
        status = "fail"
    elif max_priority in {"P1", "P2"}:
        status = "warn"

    return {
        "url": url,
        "group": group,
        "status": status,
        "max_priority": max_priority,
        "http_status": res.status,
        "content_type": ctype,
        "issues": issues,
    }


def build_index_report_from_history(base_url: str, history_dir: Path, days: int) -> Dict[str, object]:
    domain = normalize_domain(base_url)
    snapshots = list_index_track_snapshots(history_dir, domain)
    if not snapshots:
        raise ValueError(f"no index track snapshots found under {history_dir}")

    now = datetime.now(timezone.utc)
    min_time = now.timestamp() - (max(1, days) * 86400)
    selected: List[Dict[str, object]] = []
    for p in snapshots:
        data = read_json_file(p)
        ts = parse_utc(str(data.get("meta", {}).get("generated_at_utc") or ""))
        if not ts:
            continue
        if ts.timestamp() >= min_time:
            selected.append(data)

    if not selected:
        raise ValueError(f"no snapshots in last {days} days")

    selected.sort(key=lambda x: str(x.get("meta", {}).get("generated_at_utc") or ""))
    latest = selected[-1]
    latest_records = latest.get("records", []) if isinstance(latest.get("records"), list) else []

    trend: List[Dict[str, object]] = []
    deindex_events = 0
    indexed_exposure = 0
    for idx, snap in enumerate(selected):
        summary = snap.get("summary", {}) if isinstance(snap.get("summary"), dict) else {}
        changes = snap.get("changes", {}) if isinstance(snap.get("changes"), dict) else {}
        trend.append(
            {
                "generated_at_utc": snap.get("meta", {}).get("generated_at_utc", ""),
                "index_rate_pct": summary.get("index_rate_pct", 0),
                "indexed": summary.get("indexed", 0),
                "total": summary.get("total", 0),
                "newly_indexed": len(changes.get("newly_indexed", [])) if isinstance(changes.get("newly_indexed"), list) else 0,
                "dropped_indexed": len(changes.get("dropped_indexed", [])) if isinstance(changes.get("dropped_indexed"), list) else 0,
            }
        )

        if idx == 0:
            continue
        prev = selected[idx - 1]
        prev_records = prev.get("records", []) if isinstance(prev.get("records"), list) else []
        cur_records = snap.get("records", []) if isinstance(snap.get("records"), list) else []
        prev_indexed = {normalize_url_for_compare(str(r.get("url") or "")) for r in prev_records if isinstance(r, dict) and str(r.get("status")) == "indexed"}
        cur_indexed = {normalize_url_for_compare(str(r.get("url") or "")) for r in cur_records if isinstance(r, dict) and str(r.get("status")) == "indexed"}
        prev_indexed = {u for u in prev_indexed if u}
        cur_indexed = {u for u in cur_indexed if u}
        indexed_exposure += len(prev_indexed)
        deindex_events += len(prev_indexed - cur_indexed)

    latency_days: List[float] = []
    recovered = 0
    recovery_base = 0
    group_stats: Dict[str, Dict[str, int]] = {}
    long_unindexed: List[Dict[str, object]] = []
    for row in latest_records:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "unknown")
        group = str(row.get("group") or "other")
        g = group_stats.setdefault(group, {"indexed": 0, "total": 0})
        g["total"] += 1
        if status == "indexed":
            g["indexed"] += 1

        first_seen = parse_utc(str(row.get("first_seen_utc") or ""))
        first_indexed = parse_utc(str(row.get("first_indexed_utc") or ""))
        if first_seen and first_indexed and first_indexed >= first_seen:
            latency_days.append((first_indexed - first_seen).total_seconds() / 86400.0)

        first_not_idx = parse_utc(str(row.get("first_not_indexed_utc") or ""))
        if first_not_idx:
            recovery_base += 1
            if status == "indexed":
                recovered += 1
            age_days = (now - first_not_idx).total_seconds() / 86400.0
            if status == "not_indexed" and age_days >= 14:
                long_unindexed.append(
                    {
                        "url": row.get("url", ""),
                        "group": group,
                        "age_days": round(age_days, 2),
                    }
                )

    template_performance = []
    for group, g in sorted(group_stats.items(), key=lambda x: x[0]):
        total = g["total"]
        indexed = g["indexed"]
        template_performance.append(
            {
                "group": group,
                "indexed": indexed,
                "total": total,
                "index_rate_pct": round((indexed / max(1, total)) * 100.0, 2),
            }
        )

    latest_changes = latest.get("changes", {}) if isinstance(latest.get("changes"), dict) else {}
    long_unindexed.sort(key=lambda x: float(x.get("age_days", 0.0)), reverse=True)
    avg_indexing_days = round(sum(latency_days) / len(latency_days), 2) if latency_days else 0.0
    deindex_rate_pct = round((deindex_events / max(1, indexed_exposure)) * 100.0, 2)
    recovery_rate_pct = round((recovered / max(1, recovery_base)) * 100.0, 2)

    return {
        "meta": {
            "target": base_url,
            "target_domain": domain,
            "generated_at_utc": now_utc(),
            "window_days": max(1, days),
            "snapshots": len(selected),
        },
        "summary": {
            "current_index_rate_pct": float(latest.get("summary", {}).get("index_rate_pct", 0)),
            "avg_indexing_days": avg_indexing_days,
            "deindex_rate_pct": deindex_rate_pct,
            "recovery_rate_pct": recovery_rate_pct,
        },
        "template_performance": template_performance,
        "trend": trend,
        "focus_lists": {
            "newly_indexed": latest_changes.get("newly_indexed", []) if isinstance(latest_changes.get("newly_indexed"), list) else [],
            "dropped_indexed": latest_changes.get("dropped_indexed", []) if isinstance(latest_changes.get("dropped_indexed"), list) else [],
            "long_unindexed": long_unindexed[:30],
        },
    }


def handle_index(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.target)
    action = str(args.index_action or "discover")
    history_dir = Path(args.history_dir).expanduser().resolve()
    history_dir.mkdir(parents=True, exist_ok=True)
    extra_low_patterns = args.low_value_pattern or []

    if action == "discover":
        report = discover_index_url_pool(
            base_url=base_url,
            timeout=args.timeout,
            user_agent=args.user_agent,
            max_urls=args.max_urls,
            extra_low_patterns=extra_low_patterns,
        )
        body = render_index_discover_output(report, args.format)
        if args.output:
            out = Path(args.output).expanduser().resolve()
            write_text(out, body)
            print(f"Index discover report saved: {out}")
        else:
            sys.stdout.write(body)
        return 0

    if action == "track":
        pool = resolve_index_pool(
            base_url=base_url,
            timeout=args.timeout,
            user_agent=args.user_agent,
            max_urls=args.max_urls,
            extra_low_patterns=extra_low_patterns,
            urls_file=args.urls_file or "",
            discover_report_file=args.discover_report or "",
        )
        if not pool:
            raise ValueError("no URLs available for tracking")

        domain = normalize_domain(base_url)
        snapshots = list_index_track_snapshots(history_dir, domain)
        previous_snapshot = snapshots[-1] if snapshots else None
        previous_records = load_track_records(previous_snapshot) if previous_snapshot else {}

        current_records: List[Dict[str, object]] = []
        for item in pool:
            url = str(item.get("url") or "")
            group = str(item.get("group") or "other")
            probed = probe_index_status(
                url=url,
                timeout=args.timeout,
                user_agent=args.user_agent,
                search_depth=args.search_depth,
                strict_search=bool(args.strict_search),
            )
            probed["group"] = group
            current_records.append(probed)

        merged = merge_index_track_records(current_records, previous_records)
        changes = compute_index_track_changes(
            records=merged,
            previous_records=previous_records,
            long_unindexed_days=args.long_unindexed_days,
        )
        summary = summarize_index_track_records(merged)
        report = {
            "meta": {
                "target": base_url,
                "target_domain": domain,
                "generated_at_utc": now_utc(),
                "tool": "geo-llms-toolkit standalone-cli",
                "version": TOOL_VERSION,
                "previous_snapshot": str(previous_snapshot) if previous_snapshot else "",
            },
            "summary": summary,
            "changes": changes,
            "records": merged,
        }

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot_path = history_dir / f"index-track-{domain}-{stamp}.json"
        write_text(snapshot_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

        if args.alert_webhook and (len(changes["dropped_indexed"]) > 0 or len(changes["long_unindexed"]) > 0):
            payload = {
                "event": "geo_index_alert",
                "target": base_url,
                "generated_at_utc": report["meta"]["generated_at_utc"],
                "summary": summary,
                "changes": {
                    "dropped_indexed": changes["dropped_indexed"][:20],
                    "long_unindexed": changes["long_unindexed"][:20],
                },
            }
            ok, detail = execute_webhook(args.alert_webhook, args.alert_webhook_token or "", payload, args.timeout)
            report["meta"]["alert_webhook_status"] = "sent" if ok else "failed"
            report["meta"]["alert_webhook_detail"] = detail
            write_text(snapshot_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

        body = render_index_track_output(report, args.format)
        if args.output:
            out = Path(args.output).expanduser().resolve()
            write_text(out, body)
            print(f"Index track report saved: {out}")
        else:
            sys.stdout.write(body)
            print(f"\nSnapshot saved: {snapshot_path}")

        if args.alert_on_drop and len(changes["dropped_indexed"]) > 0:
            return 3
        return 0

    if action == "submit":
        pool: List[Dict[str, object]] = []
        status_filter = normalize_status_filter(args.status_filter)
        if args.from_track_report:
            pool = load_index_pool_from_track_report(Path(args.from_track_report).expanduser().resolve(), status_filter)
        else:
            pool = resolve_index_pool(
                base_url=base_url,
                timeout=args.timeout,
                user_agent=args.user_agent,
                max_urls=args.max_urls,
                extra_low_patterns=extra_low_patterns,
                urls_file=args.urls_file or "",
                discover_report_file=args.discover_report or "",
            )
        if not pool:
            raise ValueError("no URLs available for submit")

        provider = str(args.provider)
        token = args.google_token or os.environ.get("GOOGLE_INDEXING_TOKEN", "")
        items = []
        submitted = failed = skipped = 0
        for item in pool:
            url = str(item.get("url") or "")
            group = str(item.get("group") or "other")
            row = {"url": url, "group": group, "provider": provider}

            if provider == "dry-run":
                row["status"] = "submitted"
                row["detail"] = "dry-run"
                submitted += 1
                items.append(row)
                continue

            if provider == "google-indexing":
                if (not args.allow_unsupported_google_types) and (not is_google_indexing_supported_url(url)):
                    row["status"] = "skipped"
                    row["detail"] = "unsupported_for_google_indexing_api"
                    skipped += 1
                    items.append(row)
                    continue
                ok, detail = submit_to_google_indexing_api(
                    url=url,
                    token=token,
                    timeout=args.timeout,
                    notification_type=args.notification_type,
                )
                row["status"] = "submitted" if ok else "failed"
                row["detail"] = detail
                if ok:
                    submitted += 1
                else:
                    failed += 1
                items.append(row)
                continue

            if provider == "webhook":
                if not args.webhook_url:
                    raise ValueError("webhook-url is required when provider=webhook")
                payload = {
                    "event": "geo_index_submit",
                    "target_domain": normalize_domain(base_url),
                    "url": url,
                    "type": args.notification_type,
                }
                ok, detail = execute_webhook(args.webhook_url, args.webhook_token or "", payload, args.timeout)
                row["status"] = "submitted" if ok else "failed"
                row["detail"] = detail
                if ok:
                    submitted += 1
                else:
                    failed += 1
                items.append(row)
                continue

            if provider == "command":
                if not args.command_template:
                    raise ValueError("command-template is required when provider=command")
                payload = {
                    "url": url,
                    "type": args.notification_type,
                    "target_domain": normalize_domain(base_url),
                    "provider": provider,
                }
                ok, detail = execute_index_submit_command(args.command_template, payload, args.timeout)
                row["status"] = "submitted" if ok else "failed"
                row["detail"] = detail
                if ok:
                    submitted += 1
                else:
                    failed += 1
                items.append(row)
                continue

            raise ValueError(f"unsupported provider: {provider}")

        report = {
            "meta": {
                "target": base_url,
                "target_domain": normalize_domain(base_url),
                "generated_at_utc": now_utc(),
                "provider": provider,
                "notification_type": args.notification_type,
            },
            "summary": {
                "total": len(items),
                "submitted": submitted,
                "skipped": skipped,
                "failed": failed,
            },
            "items": items,
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        log_path = history_dir / f"index-submit-{normalize_domain(base_url)}-{stamp}.json"
        write_text(log_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

        body = render_index_submit_output(report, args.format)
        if args.output:
            out = Path(args.output).expanduser().resolve()
            write_text(out, body)
            print(f"Index submit report saved: {out}")
        else:
            sys.stdout.write(body)
            print(f"\nSubmit log saved: {log_path}")
        return 0 if failed == 0 else 3

    if action == "audit":
        status_filter = normalize_status_filter(args.status_filter)
        if args.from_track_report:
            pool = load_index_pool_from_track_report(Path(args.from_track_report).expanduser().resolve(), status_filter)
        else:
            pool = resolve_index_pool(
                base_url=base_url,
                timeout=args.timeout,
                user_agent=args.user_agent,
                max_urls=args.max_urls,
                extra_low_patterns=extra_low_patterns,
                urls_file=args.urls_file or "",
                discover_report_file=args.discover_report or "",
            )
        if not pool:
            raise ValueError("no URLs available for audit")

        home = fetch_url(base_url + "/", timeout=args.timeout, user_agent=args.user_agent, max_bytes=1_200_000)
        homepage_body = (home.body or "").lower() if home.status == 200 else ""
        llms_set = set()
        for p in ["/llms.txt", "/llms-full.txt"]:
            r = fetch_url(base_url + p, timeout=args.timeout, user_agent=args.user_agent, max_bytes=1_200_000)
            if r.status == 200:
                for u in extract_urls_from_text(r.body, normalize_domain(base_url)):
                    key = normalize_url_for_compare(u)
                    if key:
                        llms_set.add(key)

        records = []
        issue_counter: Dict[str, Dict[str, object]] = {}
        pass_count = warn_count = fail_count = p0 = p1 = p2 = 0
        for item in pool:
            row = audit_index_url(
                url=str(item.get("url") or ""),
                group=str(item.get("group") or "other"),
                timeout=args.timeout,
                user_agent=args.user_agent,
                thin_threshold_chars=args.thin_threshold_chars,
                homepage_body=homepage_body,
                llms_url_set=llms_set,
            )
            records.append(row)
            if row["status"] == "pass":
                pass_count += 1
            elif row["status"] == "warn":
                warn_count += 1
            else:
                fail_count += 1
            if row["max_priority"] == "P0":
                p0 += 1
            elif row["max_priority"] == "P1":
                p1 += 1
            elif row["max_priority"] == "P2":
                p2 += 1

            for issue in row.get("issues", []):
                if not isinstance(issue, dict):
                    continue
                code = str(issue.get("code") or "")
                if not code:
                    continue
                info = issue_counter.setdefault(
                    code,
                    {
                        "code": code,
                        "priority": issue.get("priority", ""),
                        "count": 0,
                        "fix": issue.get("fix", ""),
                    },
                )
                info["count"] = int(info["count"]) + 1

        issues_summary = sorted(issue_counter.values(), key=lambda x: (str(x.get("priority")), -int(x.get("count", 0))))
        report = {
            "meta": {
                "target": base_url,
                "target_domain": normalize_domain(base_url),
                "generated_at_utc": now_utc(),
            },
            "summary": {
                "total": len(records),
                "pass": pass_count,
                "warn": warn_count,
                "fail": fail_count,
                "p0": p0,
                "p1": p1,
                "p2": p2,
            },
            "issues_summary": issues_summary,
            "records": records,
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot_path = history_dir / f"index-audit-{normalize_domain(base_url)}-{stamp}.json"
        write_text(snapshot_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")

        body = render_index_audit_output(report, args.format)
        if args.output:
            out = Path(args.output).expanduser().resolve()
            write_text(out, body)
            print(f"Index audit report saved: {out}")
        else:
            sys.stdout.write(body)
            print(f"\nAudit log saved: {snapshot_path}")
        return 0 if fail_count == 0 else 2

    if action == "report":
        report = build_index_report_from_history(base_url, history_dir=history_dir, days=args.days)
        body = render_index_report_output(report, args.format)
        if args.output:
            out = Path(args.output).expanduser().resolve()
            write_text(out, body)
            print(f"Index report saved: {out}")
        else:
            sys.stdout.write(body)
        return 0

    raise ValueError(f"unsupported index action: {action}")


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


def handle_adapter_check(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.target)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else None
    adapter = StandaloneWebAdapter(
        base_url=base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        output_dir=output_dir,
        extra_low_patterns=args.low_value_pattern or [],
        webhook_url=args.webhook_url or "",
        webhook_token=args.webhook_token or "",
    )

    site = adapter.get_site_identity()
    high_pages = adapter.list_high_value_pages(args.limit)
    low_pages = adapter.list_low_value_pages(args.limit)
    home_resp = adapter.fetch(
        f"{base_url}/",
        AdapterFetchOptions(timeout=args.timeout, user_agent=args.user_agent, max_bytes=200_000),
    )

    report = {
        "meta": {
            "generated_at_utc": now_utc(),
            "tool": "geo-llms-toolkit standalone-cli",
            "version": TOOL_VERSION,
        },
        "site": {
            "name": site.name,
            "url": site.url,
            "locale": site.locale,
        },
        "summary": {
            "high_value_pages": len(high_pages),
            "low_value_pages": len(low_pages),
            "homepage_status": home_resp.status,
            "homepage_content_type": parse_content_type(home_resp.headers),
        },
        "samples": {
            "high_value_urls": [p.url for p in high_pages[:10]],
            "low_value_urls": [p.url for p in low_pages[:10]],
        },
    }

    if args.format == "json":
        body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    else:
        lines = [
            f"# Adapter Check - {site.url}",
            "",
            f"- Generated (UTC): {report['meta']['generated_at_utc']}",
            f"- Site name: {site.name}",
            f"- High value pages: {report['summary']['high_value_pages']}",
            f"- Low value pages: {report['summary']['low_value_pages']}",
            f"- Homepage status: {report['summary']['homepage_status']}",
            f"- Homepage content-type: {report['summary']['homepage_content_type']}",
            "",
            "## High Value URLs",
        ]
        for url in report["samples"]["high_value_urls"]:
            lines.append(f"- {url}")
        lines.append("")
        lines.append("## Low Value URLs")
        for url in report["samples"]["low_value_urls"]:
            lines.append(f"- {url}")
        lines.append("")
        body = "\n".join(lines)

    if args.output:
        out = Path(args.output).expanduser().resolve()
        write_text(out, body)
        print(f"Adapter check report saved: {out}")
    else:
        sys.stdout.write(body)
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
        description="GEO LLMs Toolkit standalone CLI (scan + llms + monitor + outreach + index).",
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

    adapter_check_p = sub.add_parser("adapter-check", help="Run built-in adapter contract check for a target.")
    adapter_check_p.add_argument("target", help="Domain or URL, e.g. aronhouyu.com")
    adapter_check_p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    adapter_check_p.add_argument("--limit", type=int, default=20, help="Max items for high/low value listings.")
    adapter_check_p.add_argument("--low-value-pattern", action="append", help="Extra low-value regex patterns.")
    adapter_check_p.add_argument("--webhook-url", help="Optional webhook URL for adapter notification channel.")
    adapter_check_p.add_argument("--webhook-token", help="Optional webhook token for adapter notification channel.")
    adapter_check_p.add_argument("--output-dir", help="Optional output dir for adapter write-index-files checks.")
    adapter_check_p.add_argument("--output", help="Write adapter check report to file path.")
    adapter_check_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    adapter_check_p.add_argument("--user-agent", default=DEFAULT_UA)
    adapter_check_p.set_defaults(func=handle_adapter_check)

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

    index_p = sub.add_parser(
        "index",
        help="Index workflow: discover / track / submit / audit / report.",
    )
    index_p.add_argument("index_action", choices=["discover", "track", "submit", "audit", "report"])
    index_p.add_argument("target", help="Domain or URL, e.g. aronhouyu.com")
    index_p.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    index_p.add_argument("--output", help="Write output to file.")
    index_p.add_argument("--history-dir", default=".geo-history/index", help="History/log directory for index workflows.")
    index_p.add_argument("--max-urls", type=int, default=220, help="Max URLs in index pool.")
    index_p.add_argument("--urls-file", help="Manual URL file (txt/csv/tsv/json).")
    index_p.add_argument("--discover-report", help="Use URL pool from `geo index discover --format json` output.")
    index_p.add_argument("--from-track-report", help="Use URLs from a `geo index track --format json` report.")
    index_p.add_argument("--status-filter", default="not_indexed,unknown", help="Statuses for submit/audit from track report.")
    index_p.add_argument("--search-depth", type=int, default=8, help="SERP depth for index track verification.")
    index_p.add_argument("--strict-search", action="store_true", help="Treat search no-match as not_indexed instead of unknown.")
    index_p.add_argument("--long-unindexed-days", type=int, default=14, help="Threshold for long unindexed list.")
    index_p.add_argument("--alert-on-drop", action="store_true", help="Exit non-zero if dropped indexed URLs are found.")
    index_p.add_argument("--alert-webhook", help="Optional webhook URL for drop/long-unindexed alerts.")
    index_p.add_argument("--alert-webhook-token", help="Bearer token for alert webhook.")
    index_p.add_argument("--provider", choices=["dry-run", "google-indexing", "webhook", "command"], default="dry-run")
    index_p.add_argument("--notification-type", choices=["URL_UPDATED", "URL_DELETED"], default="URL_UPDATED")
    index_p.add_argument("--google-token", help="OAuth access token for Google Indexing API.")
    index_p.add_argument("--allow-unsupported-google-types", action="store_true")
    index_p.add_argument("--webhook-url", help="Webhook endpoint for submit provider.")
    index_p.add_argument("--webhook-token", help="Webhook bearer token.")
    index_p.add_argument(
        "--command-template",
        help="Command template for submit provider=command. Vars: {url} {type} {target_domain} {provider} and *_q variants.",
    )
    index_p.add_argument("--thin-threshold-chars", type=int, default=380, help="Thin content threshold for index audit.")
    index_p.add_argument("--low-value-pattern", action="append", help="Extra regex for low-value URL grouping.")
    index_p.add_argument("--days", type=int, default=30, help="Window days for index report.")
    index_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    index_p.add_argument("--user-agent", default=DEFAULT_UA)
    index_p.set_defaults(func=handle_index)

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
