"""配置：源列表、输出路径、筛选参数。

改这个文件就能增删新闻源，不用动其它代码。

关于 requires_proxy：
    这是"国内为主 + 英文可选"的核心开关。
    - requires_proxy=False：国内可直连的源，代理开不开都能抓，且永远直连（更快）
    - requires_proxy=True ：需要翻墙的源，只有代理可用时才加载

    所以你的两种状态会自动对应两种模式：
        代理关闭 → 只加载 requires_proxy=False 的源（纯国内）
        代理开启 → 全部加载（国内直连 + 英文走代理）
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 密钥加载
# ---------------------------------------------------------------------------
# 原则：Key 不放代码里。优先读环境变量，其次读同目录的 .env 文件。
# .env 已经在 .gitignore 里，不会被提交。
def load_env(path: str = ".env") -> None:
    """把 .env 里的键值对读进环境变量。已存在的环境变量不覆盖。"""
    env_file = Path(__file__).parent / path
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env()


def get_api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


# ---------------------------------------------------------------------------
# AI 设置
# ---------------------------------------------------------------------------
# 实测该账号可用模型：deepseek-flash（快、便宜）、deepseek-v4-pro（强、贵）
# 两者都是推理模型：返回里带 reasoning_content，推理过程同样消耗 max_tokens。
#   筛选：从几百篇里挑出重要的 → 量大、单篇信息少，用便宜的 flash
#   排序：跨批次横向比较     → 调用少但要求判断力，用 pro
#   撰写：生成最终周报正文   → 量小但要求高，用 pro
API_BASE = "https://api.deepseek.com"
MODEL_SCREEN = "deepseek-flash"
MODEL_RERANK = "deepseek-v4-pro"
MODEL_WRITE = "deepseek-v4-pro"

# 每批送进模型的最大文章数。太大容易超上下文，太小会漏掉全局视野。
SCREEN_BATCH_SIZE = 55

# ---------------------------------------------------------------------------
# 周报篇幅与收录规则
# ---------------------------------------------------------------------------
# 三个板块，各有自己的下限和上限。为什么不用一个统一数字：
#   - 安全类天然高产（一周几百条漏洞通报），不压就会淹掉别的板块
#   - 开源一周真能出圈的只有几条，下限设太高会逼模型拿垃圾凑数
#   - AI 是读者最想看的，给最大的额度
#
#   min  每个板块至少几条 —— 防止某个方向整周消失（候选不够时会自动放弃）
#   max  每个板块最多几条 —— 防止高产板块淹没一切
#   order 渲染顺序（同时决定"总额度用完时谁先被砍"的优先级：排前面的先收）
#
# MIN_TOTAL/MAX_TOTAL 是总条数区间 —— 重要新闻多的周就长，平淡的周就短。
# MIN_SCORE 是过线分数 —— 低于这个分的条目只在补足篇幅时才收。
#
# 为什么不直接用"按分数排序取前 N"：
#   模型给的分数是自我评价，跨分类不可比，而且它天然偏爱"漏洞在野利用"
#   这类戏剧性事件。所以要一次跨分类联合排序（让分数可比）+ 保底和上限
#   （防止偏科）+ 分数门槛（决定篇幅），三者缺一不可。
#
# 2026-10 去掉了「科技硬件」板块：它的成本最高（硬件类源占粗筛篇数三分之一）
# 产出最低（一周往往只有 1-2 条，还经常和 cnBeta 的中文快讯重复）。
# 硬件新闻里真正有信息量的部分是算力与芯片，现在归进 AI 栏，见 screening.route_general()。
CATEGORIES = {
    "ai":         {"label": "🤖 人工智能",  "emoji": "🤖", "min": 3, "max": 6, "order": 1},
    "security":   {"label": "🔒 网络安全",  "emoji": "🔒", "min": 2, "max": 5, "order": 2},
    "opensource": {"label": "🧩 开源项目",  "emoji": "🧩", "min": 2, "max": 3, "order": 3},
}

# 综合源：这些源什么领域都发（cnBeta 一天几十条，从显卡到勒索软件），
# 不适合提前钉死在某个板块。它们的 category 写 "general"，
# 粗筛后由 screening.route_general() 按内容分派到上面三个板块之一。
GENERAL_SOURCES = {"general"}

# 总条数：宁可少而精。16 条里有 4 条读者划过去就忘，不如 10 条条条有信息。
# 开源栏固定最多 3 个（读者一周能看进去的新项目就那么几个）。
MIN_TOTAL = 8
MAX_TOTAL = 12
MIN_SCORE = 6

# 同一个源最多占几条。
# 为什么需要这个：The Hacker News 一周能发几十条，光按分数排它能占掉
# 一半篇幅 —— 读者会以为"全世界的安全新闻都长这样"。设 3 条，
# 逼着分配器去找第二、第三个源的同等级新闻，视野才真的打开。
# 只有一条例外：GitHub 开源榜本身就是一个"多项目合集"，不按同源限制。
MAX_PER_SOURCE = 3

# 「同源上限」的例外：GitHub 周榜里每条是一个独立项目，本质上是个合集，
# 不该被当成"一家媒体发了 5 条"来看待。
MAX_PER_SOURCE_EXEMPT = {"GitHub 周榜"}

# 每批筛查最多留下几条候选（这些是后面联合排序的输入池）
CANDIDATES_PER_BATCH = 6

# ---------------------------------------------------------------------------
# token 预算（防成本失控）
# ---------------------------------------------------------------------------
# 两道闸门，分开管，别混：
#
#   MAX_RUN_TOKENS     单次运行上限 —— 防"这一次"失控。
#                      一次正常周报约 5 万，设 15 万 = 3 倍。
#                      为什么是 3 倍？因为重试会把 max_tokens 放大 3 倍，
#                      所以一次异常运行的合理上限就是正常成本的 3 倍左右。
#
#   WEEKLY_TOKEN_BUDGET 每周总额度 —— 防"这一周"被反复重试掏空。
#                      设 30 万 = 6 倍，够跑 1 次正常 + 2 次重试。
#                      如果只给 3 倍，一次部分失败（真实消耗 1.5-2 倍）
#                      就会把额度耗掉大半，你连重试的机会都没有。
#
# 想更严格的话，把 WEEKLY_TOKEN_BUDGET 改成 150000 即可。
MAX_RUN_TOKENS = 150_000
WEEKLY_TOKEN_BUDGET = 300_000

# 预算周期长度（天）。7 表示每周重置一次额度。
BUDGET_PERIOD_DAYS = 7

# 单次运行用到多少比例时发告警
BUDGET_WARN_RATIO = 0.80

# 单次运行最多允许多少次模型调用（第三道闸，防逻辑闭环）
MAX_CALLS_PER_RUN = 40

# ---------------------------------------------------------------------------
# 漏跑补偿
# ---------------------------------------------------------------------------
# 记录上次成功运行的日期。如果隔了超过一个周期才跑（电脑关机、任务没触发），
# 自动把时间窗口扩展到覆盖这段间隔，避免漏掉新闻。
# 上限防止第一次运行时把几年历史全抓进来。
CATCHUP_MAX_DAYS = 30

# ---------------------------------------------------------------------------
# 通知（微信推送，走 Server 酱）
# ---------------------------------------------------------------------------
# 开通步骤见 README 的「手机和电脑上怎么看」：
#   1. 浏览器打开 https://sct.ftqq.com/ ，微信扫码登录
#   2. 复制你的 SendKey（形如 SCT123456Txxxxxxxx）
#   3. 本机：写进 .env         WECHAT_SENDKEY=SCT...
#      云端：写进 GitHub → Settings → Secrets and variables → Actions
#            名字 WECHAT_SENDKEY，值同上（workflow 里已经传进来了）
#
# 没配 SendKey 时不报错：通知只落 state/notifications.log，程序照常跑完。
NOTIFY_ENABLED = True


def get_sendkey() -> str:
    return os.environ.get("WECHAT_SENDKEY", "").strip()


# Server 酱单条正文上限 32KB —— 注意单位是**字节**，而且算的是
# form-urlencoded 之后的请求体：一个汉字 URL 编码后占 9 字节。
#
# 所以这里必须按"编码后的字节数"限流（见 notify._clip_bytes），不能按字符数：
# 30,000 字符的中文 ≈ 270KB 请求体，服务器会直接拒绝，而截断逻辑永远不会触发。
# 实测一份 6,660 字的周报 form 编码后是 30,888 字节，已经贴着 32,768 的上限 ——
# 按字符限流等于没有限流，下一次周报变长就会静默发不出去。
# 28,000 ≈ 上限的 85%，够放约 6,000 字（正常周报 5,000~7,000 字）。
# 32000 是硬上限（32,768），留 4% 给 title 和键名；实测现役周报 30,888 字节，
# 正好卡在这里 —— 所以这条线必须贴着上限，同时保证"超了就优雅截断 + 给链接"。
NOTIFY_MAX_BYTES = 31_500

# 一次运行收录少于这么多条，就不算"成功跑完"。
# 正常一周是 10-18 条；掉到 5 条以下通常意味着**某个环节坏了**——源大面积失败、
# 模型只返回了几条、分类被整批归错——而不是"这周真的没新闻"。
# 这种情况必须：① 不算成功（Actions 变红、微信告警）② 不更新 last_success
# ③ 不写 weekly_*.md（否则 13:20 的补跑闸门看到文件就以为搞定了）
MIN_ITEMS_FOR_SUCCESS = 5

# ---------------------------------------------------------------------------
# 新闻源
# ---------------------------------------------------------------------------
# name           : 显示用名字
# url            : RSS 地址
# category       : "security" / "ai" / "hardware" / "opensource"
#                  —— 只影响分批召回的桶，最终板块由模型按内容判断
# lang           : "zh" / "en"
# requires_proxy : 是否必须有代理才能访问
#
# 标注【实测】的为 2026-10-17 本机实测可用。
# ---------------------------------------------------------------------------
SOURCES = [
    # ================= 网络安全 · 国内可直连 =================
    # 说明：嘶吼（最新文章停在 2026-05）和安全脉搏（停在 2025-07）实测已停更，已剔除。
    # 说明：奇安信攻防社区（forum.butian.net）在本机能抓，但 GitHub 的 Azure 出口 IP
    #       被它的 WAF 拦，稳定返回 HTTP 403。为了让每周「数据来源」表不再挂一条失败，
    #       2026-10-18 移除。想看它的话本地手动加回来即可。
    {"name": "先知社区",          "url": "https://xz.aliyun.com/feed",                      "category": "security", "lang": "zh", "requires_proxy": False},
    {"name": "绿盟博客",          "url": "https://blog.nsfocus.net/feed/",                  "category": "security", "lang": "zh", "requires_proxy": False},
    {"name": "Seebug Paper",      "url": "https://paper.seebug.org/rss/",                   "category": "security", "lang": "zh", "requires_proxy": False},
    {"name": "安全客",            "url": "https://api.anquanke.com/data/v1/rss",            "category": "security", "lang": "zh", "requires_proxy": False},

    # ================= 网络安全 · 需要代理 =================
    {"name": "Krebs on Security", "url": "https://krebsonsecurity.com/feed/",               "category": "security", "lang": "en", "requires_proxy": True},
    {"name": "Dark Reading",      "url": "https://www.darkreading.com/rss.xml",             "category": "security", "lang": "en", "requires_proxy": True},
    {"name": "The Hacker News",   "url": "https://feeds.feedburner.com/TheHackersNews",     "category": "security", "lang": "en", "requires_proxy": True},
    {"name": "Schneier",          "url": "https://www.schneier.com/feed/atom/",             "category": "security", "lang": "en", "requires_proxy": True},

    # ================= 人工智能 · 国内可直连 =================
    {"name": "量子位",            "url": "https://www.qbitai.com/feed",                     "category": "ai",       "lang": "zh", "requires_proxy": False},
    {"name": "InfoQ 中文",        "url": "https://www.infoq.cn/feed",                       "category": "ai",       "lang": "zh", "requires_proxy": False},
    {"name": "钛媒体",            "url": "https://www.tmtpost.com/rss.xml",                 "category": "ai",       "lang": "zh", "requires_proxy": False},
    {"name": "极客公园",          "url": "https://www.geekpark.net/rss",                    "category": "ai",       "lang": "zh", "requires_proxy": False},
    {"name": "开源中国",          "url": "https://www.oschina.net/news/rss",                "category": "ai",       "lang": "zh", "requires_proxy": False},
    {"name": "Solidot",           "url": "https://www.solidot.org/index.rss",               "category": "ai",       "lang": "zh", "requires_proxy": False},
    {"name": "雷峰网",            "url": "https://www.leiphone.com/feed",                   "category": "ai",       "lang": "zh", "requires_proxy": False},

    # ================= 人工智能 · 需要代理 =================
    {"name": "MIT Tech Review",   "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "category": "ai", "lang": "en", "requires_proxy": True},
    {"name": "Ars Technica",      "url": "https://feeds.arstechnica.com/arstechnica/index", "category": "ai",       "lang": "en", "requires_proxy": True},
    {"name": "Wired",             "url": "https://www.wired.com/feed/rss",                  "category": "ai",       "lang": "en", "requires_proxy": True},
    {"name": "TechCrunch",        "url": "https://techcrunch.com/feed/",                    "category": "ai",       "lang": "en", "requires_proxy": True},

    # ================= 综合 · 国内可直连 =================
    # cnBeta 一天几十条，从显卡到勒索软件什么都发，没法提前钉死在某个板块。
    # 所以它的 category 是 "general"：粗筛后按内容分派到 AI / 安全 / 开源之一
    # （见 screening.route_general()）。篇数是全站最多的，但产出也最多，
    # 2026-10 实测 75 篇 → 12 条候选 → 3 条入选，留着划算。
    {"name": "cnBeta",            "url": "https://www.cnbeta.com.tw/backend.php",           "category": "general",  "lang": "zh", "requires_proxy": False},

    # ServeTheHome 专写服务器与加速卡，篇数不多（一周 5 条左右）但硬核，
    # 2026-10 实测 5 篇 → 1 条候选 → 1 条入选（NVIDIA RTX Spark），是全站性价比最高的源。
    # 它同样标 general：芯片/算力类会被 route_general() 归进 AI 栏。
    {"name": "ServeTheHome",      "url": "https://www.servethehome.com/feed/",               "category": "general",  "lang": "en", "requires_proxy": True},

    # ================= 开源项目 · 需要代理 =================
    # GitHub 官方博客：发布产品变更、平台策略，属于"开源生态"的权威信息源。
    # 真正的「本周爆火项目」不走 RSS，走 GitHub 搜索 API（见下面的 GITHUB_TRENDING）。
    {"name": "GitHub Blog",       "url": "https://github.blog/feed/",                       "category": "opensource", "lang": "en", "requires_proxy": True},
]

# ---------------------------------------------------------------------------
# 本周爆火的开源项目（GitHub 搜索 API，不是 RSS）
# ---------------------------------------------------------------------------
# 思路：查"最近 N 天新建、星标超过 min_stars 的仓库"，按星标倒序取前 limit 个。
# 为什么不用 GitHub Trending 页面：
#   1. 那是 HTML，改版就会挂（实测正则已经解析不到仓库名了）
#   2. 页面不提供结构化数据（语言、描述、star 数都要另外抠）
# 搜索 API 返回 JSON，字段干净，就是给程序看的。
#
# 在 GitHub Actions 里会自动带上 GITHUB_TOKEN（workflow 里已传），
# 限额 5000 次/小时；本机没有 token 也能跑（限额 10 次/分钟，一次运行只查 1 次）。
GITHUB_TRENDING = {
    "enabled": True,
    "days": 7,              # 只看最近这么多天新建的仓库
    "min_stars": 100,       # 低于这个星标的不算"爆火"
    "limit": 25,            # 最多取几个进候选池
}


# ---------------------------------------------------------------------------
# 注：关键词表在 screening.py 里（黑名单 + 领域关键词一起维护，避免两处不同步）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
# 输出目录。
# ⚠️ 必须用相对路径（相对本文件所在目录），不能用 D:\... 这种绝对路径 ——
#    GitHub Actions 跑在 Linux 上，Windows 绝对路径会直接报错。
# 想让报告进坚果云/OneDrive 同步目录，改成：
#     OUTPUT_DIR = r"D:\你的同步盘\安全AI周报"
OUTPUT_DIR = str(Path(__file__).parent / "reports")

# 摘要截断长度。太长会浪费 token，太短信息不够。
SUMMARY_MAX_CHARS = 200

# 判断"周报到底生成了没"的最小字节数（配合 --if-missing 补跑闸门）。
# 正常的周报是 8~15 KB，所以 500 字节只能是一份空壳（比如只写了标题就崩了）。
# 为什么要设这个下限：只看"文件存在"会骗人 —— 程序在写文件的半途崩掉会留下半个文件。
MIN_REPORT_BYTES = 500

# 抓取失败多少个源时，在周报头部挂 ⚠️ 警告。
# 1~2 个源偶尔抽风（限流、证书过期）不值得惊动读者；
# 到 3 个就说明「这一周的新闻可能漏了一大块」，必须让人看见。
FAILED_SOURCE_WARN_THRESHOLD = 3

# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
TIMEOUT = 12          # 单个源超时（秒）
MAX_WORKERS = 8       # 并发抓取数
