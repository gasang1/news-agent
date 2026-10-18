"""通知模块：周报推送（Server 酱 → 微信）、预算告警、运行失败等事件的出口。

通道现状：
    - Server 酱（微信）  ✅ 已实现，配了 WECHAT_SENDKEY 就发
    - 企业微信群机器人    ⬜ 留位未实现
    - 邮件               ⬜ 留位未实现
加通道：实现一个 `_send_xxx(title, message) -> bool` 函数，加进 `_channels()`。

兜底行为：无论有没有配通道、通道发没发成功，消息都会
    1. 打印到终端
    2. 追加写进 state/notifications.log（有长度上限，避免仓库被日志撑大）
所以"没配通道 = 丢失通知"这件事不会发生，你事后能翻到记录。
通知失败永远只吞异常，不影响主流程 —— 周报生成比通知重要。
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import config

LOG_FILE = Path(__file__).parent / "state" / "notifications.log"

# 日志里每条消息最多留这么多字符。周报全文会另外进 reports/，
# 没必要在日志里再存一遍（state/ 是要提交到仓库的）。
LOG_MAX_CHARS = 1_500


# ---------------------------------------------------------------------------
# 底层 HTTP（抽出来是为了测试能替换掉，不真的发请求）
# ---------------------------------------------------------------------------
def _post_form(url: str, fields: dict, proxy: str | None = None):
    """发一个 form-urlencoded 的 POST，返回响应 body（bytes）。

    proxy=None 表示直连（GitHub Actions 在海外，直连最快）。
    """
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "news-agent/1.0"},
    )
    handlers = [urllib.request.ProxyHandler(
        {"http": proxy, "https": proxy} if proxy else {})]
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=20) as resp:
        return resp.read()


def _proxy_candidates() -> list[str | None]:
    """直连优先，再试环境变量里的代理，最后试本机常见端口。"""
    cands: list[str | None] = [None]
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(var, "").strip()
        if v:
            cands.append(v)
            break
    cands.append("http://127.0.0.1:7897")
    return cands


def _clip(text: str, limit: int, hint: str) -> str:
    """按行截断到 limit 以内，尽量不把一句话切一半。

    这是按**字符**截断，只用于本地日志（state/notifications.log）。
    发给 Server 酱的正文必须按字节截断，用下面的 _clip_bytes。
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    if nl > limit // 2:
        cut = cut[:nl]
    return cut.rstrip() + f"\n\n……（{hint}）"


# 正文实际会以 form-urlencoded 发出去，所以"多大"要按编码后的字节数算：
# 一个汉字 → 3 字节 UTF-8 → %E4%B8%AD 这样的 9 字节。
def _encoded_len(text: str) -> int:
    """这段文字发出去会占多少字节（和 _post_form 的编码方式保持一致）。"""
    return len(urllib.parse.urlencode({"desp": text}).encode("utf-8"))


def _clip_bytes(text: str, limit: int, hint: str) -> str:
    """按"URL 编码后的字节数"截断 —— Server 酱的 32KB 限制就是这个单位。

    为什么不能按字符数：汉字编码后膨胀 9 倍，6,000 字的周报已经是 28KB。
    按字符算会得出"还有很大余量"的错觉，然后推送在服务端被拒，
    日志里只剩一行 400，你周一中午什么都收不到。

    按行二分：找到"能塞下的最长行前缀"，避免把一句话切两半。
    """
    text = text.strip()
    if _encoded_len(text) <= limit:
        return text

    budget = max(1_000, limit - 300)        # 给尾部的截断提示留出空间
    lines = text.splitlines()
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _encoded_len("\n".join(lines[:mid])) <= budget:
            lo = mid
        else:
            hi = mid - 1

    kept = "\n".join(lines[:lo]).rstrip()
    tail = f"\n\n……（{hint}）"
    return kept + tail if kept else f"（内容过长，已省略）{tail}"


# ---------------------------------------------------------------------------
# 通道实现
# ---------------------------------------------------------------------------
def _send_serverchan(title: str, message: str) -> bool:
    """Server 酱（微信推送）。文档：https://sct.ftqq.com/sendkey

    接口：POST https://sctapi.ftqq.com/<SENDKEY>.send
    参数：title（标题，最长 32 字）、desp（正文，支持 Markdown，上限 32KB）
    返回：JSON {"code": 0, ...} 表示成功，code 非 0 是失败原因
    """
    key = config.get_sendkey()
    if not key:
        return False

    url = f"https://sctapi.ftqq.com/{key}.send"
    fields = {"title": title[:32],
              "desp": _clip_bytes(message, config.NOTIFY_MAX_BYTES,
                                  "内容过长，完整版见仓库 reports/")}

    last_err = ""
    for proxy in _proxy_candidates():
        try:
            raw = _post_form(url, fields, proxy)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code} {e.read()[:120].decode('utf-8', 'replace')}"
            continue
        except Exception as e:                  # 网络层什么都可能出
            last_err = f"{type(e).__name__}: {e}"
            continue
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            last_err = f"返回不是 JSON：{raw[:120]!r}"
            continue
        if payload.get("code") == 0:
            data = payload.get("data") or {}
            pushid = data.get("pushid") or data.get("push_id") or ""
            print(f"   ✅ 微信推送已发出（pushid {pushid or '未知'}）")
            return True
        last_err = f"code={payload.get('code')} {payload.get('message') or payload.get('info') or ''}"
        # 凭据类错误换代理也没用，直接停
        if "SENDKEY" in last_err.upper() or "bad" in last_err.lower():
            break

    print(f"   ✗ 微信推送失败：{last_err}")
    return False


def _send_wecom(title: str, message: str) -> bool:
    """企业微信群机器人（未实现）。需要 WEBHOOK URL，例如
    POST {"msgtype": "markdown", "markdown": {"content": f"**{title}**\\n{message}"}}
    """
    return False


def _send_email(title: str, message: str) -> bool:
    """SMTP 邮件（未实现）。用 smtplib.SMTP_SSL + EmailMessage 即可。"""
    return False


def _channels() -> list:
    """当前实际启用的通道。没配 SendKey 就是空列表。"""
    chans = []
    if config.get_sendkey():
        chans.append(_send_serverchan)
    return chans


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def notify(title: str, message: str, level: str = "warning") -> bool:
    """发一条通知。返回是否成功送达任一外部通道。

    level: info / warning / error，只影响日志里的标记。
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}.get(level, "•")

    # 1. 终端可见（只打印前若干行，别让 CI 日志被整份周报淹没）
    print(f"\n{icon} [{level}] {title}")
    for line in message.strip().splitlines()[:8]:
        print(f"   {line}")
    total = len(message.strip().splitlines())
    if total > 8:
        print(f"   （共 {total} 行，其余见下方通道结果与 state/notifications.log）")

    # 2. 落地存档（即使没配通道，事后也能查到）
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] [{level}] {title}\n")
            for line in _clip(message, LOG_MAX_CHARS, "已截断，全文见 reports/").splitlines():
                f.write(f"    {line}\n")
            f.write("\n")
    except OSError as e:
        print(f"   （通知日志写入失败：{e}）")

    # 3. 外发
    if not config.NOTIFY_ENABLED:
        print("   （外发通道已关闭 NOTIFY_ENABLED=False，仅本地记录）")
        return False

    channels = _channels()
    if not channels:
        print("   （没配 SendKey，仅本地记录；开通方法见 README「手机和电脑上怎么看」）")
        return False

    delivered = False
    for channel in channels:
        try:
            if channel(title, message):
                delivered = True
        except Exception as e:                 # 通知失败绝不能影响主流程
            print(f"   （通道 {getattr(channel, '__name__', '?')} 异常：{e}）")
    return delivered


def send_weekly(done: bool, start: datetime, end: datetime, items: int,
                md: str | None = None) -> bool:
    """周报专用：推送一条"这周好了"，正文就是周报全文（Markdown）。

    手机上点开微信就能读，不用登录 GitHub。
    """
    if not done:
        return notify("本周周报没跑成功",
                      "抓取或模型环节失败，详情见 GitHub Actions 日志。",
                      level="error")
    title = f"周报 {start:%m-%d}~{end:%m-%d}｜{items} 条"
    return notify(title, md or "（正文为空）", level="info")
