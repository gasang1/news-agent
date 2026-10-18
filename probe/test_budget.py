"""离线测试 token 预算逻辑，不调用 API、不花一分钱。

覆盖：
    1. 事前拦截：额度不够时必须在发请求前就拒绝
    2. 事后越线：花了超过额度要立刻中止
    3. 持久化：写盘后重新 load 能读回相同数字
    4. 周期切换：跨周期自动清零
    5. 告警水位：达到 80% 触发
"""

import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import budget  # noqa: E402
import config  # noqa: E402

# 测试用独立目录，别污染真实的 state/usage.json
TMP_DIR = Path(__file__).parent.parent / "state_test"
TMP = TMP_DIR / "usage.json"
budget.USAGE_FILE = TMP
budget.STATE_DIR = TMP_DIR


def reset(**cfg) -> budget.Budget:
    """造一个干净预算，可选覆盖配置。"""
    # 记住原始配置，避免多个用例互相污染
    if not hasattr(reset, "_origin"):
        reset._origin = {
            "MAX_RUN_TOKENS": config.MAX_RUN_TOKENS,
            "WEEKLY_TOKEN_BUDGET": config.WEEKLY_TOKEN_BUDGET,
            "BUDGET_PERIOD_DAYS": config.BUDGET_PERIOD_DAYS,
            "BUDGET_WARN_RATIO": config.BUDGET_WARN_RATIO,
        }
    for k, v in reset._origin.items():
        setattr(config, k, v)
    for k, v in cfg.items():
        setattr(config, k, v)

    if TMP.exists():
        TMP.unlink()
    return budget.Budget.load()


def case(name: str, fn) -> None:
    print(f"── {name}")
    try:
        fn()
    except AssertionError as e:
        print(f"   ❌ 失败：{e}")
        raise
    print()


def t1_precheck() -> None:
    b = reset(WEEKLY_TOKEN_BUDGET=1000, BUDGET_PERIOD_DAYS=7)
    b.check_before_call(500)                      # 额度够，不抛
    print(f"   额度 1000，请求 500 → 放行 ✓")

    b.record({"prompt_tokens": 400, "completion_tokens": 100})
    print(f"   已用 {b.used}，剩余 {b.remaining}")

    try:
        b.check_before_call(600)                  # 1000-500=500 < 600 → 应拒绝
    except budget.BudgetExceeded as e:
        print(f"   请求 600 → 拦截 ✓（{str(e)[:40]}...）")
    else:
        raise AssertionError("额度不足却没有拦截")


def t2_overflow() -> None:
    b = reset(WEEKLY_TOKEN_BUDGET=1000, BUDGET_PERIOD_DAYS=7)
    b.record({"prompt_tokens": 900, "completion_tokens": 300})
    print(f"   已用 {b.used} > 额度 1000")
    try:
        b.check_after_call()
    except budget.BudgetExceeded as e:
        print(f"   事后越线 → 中止 ✓（{str(e)[:40]}...）")
    else:
        raise AssertionError("超限却没有中止")


def t3_persist() -> None:
    b = reset(WEEKLY_TOKEN_BUDGET=100000, BUDGET_PERIOD_DAYS=7)
    b.record({"prompt_tokens": 1234, "completion_tokens": 567})
    b.start_run()
    b.save()

    again = budget.Budget.load()
    assert again.prompt_tokens == 1234, again.prompt_tokens
    assert again.completion_tokens == 567, again.completion_tokens
    assert again.runs == 1, again.runs
    print(f"   落盘后重新读取：{again.summary()}")


def t4_period_reset() -> None:
    b = reset(WEEKLY_TOKEN_BUDGET=100000, BUDGET_PERIOD_DAYS=7)
    b.record({"prompt_tokens": 50000, "completion_tokens": 5000})
    b.save()
    print(f"   本周期已用 {b.used}")

    # 假装是下一个周期
    future = date.today() + timedelta(days=config.BUDGET_PERIOD_DAYS)
    old_fn = budget.period_start
    budget.period_start = lambda today=None: old_fn(future)
    try:
        fresh = budget.Budget.load()
        assert fresh.used == 0, fresh.used
        print(f"   跨周期读取 → 自动清零 ✓（新周期起点 {fresh.period_start}）")
    finally:
        budget.period_start = old_fn


def t5_warn() -> None:
    b = reset(WEEKLY_TOKEN_BUDGET=1000, BUDGET_PERIOD_DAYS=7, BUDGET_WARN_RATIO=0.8,
              MAX_RUN_TOKENS=100000)
    assert not b.should_warn()
    b.record({"prompt_tokens": 500, "completion_tokens": 350})
    print(f"   已用 {b.used}/1000 → {b.ratio:.0%}")
    assert b.should_warn(), "80% 以上应告警"
    print("   触发告警水位 ✓")


def t6_run_cap() -> None:
    """单次运行上限：即使周额度还很充裕，一次运行也不能无限花。"""
    b = reset(WEEKLY_TOKEN_BUDGET=1_000_000, BUDGET_PERIOD_DAYS=7,
              MAX_RUN_TOKENS=1000)
    b.start_run()

    b.check_before_call(400)
    b.record({"prompt_tokens": 500, "completion_tokens": 300})   # 本次已用 800
    print(f"   周额度充足（{b.used}/1,000,000），但本次已用 {b.run_used}/1000")

    try:
        b.check_before_call(500)          # 800 + 500 > 1000 → 应拦截
    except budget.BudgetExceeded as e:
        print(f"   单次运行拦截 ✓（{str(e)[:46]}...）")
    else:
        raise AssertionError("单次运行超限却没有拦截")

    # start_run 应该把本次计数清零，但保留周累计
    b.start_run()
    assert b.run_used == 0, b.run_used
    assert b.used == 800, b.used
    print(f"   重新开始一次运行：本次 {b.run_used}，周累计仍为 {b.used} ✓")


def t7_catchup() -> None:
    """漏跑补偿：隔了 10 天没跑，窗口应该自动扩到 10 天。

    注意：这个测试会临时删掉真实的 state/last_run.json，结束后必须恢复，
    否则下次正式运行会以为"从没跑过"，窗口退回 7 天。
    """
    backup = None
    if budget.LAST_RUN_FILE.exists():
        backup = budget.LAST_RUN_FILE.read_text(encoding="utf-8")
        budget.LAST_RUN_FILE.unlink()

    try:
        days, note = budget.catchup_days(7, today=date.today())
        print(f"   无历史记录 → 默认 {days} 天（{note or '无说明'}）")
        assert days == 7

        ten_days_ago = date.today() - timedelta(days=10)
        budget.save_last_success(ten_days_ago)
        days, note = budget.catchup_days(7, today=date.today())
        print(f"   上次运行在 10 天前 → 窗口 {days} 天")
        print(f"   {note}")
        assert days == 10, days

        # 超过上限要被截断
        old = date.today() - timedelta(days=200)
        budget.save_last_success(old)
        days, _ = budget.catchup_days(7, today=date.today())
        print(f"   上次运行在 200 天前 → 窗口 {days} 天（上限 {config.CATCHUP_MAX_DAYS}）")
        assert days == config.CATCHUP_MAX_DAYS, days
    finally:
        if backup is not None:
            budget.LAST_RUN_FILE.write_text(backup, encoding="utf-8")
            print("   已恢复真实的 state/last_run.json ✓")
        elif budget.LAST_RUN_FILE.exists():
            budget.LAST_RUN_FILE.unlink()


if __name__ == "__main__":
    case("1. 事前拦截", t1_precheck)
    case("2. 事后越线", t2_overflow)
    case("3. 持久化", t3_persist)
    case("4. 周期切换清零", t4_period_reset)
    case("5. 告警水位", t5_warn)
    case("6. 单次运行上限", t6_run_cap)
    case("7. 漏跑补偿", t7_catchup)
    print("全部通过")
    shutil.rmtree(TMP_DIR, ignore_errors=True)
