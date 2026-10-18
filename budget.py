"""token 预算：给 AI 环节装一个"用电闸"，防止成本失控。

解决什么问题？
    LLM 调用是唯一会产生真实金钱成本的地方，而且失败模式很隐蔽：
    - 模型返回空内容 → 重试 → 重试时放大 max_tokens → 成本翻几倍
    - 模型反复返回坏 JSON → 每次都要重发完整 prompt
    - 提示词写坏导致模型长篇大论 → 输出 token 暴涨
    这些都不会报错，只会让你月底看到账单才发现。

三层防护：
    1. 事前拦截 —— 每次调用前估算输入成本，剩余额度不够就直接拒绝（不花钱）
    2. 事后累计 —— 每次调用后累加真实用量，超限立即抛错中止整条流水线
    3. 调用次数限制 —— 单次运行的调用次数封顶，防止逻辑闭环无限重试

预算按"周期"计算，默认 7 天一个周期。周期标识存在 state/usage.json，
所以跨多次运行能累计 —— 你今天手动跑三次也算在同一周额度里。
"""

import json
import os
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

import config


class BudgetExceeded(RuntimeError):
    """预算不足或已超限。上层应当捕获它、通知用户、并降级输出。"""


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------
STATE_DIR = Path(__file__).parent / "state"
USAGE_FILE = STATE_DIR / "usage.json"


def period_start(today: date | None = None) -> date:
    """算出当前预算周期的起始日。

    周期长度取 config.BUDGET_PERIOD_DAYS（默认 7 天，与周报节奏一致）。
    用"固定锚点 + 整数个周期"的方式算，保证跨运行稳定，
    不会因为你在周期中间重启程序而重置额度。
    """
    today = today or date.today()
    anchor = date(2026, 1, 5)                      # 任意一个固定的周一起点
    days = config.BUDGET_PERIOD_DAYS
    delta = (today - anchor).days
    return anchor + timedelta(days=(delta // days) * days)


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------
@dataclass
class Budget:
    period_start: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    runs: int = 0
    last_run: str = ""
    # 本次运行的用量（start_run 时清零，不持久化）
    run_prompt_tokens: int = 0
    run_completion_tokens: int = 0
    run_calls: int = 0

    # ---------------- 额度计算 ----------------
    @property
    def limit(self) -> int:
        return config.WEEKLY_TOKEN_BUDGET

    @property
    def used(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def ratio(self) -> float:
        return self.used / self.limit if self.limit else 1.0

    @property
    def run_used(self) -> int:
        return self.run_prompt_tokens + self.run_completion_tokens

    @property
    def run_ratio(self) -> float:
        cap = config.MAX_RUN_TOKENS
        return self.run_used / cap if cap else 1.0

    @property
    def period_end(self) -> str:
        start = date.fromisoformat(self.period_start) if self.period_start else date.today()
        return (start + timedelta(days=config.BUDGET_PERIOD_DAYS)).isoformat()

    # ---------------- 持久化 ----------------
    @classmethod
    def load(cls) -> "Budget":
        """读本周期用量。换了周期就自动清零。"""
        cur = period_start().isoformat()

        if not USAGE_FILE.exists():
            return cls(period_start=cur)

        try:
            data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 文件坏了不能让整个程序崩，当新周期处理
            return cls(period_start=cur)

        if data.get("period_start") != cur:
            # 进入新周期，额度重置
            return cls(period_start=cur)

        return cls(
            period_start=cur,
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
            calls=int(data.get("calls", 0)),
            runs=int(data.get("runs", 0)),
            last_run=str(data.get("last_run", "")),
        )

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---------------- 记录与检查 ----------------
    def start_run(self) -> None:
        """开始一次运行：周累计保留，本次运行计数清零。"""
        self.runs += 1
        self.last_run = datetime.now().isoformat(timespec="seconds")
        self.run_prompt_tokens = 0
        self.run_completion_tokens = 0
        self.run_calls = 0

    def record(self, usage: dict | None) -> None:
        """累加一次调用的真实用量（同时记到周额度和本次运行）。"""
        if not usage:
            return
        p = int(usage.get("prompt_tokens", 0))
        c = int(usage.get("completion_tokens", 0))
        self.prompt_tokens += p
        self.completion_tokens += c
        self.calls += 1
        self.run_prompt_tokens += p
        self.run_completion_tokens += c
        self.run_calls += 1

    def check_before_call(self, estimated_input_tokens: int) -> None:
        """调用前拦截。

        同时检查两道闸门：
            本次运行是否会超 MAX_RUN_TOKENS
            本周是否会超 WEEKLY_TOKEN_BUDGET
        付不起就别发——发出去再超限，钱已经花掉了。
        """
        est = estimated_input_tokens

        if self.run_used + est > config.MAX_RUN_TOKENS:
            raise BudgetExceeded(
                f"单次运行额度不足：本次已用 {self.run_used:,} / "
                f"{config.MAX_RUN_TOKENS:,}，预计还需 {est:,}。"
                f"这通常意味着模型在异常重试，已停止以免成本翻倍。"
            )

        if self.used + est > self.limit:
            raise BudgetExceeded(
                f"本周 token 额度不足：已用 {self.used:,} / {self.limit:,}，"
                f"本次预计还需 {est:,}。"
                f"额度将在 {self.period_end} 重置。"
            )

    def check_after_call(self) -> None:
        """调用后检查是否越线。越线就立即中止，别让后面继续花。"""
        if self.run_used > config.MAX_RUN_TOKENS:
            raise BudgetExceeded(
                f"单次运行已超限：{self.run_used:,} / {config.MAX_RUN_TOKENS:,}。"
                f"已中止本次运行，避免继续烧钱。"
            )
        if self.used > self.limit:
            raise BudgetExceeded(
                f"本周 token 额度已超限：{self.used:,} / {self.limit:,}。"
                f"已停止后续所有 AI 调用。额度将在 {self.period_end} 重置。"
            )

    def should_warn(self) -> bool:
        """是否到了告警水位（默认 80%，周额度和单次运行都算）。"""
        return (self.ratio >= config.BUDGET_WARN_RATIO
                or self.run_ratio >= config.BUDGET_WARN_RATIO)

    # ---------------- 展示 ----------------
    def summary(self) -> str:
        pct = self.ratio * 100
        return (f"本周 {self.used:,}/{self.limit:,} tokens（{pct:.1f}%），"
                f"剩余 {self.remaining:,}，{self.calls} 次调用，"
                f"周期 {self.period_start} ~ {self.period_end}")

    def run_summary(self) -> str:
        return (f"本次运行 {self.run_used:,}/{config.MAX_RUN_TOKENS:,} tokens"
                f"（{self.run_ratio * 100:.1f}%），{self.run_calls} 次调用")

    def bar(self, width: int = 28) -> str:
        filled = int(width * min(1.0, self.ratio))
        return "[" + "█" * filled + "·" * (width - filled) + "]"


def estimate_tokens(text: str) -> int:
    """粗估 token 数。

    中文大约 1 token / 1.5 字，英文大约 1 token / 4 字符。
    这里用一个偏保守的混合估算：字符数 / 2。
    宁可高估，因为低估会让"事前拦截"失效。
    """
    return max(1, len(text) // 2)


# ---------------------------------------------------------------------------
# 上次成功运行记录（用于漏跑补偿）
# ---------------------------------------------------------------------------
LAST_RUN_FILE = STATE_DIR / "last_run.json"


def load_last_success() -> date | None:
    """读取上次成功生成周报的日期。没有记录则返回 None。"""
    if not LAST_RUN_FILE.exists():
        return None
    try:
        data = json.loads(LAST_RUN_FILE.read_text(encoding="utf-8"))
        return date.fromisoformat(data["last_success"])
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None


def save_last_success(day: date | None = None) -> None:
    """记录本次成功运行。只应该在周报真正写出来之后调用。"""
    day = day or date.today()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(
        json.dumps({"last_success": day.isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def catchup_days(default_days: int, today: date | None = None) -> tuple[int, str]:
    """算出这次该回溯多少天，以及给用户看的说明。

    逻辑：
        如果上次成功运行距今超过默认周期（说明漏跑了），
        就把窗口扩到覆盖这段间隔，上限 CATCHUP_MAX_DAYS。
        这样"电脑关机导致漏了一周"不会漏掉新闻。
    """
    today = today or date.today()
    last = load_last_success()
    if last is None:
        return default_days, ""

    gap = (today - last).days
    if gap <= default_days:
        return default_days, f"上次成功运行：{last}（{gap} 天前）"

    days = min(gap, config.CATCHUP_MAX_DAYS)
    note = (f"上次成功运行：{last}（{gap} 天前），已自动把窗口扩到 {days} 天 "
            f"以补上漏掉的内容")
    return days, note
