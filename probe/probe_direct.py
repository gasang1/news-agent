"""绕过系统代理，直连探测 RSS 源。

用法：
    python probe_direct.py          # 直连
    python probe_direct.py proxy    # 走系统代理
"""

import sys
import socket
import urllib.request
import urllib.error

CANDIDATES = [
    ("安全客",            "https://api.anquanke.com/data/v1/rss"),
    ("FreeBuf",           "https://www.freebuf.com/feed"),
    ("The Hacker News",   "https://feeds.feedburner.com/TheHackersNews"),
    ("BleepingComputer",  "https://www.bleepingcomputer.com/feed/"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
    ("机器之心",          "https://www.jiqizhixin.com/rss"),
    ("量子位",            "https://www.qbitai.com/feed"),
    ("MIT Tech Review",   "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("Hugging Face",      "https://huggingface.co/blog/feed.xml"),
    ("Hacker News (YC)",  "https://news.ycombinator.com/rss"),
    ("Schneier",          "https://www.schneier.com/feed/atom/"),
    ("The Verge",         "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica",      "https://feeds.arstechnica.com/arstechnica/index"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def build_opener(use_proxy: bool):
    if use_proxy:
        return urllib.request.build_opener()
    # 显式传空 dict，屏蔽掉从注册表读到的系统代理
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(opener, name: str, url: str, timeout: int = 15) -> bool:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = resp.read()
            head = data[:3000]
            looks_xml = b"<rss" in head or b"<feed" in head or b"<rdf" in head
            print(f"  OK    {name:<20} {len(data):>8}B  xml={looks_xml}")
            return looks_xml
    except urllib.error.HTTPError as e:
        print(f"  HTTP  {name:<20} {e.code} {e.reason}")
    except urllib.error.URLError as e:
        print(f"  FAIL  {name:<20} {e.reason}")
    except socket.timeout:
        print(f"  TIME  {name:<20} >{timeout}s")
    except Exception as e:
        print(f"  ERR   {name:<20} {type(e).__name__}: {e}")
    return False


if __name__ == "__main__":
    use_proxy = len(sys.argv) > 1 and sys.argv[1] == "proxy"
    mode = "系统代理" if use_proxy else "直连（屏蔽代理）"
    opener = build_opener(use_proxy)

    print(f"模式: {mode}")
    print(f"探测 {len(CANDIDATES)} 个源 ...\n")
    ok = sum(probe(opener, n, u) for n, u in CANDIDATES)
    print(f"\n可用: {ok}/{len(CANDIDATES)}")
