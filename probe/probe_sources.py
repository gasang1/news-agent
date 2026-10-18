"""探测 RSS 源连通性 —— 阶段 0，只验证能不能拿到数据，不做任何处理。

用法：python probe_sources.py
"""

import urllib.request
import urllib.error
import socket

CANDIDATES = [
    ("安全客",        "https://api.anquanke.com/data/v1/rss"),
    ("FreeBuf",       "https://www.freebuf.com/feed"),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews"),
    ("BleepingComputer", "https://www.bleepingcomputer.com/feed/"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
    ("机器之心",      "https://www.jiqizhixin.com/rss"),
    ("量子位",        "https://www.qbitai.com/feed"),
    ("MIT Tech Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("Hacker News (YC)", "https://news.ycombinator.com/rss"),
    (" Schneier",     "https://www.schneier.com/feed/atom/"),
    ("The Verge",     "https://www.theverge.com/rss/index.xml"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def probe(name: str, url: str, timeout: int = 15) -> None:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type", "?")
            # 粗略判断拿到的到底是不是 XML/RSS
            head = data[:200].lstrip()
            looks_xml = head[:5].lower() in (b"<?xml", b"<rss>", b"<feed") or b"<rss" in data[:2000] or b"<feed" in data[:2000]
            print(f"  OK   {name:<22} {len(data):>8} bytes  {ctype:<30} xml={looks_xml}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {name:<22} {e.code} {e.reason}")
    except urllib.error.URLError as e:
        print(f"  FAIL {name:<22} {e.reason}")
    except socket.timeout:
        print(f"  TIMEOUT {name:<22} >{timeout}s")
    except Exception as e:
        print(f"  ERR  {name:<22} {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"探测 {len(CANDIDATES)} 个源 ...\n")
    for name, url in CANDIDATES:
        probe(name, url)
    print("\n完")
