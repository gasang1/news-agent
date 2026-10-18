"""筛选层：把"抓到的一大堆"变成"真正相关的一批"。

不筛的话，325 篇里大约只有三分之一跟安全/AI 有关系——
Wired 会给促销码，TechCrunch 会给影视八卦，开源中国会给发版公告。

两级筛选，都不花钱：
    1. 黑名单：按标题模式丢掉明显无关的内容（促销码、评测、娱乐…）
    2. 关键词：标题或摘要里必须命中至少一个领域关键词

重要的规则：标题命中关键词时，黑名单不生效。
否则 "Kling 4.0 正式上线" 会被"发版公告"规则误杀，
而 "novel-plus v5.4.0 发布" 才是真正想丢掉的。
这个优先级是调试出来的，不是想出来的。

真正的"重要性判断"留给后面的模型（阶段 3）。
这一层只负责把量降下来，宁可放过，不要错杀。
"""

import re

from fetch import Article

BLOCK_PATTERNS = [
    # 促销 / 优惠券
    r"promo code", r"coupon", r"discount", r"deals?\b", r"\bsale\b",
    r"off \d", r"\d+% off", r"save up to", r"black friday", r"cyber monday",
    r"促销", r"优惠", r"双1[12]", r"大促", r"折扣", r"满减",
    # 产品评测 / 导购
    r"^review\b", r"\breview\b", r"hands-on", r"best .* (2026|2025)",
    r"buying guide", r"评测", r"上手", r"选购",
    # 发版公告 / 更新日志
    r"发版", r"更新日志", r"升级公告", r"changelog", r"is now available",
    # 影视 / 娱乐
    r"\bmovie\b", r"\bfilm\b", r"\bseries\b", r"\bcomic\b", r"documentary",
    r"box office", r"电影", r"影视", r"剧集", r"综艺",
    # 政治
    r"midterm", r"\belection", r"trump administration", r"white house",
    r"选举", r"白宫",
    # 生活方式 / 健康科普
    r"coral", r"dementia", r"suicide", r"social life", r"peptide",
    r"催产素", r"痴呆症", r"自杀率", r"珊瑚", r"squid blogging",
    # 活动 / 招聘 / 邀请函
    r"邀请函", r"倒计时", r"招募", r"报名", r"招聘", r"大赛", r"峰会", r"议程",
    # 太空 / 汽车
    r"rocket report", r"starbase", r"supercar", r"\broadster\b",
    r"火箭", r"探测器",
]

BLOCK_RE = [re.compile(p, re.IGNORECASE) for p in BLOCK_PATTERNS]

RELEVANCE_KEYWORDS = {
    "security": [
        "漏洞", "0day", "零日", "勒索", "攻击", "入侵", "木马", "钓鱼", "后门",
        "泄露", "窃取", "提权", "越权", "反序列化", "注入", "远控", "僵尸网络",
        "黑产", "黑客", "渗透", "红队", "蓝队", "应急响应", "补丁", "病毒",
        "恶意", "间谍", "供应链", "凭据", "凭证", "加密", "绕过", "内网",
        "APT", "CVE", "RCE", "XSS", "SSRF", "SQL", "DDoS", "WAF", "SOC", "EDR",
        "免杀", "横向移动", "权限提升", "信息收集", "审计",
        "vulnerab", "ransom", "malware", "exploit", "breach", "phishing",
        "zero-day", "0-day", "backdoor", "botnet", "malicious", "spyware",
        "credential", "intrusion", "cyberattack", "cybersecurity", "hacked",
        "attacker", "threat actor", "data leak", "stolen", "encryption",
        "patch", "remote code", "privilege", "authentication", "security",
        "shellcode", "payload", "infostealer", "lateral movement",
    ],
    "ai": [
        "大模型", "模型", "人工智能", "智能体", "算法", "训练", "推理", "微调",
        "蒸馏", "对齐", "多模态", "算力", "开源模型", "参数量", "涌现", "幻觉",
        "提示词", "深度学习", "神经网络", "具身", "机器人", "世界模型", "语料",
        "上下文", "生成式", "文生图", "文生视频", "视频生成", "代码生成",
        "千问", "文心", "豆包", "混元", "星火", "智谱", "月之暗面", "百川",
        "可灵", "智能驾驶", "自动驾驶",
        "artificial intelligence", "machine learning", "deep learning",
        "neural", "llm", "large language model", "gpt", "claude", "gemini",
        "deepseek", "qwen", "llama", "transformer", "diffusion", "inference",
        "fine-tun", "agentic", "multimodal", "embodied", "kling", "sora",
        "open-weight", "ai model", "ai lab", "ai company", "text-to-",
    ],
    # 硬件这一栏的词表刻意只收"产业级"词汇（芯片、制程、大厂动作），
    # 不收"手机/耳机/相机"这类消费电子词 —— 那些绝大多数是导购和评测，
    # 正是黑名单要挡的东西。收进来等于把噪声放进候选池，白烧 token。
    "hardware": [
        "芯片", "半导体", "制程", "晶圆", "光刻", "代工", "封装", "晶体管",
        "显卡", "算力", "数据中心", "服务器", "内存", "存储", "固态", "电池",
        "英伟达", "台积电", "英特尔", "超威", "三星电子", "阿斯麦", "博通",
        "量产", "产能", "扩产", "收购", "并购", "投资", "裁员", "重组",
        "nvidia", "tsmc", "intel", "amd", "asml", "broadcom", "samsung",
        "semiconductor", "foundry", "wafer", "chipset", "gpu", "hbm",
        "transistor", "data center", "lithography", "fab ", "memory",
        "acquisition", "layoff",
    ],
    # 开源项目的词表：要么是"项目本身"（仓库、星标、移植、复刻），
    # 要么是"开源生态"（许可证、贡献者、基金会）。
    "opensource": [
        "开源", "星标", "仓库", "复刻", "移植", "重写", "贡献者", "维护者",
        "许可证", "协议变更", "基金会", "分支", "合并请求", "众筹",
        "github", "open source", "open-source", "openweight", "repo",
        "repository", "pull request", "fork", "mit license", "apache",
        "gpl", "contributor", "maintainer", "roadmap", "self-hosted",
        "reimplementation", "port of",
    ],
}

# 短词必须限定"前后都不是英文字母"，不能当子串用。
# 比如 "ai" 作为子串会命中 "said"、"email"、"detail"——那就全乱了。
#
# 为什么不用 \b：中文字符在 Python 正则里也算 word 字符，
# 所以 \b 在 "AI芯片"、"gpu加速"、"CVE-2026-1234漏洞" 里都不成立（实测全是 False）。
# 用"前后不是英文字母"才能同时管住中英混排和子串误命中：
#   ai芯片     ✓ 命中     email / said   ✗ 不命中
#   GPU 加速   ✓ 命中     html           ✗ 不命中
SHORT_TOKEN_PATTERNS = [
    r"(?<![a-z])ai(?![a-z])",
    r"(?<![a-z])ml(?![a-z])",
    r"(?<![a-z])gpu(?![a-z])",
    r"(?<![a-z])cve-\d{4}",
]
_SHORT_RE = [re.compile(p, re.IGNORECASE) for p in SHORT_TOKEN_PATTERNS]

_KEYWORD_SETS = {
    cat: [k.lower() for k in words] for cat, words in RELEVANCE_KEYWORDS.items()
}


def _match(haystack: str) -> str | None:
    """在文本里找关键词，返回第一个命中的。"""
    for keywords in _KEYWORD_SETS.values():
        for kw in keywords:
            if kw in haystack:
                return kw
    # 短词走词边界正则，避免 "ai" 命中 "email"
    for pattern, regex in zip(SHORT_TOKEN_PATTERNS, _SHORT_RE):
        if regex.search(haystack):
            return pattern
    return None


def _blocked(title: str) -> str | None:
    """命中黑名单则返回命中的模式。"""
    for pattern, regex in zip(BLOCK_PATTERNS, BLOCK_RE):
        if regex.search(title):
            return pattern
    return None


def apply_filters(articles: list[Article], use_blocklist: bool = True
                  ) -> tuple[list[Article], dict]:
    """两级筛选。返回 (保留的文章, 统计信息)。"""
    kept: list[Article] = []
    blocked: list[tuple[str, str]] = []
    keyword_miss: list[str] = []

    for a in articles:
        title_lower = a.title.lower()
        full_lower = f"{title_lower} {a.summary.lower()}"

        # 标题命中领域关键词 → 最可信，黑名单不许拦它
        if _match(title_lower):
            kept.append(a)
            continue

        # 标题没命中，再看黑名单
        if use_blocklist:
            pattern = _blocked(a.title)
            if pattern:
                blocked.append((a.title, pattern))
                continue

        # 最后看摘要
        if _match(full_lower):
            kept.append(a)
            continue

        keyword_miss.append(a.title)

    stats = {
        "input": len(articles),
        "kept": len(kept),
        "blocked": len(blocked),
        "keyword_miss": len(keyword_miss),
        "blocked_samples": blocked[:15],
        "keyword_miss_samples": keyword_miss[:15],
    }
    return kept, stats


# ---------------------------------------------------------------------------
# 综合源归栏：cnBeta / ServeTheHome 这类"什么都发"的源，按内容分派板块
# ---------------------------------------------------------------------------
# 为什么需要它：以前 cnBeta 钉死在「科技硬件」板块，硬件板块一取消，
# 它的文章就找不到归属的批次，会在 analyze() 里被静默丢弃。
#
# 规则（按优先级，第一个命中就算）：
#   security → ai → opensource
# 只命中硬件词（芯片/制程/HBM/显卡）的，归进 ai 栏 —— 硬件板块没了，
# 但"算力与芯片"本身就是 AI 产业的主线，不该跟着板块一起消失。
# 什么都不命中的，返回 None（调用方丢弃）：说明它既不是科技新闻也不够专业。
_GENERAL_ROUTE_ORDER = ("security", "ai", "opensource")
_HARDWARE_FALLBACK = "ai"

# SHORT_TOKEN_PATTERNS 与板块的对应关系（顺序必须跟上面对齐）
_SHORT_CATEGORIES = ("ai", "ai", "ai", "security")


def _hits(category: str, haystack: str) -> bool:
    """该板块的关键词是否命中。"""
    for kw in _KEYWORD_SETS.get(category, ()):
        if kw in haystack:
            return True
    return False


def _short_hits(category: str, haystack: str) -> bool:
    """该板块的短词（ai/ml/gpu/cve）是否命中。"""
    for cat, regex in zip(_SHORT_CATEGORIES, _SHORT_RE):
        if cat == category and regex.search(haystack):
            return True
    return False


def route_general(title: str, summary: str) -> str | None:
    """把综合源的文章分派到板块。返回板块名，判不出来返回 None。"""
    text = f"{title} {summary}".lower()
    for cat in _GENERAL_ROUTE_ORDER:
        if _hits(cat, text) or _short_hits(cat, text):
            return cat
    if _hits("hardware", text):
        return _HARDWARE_FALLBACK
    return None


if __name__ == "__main__":
    samples = [
        "Surfshark Promo Codes: 87% Off | October 2026",
        "Monday Squid Blogging: Squid Before the Chicxulub Meteor Impact",
        "novel-plus v5.4.0 发布，原创文学 CMS 系统",
        "Razer Barracuda V2 Pro Review: A Gaming Headset for the Office",
        "Democrats Are Gearing Up to Investigate the Trump Administration",
        "刚刚，Gemini 4终于来了：谷歌重回御三家，API限时半价",
        "Hackers Use Compromised ConnectWise Credential to Sign In to 173 Microsoft 365 Tenants",
        "Kling 4.0正式上线：10-bit HDR、30秒原生直出",
        "蚂蚁百灵开源 560B 模型 Ling-3.0-flash：0.19 美元搞定复杂工作流",
        "Starlink 用户数据泄露，涉及 300 万个邮箱地址",
    ]
    for title in samples:
        a = Article(title=title, url="x", summary="", published=None,
                    source="test", category="ai", lang="zh")
        hit = _match(title.lower())
        pattern = _blocked(title)
        if hit:
            verdict = f"OK   保留(关键词 {hit})"
        elif pattern:
            verdict = f"DROP 黑名单({pattern})"
        else:
            verdict = "DROP 无关键词"
        print(f"{verdict:<34} {title[:56]}")
