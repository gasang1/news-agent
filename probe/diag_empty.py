"""诊断：为什么全局排序那次调用返回空 content。"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

# 构造一个和 rerank 规模相近的请求
items = "\n".join(
    f"[{i}] (security/10-1{i % 10}/The Hacker News) Some reasonably long English headline "
    f"about a zero-day vulnerability being exploited in the wild number {i}\n"
    f"    摘要：Researchers disclosed a vulnerability that allows remote code execution. "
    f"Patches are available and exploitation is ongoing. " * 2
    for i in range(1, 21)
)

SYSTEM = (
    "你是一名资深的网络安全与人工智能新闻编辑，为一份周报挑选选题。\n"
    "只输出 JSON。"
)
USER = (
    f"以下是初筛留下的 20 条候选，来自不同批次。\n"
    f"请跨批次比较，挑出最值得放进周报的 14 条。\n\n"
    + items
    + '\n\n只返回 JSON：{"selected":[{"id":1,"score":9,"reason":"..."}]}'
)


def call(model: str, max_tokens: int) -> None:
    print(f"\n===== model={model} max_tokens={max_tokens} =====")
    print(f"输入字符数: {len(SYSTEM) + len(USER)}")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        f"{config.API_BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.get_api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    choice = data["choices"][0]
    msg = choice["message"]
    print(f"finish_reason = {choice.get('finish_reason')}")
    print(f"usage         = {data.get('usage')}")
    print(f"message 字段  = {list(msg.keys())}")
    print(f"content 长度  = {len(msg.get('content') or '')}")
    print(f"reasoning 长度= {len(msg.get('reasoning_content') or '')}")
    content = msg.get("content") or ""
    print(f"content 前 200 字: {content[:200]!r}")


if __name__ == "__main__":
    for model, mt in [("deepseek-flash", 2500),
                      ("deepseek-flash", 8000),
                      ("deepseek-v4-pro", 2500)]:
        try:
            call(model, mt)
        except Exception as e:
            print(f"失败: {type(e).__name__}: {e}")
