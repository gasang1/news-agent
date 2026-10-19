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
    python main.py --if-missing    # 今天的周报已存在就跳过，没有才补跑
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import analyzer
import budget
import config
import fetch
import netcheck
import notify
import replay
import screening

CATEGORY_LABEL = {c: v["label"] for c, v in config.CATEGORIES.items()}
CATEGORY_ORDER = sorted(config.CATEGORIES,
                        key=lambda c: config.CATEGORIES[c]["order"])
LANG_LABEL = {"zh": "中文", "en": "英文"}
REPORT_TITLE = "# 科技周报"


# ---------------------------------------------------------------------------
# 时间窗口
# ---------------------------------------------------------------------------
def today_report_path(day: date | None = None) -> Path:
    """今天那次运行"应该"产出的周报路径。

    文件名用的是**运行当天日期**（见下面保存周报那一段），
    所以补跑闸门只要看这一个路径就知道今天有没有成功过。
    """
    day = day or date.today()
    return Path(config.OUTPUT_DIR) / f"weekly_{day:%Y-%m-%d}.md"


def report_ready(path: Path) -> bool:
    """判断一份周报是否"真的生成了"。

    只看文件存不存在不够：写入过程中崩溃会留下半个文件。所以还要求体积达标
    （正常周报 8~15 KB，下限设成 `config.MIN_REPORT_BYTES`）。
    """
    try:
        return path.is_file() and path.stat().st_size >= config.MIN_REPORT_BYTES
    except OSError:
        return False


def week_anchor(day: date | None = None) -> date:
    """这一周的周一（周报的"周期锚点"）。"""
    day = day or date.today()
    return day - timedelta(days=day.weekday())


def week_report_ready(day: date | None = None) -> tuple[bool, Path | None]:
    """本周（从周一起）是否已经有可用的周报。

    为什么按"周"而不是按"今天"判断——这是 2026-10-19 那次事故换来的教训：
    定时任务只是"尽力而为"，GitHub 可能延迟几十分钟，也可能整个丢掉。
    按天判断的话，周一那次没触发就等于这一周都没人管，而用户只会觉得
    "说好的周一中午呢"。按周判断，只要本周**任意一天**成功过一次，
    后面几天就不会重复花钱；反过来，周一没跑成，周二到周日任何一天都还能补上。
    """
    anchor = week_anchor(day)
    out = Path(config.OUTPUT_DIR)
    if not out.is_dir():
        return False, None

    best: tuple[date, Path] | None = None
    for p in out.glob("weekly_*.md"):
        try:
            d = datetime.strptime(p.stem[len("weekly_"):], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < anchor or not report_ready(p):
            continue
        if best is None or d > best[0]:
            best = (d, p)
    return (best is not None), (best[1] if best else None)


def too_few_items(n_items: int) -> bool:
    """收录条数是不是低到"该当故障处理"。

    正常一周 10-18 条。掉到 `config.MIN_ITEMS_FOR_SUCCESS` 以下通常不是
    "这周真没新闻"，而是某个环节坏了 —— 源大面积失败、模型只返回几条、
    分类被整批归错。这种情况必须让补跑机制接手（见 main() 里的注释）。
    """
    return n_items < config.MIN_ITEMS_FOR_SUCCESS


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
        f"跳过 {stats['sources_skipped']}，失败 {stats['sources_failed']}）"
        + (" ⚠️" if stats["sources_failed"] >= config.FAILED_SOURCE_WARN_THRESHOLD else "")
        + f"　|　抓到 {stats['fetched']} 篇　→　窗口内 {stats['in_window']} 篇　→　"
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
    # 板块顺序由 config.CATEGORIES[*]["order"] 决定（AI → 安全 → 开源 → 硬件）。
    # 模型理论上只输出四类之一，但存档/旧数据里可能有别的值 —— 兜底塞进 AI，
    # 免得条目莫名其妙消失。
    groups: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    for it in items:
        cat = it.get("category")
        groups[cat if cat in groups else "ai"].append(it)

    add(f"{REPORT_TITLE} · {start:%Y-%m-%d} ~ {end:%Y-%m-%d}")
    add("")
    add(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M}　|　时间窗口：{window_desc}")
    add(">")
    # 失败源少于阈值时保持干净，到阈值才挂警告 ——
    # 见 config.FAILED_SOURCE_WARN_THRESHOLD 的说明。
    failed = stats.get("sources_failed", 0)
    if stats.get("replay"):
        # 存档重放：没联网，源统计无意义 —— 别写出"成功 0"让人以为全挂了
        head = (f"> 源：存档重放（{stats['fetched']} 篇，未联网）"
                f"　|　**收录 {len(items)} 条**")
    else:
        head = (f"> 源：{stats['sources_total']} 个（成功 {stats['sources_ok']}"
                + (f"，失败 {failed}" if failed else "") + "）")
        if failed >= config.FAILED_SOURCE_WARN_THRESHOLD:
            head += f" ⚠️ 有 {failed} 个源抓取失败，本期可能漏掉部分新闻"
        head += (f"　|　抓取 {stats['fetched']} 篇　→　窗口内 {stats['in_window']} 篇　→　"
                 f"去重后 {stats['after_dedupe']} 篇　→　粗筛 {stats['after_filter']} 篇　→　"
                 f"**收录 {len(items)} 条**")
    add(head)
    add(">")
    add(f"> 模型：{config.MODEL_SCREEN}（召回）+ {config.MODEL_RERANK}（排序）"
        f"+ {config.MODEL_WRITE}（撰写）　|　用量：{usage}")
    add("")

    if analysis.get("overview"):
        add("## 本周观察")
        add("")
        add(analysis["overview"].strip())
        add("")

    # 按板块顺序渲染：人工智能 → 网络安全 → 开源项目 → 科技硬件
    for cat in CATEGORY_ORDER:
        group = groups[cat]
        if not group:
            continue
        add(f"## {CATEGORY_LABEL[cat]}（{len(group)} 条）")
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

    # 正文到此结束。数据来源明细和给新人的建议都不再渲染进周报，
    # 想看每个源的成败请看本次运行的日志输出（build_markdown 里的『抓取情况』）。
    add(f"<sub>覆盖 {start:%Y-%m-%d} 至 {end:%Y-%m-%d}。"
        f"摘要与点评由 AI 基于 RSS 提供的标题和描述生成，"
        f"原文链接均来自源站，请以原文为准。</sub>")
    add("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 零成本重放：用存档的模型判定重新渲染
# ---------------------------------------------------------------------------
def render_from_analysis(path: str, no_save: bool, no_notify: bool) -> int:
    """用 --save-analysis 存下的判定重新渲染周报。不联网、不调模型、零 token。

    调"分配篇幅 / 板块顺序 / 排版"时用它：改完代码立刻能看新效果，
    而不用重新抓 RSS、重新烧 5 万 tokens。
    """
    data = replay.load_analysis(path)
    analysis = data["analysis"]
    stats = dict(data["stats"] or {})
    stats["replay"] = True
    md = build_weekly_markdown(analysis, data["usage"], [], None,
                               data["window_desc"], data["start"], data["end"],
                               stats)

    print("=" * 78)
    print(f"从存档重放：{path}（{len(analysis['items'])} 条，零 token）")
    print(f"存档参数：{data['window_desc']}，{data['start']:%Y-%m-%d} ~ {data['end']:%Y-%m-%d}")
    print(f"存档用量：{data['usage']}")
    print("-" * 78)
    for i, item in enumerate(analysis["items"], 1):
        cat = item.get("category", "?")
        label = config.CATEGORIES.get(cat, {}).get("label", cat)
        print(f"{i:>3}. [{label}] {item['title']}")

    if no_save:
        print("\n[--no-save] 不写文件。以下是渲染结果：\n")
        print(md)
        return 0

    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"weekly_{datetime.now():%Y-%m-%d}.md"
    out_path.write_text(md, encoding="utf-8")
    print("\n" + "=" * 78)
    print(f"周报已保存：{out_path}")
    print(f"文件大小：{out_path.stat().st_size:,} 字节")
    if not no_notify:
        notify.send_weekly(True, data["start"], data["end"],
                           len(analysis["items"]), md)
    return 0


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
    parser.add_argument("--no-notify", action="store_true",
                        help="不推送微信，只写文件")
    parser.add_argument("--check-budget", action="store_true",
                        help="只看本周 token 额度，不抓取不调用模型")
    parser.add_argument("--if-missing", action="store_true",
                        help="今天的周报已经生成就直接退出，没有才补跑（定时补跑用）")
    parser.add_argument("--if-week-missing", action="store_true",
                        help="本周（周一起）已经有周报就直接退出（每日定时检查用，"
                             "防止 GitHub 丢掉某一次 cron 就整周没周报）")
    parser.add_argument("--save-raw", metavar="PATH",
                        help="把抓取+粗筛后的文章清单存成 JSON（调试挑选规则用）")
    parser.add_argument("--from-raw", metavar="PATH",
                        help="从 JSON 加载文章清单，跳过网络抓取（省时间，仍花 token）")
    parser.add_argument("--save-analysis", metavar="PATH",
                        help="把模型的判定结果存成 JSON（之后可零成本重新渲染）")
    parser.add_argument("--from-analysis", metavar="PATH",
                        help="用存档的模型判定重新渲染周报，不抓取不调模型（零 token）")
    parser.add_argument("--limit", type=int, default=0, help="终端每类最多显示几条（0=全部）")
    args = parser.parse_args()

    # --no-notify 是总闸：本地调试时不要惊动手机上的读者。
    # 只把它传给 send_weekly 是不够的 —— 额度告警、失败告警走的是 notify.notify()，
    # 2026-10-19 本地调试时就漏推了一条"本周 AI 额度已用到 80% 以上"。
    if args.no_notify:
        config.NOTIFY_ENABLED = False

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

    # 零成本重放：不抓取、不调模型，只重新渲染
    if args.from_analysis:
        return render_from_analysis(args.from_analysis,
                                    no_save=args.no_save,
                                    no_notify=args.no_notify)

    # 补跑闸门：定时的第二次触发（周一 13:20）用 --if-missing。
    # 12:05 那次跑成功了，这里直接退出，不重复花钱、不重复推送；
    # 没成功（红、降级、被额度拦）才会走到下面，自动补一期。
    if args.if_missing:
        path = today_report_path()
        if report_ready(path):
            print(f"✅ 今天的周报已经生成（{path}，{path.stat().st_size:,} 字节），"
                  f"本次不补跑。")
            return 0
        print(f"⚠️  今天还没有可用的周报（{path}），开始补跑。")

    # 每日检查闸门（--if-week-missing）：本周有周报就什么都不做。
    # 定时任务改成"每天都看一眼"，是因为 GitHub 的 cron 会丢：
    # 2026-10-19（周一）那次 12:05 和 13:20 两个 cron 都没触发，
    # 如果只押注周一那一次，整周就白等。现在周一没跑成，周二到周日都能补上，
    # 而已经生成过的那几天只读一个文件就退出，零 token。
    if args.if_week_missing:
        ready, path = week_report_ready()
        if ready and path is not None:
            print(f"✅ 本周的周报已经生成（{path.name}，{path.stat().st_size:,} 字节），"
                  f"本次跳过，不花钱。")
            return 0
        print(f"⚠️  本周（自 {week_anchor():%Y-%m-%d} 周一 起）还没有可用的周报，开始生成。")

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
    gh_enabled = bool(config.GITHUB_TRENDING.get("enabled")) and not args.from_raw
    if args.from_raw:
        all_articles = replay.load_articles(args.from_raw)
        outcomes: list[fetch.FetchOutcome] = []
        print(f"从存档加载文章：{len(all_articles)} 篇（{args.from_raw}，跳过网络抓取）")
    else:
        print("开始抓取：")
        all_articles, outcomes = fetch.fetch_all(sources, proxy, progress=print,
                                                 force_direct=is_ci)
        if gh_enabled:
            cfg_gh = config.GITHUB_TRENDING
            print(f"  查询最近 {cfg_gh['days']} 天新建、星标 > {cfg_gh['min_stars']} "
                  f"的仓库（GitHub API）...")
            gh = fetch.fetch_github_trending(
                proxy, force_direct=is_ci,
                days=int(cfg_gh["days"]), min_stars=int(cfg_gh["min_stars"]),
                limit=int(cfg_gh["limit"]),
            )
            if gh.error:
                print(f"  ✗ {gh.name:<18} {gh.error}")
            else:
                tag = "代理" if gh.via_proxy else "直连"
                print(f"  ✓ {gh.name:<18} {len(gh.articles):>3} 条  [{tag}]")
            outcomes.append(gh)
            all_articles.extend(gh.articles)

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

    # ---- 5.5 综合源归栏：cnBeta / ServeTheHome 按内容分派到 AI / 安全 / 开源 ----
    # 这一步必须在 analyze() 之前：analyze() 按 article.category 分批，
    # 标着 "general" 的文章不属于任何板块，进去也会被静默丢掉。
    routed: list[fetch.Article] = []
    route_stats: dict[str, int] = {}
    dropped_general = 0
    for a in filtered:
        if a.category not in config.GENERAL_SOURCES:
            routed.append(a)
            continue
        cat = screening.route_general(a.title, a.summary)
        if cat is None:
            dropped_general += 1
            continue
        a.category = cat
        route_stats[cat] = route_stats.get(cat, 0) + 1
        routed.append(a)
    filtered = routed

    # ---- 6. 排序：日期新的在前，无日期的垫底 ----
    filtered.sort(key=lambda a: (a.published is not None, a.published or datetime.min),
                  reverse=True)

    ok = sum(1 for o in outcomes if not o.error and not o.skipped)
    stats = {
        "sources_total": len(config.SOURCES) + (1 if gh_enabled else 0),
        "sources_ok": ok,
        "sources_skipped": skipped_sources + sum(1 for o in outcomes if o.skipped),
        "sources_failed": sum(1 for o in outcomes if o.error and not o.skipped),
        "fetched": len(all_articles),
        "in_window": len(in_window),
        "after_dedupe": len(deduped),
        "after_filter": len(filtered),
    }
    if args.from_raw:
        # 重放模式下没有 outcomes，那几个计数只会写出"成功 0"，误导读者
        stats["replay"] = True
        stats["sources_total"] = stats["sources_ok"] = 0
        stats["sources_skipped"] = stats["sources_failed"] = 0

    print("-" * 78)
    print(f"抓取完成：{stats['fetched']} 篇 → 窗口内 {stats['in_window']} 篇 "
          f"→ 去重后 {stats['after_dedupe']} 篇（去掉重复 {removed} 篇）")
    print(f"筛选结果：{fstats['input']} 篇 → 保留 {fstats['kept']} 篇"
          f"（黑名单丢 {fstats['blocked']}，无关丢 {fstats['keyword_miss']}）")
    if route_stats or dropped_general:
        parts = "、".join(f"{CATEGORY_LABEL.get(c, c)} {n}" for c, n in route_stats.items())
        print(f"综合源归栏：{sum(route_stats.values())} 篇（{parts}）"
              + (f"，另有 {dropped_general} 篇判不出领域已丢弃" if dropped_general else ""))
    if undated:
        print(f"注意：{undated} 篇没有发布时间，已一并保留（无法判断是否在本周）")
    if stats["sources_failed"]:
        print(f"有 {stats['sources_failed']} 个源抓取失败，明细见本次日志里的『抓取情况』"
              f"（周报正文已不再附来源表）")

    # ---- 存档：调挑选规则时用它重跑，不必重抓 RSS ----
    if args.save_raw:
        saved = replay.dump_articles(args.save_raw, filtered)
        print(f"文章清单已存档：{saved}（{len(filtered)} 篇）"
              f"　→ 之后可用 --from-raw {args.save_raw} 跳过抓取")

    # ---- 7. AI 精选（有 Key 且未禁用时）----
    use_ai = not args.no_ai and bool(config.get_api_key())
    if args.no_ai:
        print("\n[--no-ai] 跳过 AI 环节，只输出粗筛后的清单")
    elif not config.get_api_key():
        print("\n[跳过 AI] 没有找到 DEEPSEEK_API_KEY，只输出粗筛后的清单")

    ai_budget = None
    ai_failed = False          # AI 环节真的失败了（不是用户主动 --no-ai）
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
            ai_failed = True
        except analyzer.ApiError as e:
            print(f"\nAI 环节失败：{e}")
            notify.notify(
                "AI 环节失败，本次周报未生成",
                f"{e}\n\n已自动降级为粗筛清单输出。\n{ai_budget.summary()}",
                level="error",
            )
            use_ai = False
            ai_failed = True
        finally:
            ai_budget.save()

    too_few = False            # 收录条数低于阈值：这次不算成功
    if use_ai and args.save_analysis:
        saved = replay.dump_analysis(args.save_analysis, analysis, usage,
                                     start, end, window_desc, stats)
        print(f"模型判定已存档：{saved}（之后可用 --from-analysis 零成本重渲染）")
    if use_ai:
        n_items = len(analysis["items"])
        if too_few_items(n_items):
            # 不写 weekly_*.md、不更新 last_success，直接走降级。
            # 为什么不硬着头皮写一份 3 条新闻的周报：
            #   1. 13:20 的补跑闸门看到文件存在就不会重试（它只认"有没有文件"）
            #   2. 漏跑补偿看到"上次成功"就不会扩大窗口
            #   3. Actions 变绿，你不会知道这一周其实是坏的
            print(f"\n⚠️  只收录了 {n_items} 条（低于阈值 "
                  f"{config.MIN_ITEMS_FOR_SUCCESS} 条），不算成功运行。")
            print("    不写周报、不更新上次成功时间，周一 13:20 的兜底任务会再试一次。")
            notify.notify(
                f"本周只收录到 {n_items} 条新闻，不算成功",
                f"低于阈值 {config.MIN_ITEMS_FOR_SUCCESS} 条，这次的周报不作为本周成果保存，"
                f"周一 13:20 会自动补跑一次。\n\n"
                f"{ai_budget.summary() if ai_budget else ''}",
                level="warning",
            )
            use_ai = False
            too_few = True

    if use_ai:
        print("-" * 78)
        print(f"收录 {len(analysis['items'])} 条（{usage}）")
        for i, item in enumerate(analysis["items"], 1):
            cat = item["category"]
            tag = config.CATEGORIES.get(cat, {}).get("label", cat)
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
            # 推送（手机微信能直接读全文；没配 SendKey 时只落本地日志）
            if not args.no_notify:
                notify.send_weekly(True, start, end, len(analysis["items"]), md)

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

    # 退出码约定：0 = 正常；3 = 该出周报但没出（AI 环节失败、额度耗尽、收录太少，
    # 只剩粗筛清单）。为什么要区分：GitHub Actions 只看退出码。全都返回 0 的话，
    # 模型 JSON 解析失败、额度耗尽这些事故在页面上都是绿色对勾，
    # 你只会以为"这周没新闻"，而不是"程序坏了"。
    # --no-ai / 没配 Key 属于用户主动选择，仍然返回 0。
    if ai_failed or too_few:
        print("\n⚠️  本次没有生成周报，只产出了粗筛清单（退出码 3，让 Actions 变红）。")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
