"""screening.py 的离线测试：不联网、不花 token。

守两条规则，都是真踩过坑的：

    1. 短词（ai / ml / gpu / cve）必须"前后都不是英文字母"。
       用 \\b 时中文会漏：'ai芯片'、'gpu加速'、'CVE-2026-1234漏洞' 全不命中，
       因为中文在 Python 正则里也算 word 字符，词边界不成立。
       反过来又不能让 'ai' 当成子串命中 'email'、'said'。

    2. 标题命中领域关键词时，黑名单不生效。
       否则 "Kling 4.0 正式上线" 会被"发版公告"规则误杀 —— 那是本周最重要的新闻之一。

用法：python probe/test_screening.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import screening  # noqa: E402
from fetch import Article  # noqa: E402

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'✓' if ok else '✗'} {name}: {got!r}" + ("" if ok else f"  ← 期望 {want!r}"))
    if not ok:
        FAILED.append(name)


def hit(text: str) -> bool:
    return screening._match(text.lower()) is not None


def keep(title: str, summary: str = "") -> bool:
    a = Article(title=title, url="u", summary=summary, published=None,
                source="test", category="ai", lang="zh")
    kept, _ = screening.apply_filters([a])
    return bool(kept)


def main() -> int:
    print("=== 1. 短词：中文连写要命中 ===")
    check("ai芯片", hit("ai芯片发布"), True)
    check("AI Agent", hit("AI Agent 工作流"), True)
    check("gpu加速", hit("gpu加速方案"), True)
    check("GPU 加速（带空格）", hit("GPU 加速"), True)
    check("CVE-2026-1234漏洞", hit("CVE-2026-1234漏洞在野利用"), True)
    check("小写 cve", hit("cve-2026-1234"), True)
    check("纯 ai", hit("ai"), True)

    print("=== 2. 短词：不能误命中英文子串 ===")
    check("email", hit("send me an email"), False)
    check("said", hit("he said nothing"), False)
    check("detail", hit("details matter"), False)
    check("html", hit("html parser"), False)
    check("capital（含 ml 无关）", hit("capital letter"), False)

    print("=== 3. 关键词命中优先于黑名单 ===")
    # 标题里有"正式上线"，但命中了 ai 关键词（kling）→ 必须保留
    check("Kling 4.0正式上线", keep("Kling 4.0正式上线：10-bit HDR、30秒原生直出"), True)
    check("Gemini 4上线（含限时半价）", keep("刚刚，Gemini 4终于来了：谷歌重回御三家，API限时半价"), True)

    print("=== 4. 无关内容该丢就得丢 ===")
    check("促销码", keep("Surfshark Promo Codes: 87% Off | October 2026"), False)
    check("产品评测", keep("Razer Barracuda V2 Pro Review: A Gaming Headset"), False)
    check("政治", keep("Democrats Are Gearing Up to Investigate the Trump Administration"), False)
    check("纯发版公告", keep("novel-plus v5.4.0 发布，原创文学 CMS 系统"), False)

    print("=== 5. 标题没命中、摘要命中 → 保留 ===")
    check("摘要里提到勒索", keep("某公司昨天出事了", "这次是勒索软件攻击"), True)
    check("摘要里完全没有", keep("某公司发布新款键盘", "手感很好，价格便宜"), False)

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
