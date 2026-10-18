"""离线测试 allocate() 的篇幅分配逻辑，不调用 API、不花 token。

覆盖几种真实会遇到的情况（断言的是"分配规则"，不是具体新闻）：
    1. 两类都有很多 must —— 被每类上限 + 总数上限截住
    2. 某一类候选很少 —— 不能强凑，保证至少 2 条 AI
    3. 平淡的一周（分数都低于门槛）—— 补到 MIN_TOTAL
    4. 一边倒（安全 12 条高分，AI 只有 2 条低分）—— AI 保底只能拿到 2
    5. 候选总数少于 MIN_TOTAL —— 全收，不硬凑

用法：python probe/test_allocate.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from analyzer import allocate  # noqa: E402
from fetch import Article  # noqa: E402

FAILED: list[str] = []


def mk(cat: str, i: int, score: int, tier: str):
    a = Article(title=f"{cat}-{i}", url=f"http://x/{cat}/{i}", summary="s",
                published=None, source="t", category=cat, lang="zh")
    return (a, {"score": score, "tier": tier, "reason": "r"})


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}: {got!r}" + ("" if ok else f"  ← 期望 {want!r}"))
    if not ok:
        FAILED.append(name)


def run_case(title: str, ranked):
    print(title)
    out = allocate(ranked, progress=lambda *_: None)
    sec = sum(1 for a, _ in out if a.category == "security")
    ai = sum(1 for a, _ in out if a.category == "ai")
    print(f"   候选 {len(ranked)} → 收录 {len(out)}（安全 {sec} / AI {ai}）"
          f" 分数 {[m['score'] for _, m in out]}")
    # 所有用例都该成立的不变量
    check("总数不超过上限", len(out) <= config.MAX_TOTAL, True)
    check("每类不超过上限",
          sec <= config.MAX_PER_CATEGORY and ai <= config.MAX_PER_CATEGORY, True)
    check("没有重复条目", len({id(a) for a, _ in out}) == len(out), True)
    print()
    return out, sec, ai


def main() -> int:
    print(f"配置：MIN_PER_CAT={config.MIN_PER_CATEGORY} "
          f"MAX_PER_CAT={config.MAX_PER_CATEGORY} "
          f"MIN_TOTAL={config.MIN_TOTAL} MAX_TOTAL={config.MAX_TOTAL} "
          f"MIN_SCORE={config.MIN_SCORE}\n")

    # 1. 两类都很强，各 12 条 must
    ranked = sorted(
        [mk("security", i, 9, "must") for i in range(12)]
        + [mk("ai", i, 9, "must") for i in range(12)],
        key=lambda x: -x[1]["score"])
    out, sec, ai = run_case("【1】两类各 12 条 must（应被每类上限 + 总数上限截住）", ranked)
    check("总数等于 MAX_TOTAL", len(out), config.MAX_TOTAL)
    check("先被截住的那一类拿满上限", sec, config.MAX_PER_CATEGORY)

    # 2. 安全很多，AI 只有 2 条候选
    ranked = sorted(
        [mk("security", i, 8, "good") for i in range(15)]
        + [mk("ai", i, 8, "good") for i in range(2)],
        key=lambda x: -x[1]["score"])
    out, sec, ai = run_case("【2】AI 只有 2 条候选（保底补不满，应收 2 条 AI）", ranked)
    check("AI 全部收下", ai, 2)
    check("安全拿满每类上限", sec, config.MAX_PER_CATEGORY)

    # 3. 平淡的一周，分数都低于门槛
    ranked = sorted(
        [mk("security", i, 5, "filler") for i in range(10)]
        + [mk("ai", i, 4, "filler") for i in range(10)],
        key=lambda x: -x[1]["score"])
    out, sec, ai = run_case("【3】平淡一周（分数 4-5，应补到 MIN_TOTAL）", ranked)
    check("补到 MIN_TOTAL", len(out), config.MIN_TOTAL)
    check("两类都有", sec > 0 and ai > 0, True)

    # 4. 一边倒：安全 12 条 9 分，AI 2 条 5 分
    ranked = sorted(
        [mk("security", i, 9, "must") for i in range(12)]
        + [mk("ai", i, 5, "filler") for i in range(2)],
        key=lambda x: -x[1]["score"])
    out, sec, ai = run_case("【4】一边倒（安全 12 must，AI 2 低分）", ranked)
    check("安全拿满每类上限", sec, config.MAX_PER_CATEGORY)
    check("AI 不被清零", ai, 2)

    # 5. 候选很少
    ranked = sorted(
        [mk("security", i, 8, "good") for i in range(2)]
        + [mk("ai", i, 8, "good") for i in range(2)],
        key=lambda x: -x[1]["score"])
    out, sec, ai = run_case("【5】候选只有 4 条（应全收，不硬凑）", ranked)
    check("全收", len(out), 4)

    # 6. 混合：must / good / filler 都有
    ranked = sorted(
        [mk("security", 1, 9, "must"), mk("security", 2, 8, "must"),
         mk("security", 3, 7, "good"), mk("security", 4, 5, "filler")]
        + [mk("ai", 1, 9, "must"), mk("ai", 2, 7, "good"),
           mk("ai", 3, 6, "good"), mk("ai", 4, 3, "filler")],
        key=lambda x: -x[1]["score"])
    out, sec, ai = run_case("【6】混合档位（低于门槛的要靠保底补足）", ranked)
    check("三条 must 都在", sum(1 for _, m in out if m["tier"] == "must"), 3)
    check("总分最高的排第一", out[0][1]["score"], 9)

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
