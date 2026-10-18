"""验证某个端口是否真的是能用的 HTTP 代理。

"端口有服务在听" ≠ "它是代理" ≠ "它能出网"。
必须真发一个请求穿过它，拿到内容才算数。
"""

import urllib.request
import sys

TEST_URLS = [
    "http://www.gstatic.com/generate_204",     # Google 的连通性测试端点，返回 204 空响应
    "https://www.google.com",
    "http://example.com",
]


def test_proxy(host: str, port: int, timeout: int = 10) -> tuple[bool, str]:
    proxy = f"{host}:{port}"
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    opener = urllib.request.build_opener(handler)

    for url in TEST_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=timeout) as r:
                body = r.read(200)
                return True, f"{url} -> HTTP {r.status}, {len(body)}B"
        except Exception as e:
            last = f"{url} -> {type(e).__name__}: {e}"
    return False, last


if __name__ == "__main__":
    ports = [int(p) for p in sys.argv[1:]] or [8080]
    for port in ports:
        ok, detail = test_proxy("127.0.0.1", port)
        print(f"127.0.0.1:{port:<6} {'✅ 可用代理' if ok else '❌ 不是可用代理'}  {detail}")
