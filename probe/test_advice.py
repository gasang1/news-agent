"""离线验证：周报末尾的"给想入行网络安全的你"段落能正确渲染。

不调用 API，不花钱。只是拿假数据走一遍 build_weekly_markdown。
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch  # noqa: E402
import main  # noqa: E402

NOW = datetime(2026, 10, 18, 16, 9)
START = datetime(2026, 10, 11)
END = datetime(2026, 10, 18)

ANALYSIS = {
    "overview": "本周 VPN 与邮件网关成为主要突破口，AI 侧则集中在代理越界与模型能力扩张。",
    "items": [
        {
            "title": "测试标题 A",
            "summary": "测试摘要 A。",
            "why": "测试影响 A。",
            "url": "https://example.com/a",
            "source": "测试源",
            "category": "security",
            "lang": "zh",
            "published": datetime(2026, 10, 16),
            "score": 9,
        },
        {
            "title": "测试标题 B",
            "summary": "测试摘要 B。",
            "why": "测试影响 B。",
            "url": "https://example.com/b",
            "source": "测试源",
            "category": "ai",
            "lang": "en",
            "published": datetime(2026, 10, 15),
            "score": 8,
        },
    ],
    "advice": [
        "先补网络协议：本周三起在野利用都打在 VPN 网关上，把 TLS 握手和 IKE 协商流程走一遍，再去看真实设备日志。",
        "从日志开始练排查：找一份 SSH 或 Nginx 的 auth 日志，试着把一次暴力破解的完整链条还原出来。",
        "别追新工具，先会读公告：挑 3 个本周提到的 CVE，读原厂公告而不是二手解读，看你能不能复述影响面。",
    ],
}

OUTCOMES = [
    fetch.FetchOutcome(name="测试源", articles=[], error="", via_proxy=False),
    fetch.FetchOutcome(name="坏源", articles=[], error="HTTP 403", via_proxy=False),
]
# articles 字段是 list[Article]，这里只需要长度
OUTCOMES[0].articles = [None] * 5  # type: ignore[list-item]

STATS = {
    "sources_total": 2,
    "sources_ok": 1,
    "sources_skipped": 0,
    "sources_failed": 1,
    "fetched": 5,
    "in_window": 5,
    "after_dedupe": 5,
    "after_filter": 2,
}


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {label}{(' — ' + detail) if detail else ''}")
    return ok


def main_test() -> int:
    md = main.build_weekly_markdown(
        ANALYSIS, "7 次调用，输入 24,182 tokens，输出 18,022 tokens",
        OUTCOMES, "CI 直连", "最近 7 天", START, END, STATS,
    )

    print("=== 1. 建议段落存在 ===")
    ok1 = check("标题正确", "## 🎯 给想入行网络安全的你" in md)
    ok2 = check("三条建议都在", all(t in md for t in ANALYSIS["advice"]))
    ok3 = check("编号为 1./2./3.", "\n1. " in md and "\n2. " in md and "\n3. " in md)

    print("=== 2. 位置正确（正文末尾、附录之前）===")
    i_advice = md.find("给想入行网络安全的你")
    i_sec = md.find("## 🔒 网络安全")
    i_src = md.find("## 附：数据来源")
    ok4 = check("在安全条目之后", i_sec < i_advice, f"安全 {i_sec} < 建议 {i_advice}")
    ok5 = check("在数据来源之前", i_advice < i_src, f"建议 {i_advice} < 附录 {i_src}")

    print("=== 3. 没有 advice 时不出现空标题 ===")
    analysis_no_advice = {k: v for k, v in ANALYSIS.items() if k != "advice"}
    md2 = main.build_weekly_markdown(
        analysis_no_advice, "x", OUTCOMES, "直连", "最近 7 天", START, END, STATS,
    )
    ok6 = check("段落整体省略", "给想入行网络安全的你" not in md2)

    print("=== 4. 建议为空列表时同样省略 ===")
    analysis_empty = dict(ANALYSIS, advice=[])
    md3 = main.build_weekly_markdown(
        analysis_empty, "x", OUTCOMES, "直连", "最近 7 天", START, END, STATS,
    )
    ok7 = check("段落整体省略", "给想入行网络安全的你" not in md3)

    print("\n=== 渲染出的末尾 20 行 ===")
    for line in md.splitlines()[-20:]:
        print("  " + line)

    passed = all([ok1, ok2, ok3, ok4, ok5, ok6, ok7])
    print(f"\n{'全部通过' if passed else '有失败项'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main_test())
