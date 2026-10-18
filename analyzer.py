"""AI 层：用 DeepSeek 把几百篇压成十几条精选。

三步，分工明确：
    screen()      —— 按分类分批召回候选，用便宜的 deepseek-flash
    joint_rank()  —— 两类候选联合打分分档，用 deepseek-v4-pro
    write()       —— 写中文标题/摘要/点评，用 deepseek-v4-pro

关键设计：**模型只返回 JSON，Markdown 由本地代码渲染。**
    这样标题、链接、来源、日期全部来自原始数据，
    模型没有机会编造一个不存在的 URL —— 这是新闻类 agent 最容易出的事故。

成本防护：所有模型调用都经过 budget.Budget 的事前拦截与事后累计，
    预算耗尽会抛 BudgetExceeded，由 main.py 捕获后降级输出并通知。
"""

import json
import time
import urllib.error
import urllib.request

import budget as budget_mod
import config
from fetch import Article


class ApiError(RuntimeError):
    """API 调用失败。单独定义，方便上层区分"网络问题"和"逻辑问题"。"""


# ---------------------------------------------------------------------------
# 底层：HTTP 调用
# ---------------------------------------------------------------------------
def _post(payload: dict, timeout: int = 180) -> dict:
    """向 DeepSeek 发一次请求。用 urllib 而不是 openai 库，保持零依赖。"""
    key = config.get_api_key()
    if not key:
        raise ApiError("没有找到 DEEPSEEK_API_KEY，请在 .env 或环境变量里设置")

    req = urllib.request.Request(
        f"{config.API_BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # DeepSeek 国内可直连，绕开系统代理更快也更稳
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    for attempt in range(3):
        try:
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            # 429 限流 / 5xx 服务端问题 → 退避重试
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise ApiError(f"HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise ApiError(f"网络错误: {e.reason}") from e

    raise ApiError("重试 3 次仍失败")


class Usage:
    """累计单次运行的 token 用量，用来告诉你这次花了多少。

    和 budget.Budget 的区别：
        Usage   —— 这一次运行花了多少（临时，跑完就算）
        Budget  —— 这个周期累计花了多少（持久化，跨运行累加，负责拦截）
    """

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.calls = 0
        self.last_note = ""

    def add(self, usage: dict | None) -> None:
        if not usage:
            return
        self.prompt += usage.get("prompt_tokens", 0)
        self.completion += usage.get("completion_tokens", 0)
        self.calls += 1

    def __str__(self) -> str:
        return (f"{self.calls} 次调用，输入 {self.prompt:,} tokens，"
                f"输出 {self.completion:,} tokens")


def _chat_json(system: str, user: str, model: str, usage: Usage,
               max_tokens: int = 4000, budget=None) -> dict:
    """要模型返回 JSON 并解析。

    ⚠️ 踩过的坑：deepseek-flash / deepseek-v4-pro 都是**推理模型**，
       返回里除了 content 还有 reasoning_content，而推理过程同样消耗 max_tokens。
       预算给小了，推理就把额度吃光，content 直接是空字符串。
       所以这里对空内容自动重试，并把预算放大 —— 否则会得到一个莫名其妙的
       "返回的不是合法 JSON："（冒号后面是空的）。
    """
    content = ""
    for attempt in range(3):
        # ---- 事前拦截：付不起就别发 ----
        if budget is not None:
            if usage.calls >= config.MAX_CALLS_PER_RUN:
                raise budget_mod.BudgetExceeded(
                    f"单次运行调用次数已达上限 {config.MAX_CALLS_PER_RUN}，"
                    f"停止继续调用（防止异常重试失控）"
                )
            budget.check_before_call(
                budget_mod.estimate_tokens(system + user) + max_tokens)

        res = _post({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0.3,
        })
        usage.add(res.get("usage"))

        # ---- 事后累计 + 越线检查 ----
        if budget is not None:
            budget.record(res.get("usage"))
            budget.save()                       # 立刻落盘，程序崩了也不丢账
            budget.check_after_call()

        choice = res["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        if content:
            break

        # 空内容 → 推理吃光了预算，放大后重试
        finish = choice.get("finish_reason")
        usage.last_note = f"空返回(finish={finish}, max_tokens={max_tokens})，放大重试"
        max_tokens = min(max_tokens * 3, 32000)

    if not content:
        raise ApiError(
            f"模型连续返回空内容（model={model}，最后 max_tokens={max_tokens}）。"
            f"多半是推理过程占满了 token 预算。"
        )

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # 模型偶尔会在 JSON 前后加解释文字，尝试截取最外层大括号
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise ApiError(f"模型返回的不是合法 JSON：{content[:400]}") from e


# ---------------------------------------------------------------------------
# 第一步：筛选
# ---------------------------------------------------------------------------
SCREEN_SYSTEM = """你是一名资深的网络安全与人工智能新闻编辑，为一份周报挑选选题。

评判标准，按重要性从高到低：
1. 有实质影响的安全事件：在野利用的漏洞、大规模数据泄露、供应链攻击、勒索事件、APT 活动
2. 重要的技术进展：新模型发布、关键能力突破、重要开源项目、行业格局变化
3. 有信息量的分析与政策：监管动向、重大并购、行业数据
4. 可直接排除：产品促销、消费电子评测、企业宣传稿、活动预告、与安全或 AI 无关的内容

务必严格：宁可少选，不要凑数。同一事件被多家报道时，只保留信息最全的那一条。

只输出 JSON，格式：
{"selected": [{"id": 编号, "score": 1到10的整数, "reason": "不超过30字的选择理由"}]}
"""


def _render_batch(articles: list[Article], offset: int) -> str:
    """把一批文章渲染成给模型看的编号清单。"""
    lines: list[str] = []
    for i, a in enumerate(articles):
        idx = offset + i + 1
        date_s = a.published.strftime("%m-%d") if a.published else "??"
        summary = (a.summary or "（无摘要）")[:160]
        lines.append(f"[{idx}] ({a.category}/{date_s}/{a.source}) {a.title}\n    {summary}")
    return "\n".join(lines)


def screen(articles: list[Article], usage: Usage, progress=print,
           budget=None) -> list[tuple[Article, dict]]:
    """按分类分批召回候选，返回 [(文章, {score, reason}), ...]。

    这一步只求"别漏"，不负责最终排名 ——
    排名交给后面的 joint_rank()，那里两类的候选才放在一起比较。

    为什么要分批？
        200 篇一次性送进去，输入约 3-4 万 token。虽然塞得进上下文，
        但模型在长上下文里对中间部分的注意力会下降，容易漏掉重要条目。
        分批后每批只有 50 多篇，模型能认真看每一条。
    """
    if not articles:
        return []

    batch_size = config.SCREEN_BATCH_SIZE
    per_batch = config.CANDIDATES_PER_BATCH
    batches = [articles[i:i + batch_size] for i in range(0, len(articles), batch_size)]
    progress(f"分 {len(batches)} 批召回（每批最多 {batch_size} 篇，各留最多 {per_batch} 条）")

    picked: dict[int, dict] = {}          # 全局编号 → {score, reason}
    for bi, batch in enumerate(batches, 1):
        offset = (bi - 1) * batch_size
        user = (
            f"以下是本周抓到的第 {bi}/{len(batches)} 批新闻，共 {len(batch)} 条。\n"
            f"请挑出其中值得进入候选池的（每批最多 {per_batch} 条，"
            f"没有值得选的就返回空数组）。\n"
            f"这一步只求别漏掉有价值的条目，不需要排出最终顺序。\n\n"
            + _render_batch(batch, offset)
            + '\n\n只返回 JSON：{"selected":[{"id":1,"score":9,"reason":"..."}]}'
        )
        try:
            data = _chat_json(SCREEN_SYSTEM, user, config.MODEL_SCREEN, usage,
                              max_tokens=4000, budget=budget)
        except ApiError as e:
            progress(f"  ! 第 {bi} 批筛选失败：{e}")
            continue

        got = data.get("selected") or []
        progress(f"  第 {bi}/{len(batches)} 批：{len(batch)} 篇 → 选中 {len(got)} 条")
        for item in got:
            try:
                idx = int(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if 1 <= idx <= len(articles):
                picked[idx] = {
                    "score": int(item.get("score", 5)),
                    "reason": str(item.get("reason", ""))[:60],
                }

    results = [(articles[i - 1], meta) for i, meta in picked.items()]
    results.sort(key=lambda x: -x[1]["score"])
    return results


RERANK_SYSTEM = """你是一名资深的安全与 AI 新闻主编，负责决定本周周报收录哪些条目。

这是一个**跨分类的联合评审**：网络安全和人工智能的候选放在一起比较。
请把它们放在同一把尺子上打分，不要偏向任何一类。

评分锚点（严格按此校准，不要凭感觉给分）：
  9-10 重大：大规模在野利用的 0day、国家级 APT 曝光、影响关键基础设施的勒索、
             头部厂商的重大模型发布、明确改变行业格局的事件
  7-8  重要：单个高危漏洞在野利用、官网补丁已发布、重要模型或产品发布、
             有具体数据和结论的行业分析、重大监管或诉讼
  5-6  一般：常规漏洞披露、漏洞技术分析、一般性行业动态、企业合作
  1-4  可省：产品更新与发版、厂商宣传稿、观点评论、纯活动报道

重要性分档：
  must   —— 本周必看，漏掉就是失职
  good   —— 值得看，读者会有收获
  filler —— 可收录但价值有限，只在篇幅允许时放

规则：
- 同一事件只保留信息最全的一条
- "上周就报过、这周没进展"的条目降档
- 宁可少给 must，不要为了凑数把 good 抬成 must

只输出 JSON：
{"items":[{"id":编号,"score":1到10的整数,"tier":"must或good或filler","reason":"不超过30字"}]}
"""


def joint_rank(candidates: list[tuple[Article, dict]], usage: Usage,
               progress=print, budget=None) -> list[tuple[Article, dict]]:
    """把所有分类的候选放在一起做一次联合排序。

    为什么必须联合排？
        分批筛选时模型只看得到自己那一批，给出的分数是"批内相对分"，
        跨分类根本不可比。只有让它在同一个视野里横向比较，
        分数才有意义，才能按重要性分配篇幅。

    这一步只负责打分和分档，**收录多少条由 allocate() 按规则决定**，
    模型不直接决定篇幅。
    """
    if not candidates:
        return []

    progress(f"联合排序：{len(candidates)} 条候选（两个分类放在一起比较）")
    lines = []
    for i, (a, meta) in enumerate(candidates, 1):
        date_s = a.published.strftime("%m-%d") if a.published else "??"
        cat_name = "安全" if a.category == "security" else "AI"
        lines.append(
            f"[{i}] ({cat_name}/{date_s}/{a.source}) {a.title}"
            f"\n    摘要：{(a.summary or '无')[:180]}"
            f"\n    批内初选理由：{meta['reason']}"
        )

    user = (
        f"以下是本周初筛留下的 {len(candidates)} 条候选，"
        f"其中安全类 {sum(1 for a, _ in candidates if a.category == 'security')} 条、"
        f"AI 类 {sum(1 for a, _ in candidates if a.category == 'ai')} 条。\n"
        f"请按上面的锚点统一打分并分档。\n\n"
        + "\n".join(lines)
        + '\n\n只返回 JSON：{"items":[{"id":1,"score":9,"tier":"must","reason":"..."}]}'
    )

    data = _chat_json(RERANK_SYSTEM, user, config.MODEL_RERANK, usage,
                      max_tokens=12000, budget=budget)

    ranked: list[tuple[Article, dict]] = []
    for item in data.get("items") or []:
        try:
            idx = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 1 <= idx <= len(candidates):
            continue
        article, meta = candidates[idx - 1]
        tier = str(item.get("tier", "")).strip().lower()
        if tier not in ("must", "good", "filler"):
            tier = "good"
        ranked.append((article, {
            "score": max(1, min(10, int(item.get("score", meta["score"])))),
            "tier": tier,
            "reason": str(item.get("reason") or meta["reason"])[:60],
        }))

    ranked.sort(key=lambda x: (-x[1]["score"],
                               {"must": 0, "good": 1, "filler": 2}[x[1]["tier"]]))

    # 模型漏掉的候选按原分数补在后面，避免它们永远没机会
    seen = {id(a) for a, _ in ranked}
    for article, meta in candidates:
        if id(article) not in seen:
            ranked.append((article, {**meta, "tier": "filler"}))

    must = sum(1 for _, m in ranked if m["tier"] == "must")
    good = sum(1 for _, m in ranked if m["tier"] == "good")
    progress(f"  分档结果：must {must} 条，good {good} 条，"
             f"filler {len(ranked) - must - good} 条")
    return ranked


def allocate(ranked: list[tuple[Article, dict]], progress=print
             ) -> list[tuple[Article, dict]]:
    """按重要性分配篇幅。这是替代"固定 7/7 名额"的核心算法。

    规则（顺序很重要）：
        1. 先收所有 must —— 但每个分类最多 MAX_PER_CATEGORY 条，
           总数最多 MAX_TOTAL 条
        2. 保证每个分类至少 MIN_PER_CATEGORY 条 —— 防止某个方向整周消失
        3. 按分数从高到低继续收，直到分数低于 MIN_SCORE 或达到 MAX_TOTAL
        4. 如果还没到 MIN_TOTAL，继续收（不看 MIN_SCORE 了），补到 MIN_TOTAL

    结果就是：重要新闻多的周报会变长（最多 MAX_TOTAL），
    平淡的一周就短（最少 MIN_TOTAL），而不是永远 14 条。
    """
    if not ranked:
        return []

    order = {"must": 0, "good": 1, "filler": 2}
    cfg = config
    chosen: list[tuple[Article, dict]] = []
    counts = {"security": 0, "ai": 0}

    def try_add(item: tuple[Article, dict], ignore_score: bool = False) -> bool:
        article, meta = item
        if item in chosen:
            return False
        if len(chosen) >= cfg.MAX_TOTAL:
            return False
        if counts[article.category] >= cfg.MAX_PER_CATEGORY:
            return False
        if not ignore_score and meta["score"] < cfg.MIN_SCORE:
            return False
        chosen.append(item)
        counts[article.category] += 1
        return True

    # 1. 收 must
    for item in ranked:
        if item[1]["tier"] == "must":
            try_add(item, ignore_score=True)

    # 2. 每类保底：不够就从同类里分最高的补
    for cat in ("security", "ai"):
        while counts[cat] < cfg.MIN_PER_CATEGORY:
            pool = [x for x in ranked
                    if x[0].category == cat and x not in chosen]
            if not pool:
                break                      # 这一类候选本来就不够，补不了
            if not try_add(pool[0], ignore_score=True):
                break

    # 3. 按分数续收，直到过不了门槛
    for item in ranked:
        if len(chosen) >= cfg.MAX_TOTAL:
            break
        if item[1]["score"] >= cfg.MIN_SCORE:
            try_add(item)

    # 4. 还不够 MIN_TOTAL，就放宽门槛补齐
    if len(chosen) < cfg.MIN_TOTAL:
        for item in ranked:
            if len(chosen) >= cfg.MIN_TOTAL:
                break
            try_add(item, ignore_score=True)

    chosen.sort(key=lambda x: (-x[1]["score"], order[x[1]["tier"]]))
    progress(f"  篇幅分配：收录 {len(chosen)} 条"
             f"（安全 {counts['security']} / AI {counts['ai']}，"
             f"区间 {cfg.MIN_TOTAL}-{cfg.MAX_TOTAL}）")
    return chosen


# ---------------------------------------------------------------------------
# 第二步：撰写
# ---------------------------------------------------------------------------
WRITE_SYSTEM = """你是一名资深的安全与 AI 领域分析师，为一位关注这两个方向的技术从业者撰写周报。

写作要求：
- 中文，专业但不堆术语，像懂行的人给同事做口头简报
- 每条摘要 60-100 字，说清"发生了什么"，不要复述标题
- "为什么重要" 40-80 字，讲清影响或趋势，不要空洞评价如"值得关注""影响深远"
- 只依据我提供的标题和摘要。**不得引入任何外部信息，不得编造数字、公司名、时间。**
  信息不足时就写得保守，宁可说"细节尚未披露"
- 英文标题请翻译成准确的中文标题，保留专有名词原文（如 OpenAI、Chrome）

分类要求（重要）：
- 按**内容**判断分类，不要照抄我给的来源分类。
  比如"某产品漏洞被在野利用"即使来自 AI 媒体，也属于 security。
- security：漏洞、攻击、数据泄露、勒索、恶意软件、间谍活动、安全防护
- ai：模型发布/能力、AI 产品与产业、AI 治理、AI 引发的社会与安全争议

只输出 JSON，格式：
{"overview":"2-3 句话概括本周整体趋势",
 "items":[{"id":编号,"category":"security或ai","title":"中文标题","summary":"摘要","why":"为什么重要"}]}
"""


def write(selected: list[tuple[Article, dict]], usage: Usage, progress=print,
          budget=None) -> dict:
    """为选中的文章生成中文标题、摘要和"为什么重要"。"""
    lines = []
    for i, (a, meta) in enumerate(selected, 1):
        date_s = a.published.strftime("%Y-%m-%d") if a.published else "日期未知"
        lines.append(
            f"[{i}] 分类：{a.category} | 来源：{a.source} | 日期：{date_s}\n"
            f"    原标题：{a.title}\n"
            f"    原始摘要：{a.summary or '（该源未提供摘要）'}\n"
            f"    初选理由：{meta['reason']}"
        )

    user = (
        f"本周共选出 {len(selected)} 条新闻，请为每条撰写中文标题、摘要和重要性说明，"
        f"并按内容判断每条的分类，另写一段本周整体观察。\n"
        f"（注意：下面标注的分类是「来源分类」，仅供参考，"
        f"你要按内容重新判断——比如某个 AI 媒体报的漏洞利用，应该归 security。）\n\n"
        + "\n\n".join(lines)
        + '\n\n只返回 JSON：{"overview":"...","items":[{"id":1,"category":"security",'
          '"title":"...","summary":"...","why":"..."}]}'
    )

    progress(f"正在撰写 {len(selected)} 条摘要 ...")
    data = _chat_json(WRITE_SYSTEM, user, config.MODEL_WRITE, usage,
                      max_tokens=16000, budget=budget)

    if not data.get("items"):
        raise ApiError(f"模型没有返回 items 字段：{str(data)[:300]}")
    return data


# ---------------------------------------------------------------------------
# 对外：一次跑完
# ---------------------------------------------------------------------------
def analyze(articles: list[Article], progress=print, budget=None) -> tuple[dict, Usage]:
    """筛选 → 联合排序 → 分配篇幅 → 撰写，返回 (模型输出, token 用量)。

    三步的分工，别混：
        1. screen()      按分类分批召回候选 —— 只求"别漏"，不排名
        2. joint_rank()  两类候选放一起统一打分分档 —— 只排名，不决定篇幅
        3. allocate()    按保底/上限/分数门槛决定收几条 —— 代码算，模型不参与

    这样篇幅跟着内容走：重要新闻多的周会变长，平淡的周会变短。
    """
    usage = Usage()

    groups: dict[str, list[Article]] = {"security": [], "ai": []}
    for a in articles:
        groups.setdefault(a.category, []).append(a)

    # ---- 1. 按分类召回候选 ----
    candidates: list[tuple[Article, dict]] = []
    for cat in ("security", "ai"):
        pool = groups.get(cat, [])
        if not pool:
            continue
        label = "网络安全" if cat == "security" else "人工智能"
        progress(f"召回【{label}】：{len(pool)} 篇")
        got = screen(pool, usage, progress, budget=budget)
        if not got:
            progress(f"  ! {label} 没有选出任何条目")
        candidates.extend(got)

    if not candidates:
        raise ApiError("筛选阶段没有选中任何文章，检查筛选提示词或数据")
    progress(f"候选池共 {len(candidates)} 条")

    # ---- 2. 跨分类联合排序 ----
    ranked = joint_rank(candidates, usage, progress, budget=budget)

    # ---- 3. 按重要性分配篇幅 ----
    selected = allocate(ranked, progress)
    if not selected:
        raise ApiError("篇幅分配后没有剩下任何条目，检查 config 里的收录阈值")

    progress(f"最终收录 {len(selected)} 条，开始撰写")
    result = write(selected, usage, progress, budget=budget)

    # 把本地元数据（链接、来源、日期）补回去 —— 这些绝不由模型生成
    by_id = {i: selected[i - 1] for i in range(1, len(selected) + 1)}
    enriched: list[dict] = []
    for item in result["items"]:
        try:
            idx = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if idx not in by_id:
            continue
        article, meta = by_id[idx]
        # 分类以模型按内容判断的为准；模型没给或给了非法值就退回源的分类
        model_cat = str(item.get("category", "")).strip().lower()
        category = model_cat if model_cat in ("security", "ai") else article.category
        enriched.append({
            "title": str(item.get("title") or article.title),
            "summary": str(item.get("summary") or article.summary),
            "why": str(item.get("why", "")),
            "url": article.url,                      # 本地数据，不让模型碰
            "source": article.source,
            "category": category,
            "lang": article.lang,
            "published": article.published,
            "score": meta["score"],
        })

    if not enriched:
        raise ApiError("撰写阶段返回的 id 与候选文章对不上，检查提示词")

    return {"overview": str(result.get("overview", "")), "items": enriched}, usage
