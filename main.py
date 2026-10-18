"""主入口：抓取 → 筛选 → AI 精选 → 输出周报。

完整流程：
    1. 探测代理，决定加载哪套源（国内源 / 国内+英文）
    2. 并发抓取所有 RSS
    3. 时间窗口过滤 + 标题去重
    4. 关键词粗筛（黑名单 + 领域词），把量压下来
    5. AI 精选：召回 → 联合排序 → 按重要性分配篇幅 → 撰写（受 token 预算约束）
    6. 渲染 Markdown 周报

用法：
    python main.py                 # 最近 7 天，有 Key 就走 AI，自动判断代理
    python main.py --days 5        # 最近 5 天
    python main.py --week          # 按自然周（周一起）
    python main.py --no-proxy      # 强制直连，只加载国内源
    python main.py --no-ai         # 不调模型，只输出筛选后的全量清单
    python main.py --no-save       # 只打印，不写文件
    python main.py --check-budget  # 只看本周额度用了多少，不抓取
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import analyzer
import budget
import config
import fetch
import netcheck
import notify
import screening

CATEGORY_LABEL = {
    "security": "🔒 网络安全",
    "ai": "🤖 人工智能",
}
LANG_LABEL = {"zh": "中文", "en": "英文"}


# ---------------------------------------------------------------------------
# 时间窗口
# ---------------------------------------------------------------------------
def resolve_window(args) -> tuple[datetime, datetime, str]:
    """返回 (起, 止, 给人看的描述)。

    会顺便做"漏跑补偿"：如果上次成功运行距今超过一个周期，
    自动把窗口扩大到覆盖这段间隔，避免漏掉新闻。
    """
    now = datetime.now()

    if args.all:
        return datetime(1970, 1, 1), now, "不限时间"

    if args.week:
        # 本周：从本周一 00:00 开始
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, now, f"本周（{start:%m-%d} 起）"

    days, note = budget.catchup_days(args.days)
    if note:
        print(f"⏱  {note}")

    start = now - timedelta(days=days)
    desc = f"最近 {days} 天" if days == args.days else f"最近 {days} 天（含补跑）"
    return start, now, desc


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------
_PUNCT = "！？。，、；：""''（）【】《》…—·,.!?;:\"'()[]<>-_/\\|@#$%^&*+=~`"


def normalize_title(title: str) -> str:
    """归一化标题：去掉标点和空白，转小写。用于查重。"""
    cleaned = "".join(ch for ch in title.lower() if ch not in _PUNCT and not ch.isspace())
    return cleaned


def dedupe(articles: list[fetch.Article]) -> tuple[list[fetch.Article], int]:
    """按归一化标题去重。返回 (去重后列表, 去掉的数量)。

    注意：这只能去掉"同一篇内容被转载"的重复。
    中英文对同一条新闻的不同报道，靠标题没法判断，得靠模型做语义去重。
    """
    seen: set[str] = set()
    kept: list[fetch.Article] = []
    removed = 0

    for a in articles:
        key = normalize_title(a.title)
        if not key:
            removed += 1
            continue
        # 标题前 18 字相同也视为重复（应对"标题后加了后缀"的转载）
        short = key[:18]
        if key in seen or (len(short) >= 12 and short in seen):
            removed += 1
            continue
        seen.add(key)
        if len(short) >= 12:
            seen.add(short)
        kept.append(a)

    return kept, removed


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def print_articles(articles: list[fetch.Article], limit: int | None = None) -> None:
    """终端里按分类打印，方便你肉眼检查抓得对不对。"""
    for cat in ("security", "ai"):
        group = [a for a in articles if a.category == cat]
        if not group:
            continue
        print(f"\n{'=' * 78}")
        print(f"{CATEGORY_LABEL[cat]}   共 {len(group)} 篇")
        print("=" * 78)
        for i, a in enumerate(group[: limit or len(group)], 1):
            date_s = a.published.strftime("%m-%d") if a.published else "  ?  "
            lang = "中" if a.lang == "zh" else "EN"
            print(f"{i:>3}. [{date_s}] [{lang}] {a.title}")
            print(f"       {a.source}  {a.url}")
            if a.summary:
                preview = a.summary[:100].replace("\n", " ")
                print(f"       └ {preview}...")


def build_markdown(articles: list[fetch.Article], outcomes: list[fetch.FetchOutcome],
                   proxy: netcheck.ProxyStatus, window_desc: str,
                   start: datetime, end: datetime, stats: dict,
                   fstats: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# 安全 & AI 新闻摘要 · {start:%Y-%m-%d} ~ {end:%Y-%m-%d}")
    add("")
    add(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M}　|　时间窗口：{window_desc}　|　"
        f"网络模式：{proxy}")
    add("")
    add(f"> 源：{stats['sources_total']} 个（成功 {stats['sources_ok']}，"
        f"跳过 {stats['sources_skipped']}，失败 {stats['sources_failed']}）　|　"
        f"抓到 {stats['fetched']} 篇　→　窗口内 {stats['in_window']} 篇　→　"
        f"去重后 {stats['after_dedupe']} 篇　→　**筛选后 {stats['after_filter']} 篇**")
    add("")

    for cat in ("security", "ai"):
        group = [a for a in articles if a.category == cat]
        if not group:
            continue
        zh = sum(1 for a in group if a.lang == "zh")
        add(f"## {CATEGORY_LABEL[cat]}（{len(group)} 条，中文 {zh} / 英文 {len(group) - zh}）")
        add("")
        for i, a in enumerate(group, 1):
            date_s = a.published.strftime("%Y-%m-%d") if a.published else "日期未知"
            add(f"### {i}. {a.title}")
            add("")
            add(f"- **来源**：{a.source}（{LANG_LABEL.get(a.lang, a.lang)}）　"
                f"**时间**：{date_s}")
            if a.summary:
                add(f"- **摘要**：{a.summary}")
            else:
                add("- **摘要**：（该源未提供摘要，只有标题）")
            add(f"- **链接**：{a.url}")
            add("")

    # 附：筛选情况。用来观察关键词表有没有误杀，调参必备。
    add("---")
    add("")
    add("## 附：筛选情况")
    add("")
    add(f"- 去重后 {fstats['input']} 篇")
    add(f"- 黑名单丢弃 {fstats['blocked']} 篇（促销码、评测、娱乐、发版公告等）")
    add(f"- 无关键词丢弃 {fstats['keyword_miss']} 篇（跟安全/AI 无关）")
    add(f"- **保留 {fstats['kept']} 篇**")
    add("")
    if fstats["blocked_samples"]:
        add("<details><summary>黑名单丢弃样例</summary>")
        add("")
        for title, pattern in fstats["blocked_samples"]:
            add(f"- `{pattern}` — {title}")
        add("")
        add("</details>")
        add("")
    if fstats["keyword_miss_samples"]:
        add("<details><summary>无关键词丢弃样例（检查是否误杀）</summary>")
        add("")
        for title in fstats["keyword_miss_samples"]:
            add(f"- {title}")
        add("")
        add("</details>")
        add("")

    # 附：抓取情况。这张表是排查问题用的。
    add("---")
    add("")
    add("## 附：抓取情况")
    add("")
    add("| 源 | 状态 | 条数 | 说明 |")
    add("| --- | --- | --- | --- |")
    for o in sorted(outcomes, key=lambda x: (x.error == "", x.name)):
        if o.skipped:
            state, note = "跳过", o.error
            count = "-"
        elif o.error:
            state, note = "**失败**", o.error
            count = "-"
        else:
            state = "正常"
            note = "走代理" if o.via_proxy else "直连"
            count = str(len(o.articles))
        add(f"| {o.name} | {state} | {count} | {note} |")
    add("")

    return "\n".join(lines)


def build_weekly_markdown(analysis: dict, usage, outcomes: list[fetch.FetchOutcome],
                          proxy: netcheck.ProxyStatus, window_desc: str,
                          start: datetime, end: datetime, stats: dict) -> str:
    """渲染最终周报。

    注意：标题、摘要、"为什么重要"来自模型；
    链接、来源、日期来自本地数据 —— 模型没有机会编造链接。

    不显示重要性评分：能进这份周报的都已经过门槛，
    评分只用于内部排序，读者不需要看见。
    """
    lines: list[str] = []
    add = lines.append

    items = analysis["items"]
    sec = [i for i in items if i["category"] == "security"]
    ai = [i for i in items if i["category"] == "ai"]

    add(f"# 安全 & AI 周报 · {start:%Y-%m-%d} ~ {end:%Y-%m-%d}")
    add("")
    add(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M}　|　时间窗口：{window_desc}")
    add(">")
    add(f"> 源：{stats['sources_total']} 个（成功 {stats['sources_ok']}）　|　"
        f"抓取 {stats['fetched']} 篇　→　窗口内 {stats['in_window']} 篇　→　"
        f"去重后 {stats['after_dedupe']} 篇　→　粗筛 {stats['after_filter']} 篇　→　"
        f"**收录 {len(items)} 条**")
    add(">")
    add(f"> 模型：{config.MODEL_SCREEN}（召回）+ {config.MODEL_RERANK}（排序）"
        f"+ {config.MODEL_WRITE}（撰写）　|　用量：{usage}")
    add("")

    if analysis.get("overview"):
        add("## 本周观察")
        add("")
        add(analysis["overview"].strip())
        add("")

    # 人工智能在前，网络安全在后
    for label, group in (("🤖 人工智能", ai), ("🔒 网络安全", sec)):
        if not group:
            continue
        add(f"## {label}（{len(group)} 条）")
        add("")
        for i, item in enumerate(group, 1):
            date_s = item["published"].strftime("%Y-%m-%d") if item["published"] else "日期未知"
            add(f"### {i}. {item['title']}")
            add("")
            add(f"- **摘要**：{item['summary']}")
            add(f"- **为什么重要**：{item['why']}")
            add(f"- **来源**：{item['source']}（{date_s}）")
            add(f"- **原文**：{item['url']}")
            add("")

    # 给新人的建议：放在正文末尾、附录之前。模型没给就整段略去。
    advice = analysis.get("advice") or []
    if advice:
        add("---")
        add("")
        add("## 🎯 给想入行网络安全的你")
        add("")
        for i, tip in enumerate(advice, 1):
            add(f"{i}. {tip}")
        add("")

    add("---")
    add("")
    add("## 附：数据来源")
    add("")
    add("| 源 | 状态 | 抓取条数 | 路线 |")
    add("| --- | --- | --- | --- |")
    for o in sorted(outcomes, key=lambda x: (x.error == "", x.name)):
        if o.skipped:
            state, note, count = "跳过", o.error, "-"
        elif o.error:
            state, note, count = "**失败**", o.error, "-"
        else:
            state = "正常"
            note = "走代理" if o.via_proxy else "直连"
            count = str(len(o.articles))
        add(f"| {o.name} | {state} | {count} | {note} |")
    add("")
    add(f"<sub>覆盖 {start:%Y-%m-%d} 至 {end:%Y-%m-%d}。"
        f"摘要与点评由 AI 基于 RSS 提供的标题和描述生成，"
        f"原文链接均来自源站，请以原文为准。</sub>")
    add("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="抓取本周网络安全 / AI 新闻")
    parser.add_argument("--days", type=int, default=7,
                        help="回溯天数（默认 7，对应每周一期）")
    parser.add_argument("--week", action="store_true", help="按本周（周一起）而非滚动 7 天")
    parser.add_argument("--all", action="store_true", help="不限时间窗口")
    parser.add_argument("--no-proxy", action="store_true", help="强制不使用代理")
    parser.add_argument("--direct-all", action="store_true",
                        help="所有源都直连（GitHub Actions 等可直连墙外的环境用）")
    parser.add_argument("--no-filter", action="store_true", help="不做关键词筛选，看原始全量")
    parser.add_argument("--no-ai", action="store_true", help="不调模型，只输出筛选后的清单")
    parser.add_argument("--no-save", action="store_true", help="只在终端显示，不写文件")
    parser.add_argument("--check-budget", action="store_true",
                        help="只看本周 token 额度，不抓取不调用模型")
    parser.add_argument("--limit", type=int, default=0, help="终端每类最多显示几条（0=全部）")
    args = parser.parse_args()

    # 只看额度，直接返回
    if args.check_budget:
        b = budget.Budget.load()
        print(b.bar(40))
        print(b.summary())
        print(f"单次运行上限：{config.MAX_RUN_TOKENS:,} tokens，"
              f"{config.MAX_CALLS_PER_RUN} 次调用")
        last = budget.load_last_success()
        print(f"上次成功运行：{last or '（还没有记录）'}")
        return 0

    start, end, window_desc = resolve_window(args)

    # ---- 1. 判断网络模式 ----
    # GitHub Actions 的服务器本来就能访问墙外站点，
    # 所以在那里不需要代理，也不该因为"没代理"就把英文源跳过。
    is_ci = bool(os.environ.get("GITHUB_ACTIONS")) or args.direct_all

    print("=" * 78)
    if is_ci:
        proxy = netcheck.ProxyStatus(
            False, source="ci",
            note="直连模式（GitHub Actions / --direct-all）：所有源都直连，不检测代理")
        print(f"网络模式：{proxy}")
        sources = list(config.SOURCES)
        skipped_sources = 0
    else:
        if args.no_proxy:
            proxy = netcheck.ProxyStatus(False, source="forced",
                                         note="已强制直连（--no-proxy）")
        else:
            print("正在探测代理 ...")
            proxy = netcheck.detect()
        print(f"网络模式：{proxy}")
        sources = [s for s in config.SOURCES
                   if not (s.get("requires_proxy") and not proxy.available)]
        skipped_sources = len(config.SOURCES) - len(sources)

    print(f"加载源：{len(sources)} 个"
          + (f"（另有 {skipped_sources} 个需要代理的源已跳过）" if skipped_sources else ""))
    print(f"时间窗口：{window_desc}  （{start:%Y-%m-%d} ~ {end:%Y-%m-%d}）")
    print("-" * 78)

    # ---- 2. 抓取 ----
    print("开始抓取：")
    all_articles, outcomes = fetch.fetch_all(sources, proxy, progress=print,
                                             force_direct=is_ci)

    # ---- 3. 时间过滤 ----
    in_window = [
        a for a in all_articles
        if a.published is None or (start <= a.published <= end)
    ]
    undated = sum(1 for a in in_window if a.published is None)

    # ---- 4. 去重 ----
    deduped, removed = dedupe(in_window)

    # ---- 5. 两级筛选（黑名单 + 关键词）----
    filtered, fstats = screening.apply_filters(deduped, use_blocklist=not args.no_filter)

    # ---- 6. 排序：日期新的在前，无日期的垫底 ----
    filtered.sort(key=lambda a: (a.published is not None, a.published or datetime.min),
                  reverse=True)

    ok = sum(1 for o in outcomes if not o.error and not o.skipped)
    stats = {
        "sources_total": len(config.SOURCES),
        "sources_ok": ok,
        "sources_skipped": skipped_sources + sum(1 for o in outcomes if o.skipped),
        "sources_failed": sum(1 for o in outcomes if o.error and not o.skipped),
        "fetched": len(all_articles),
        "in_window": len(in_window),
        "after_dedupe": len(deduped),
        "after_filter": len(filtered),
    }

    print("-" * 78)
    print(f"抓取完成：{stats['fetched']} 篇 → 窗口内 {stats['in_window']} 篇 "
          f"→ 去重后 {stats['after_dedupe']} 篇（去掉重复 {removed} 篇）")
    print(f"筛选结果：{fstats['input']} 篇 → 保留 {fstats['kept']} 篇"
          f"（黑名单丢 {fstats['blocked']}，无关丢 {fstats['keyword_miss']}）")
    if undated:
        print(f"注意：{undated} 篇没有发布时间，已一并保留（无法判断是否在本周）")
    if stats["sources_failed"]:
        print(f"有 {stats['sources_failed']} 个源抓取失败，见报告末尾的「抓取情况」表")

    # ---- 7. AI 精选（有 Key 且未禁用时）----
    use_ai = not args.no_ai and bool(config.get_api_key())
    if args.no_ai:
        print("\n[--no-ai] 跳过 AI 环节，只输出粗筛后的清单")
    elif not config.get_api_key():
        print("\n[跳过 AI] 没有找到 DEEPSEEK_API_KEY，只输出粗筛后的清单")

    ai_budget = None
    if use_ai:
        # ---- 加载本周 token 预算 ----
        ai_budget = budget.Budget.load()
        ai_budget.start_run()
        ai_budget.save()
        print(f"\n{ai_budget.bar()}  {ai_budget.summary()}")
        if ai_budget.should_warn():
            notify.notify(
                "本周 AI 额度接近上限",
                f"{ai_budget.summary()}\n本次运行会继续尝试，但如果额度耗尽会自动中止。",
                level="warning",
            )

        print("\n" + "=" * 78)
        print(f"AI 精选（候选 {len(filtered)} 篇，篇幅区间 "
              f"{config.MIN_TOTAL}-{config.MAX_TOTAL} 条，按重要性浮动）")
        print("-" * 78)
        try:
            analysis, usage = analyzer.analyze(filtered, progress=print,
                                               budget=ai_budget)
        except budget.BudgetExceeded as e:
            # 预算耗尽：这是要让人知道的事件，走通知
            print(f"\n🛑 预算拦截：{e}")
            notify.notify(
                "AI token 预算已耗尽，本次周报未生成",
                f"{e}\n\n已自动降级为粗筛清单输出。\n{ai_budget.summary()}",
                level="error",
            )
            use_ai = False
        except analyzer.ApiError as e:
            print(f"\nAI 环节失败：{e}")
            notify.notify(
                "AI 环节失败，本次周报未生成",
                f"{e}\n\n已自动降级为粗筛清单输出。\n{ai_budget.summary()}",
                level="error",
            )
            use_ai = False
        finally:
            ai_budget.save()

    if use_ai:
        print("-" * 78)
        print(f"收录 {len(analysis['items'])} 条（{usage}）")
        for i, item in enumerate(analysis["items"], 1):
            tag = "AI" if item["category"] == "ai" else "安全"
            print(f"{i:>3}. [{tag}] {item['title']}")
            print(f"       {item['summary'][:80]}")

        if not args.no_save:
            out_dir = Path(config.OUTPUT_DIR)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"weekly_{datetime.now():%Y-%m-%d}.md"
            md = build_weekly_markdown(analysis, usage, outcomes, proxy,
                                       window_desc, start, end, stats)
            out_path.write_text(md, encoding="utf-8")
            print("\n" + "=" * 78)
            print(f"周报已保存：{out_path}")
            print(f"文件大小：{out_path.stat().st_size:,} 字节")
            # 只有真正写出周报才算"成功运行"，用于下次的漏跑补偿
            budget.save_last_success()

        # 本次运行结束后的额度状态
        if ai_budget:
            print(f"{ai_budget.bar()}  {ai_budget.summary()}")
            print(f"          {ai_budget.run_summary()}")
            if ai_budget.should_warn():
                notify.notify(
                    "本周 AI 额度已用到 80% 以上",
                    f"{ai_budget.summary()}\n{ai_budget.run_summary()}\n"
                    f"后续运行可能因额度不足而中止。",
                    level="warning",
                )
        return 0

    # ---- 8. 无 AI 时的降级输出：粗筛清单 ----
    print_articles(filtered, limit=args.limit or None)

    if not args.no_save:
        out_dir = Path(config.OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"digest_{datetime.now():%Y-%m-%d}.md"
        md = build_markdown(filtered, outcomes, proxy, window_desc, start, end,
                            stats, fstats)
        out_path.write_text(md, encoding="utf-8")
        print("\n" + "=" * 78)
        print(f"已保存：{out_path}")
        print(f"文件大小：{out_path.stat().st_size:,} 字节")

    return 0


if __name__ == "__main__":
    sys.exit(main())
