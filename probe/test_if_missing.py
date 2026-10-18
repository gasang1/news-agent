"""补跑闸门（--if-missing）的离线测试：不联网、不调模型、不花 token。

测什么：
    1. today_report_path() 用的是运行当天日期，落在 config.OUTPUT_DIR 下
    2. 文件不存在 → report_ready() False
    3. 空文件 → False
    4. 半个文件（小于 MIN_REPORT_BYTES）→ False
    5. 正常大小 → True
    6. 目录（而不是文件）→ False，不抛异常
    7. 有文件但读不了权限时不崩（用不存在的父目录模拟 OSError）

用法：python probe/test_if_missing.py
"""

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import main  # noqa: E402

FAILED = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'✓' if ok else '✗'} {name}: {got!r}" + ("" if ok else f"  ← 期望 {want!r}"))
    if not ok:
        FAILED.append(name)


def main_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        old_dir = config.OUTPUT_DIR
        config.OUTPUT_DIR = str(tmp)
        try:
            # 1. 路径命名
            day = date(2026, 11, 2)
            p = main.today_report_path(day)
            check("路径在 OUTPUT_DIR 下", p.parent, tmp)
            check("文件名用当天日期", p.name, "weekly_2026-11-02.md")

            # 2. 不存在
            check("文件不存在", main.report_ready(p), False)

            # 3. 空文件
            p.write_text("", encoding="utf-8")
            check("空文件不算成功", main.report_ready(p), False)

            # 4. 半截文件
            p.write_text("# 安全 & AI 周报\n\n> 生成时间", encoding="utf-8")
            check(f"小于 {config.MIN_REPORT_BYTES} 字节不算成功",
                  main.report_ready(p), False)

            # 5. 正常大小（刚好过线）
            p.write_text("x" * config.MIN_REPORT_BYTES, encoding="utf-8")
            check("刚好达到下限算成功", main.report_ready(p), True)

            # 6. 正常周报级别（10 KB）
            p.write_text("x" * 10_000, encoding="utf-8")
            check("10KB 算成功", main.report_ready(p), True)

            # 7. 传目录进来不能抛异常
            check("目录不算成功", main.report_ready(tmp), False)

            # 8. 父目录不存在（模拟路径不可达）
            gone = tmp / "nope" / "weekly_x.md"
            check("路径不可达不崩", main.report_ready(gone), False)
        finally:
            config.OUTPUT_DIR = old_dir

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main_test())
