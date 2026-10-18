"""偷看几个源返回的真实内容，判断是不是 RSS。"""

import urllib.request

TARGETS = [
    ("安全客",   "https://api.anquanke.com/data/v1/rss"),
    ("FreeBuf",  "https://www.freebuf.com/feed"),
    ("机器之心", "https://www.jiqizhixin.com/rss"),
]

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}

for name, url in TARGETS:
    print("=" * 70)
    print(f"{name}  {url}")
    try:
        with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=15) as r:
            raw = r.read()
            print(f"Content-Type: {r.headers.get('Content-Type')}")
            print(f"长度: {len(raw)}")
            print("--- 前 600 字节 ---")
            print(raw[:600].decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"失败: {type(e).__name__}: {e}")
    print()
