"""抓取层：把 RSS 变成 Article 列表。

设计要点（新手最容易踩的坑都在这里）：
1. 某个源挂了不能影响其它源          → 每个源单独 try/except，失败只记录不抛出
2. 国内源永远直连、英文源走代理      → 按 requires_proxy + 代理状态决定路线
3. RSS 日期格式五花八门              → 多格式依次尝试
4. 中文源可能是 GBK 编码             → 从 XML 声明里读真实编码
5. 摘要里带 HTML 标签                → 剥掉再截断
"""

import html
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime

from config import MAX_WORKERS, SUMMARY_MAX_CHARS, TIMEOUT
from netcheck import ProxyStatus

# 伪装成浏览器。不加这个，部分站（尤其国内带 WAF 的）会直接返回拒绝页。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, application/atom+xml, text/xml, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class Article:
    """一篇文章。字段刻意少而明确，方便后面处理。"""

    title: str
    url: str
    summary: str
    published: datetime | None
    source: str
    category: str          # security / ai / hardware / opensource
    lang: str              # zh / en

    def is_valid(self) -> bool:
        """标题和链接都没有的条目是垃圾，直接丢。"""
        return bool(self.title.strip()) and bool(self.url.strip())


@dataclass
class FetchOutcome:
    """一个源的抓取结果。三种状态：成功 / 失败 / 跳过（需要代理但没代理）。"""

    name: str
    articles: list[Article] = field(default_factory=list)
    error: str = ""
    skipped: bool = False
    via_proxy: bool = False


# ---------------------------------------------------------------------------
# 网络请求
# ---------------------------------------------------------------------------
def _http_get(url: str, proxy_url: str | None, timeout: int = TIMEOUT) -> bytes:
    """发 GET 请求。proxy_url 为 None 表示直连（屏蔽系统代理）。"""
    if proxy_url:
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    else:
        # 显式传空 dict，屏蔽注册表里的系统代理，保证"直连"就是真的直连
        handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers=HEADERS)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _decode(raw: bytes) -> str:
    """字节 → 字符串。

    顺序重要：先看 XML 声明里的 encoding，再 UTF-8，最后 GB18030。
    国内源经常是 GBK，直接 decode('utf-8') 会炸。
    """
    m = re.search(rb'encoding=["\']([\w-]+)["\']', raw[:200])
    if m:
        enc = m.group(1).decode("ascii", errors="ignore").lower()
        try:
            return raw.decode(enc, errors="replace")
        except LookupError:
            pass
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _parse_date(text: str) -> datetime | None:
    """RSS/Atom 日期格式太多，挨个试。

    常见三种：
      Mon, 12 Oct 2026 18:45:42 +0800   ← RSS 标准（RFC 822）
      2026-10-12T18:45:42Z              ← Atom（ISO 8601）
      2026-10-12 18:45:42               ← 国内站常见的非标准格式
    """
    if not text:
        return None
    text = text.strip()

    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        pass

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(raw: str) -> str:
    """剥掉 HTML 标签、还原实体、压缩空白、截断。"""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:SUMMARY_MAX_CHARS]


def _child_text(node, *names: str) -> str:
    """按顺序找第一个非空的子节点文本。同时兼容 RSS 和 Atom 命名空间。"""
    for name in names:
        v = node.findtext(name)
        if v and v.strip():
            return v.strip()
        v = node.findtext(f"atom:{name}", namespaces=ATOM_NS)
        if v and v.strip():
            return v.strip()
    return ""


def _child_link(node) -> str:
    """取链接。RSS 是 <link>文本</link>，Atom 是 <link href="..."/>。"""
    v = node.findtext("link")
    if v and v.strip():
        return v.strip()
    for link in node.findall("atom:link", ATOM_NS):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href", "").strip()
    for link in node.findall("atom:link", ATOM_NS):
        if link.get("href"):
            return link.get("href", "").strip()
    v = node.findtext("guid") or node.findtext("atom:id", namespaces=ATOM_NS)
    return (v or "").strip()


def parse_feed(text: str, source: dict) -> list[Article]:
    """把 RSS/Atom 文本解析成 Article 列表。"""
    root = ET.fromstring(text)

    items = root.findall(".//item")
    if not items:
        items = root.findall(".//atom:entry", ATOM_NS)

    out: list[Article] = []
    for node in items:
        article = Article(
            title=_clean_text(_child_text(node, "title")),
            url=_child_link(node),
            summary=_clean_text(
                _child_text(node, "description", "summary", "content", "encoded")
            ),
            published=_parse_date(
                _child_text(node, "pubDate", "published", "updated", "date")
            ),
            source=source["name"],
            category=source["category"],
            lang=source["lang"],
        )
        if article.is_valid():
            out.append(article)
    return out


# ---------------------------------------------------------------------------
# 单源抓取（含路线决策）
# ---------------------------------------------------------------------------
def fetch_source(source: dict, proxy: ProxyStatus,
                 force_direct: bool = False) -> FetchOutcome:
    """抓单个源。绝不抛异常 —— 失败只记录原因，不影响其它源。

    force_direct=True 用于 GitHub Actions 这类直连环境：
        那里本来就能访问所有站点，`requires_proxy` 这个本地判断不再适用，
        所以忽略它，全部直连。

    路线规则（force_direct=False 时）：
        需要代理 + 代理可用   → 走代理
        需要代理 + 代理不可用 → 跳过（不算失败，报告里说明）
        不需要代理            → 先直连，失败了再试代理兜底
    """
    name = source["name"]
    needs_proxy = source.get("requires_proxy", False) and not force_direct
    url = source["url"]

    if needs_proxy and not proxy.available:
        return FetchOutcome(name, skipped=True, error="需要代理，当前无代理")

    # 决定首选路线
    if force_direct:
        attempts = [None]
    elif needs_proxy:
        attempts = [proxy.url()]
    else:
        attempts = [None]      # 国内源优先直连，最快
        if proxy.available:
            attempts.append(proxy.url())   # 直连失败时用代理兜底

    last_err = ""
    for proxy_url in attempts:
        try:
            text = _decode(_http_get(url, proxy_url))
            articles = parse_feed(text, source)
            return FetchOutcome(name, articles, via_proxy=proxy_url is not None)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except urllib.error.URLError as e:
            last_err = f"网络错误 {e.reason}"
        except socket.timeout:
            last_err = f"超时 >{TIMEOUT}s"
        except ET.ParseError:
            last_err = "不是有效 RSS/XML（可能是反爬页面）"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

    return FetchOutcome(name, error=last_err)


def fetch_all(sources: list[dict], proxy: ProxyStatus,
              progress=None, force_direct: bool = False
              ) -> tuple[list[Article], list[FetchOutcome]]:
    """并发抓所有源。返回 (全部文章, 每个源的结果)。"""
    articles: list[Article] = []
    outcomes: list[FetchOutcome] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for outcome in pool.map(
                lambda s: fetch_source(s, proxy, force_direct=force_direct), sources):
            outcomes.append(outcome)
            if progress:
                if outcome.skipped:
                    progress(f"  – {outcome.name:<18} 跳过（{outcome.error}）")
                elif outcome.error:
                    progress(f"  ✗ {outcome.name:<18} {outcome.error}")
                else:
                    tag = "代理" if outcome.via_proxy else "直连"
                    progress(f"  ✓ {outcome.name:<18} {len(outcome.articles):>3} 条  [{tag}]")
            articles.extend(outcome.articles)

    return articles, outcomes


# ---------------------------------------------------------------------------
# 特殊源：GitHub 本周爆火项目（搜索 API，不是 RSS）
# ---------------------------------------------------------------------------
def fetch_github_trending(proxy: ProxyStatus, force_direct: bool = False,
                          days: int = 7, min_stars: int = 100,
                          limit: int = 25) -> FetchOutcome:
    """查最近 N 天新建、星标超过阈值的高星仓库（"本周爆火"）。

    为什么用搜索 API 而不是 Trending 页面：
        Trending 是给人看的 HTML，改版就解析不到（实测正则已经失效），
        而且没有语言、描述、star 数这些结构字段。API 返回 JSON，一步到位。

    为什么要带 token：
        匿名调用限额 10 次/分钟，够用但很紧；GitHub Actions 里本来就有
        GITHUB_TOKEN（workflow 已传进环境），带上就是 5000 次/小时。
        本机没有 token 时也照跑，只是别在 10 分钟内反复重试。

    失败不影响其它源 —— 和其它源一样只记录原因，绝不抛异常。
    """
    from datetime import timedelta

    name = "GitHub 周榜"
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    query = f"created:>{since} stars:>{min_stars}"
    url = ("https://api.github.com/search/repositories?q="
           + urllib.parse.quote(query)
           + f"&sort=stars&order=desc&per_page={max(1, min(limit, 50))}")

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = dict(HEADERS)
    headers["Accept"] = "application/vnd.github+json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # 路线：CI 直连；本机有代理就先代理，失败再直连兜底
    if force_direct:
        attempts: list[str | None] = [None]
    elif proxy.available:
        attempts = [proxy.url(), None]
    else:
        attempts = [None]

    last_err = ""
    for proxy_url in attempts:
        try:
            if proxy_url:
                handler = urllib.request.ProxyHandler(
                    {"http": proxy_url, "https": proxy_url})
            else:
                handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(handler)
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=TIMEOUT + 8) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            items = data.get("items") or []
            articles: list[Article] = []
            for r in items:
                full_name = str(r.get("full_name") or "").strip()
                html_url = str(r.get("html_url") or "").strip()
                if not full_name or not html_url:
                    continue
                stars = int(r.get("stargazers_count") or 0)
                desc = re.sub(r"\s+", " ", str(r.get("description") or "")).strip()
                lang = str(r.get("language") or "").strip()
                topics = [str(t) for t in (r.get("topics") or [])][:6]
                created = _parse_date(str(r.get("created_at") or ""))

                bits: list[str] = []
                if desc:
                    bits.append(desc)
                if lang:
                    bits.append(f"主要语言 {lang}")
                if created:
                    age = max(0, (datetime.now() - created).days)
                    bits.append(f"上线 {age} 天，累计 {stars:,} 星")
                else:
                    bits.append(f"累计 {stars:,} 星")
                if topics:
                    bits.append("标签 " + "、".join(topics))

                headline = f"{full_name} — {desc[:70]}" if desc else full_name
                articles.append(Article(
                    title=_clean_text(headline),
                    url=html_url,
                    summary=_clean_text("；".join(bits)),
                    published=created,
                    source=name,
                    category="opensource",
                    lang="en",
                ))
            return FetchOutcome(name, articles, via_proxy=proxy_url is not None)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (403, 429):
                last_err += "（多半是 API 限额）"
        except urllib.error.URLError as e:
            last_err = f"网络错误 {e.reason}"
        except socket.timeout:
            last_err = f"超时 >{TIMEOUT + 8}s"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

    return FetchOutcome(name, error=last_err)
