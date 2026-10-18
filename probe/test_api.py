"""验证 DeepSeek API Key 是否可用，并测一次真实调用。

只用标准库 urllib，不装任何第三方包。
"""

import json
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.deepseek.com"
API_KEY = sys.argv[1] if len(sys.argv) > 1 else ""

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def call(path: str, payload: dict | None = None, method: str = "POST",
         use_proxy: bool = False) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)

    if use_proxy:
        opener = urllib.request.build_opener()
    else:
        # DeepSeek 在国内可直连，绕开系统代理更快也更稳
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    with opener.open(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    if not API_KEY:
        print("用法: python test_api.py <api-key>")
        sys.exit(1)

    print("1) 查询可用模型 ...")
    try:
        models = call("/models", method="GET")
        names = [m["id"] for m in models.get("data", [])]
        print(f"   可用模型: {names}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        print(f"   失败 HTTP {e.code}: {body}")
        sys.exit(1)
    except Exception as e:
        print(f"   失败 {type(e).__name__}: {e}")
        sys.exit(1)

    print("\n2) 发一次对话请求 ...")
    try:
        res = call("/chat/completions", {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "你只回答一个词，不要标点。"},
                {"role": "user", "content": "中国的首都是哪里？"},
            ],
            "max_tokens": 20,
        })
        print(f"   回复: {res['choices'][0]['message']['content']!r}")
        print(f"   token 用量: {res.get('usage')}")
    except Exception as e:
        print(f"   失败 {type(e).__name__}: {e}")
        sys.exit(1)

    print("\n3) 测 JSON 结构化输出 ...")
    try:
        res = call("/chat/completions", {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system",
                 "content": '返回严格 JSON：{"picked":[1,3],"reason":"..."}'},
                {"role": "user",
                 "content": "从这 4 条里挑出与网络安全相关的编号：1.苹果发布新手机 2.某厂商修复高危漏洞 3.新款耳机评测 4.勒索软件攻击"},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 100,
        })
        content = res["choices"][0]["message"]["content"]
        print(f"   原始返回: {content}")
        print(f"   解析结果: {json.loads(content)}")
    except Exception as e:
        print(f"   失败 {type(e).__name__}: {e}")
        sys.exit(1)

    print("\n全部通过。")
