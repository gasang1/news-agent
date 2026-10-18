"""代理探测：判断本机代理此刻是否真的可用。

为什么需要这个模块？
    "系统里配置了代理" 和 "代理真的在运行" 是两件事。
    你的电脑就是这样：注册表里配了 127.0.0.1:7897，但 Clash 没开时该端口无人监听。
    只看配置不测端口，所有请求都会失败（我们第一次探测就撞上了）。

    更隐蔽的一种误判："端口有人监听" ≠ "它是代理"。
    本机 8080 就有服务在听，但它不是 HTTP 代理，穿过去请求会被直接断开。
    所以这里的每一次发现，都必须通过"真发一个请求穿过去"的验证。

探测顺序：
    1. 环境变量 HTTPS_PROXY / HTTP_PROXY
    2. Windows 注册表的系统代理设置
    3. 常见代理端口扫描（应对"代理开着但没设系统代理"）
    每一步都：先测端口能否连接，再验证能否真正代理出网。
"""

import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

# winreg 是 Windows 专用模块，在 Linux（GitHub Actions 的 ubuntu runner）上不存在。
# 顶层直接 import 会让程序在那边一启动就 ImportError 崩溃，所以必须容错。
try:
    import winreg
except ImportError:        # 非 Windows 平台
    winreg = None

# 常见代理工具的默认端口：Clash Verge / Clash / v2ray / SS / 通用
COMMON_PORTS = [7897, 7890, 7891, 10808, 10809, 1080, 2080, 20171, 8080]

CONNECT_TIMEOUT = 0.6      # 端口连接测试超时（秒）
VERIFY_TIMEOUT = 5         # 代理可用性验证超时（秒）

# 用来验证代理能否出网的地址。gstatic 的 204 端点最轻量。
VERIFY_URLS = [
    "http://www.gstatic.com/generate_204",
    "https://www.google.com",
    "http://example.com",
]


@dataclass
class ProxyStatus:
    available: bool
    host: str = ""
    port: int = 0
    source: str = ""       # env / registry / scan / none
    note: str = ""         # 给人看的说明

    def url(self) -> str:
        return f"http://{self.host}:{self.port}" if self.available else ""

    def __str__(self) -> str:
        return self.note


def _from_env() -> str | None:
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        val = os.environ.get(key)
        if val:
            return val
    return None


def _from_registry() -> str | None:
    """读 Windows 系统代理设置。ProxyEnable=0 表示未启用。"""
    if winreg is None:                 # 非 Windows 平台直接跳过
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enabled:
            return None
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
        return server or None
    except OSError:
        return None


def parse_proxy(value: str) -> tuple[str, int] | None:
    """把代理字符串解析成 (host, port)。

        '127.0.0.1:7897'                      → ('127.0.0.1', 7897)
        'http=127.0.0.1:7897;https=...'       → ('127.0.0.1', 7897)
    """
    if not value:
        return None
    value = value.strip()

    if "=" in value:
        picked = None
        for part in value.split(";"):
            if part.lower().startswith("http="):
                picked = part.split("=", 1)[1]
                break
        if picked is None:
            picked = value.split(";")[0].split("=", 1)[-1]
        value = picked

    value = value.replace("http://", "").replace("https://", "").strip("/")
    value = value.split("/")[0]

    if ":" in value:
        host, port = value.rsplit(":", 1)
        try:
            return host, int(port)
        except ValueError:
            return None
    return value, 80


def is_alive(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> bool:
    """TCP 连一下。只说明有服务在听，不说明它是代理。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def verify_proxy(host: str, port: int, timeout: int = VERIFY_TIMEOUT) -> tuple[bool, str]:
    """真的穿过代理发请求，确认它能出网。这是唯一可信的判断。"""
    proxy = f"http://{host}:{port}"
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    opener = urllib.request.build_opener(handler)

    last = "无响应"
    for url in VERIFY_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=timeout) as resp:
                resp.read(64)
                return True, f"{url} → HTTP {resp.status}"
        except Exception as e:
            last = f"{type(e).__name__}"
    return False, last


def _scan_ports(exclude_port: int | None = None) -> tuple[str, int] | None:
    """扫常见端口，返回第一个"既是监听中、又通过代理验证"的。"""
    for port in COMMON_PORTS:
        if port == exclude_port:
            continue
        if not is_alive("127.0.0.1", port):
            continue
        ok, _ = verify_proxy("127.0.0.1", port)
        if ok:
            return "127.0.0.1", port
    return None


def detect(scan: bool = True) -> ProxyStatus:
    """探测代理状态。这是"国内为主 / 国内+英文"自动切换的开关。"""

    # --- 1. 环境变量 ---
    raw = _from_env()
    if raw:
        parsed = parse_proxy(raw)
        if parsed:
            host, port = parsed
            if is_alive(host, port):
                ok, detail = verify_proxy(host, port)
                if ok:
                    return ProxyStatus(True, host, port, "env",
                                       f"代理可用（环境变量 {host}:{port}，{detail}）")
            return ProxyStatus(False, host, port, "env",
                               f"环境变量配了代理 {host}:{port}，但无法使用")

    # --- 2. 注册表（Windows 系统代理）---
    raw = _from_registry()
    if raw:
        parsed = parse_proxy(raw)
        if parsed:
            host, port = parsed
            if is_alive(host, port):
                ok, detail = verify_proxy(host, port)
                if ok:
                    return ProxyStatus(True, host, port, "registry",
                                       f"代理可用（系统代理 {host}:{port}，{detail}）")
            # 这就是你的常见情况：配置了但没运行
            found = _scan_ports(exclude_port=port) if scan else None
            if found:
                h, p = found
                return ProxyStatus(True, h, p, "scan",
                                   f"系统代理 {host}:{port} 不可用，但扫描到 {h}:{p} 可用")
            return ProxyStatus(False, host, port, "registry",
                               f"系统代理配了 {host}:{port}，但该端口无响应（代理软件没开？）")

    # --- 3. 扫常见端口 ---
    if scan:
        found = _scan_ports()
        if found:
            h, p = found
            return ProxyStatus(True, h, p, "scan",
                               f"未配置系统代理，但扫描到 {h}:{p} 是可用代理")

    return ProxyStatus(False, source="none",
                       note="未发现可用代理 → 直连模式，只用国内源")


if __name__ == "__main__":
    status = detect()
    print(f"代理状态: {status}")
    print(f"  available = {status.available}")
    print(f"  source    = {status.source}")
    if status.available:
        print(f"  url       = {status.url()}")
