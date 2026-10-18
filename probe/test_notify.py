"""离线验证 notify.py：不联网、不花钱、不打扰微信。

做法：把 notify._post_form 换成一个假的，拦截真正要发出去的请求，
      然后检查 URL、标题长度、正文长度、失败处理是否符合预期。
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import notify  # noqa: E402

FAKE_KEY = "SCT_TEST_KEY_123"
MD = "# 安全 & AI 周报 · 2026-10-11 ~ 2026-10-18\n\n## 本周观察\n\n测试观察。\n\n### 1. 测试条目\n\n- **摘要**：测试。\n"

results: list[bool] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'✓' if ok else '✗'} {label}{(' — ' + detail) if detail else ''}")


class Spy:
    """假通道：记录每次请求，按脚本返回。"""

    def __init__(self, reply=None, raises=None):
        self.calls: list[tuple[str, dict, str | None]] = []
        self.reply = reply if reply is not None else {"code": 0, "data": {"pushid": "p1"}}
        self.raises = raises

    def __call__(self, url, fields, proxy=None):
        self.calls.append((url, fields, proxy))
        if self.raises:
            raise self.raises
        return json.dumps(self.reply).encode()


def main() -> int:
    real_post = notify._post_form
    real_log = notify.LOG_FILE
    tmp_log = Path(__file__).parent / "_notify_test.log"
    notify.LOG_FILE = tmp_log

    try:
        print("=== 1. 没配 SendKey：不发、不报错 ===")
        os.environ.pop("WECHAT_SENDKEY", None)
        spy = Spy()
        notify._post_form = spy
        ok = notify.notify("测试", "正文", level="info")
        check("返回 False", ok is False)
        check("一个请求都没发", len(spy.calls) == 0, f"{len(spy.calls)} 次")

        print("=== 2. 配了 SendKey：请求格式正确 ===")
        os.environ["WECHAT_SENDKEY"] = FAKE_KEY
        spy = Spy()
        notify._post_form = spy
        ok = notify.notify("周报 10-11~10-18｜18 条", MD, level="info")
        check("返回 True", ok is True)
        check("只发一次", len(spy.calls) == 1, f"{len(spy.calls)} 次")
        url, fields, proxy = spy.calls[0]
        check("URL 里带 SendKey", url == f"https://sctapi.ftqq.com/{FAKE_KEY}.send", url)
        check("首次尝试是直连", proxy is None, str(proxy))
        check("标题不超过 32 字", len(fields["title"]) <= 32, f'{len(fields["title"])} 字')
        check("正文带上了周报内容", "本周观察" in fields["desp"] and "测试条目" in fields["desp"])

        print("=== 3. 标题超长会被截到 32 字 ===")
        spy = Spy()
        notify._post_form = spy
        notify.notify("很" * 80, MD, level="info")
        check("标题被截断", len(spy.calls[0][1]["title"]) == 32, f'{len(spy.calls[0][1]["title"])} 字')

        print("=== 4. 超长正文按上限截断并注明 ===")
        spy = Spy()
        notify._post_form = spy
        long_md = "\n".join(f"- 第 {i} 条：这是一行用来把正文撑长的测试内容。" for i in range(3000))
        notify.notify("长正文", long_md, level="info")
        desp = spy.calls[0][1]["desp"]
        check("长度不超过上限", len(desp) <= config.NOTIFY_MAX_CHARS + 100, f"{len(desp)} 字符")
        check("有截断提示", "完整版见仓库" in desp)
        check("不是把一行切成两半", desp.count("\n- 第 ") > 100)

        print("=== 5. 服务端返回错误码：不抛异常、返回 False ===")
        spy = Spy(reply={"code": 40001, "message": "bad sendkey"})
        notify._post_form = spy
        ok = notify.notify("测试", MD, level="info")
        check("返回 False", ok is False)

        print("=== 6. 网络异常：换代理重试，最终仍返回 False ===")
        spy = Spy(raises=OSError("network is unreachable"))
        notify._post_form = spy
        ok = notify.notify("测试", MD, level="info")
        check("返回 False", ok is False)
        check("试过多个代理候选", len(spy.calls) >= 2, f"{len(spy.calls)} 次")

        print("=== 7. send_weekly 的标题与语义 ===")
        spy = Spy()
        notify._post_form = spy
        notify.send_weekly(True, datetime(2026, 10, 11), datetime(2026, 10, 18), 18, MD)
        title = spy.calls[0][1]["title"]
        check("标题形如 周报 10-11~10-18｜18 条", title == "周报 10-11~10-18｜18 条", title)
        ok = notify.send_weekly(False, datetime(2026, 10, 11), datetime(2026, 10, 18), 0, None)
        check("没跑成功时发的是失败通知", spy.calls[-1][1]["title"] == "本周周报没跑成功")

        print("=== 8. 本地日志：落地、有长度上限 ===")
        text = tmp_log.read_text(encoding="utf-8") if tmp_log.exists() else ""
        check("日志文件已生成", bool(text))
        check("记录里有级别标记", "[info]" in text or "[error]" in text)
        import re
        blocks = [b for b in re.split(r"(?m)^\[", text) if b.strip()]
        # 容差：每条正文行会被缩进 4 格，加上时间戳/级别/标题前缀，
        # 实际落盘长度会比 LOG_MAX_CHARS 多出几百字符。
        check("每条记录不超过上限",
              all(len(b) <= notify.LOG_MAX_CHARS + 400 for b in blocks),
              f"最长 {max((len(b) for b in blocks), default=0)} 字符 / 上限 {notify.LOG_MAX_CHARS}")

        print("=== 9. NOTIFY_ENABLED=False 时彻底不外发 ===")
        spy = Spy()
        notify._post_form = spy
        old = config.NOTIFY_ENABLED
        config.NOTIFY_ENABLED = False
        ok = notify.notify("关掉时", MD, level="info")
        config.NOTIFY_ENABLED = old
        check("返回 False", ok is False)
        check("没发请求", len(spy.calls) == 0)
    finally:
        notify._post_form = real_post
        notify.LOG_FILE = real_log
        os.environ.pop("WECHAT_SENDKEY", None)
        if tmp_log.exists():
            tmp_log.unlink()

    passed = all(results)
    print(f"\n共 {len(results)} 项，{'全部通过' if passed else '有失败项'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
