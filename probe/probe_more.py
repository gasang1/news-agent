"""批量探测更多候选源，并检查是否有摘要。"""

import urllib.request
import xml.etree.ElementTree as ET

CANDIDATES = [
    # 中文安全
    ("安全牛",        "https://www.aqniu.com/feed"),
    ("嘶吼",          "https://www.4hou.com/feed"),
    ("安全内参",      "https://www.secrss.com/rss"),
    ("奇安信",        "https://www.qianxin.com/news/rss"),
    # 中文技术/AI
    ("InfoQ中文",     "https://www.infoq.cn/feed"),
    ("开源中国",      "https://www.oschina.net/news/rss"),
    ("Solidot",       "https://www.solidot.org/index.rss"),
    ("雷锋网",        "https://www.leiphone.com/feed"),
    ("36氪",          "https://36kr.com/feed"),
    ("虎嗅",          "https://www.huxiu.com/rss/0.xml"),
    ("品玩",          "https://www.pingwest.com/feed"),
    ("少数派",        "https://sspai.com/feed"),
    ("钛媒体",        "https://www.tmtpost.com/rss.xml"),
    ("爱范儿",        "https://www.ifanr.com/feed"),
    # 英文补充
    ("Dark Reading",  "https://www.darkreading.com/rss.xml"),
    ("SecurityWeek",  "https://www.securityweek.com/feed/"),
    ("The Record",    "https://therecord.media/feed"),
    ("VentureBeat AI","https://venturebeat.com/category/ai/feed/"),
    ("TechCrunch",    "https://techcrunch.com/feed/"),
    ("Wired",         "https://www.wired.com/feed/rss"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def analyze(raw: bytes):
    """返回 (是否RSS, 条目数, 有摘要的条目数, 样例标题)"""
    root = ET.fromstring(raw)
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not items:
        return False, 0, 0, ""

    with_desc = 0
    for it in items:
        desc = it.findtext("description") or it.findtext(
            "{http://www.w3.org/2005/Atom}summary") or ""
        if len(desc.strip()) > 30:
            with_desc += 1

    t = items[0].findtext("title") or items[0].findtext("{http://www.w3.org/2005/Atom}title") or ""
    return True, len(items), with_desc, t[:40]


for name, url in CANDIDATES:
    try:
        with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=12) as r:
            raw = r.read()
        is_rss, n, with_desc, sample = analyze(raw)
        if not is_rss:
            print(f"  非RSS  {name:<16} {len(raw):>7}B  可能是HTML/WAF")
        else:
            flag = "✓" if with_desc > 0 else "✗无摘要"
            print(f"  {flag:<7} {name:<16} {n:>3}条  有摘要{n:>3}  | {sample}")
    except Exception as e:
        print(f"  失败   {name:<16} {type(e).__name__}")
