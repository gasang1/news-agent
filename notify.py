"""通知模块：预算告警、运行失败等事件的出口。

⚠️ 现在**只落地，不外发**。
   通知动作按你的要求先不做，但接入位置全部留好：
   - 想接微信 → 填 _send_serverchan()
   - 想接企业微信机器人 → 填 _send_wecom()
   - 想接邮件 → 填 _send_email()
   然后把它加进 CHANNELS 列表即可，调用方一行都不用改。

兜底行为：无论有没有配通道，消息都会
    1. 打印到终端
    2. 追加写进 state/notifications.log
所以"没配通道 = 丢失通知"这件事不会发生，你事后能翻到记录。
"""

from datetime import datetime
from pathlib import Path

import config

LOG_FILE = Path(__file__).parent / "state" / "notifications.log"


# ---------------------------------------------------------------------------
# 通道实现（目前全部未配置）
# ---------------------------------------------------------------------------
def _send_serverchan(title: str, message: str) -> bool:
    """Server 酱（微信推送）。需要 SENDKEY。

    TODO: 开通 https://sct.ftqq.com/ 拿到 SENDKEY 后实现：
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            "title": title[:32], "desp": message,
        }).encode()
        req = urllib.request.Request(
            f"https://sctapi.ftqq.com/{config.SERVERCHAN_KEY}.send", data=data)
        urllib.request.urlopen(req, timeout=10)
        return True
    """
    return False


def _send_wecom(title: str, message: str) -> bool:
    """企业微信群机器人。需要 WEBHOOK URL。

    TODO: 实现方式与 Server 酱类似，POST JSON 到 webhook 地址：
        {"msgtype": "markdown", "markdown": {"content": f"**{title}**\\n{message}"}}
    """
    return False


def _send_email(title: str, message: str) -> bool:
    """SMTP 邮件。需要服务器、账号、授权码。

    TODO: 用 smtplib.SMTP_SSL + EmailMessage 实现。
    """
    return False


# TODO: 配好某个通道后，把它加到这个列表里，通知就会真的发出去。
CHANNELS = []


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def notify(title: str, message: str, level: str = "warning") -> bool:
    """发一条通知。返回是否成功送达任一外部通道。

    level: info / warning / error，只影响日志里的标记。
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌"}.get(level, "•")

    # 1. 终端可见
    print(f"\n{icon} [{level}] {title}")
    for line in message.strip().splitlines():
        print(f"   {line}")

    # 2. 落地存档（即使没配通道，事后也能查到）
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] [{level}] {title}\n")
            for line in message.strip().splitlines():
                f.write(f"    {line}\n")
            f.write("\n")
    except OSError as e:
        print(f"   （通知日志写入失败：{e}）")

    # 3. 外发（当前 CHANNELS 为空，所以不会发任何东西）
    if not config.NOTIFY_ENABLED:
        print("   （外发通道未启用，仅本地记录）")
        return False

    delivered = False
    for channel in CHANNELS:
        try:
            if channel(title, message):
                delivered = True
        except Exception as e:                 # 通知失败绝不能影响主流程
            print(f"   （通道 {getattr(channel, '__name__', '?')} 失败：{e}）")
    return delivered
