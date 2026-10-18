"""离线测试 allocate() 的篇幅分配逻辑，不调用 API、不花 token。

覆盖几种真实会遇到的情况（断言的是"分配规则"，不是具体新闻）：
    1. 四个板块都爆满 —— 被每个板块的上限 + 总上限截住
    2. 同一个源发了 10 条高分 —— 同源上限 3 条生效
    3. 某个板块候选很少 —— 不强凑（保底补不满就空着）
    4. 平淡的一周（分数都低于门槛）—— 补到 MIN_TOTAL 且四个板块都在
    5. 候选总数少于 MIN_TOTAL —— 全收，不硬凑
    6. 总名额被高分板块吃满 —— 倒换机制给缺保底的板块腾位子

用法：python probe/test_allocate.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from analyzer import allocate  # noqa: E402
from fetch import Article  # noqa: E402

FAILED: list[str] = []


def mk(cat: str, i: int, score: int, tier: str, source: str | None = None):
    src = source or f"{cat}-src-{i}"
    a = Article(title=f"{cat}-{i}", url=f"http://x/{cat}/{i}", summary="s",
                published=None, source=src, category=cat, lang="zh")
    # 板块放在 meta 里 —— 生产代码里这是 joint_rank() 判定的结果
    return (a, {"score": score, "tier": tier, "reason": "r", "category": cat})


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}: {got!r}" + ("" if ok else f"  ← 期望 {want!r}"))
    if not ok:
        FAILED.append(name)


def counts_of(out) -> dict:
    d = {c: 0 for c in config.CATEGORIES}
    for _, m in out:
        d[m["category"]] = d.get(m["category"], 0) + 1
    return d


def run_case(title: str, ranked):
    print(title)
    out = allocate(ranked, progress=lambda *_: None)
    d = counts_of(out)
    dist = " / ".join(f"{c} {n}" for c, n in d.items() if n)
    print(f"   候选 {len(ranked)} → 收录 {len(out)}（{dist}）"
          f" 分数 {[m['score'] for _, m in out]}")
    # 所有用例都该成立的不变量
    check("总数不超过上限", len(out) <= config.MAX_TOTAL, True)
    check("每个板块不超过上限",
          all(d[c] <= config.CATEGORIES[c]["max"] for c in config.CATEGORIES), True)
    check("没有重复条目", len({id(a) for a, _ in out}) == len(out), True)
    per_src: dict[str, int] = {}
    for a, _ in out:
        per_src[a.source] = per_src.get(a.source, 0) + 1
    over = {s: n for s, n in per_src.items()
            if n > config.MAX_PER_SOURCE and s not in config.MAX_PER_SOURCE_EXEMPT}
    check("没有源超编", over, {})
    print()
    return out, d


def main() -> int:
    caps = "，".join(f"{c} {config.CATEGORIES[c]['min']}-{config.CATEGORIES[c]['max']}"
                    for c in config.CATEGORIES)
    print(f"配置：{caps}；总 {config.MIN_TOTAL}-{config.MAX_TOTAL}；"
          f"MIN_SCORE={config.MIN_SCORE}，同源 ≤{config.MAX_PER_SOURCE}\n")

    # 1. 四个板块各 15 条 must —— 每个板块上限 + 总上限都会生效
    ranked = sorted(
        [mk(c, i, 9, "must") for c in config.CATEGORIES for i in range(15)],
        key=lambda x: -x[1]["score"])
    out, d = run_case("【1】四板块各 15 条 must（应被板块上限 + 总上限截住）", ranked)
    check("总数等于 MAX_TOTAL", len(out), config.MAX_TOTAL)
    check("每个板块都到上限或总上限",
          all(d[c] == config.CATEGORIES[c]["max"] for c in config.CATEGORIES)
          or sum(d.values()) == config.MAX_TOTAL, True)

    # 2. 同源限制：一家媒体发 10 条 9 分
    ranked = sorted(
        [mk("security", i, 9, "must", source="The Hacker News") for i in range(10)]
        + [mk("security", i, 7, "good", source=f"other-{i}") for i in range(6)],
        key=lambda x: -x[1]["score"])
    out, d = run_case("【2】同源 10 条高分（每个源最多 3 条）", ranked)
    check("同源条目正好 3 条",
          sum(1 for a, _ in out if a.source == "The Hacker News"), config.MAX_PER_SOURCE)
    check("用别的源补齐了", len(out) >= 5, True)

    # 3. 某个板块候选很少
    ranked = sorted(
        [mk("ai", i, 8, "good") for i in range(12)]
        + [mk("hardware", i, 8, "good") for i in range(1)],
        key=lambda x: -x[1]["score"])
    out, d = run_case("【3】硬件只有 1 条候选（保底补不满，应收 1 条）", ranked)
    check("硬件全部收下", d["hardware"], 1)

    # 4. 平淡的一周，分数都低于门槛
    ranked = sorted(
        [mk(c, i, 5 if c != "hardware" else 4, "filler")
         for c in config.CATEGORIES for i in range(6)],
        key=lambda x: -x[1]["score"])
    out, d = run_case("【4】平淡一周（分数 4-5，应补到 MIN_TOTAL 且四板块都有）", ranked)
    check("补到 MIN_TOTAL", len(out), config.MIN_TOTAL)
    check("四个板块都在", all(d[c] > 0 for c in config.CATEGORIES), True)

    # 5. 候选很少
    ranked = sorted(
        [mk("ai", i, 8, "good") for i in range(2)]
        + [mk("security", i, 8, "good") for i in range(2)],
        key=lambda x: -x[1]["score"])
    out, d = run_case("【5】候选只有 4 条（应全收，不硬凑）", ranked)
    check("全收", len(out), 4)

    # 6. 全部名额被 must 吃满：不许为保底踢掉 must（宁可让硬件空着）
    ranked = sorted(
        [mk("ai", i, 9, "must") for i in range(8)]
        + [mk("security", i, 9, "must") for i in range(6)]
        + [mk("opensource", i, 9, "must") for i in range(6)]
        + [mk("hardware", i, 5, "filler") for i in range(3)],
        key=lambda x: -x[1]["score"])
    out, d = run_case("【6】名额被 must 占满（不许踢 must，硬件允许空着）", ranked)
    check("must 一条没丢", sum(1 for _, m in out if m["tier"] == "must"),
          sum(1 for _, m in ranked if m["tier"] == "must"))

    # 6b. 倒换：富余板块里有 good，就该让位给缺保底的板块
    ranked = sorted(
        [mk("ai", i, 9, "must") for i in range(8)]
        + [mk("security", i, 9, "must") for i in range(6)]
        + [mk("opensource", i, 9, "must") for i in range(4)]
        + [mk("opensource", 10 + i, 7, "good") for i in range(2)]
        + [mk("hardware", i, 5, "filler") for i in range(3)],
        key=lambda x: -x[1]["score"])
    out, d = run_case("【6b】富余板块有 good（应倒换腾位给硬件）", ranked)
    check("硬件达到保底", d["hardware"], config.CATEGORIES["hardware"]["min"])
    check("must 一条没丢", sum(1 for _, m in out if m["tier"] == "must"),
          sum(1 for _, m in ranked if m["tier"] == "must"))

    # 7. 混合档位
    ranked = sorted(
        [mk("security", 1, 9, "must"), mk("security", 2, 8, "must"),
         mk("security", 3, 7, "good"), mk("security", 4, 5, "filler")]
        + [mk("ai", 1, 9, "must"), mk("ai", 2, 7, "good"),
           mk("ai", 3, 6, "good"), mk("ai", 4, 3, "filler")]
        + [mk("opensource", 1, 8, "good"), mk("opensource", 2, 6, "good")]
        + [mk("hardware", 1, 7, "good")],
        key=lambda x: -x[1]["score"])
    out, d = run_case("【7】混合档位（低于门槛的靠保底补足）", ranked)
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
