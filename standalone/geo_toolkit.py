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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT = 12
TOOL_VERSION = "0.3.0"
DEFAULT_UA = (
    "geo-llms-toolkit/0.3 standalone-cli (+https://github.com/aronhy/geo-llms-toolkit)"
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
        rows.append(
            {
                "keyword": item.keyword,
                "group": item.group,
                "value": item.value,
                "is_brand": item.is_brand,
                "target_rank": target_rank,
                "domains": domains,
                "rank_by_domain": rank_by_domain,
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

        score = (keyword_overlap * 0.45) + (serp_coappear * 0.35) + (rank_pressure * 0.20)
        competitor_profiles.append(
            {
                "domain": comp,
                "score": round(score, 2),
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
        },
        "summary": {
            "keywords_total": total_keywords,
            "keywords_brand": brand_keywords,
            "keywords_non_brand": non_brand_keywords,
            "keywords_target_ranked": keyword_hits_target,
            "keywords_with_serp_results": keywords_with_serp_results,
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
        f"- Keywords: {summary['keywords_total']} (brand={summary['keywords_brand']}, non-brand={summary['keywords_non_brand']})",
        f"- Keywords with SERP data: {summary['keywords_with_serp_results']}",
        f"- Competitors tracked: {summary['competitors_tracked']}",
        f"- Priority actions: {summary['actions_generated']}",
        "",
        "## Competitor Scores",
        "",
        "| Domain | Tier | Score | Keyword Overlap % | Co-appear % | Rank Pressure % | Non-Brand Share % | Avg Rank |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for c in competitors:
        lines.append(
            f"| {c['domain']} | {str(c['tier']).upper()} | {c['score']} | {c['keyword_overlap_pct']} | "
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
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot_path = history_dir / f"monitor-{normalize_domain(base_url)}-{stamp}.json"
        write_text(snapshot_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Snapshot saved: {snapshot_path}")
    return 0


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
        description="GEO LLMs Toolkit standalone CLI (scan + llms + competitor monitor).",
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
    monitor_p.add_argument("--history-dir", default=".geo-history", help="Directory to store JSON snapshots.")
    monitor_p.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    monitor_p.add_argument("--output", help="Write report to a file path.")
    monitor_p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    monitor_p.add_argument("--user-agent", default=DEFAULT_UA)
    monitor_p.set_defaults(func=handle_monitor)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
