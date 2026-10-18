"""离线测试 allocate() 的篇幅分配逻辑，不调用 API。

覆盖几种真实会遇到的情况：
    1. 两类都有很多 must —— 应该被每类上限截住
    2. 某一类候选很少 —— 不能强凑，尽量补但补不满
    3. 平淡的一周（分数都不高）—— 只收 MIN_TOTAL
    4. 一边倒（安全 12 条高分，AI 只有 2 条候选）—— AI 保底只能拿到 2
    5. 候选总数少于 MIN_TOTAL —— 全收
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from analyzer import allocate  # noqa: E402
from fetch import Article  # noqa: E402


def mk(cat: str, i: int, score: int, tier: str):
    a = Article(title=f"{cat}-{i}", url=f"http://x/{cat}/{i}", summary="s",
                published=None, source="t", category=cat, lang="zh")
    return (a, {"score": score, "tier": tier, "reason": "r"})


def show(name: str, ranked):
    out = allocate(ranked, progress=lambda *_: None)
    sec = sum(1 for a, _ in out if a.category == "security")
    ai = sum(1 for a, _ in out if a.category == "ai")
    print(f"{name}")
    print(f"   候选 {len(ranked)} → 收录 {len(out)} 条（安全 {sec} / AI {ai}）")
    print(f"   分数: {[m['score'] for _, m in out]}")
    print()


def main() -> None:
    print(f"配置：MIN_PER_CAT={config.MIN_PER_CATEGORY} "
          f"MAX_PER_CAT={config.MAX_PER_CATEGORY} "
          f"MIN_TOTAL={config.MIN_TOTAL} MAX_TOTAL={config.MAX_TOTAL} "
          f"MIN_SCORE={config.MIN_SCORE}\n")

    # 1. 两类都很强，各 12 条 must
    ranked = sorted(
        [mk("security", i, 9, "must") for i in range(12)]
        + [mk("ai", i, 9, "must") for i in range(12)],
        key=lambda x: -x[1]["score"])
    show("【1】两类各 12 条 must（应被每类上限 + 总数上限截住）", ranked)

    # 2. 安全很多，AI 只有 2 条候选
    ranked = sorted(
        [mk("security", i, 8, "good") for i in range(15)]
        + [mk("ai", i, 8, "good") for i in range(2)],
        key=lambda x: -x[1]["score"])
    show("【2】AI 只有 2 条候选（保底补不满，应收 2 条 AI）", ranked)

    # 3. 平淡的一周，分数都低于门槛
    ranked = sorted(
        [mk("security", i, 5, "filler") for i in range(10)]
        + [mk("ai", i, 4, "filler") for i in range(10)],
        key=lambda x: -x[1]["score"])
    show("【3】平淡一周（分数 4-5，应只收到 MIN_TOTAL）", ranked)

    # 4. 一边倒：安全 12 条 9 分，AI 2 条 5 分
    ranked = sorted(
        [mk("security", i, 9, "must") for i in range(12)]
        + [mk("ai", i, 5, "filler") for i in range(2)],
        key=lambda x: -x[1]["score"])
    show("【4】一边倒（安全 12 must，AI 2 低分）", ranked)

    # 5. 候选很少
    ranked = sorted(
        [mk("security", i, 8, "good") for i in range(2)]
        + [mk("ai", i, 8, "good") for i in range(2)],
        key=lambda x: -x[1]["score"])
    show("【5】候选只有 4 条（应全收）", ranked)

    # 6. 混合：must / good / filler 都有
    ranked = sorted(
        [mk("security", 1, 9, "must"), mk("security", 2, 8, "must"),
         mk("security", 3, 7, "good"), mk("security", 4, 5, "filler")]
        + [mk("ai", 1, 9, "must"), mk("ai", 2, 7, "good"),
           mk("ai", 3, 6, "good"), mk("ai", 4, 3, "filler")],
        key=lambda x: -x[1]["score"])
    show("【6】混合档位（应排除 3 分 filler，分数低于门槛的要补足）", ranked)


if __name__ == "__main__":
    main()
