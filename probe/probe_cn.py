"""批量探测国内中文源：是否有效 RSS、条目数、有无摘要、最新文章日期。

只测国内可直连的站，目标是不依赖代理也能撑起一份周报。
"""

import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

CANDIDATES = [
    # ---------------- 网络安全 ----------------
    ("嘶吼",           "https://www.4hou.com/feed"),
    ("安全客",         "https://api.anquanke.com/data/v1/rss"),
    ("安全脉搏",       "https://www.secpulse.com/feed"),
    ("FreeBuf",        "https://www.freebuf.com/feed"),
    ("先知社区",       "https://xz.aliyun.com/feed"),
    ("奇安信攻防社区", "https://forum.butian.net/Rss"),
    ("Seebug Paper",   "https://paper.seebug.org/rss/"),
    ("看雪",           "https://bbs.pediy.com/rss"),
    ("长亭技术博客",   "https://blog.chaitin.cn/feed.xml"),
    ("知道创宇",       "https://blog.knownsec.com/feed/"),
    ("绿盟博客",       "https://blog.nsfocus.net/feed/"),
    ("安全牛",         "https://www.aqniu.com/feed"),
    ("安全内参",       "https://www.secrss.com/rss"),
    ("代码卫士",       "https://www.codesafe.cn/feed"),
    ("CNCERT",         "https://www.cert.org.cn/publish/main/rss.xml"),

    # ---------------- 人工智能 / 科技 ----------------
    ("量子位",         "https://www.qbitai.com/feed"),
    ("InfoQ 中文",     "https://www.infoq.cn/feed"),
    ("钛媒体",         "https://www.tmtpost.com/rss.xml"),
    ("开源中国",       "https://www.oschina.net/news/rss"),
    ("Solidot",        "https://www.solidot.org/index.rss"),
    ("雷锋网",         "https://www.leiphone.com/feed"),
    ("智东西",         "https://zhidx.com/feed"),
    ("极客公园",       "https://www.geekpark.net/rss"),
    ("IT之家",         "https://www.ithome.com/rss/"),
    ("cnBeta",         "https://www.cnbeta.com.tw/backend.php"),
    ("博客园",         "https://feed.cnblogs.com/blog/sitehome/rss"),
    ("51CTO",          "https://www.51cto.com/rss"),
    ("少数派",         "https://sspai.com/feed"),
    ("爱范儿",         "https://www.ifanr.com/feed"),
    ("果壳",           "https://www.guokr.com/rss/"),
    ("机器之心",       "https://www.jiqizhixin.com/rss"),
    ("36氪",           "https://36kr.com/feed"),
    ("品玩",           "https://www.pingwest.com/feed"),
    ("虎嗅",           "https://www.huxiu.com/rss/0.xml"),
    ("掘金",           "https://juejin.cn/rss"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, application/atom+xml, text/xml, */*",
}
ATOM = {"a": "http://www.w3.org/2005/Atom"}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except Exception:
        pass
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, f)
        except Exception:
            continue
    return None


def analyze(raw):
    root = ET.fromstring(raw)
    items = root.findall(".//item") or root.findall(".//a:entry", ATOM)
    if not items:
        return 0, 0, None, ""
    with_desc = 0
    newest = None
    for it in items:
        desc = (it.findtext("description") or it.findtext("a:summary", namespaces=ATOM)
                or it.findtext("a:content", namespaces=ATOM) or "")
        if len(re.sub(r"<[^>]+>", "", desc).strip()) > 30:
            with_desc += 1
        d = parse_date(it.findtext("pubDate") or it.findtext("a:published", namespaces=ATOM)
                       or it.findtext("a:updated", namespaces=ATOM) or "")
        if d and (newest is None or d > newest):
            newest = d
    title = items[0].findtext("title") or items[0].findtext("a:title", namespaces=ATOM) or ""
    return len(items), with_desc, newest, title.strip()[:38]


print(f"{'源':<16} {'条目':>4} {'有摘要':>5} {'最新':<12} 样例")
print("-" * 95)
usable = []
for name, url in CANDIDATES:
    try:
        with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=12) as r:
            raw = r.read()
        n, wd, newest, sample = analyze(raw)
        if n == 0:
            print(f"{name:<16} {'--':>4} 非RSS（HTML/WAF）")
            continue
        date_s = newest.strftime("%Y-%m-%d") if newest else "无日期"
        mark = "✓" if wd > 0 else "✗"
        print(f"{name:<16} {n:>4} {wd:>4}{mark} {date_s:<12} {sample}")
        usable.append((name, n, wd))
    except ET.ParseError:
        print(f"{name:<16} {'--':>4} 非RSS（HTML/WAF）")
    except Exception as e:
        print(f"{name:<16} {'--':>4} {type(e).__name__}")

print("-" * 95)
print(f"可用源：{len(usable)}/{len(CANDIDATES)}   其中有摘要：{sum(1 for _, _, w in usable if w > 0)}")
