"""离线验证：周报只保留正文，"给想入行网络安全的你"和「附：数据来源」都不再出现。

不调用 API，不花钱。只是拿假数据走一遍 build_weekly_markdown。
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch  # noqa: E402
import main  # noqa: E402

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
        {
            "title": "测试标题 C",
            "summary": "测试摘要 C。",
            "why": "测试影响 C。",
            "url": "https://example.com/c",
            "source": "GitHub 周榜",
            "category": "opensource",
            "lang": "en",
            "published": datetime(2026, 10, 14),
            "score": 7,
        },
        {
            "title": "测试标题 D",
            "summary": "测试摘要 D。",
            "why": "测试影响 D。",
            "url": "https://example.com/d",
            "source": "测试源",
            "category": "hardware",
            "lang": "zh",
            "published": datetime(2026, 10, 13),
            "score": 7,
        },
    ],
    # 旧数据里可能还带 advice 字段：必须照样被忽略，不能渲染出来
    "advice": ["这是一条不该出现的旧建议"],
}

OUTCOMES = [
    fetch.FetchOutcome(name="测试源", articles=[], error="", via_proxy=False),
    fetch.FetchOutcome(name="坏源", articles=[], error="HTTP 403", via_proxy=False),
]
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

USAGE = "7 次调用，输入 24,182 tokens，输出 18,022 tokens"


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {label}{(' — ' + detail) if detail else ''}")
    return ok


def test() -> int:
    md = main.build_weekly_markdown(
        ANALYSIS, USAGE, OUTCOMES, "CI 直连", "最近 7 天", START, END, STATS,
    )

    print("=== 1. 两段都消失 ===")
    ok1 = check("没有建议段标题", "给想入行网络安全的你" not in md)
    ok2 = check("没有旧 advice 内容", "这是一条不该出现的旧建议" not in md)
    ok3 = check("没有数据来源标题", "数据来源" not in md)
    ok4 = check("没有来源表格", "| 源 | 状态 |" not in md and "--- | ---" not in md)
    ok5 = check("没有失败源的名字", "坏源" not in md)

    print("=== 2. 正文完好 ===")
    ok6 = check("有一级标题", md.startswith("# 科技周报"), md.splitlines()[0][:24])
    ok7 = check("有本周观察", "## 本周观察" in md)
    ok8 = check("有 AI 分组", "## 🤖 人工智能（1 条）" in md)
    ok9 = check("有安全分组", "## 🔒 网络安全（1 条）" in md)
    ok21 = check("有开源分组", "## 🧩 开源项目（1 条）" in md)
    ok22 = check("有硬件分组", "## 💻 科技硬件（1 条）" in md)
    order = [md.find(f"## {main.CATEGORY_LABEL[c]}") for c in main.CATEGORY_ORDER]
    ok10 = check("板块顺序 = AI → 安全 → 开源 → 硬件",
                 all(x >= 0 for x in order) and order == sorted(order),
                 str(order))
    ok11 = check("条目四要素齐全",
                 all(k in md for k in ("- **摘要**：", "- **为什么重要**：",
                                       "- **来源**：", "- **原文**：")))
    ok12 = check("原文链接保留", "https://example.com/a" in md)
    ok13 = check("头部统计行保留", "**收录 4 条**" in md)

    print("=== 3. 结尾只剩一行免责说明 ===")
    tail = [ln for ln in md.splitlines() if ln.strip()]
    ok14 = check("最后一行是 <sub> 免责说明", tail[-1].startswith("<sub>覆盖"), tail[-1][:40])
    ok15 = check("免责说明在正文之后", md.find("<sub>") > md.find("https://example.com/b"))

    print("=== 4. 失败源告警（阈值 3）===")

    def head_of(failed: int) -> str:
        stats = dict(STATS, sources_failed=failed,
                     sources_ok=max(0, STATS["sources_total"] - failed))
        out = main.build_weekly_markdown(
            ANALYSIS, USAGE, OUTCOMES, "CI 直连", "最近 7 天", START, END, stats,
        )
        return next(ln for ln in out.splitlines() if ln.startswith("> 源："))

    h1 = head_of(1)
    ok16 = check("失败 1 个：不挂 ⚠️", "⚠️" not in h1, h1[:52])
    ok17 = check("失败 1 个：但如实写明失败数", "失败 1" in h1)
    h3 = head_of(3)
    ok18 = check("失败 3 个：挂 ⚠️", "⚠️" in h3, h3[:72])
    ok19 = check("失败 3 个：写明 3 个源失败", "有 3 个源抓取失败" in h3)
    h0 = head_of(0)
    ok20 = check("零失败：头部最干净", "失败" not in h0 and "⚠️" not in h0, h0[:52])

    print("\n=== 渲染出的末尾 12 行 ===")
    for line in md.splitlines()[-12:]:
        print("  " + line)

    passed = all([ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8, ok9, ok10,
                  ok11, ok12, ok13, ok14, ok15, ok16, ok17, ok18, ok19, ok20,
                  ok21, ok22])
    print(f"\n{'全部通过' if passed else '有失败项'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(test())
