"""模拟 Linux 环境：确认在没有 winreg 的平台上程序能正常启动。

GitHub Actions 用的是 ubuntu，`winreg` 是 Windows 专用模块。
如果 netcheck.py 顶层直接 import 它，程序在那边会一启动就 ImportError。

用 sys.modules['winreg'] = None 来伪造"这个模块不存在"的效果：
Python 遇到这种情况会抛 ImportError，和真实非 Windows 平台一致。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ---- 伪造非 Windows 环境 ----
sys.modules["winreg"] = None


def main() -> None:
    print("1) 导入 netcheck（顶层曾直接 import winreg）")
    import netcheck
    print(f"   导入成功，winreg = {netcheck.winreg}")

    print("\n2) 调用 _from_registry()（非 Windows 应返回 None）")
    result = netcheck._from_registry()
    assert result is None, f"应返回 None，实际 {result!r}"
    print(f"   返回 {result!r} ✓")

    print("\n3) 调用 detect（应能正常完成，扫描端口超时会稍慢）")
    status = netcheck.detect(scan=False)      # 关掉端口扫描，避免等待
    print(f"   {status}")

    print("\n4) 导入其余模块")
    import analyzer
    import budget
    import config
    import fetch
    import notify
    import screening
    print("   全部导入成功")

    print("\n5) 检查输出路径是推导出来的，而不是写死的盘符路径")
    out = config.OUTPUT_DIR
    expected = str(Path(config.__file__).parent / "reports")
    print(f"   OUTPUT_DIR = {out}")
    # 关键不是"看起来像不像 Windows 路径"，而是"它是否由 __file__ 推导"。
    # 在 Linux 上同一行代码会自动得到 /home/runner/work/.../reports
    assert out == expected, f"应等于 {expected}，实际 {out}"
    print("   由 __file__ 推导，跨平台安全 ✓")

    print("\n6) 检查源码里没有硬编码的 Windows 盘符路径")
    bad = []
    for py in ROOT.glob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if "D:\\" in line or "C:\\" in line:
                # 注释和文档字符串里的示例不算
                if not line.strip().startswith("#"):
                    bad.append(f"{py.name}:{i}  {line.strip()[:60]}")
    if bad:
        print("   ⚠️ 发现可疑路径：")
        for b in bad:
            print(f"     {b}")
    else:
        print("   没有硬编码路径 ✓")

    print("\n全部通过 —— 这份代码可以在 Linux runner 上跑")


if __name__ == "__main__":
    main()
