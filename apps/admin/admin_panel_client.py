# -*- coding: utf-8 -*-
"""
Sky 管理面板 · 客户端（更新接收版 · 单文件可替换）
=====================================================

用法（客户端机器）：
    1. 把本文件改名为 admin_panel.py；
    2. 覆盖掉客户端机器上原来的 admin_panel.py；
    3. 双击 / 运行 python admin_panel.py。

本文件已经把面板基类、推送接收、状态胶囊、确认更新、热更新全部打包在一起，
不再依赖外部的 admin_panel.py；同目录仍需要原来面板用到的
game_control_panel.py / api_response_manager.py 等文件。

特性：
  * 内置 HTTP 服务（默认 9527 端口），可直接用浏览器访问 HTML 主页
  * 右上角版本状态胶囊：未连接 → 资源检测中 → 有新版本 → 已是最新
  * 服务端推送后弹窗列出「接口名 + 大小」，点确定才会替换源码并热更新
  * 替换前自动备份（.skybak-时间戳），Python 源码先做语法编译检查

协议：sky-push/1（与作为服务端的 admin_panel.py 配套）
"""
from __future__ import annotations

import os
import sys
import json
import time
import socket
import sqlite3
import threading
import subprocess
import re
import datetime
import urllib.request
import ast
import hashlib
import ssl
import uuid
import webbrowser
import urllib.error
import urllib.parse
from game_control_panel import build_game_control_tab
from api_response_manager import ApiResponseManager
try:
    from other_control_panel import build_other_tab
except Exception:
    build_other_tab = None
try:
    from magic_control_panel import build_magic_tab
except Exception:
    build_magic_tab = None
try:
    # 纸船 / 留影 / 空间(社交动态 UGC)控制页
    from ugc_control_panel import build_ugc_tab
except Exception:
    build_ugc_tab = None
try:
    # 好友控制(自由加/删两个账号之间的好友; 双向同步 + 推送刷新)
    from friend_control_panel import build_friend_control_tab
except Exception:
    build_friend_control_tab = None
try:
    import ddns_updater
except Exception:
    ddns_updater = None

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import collections
import hmac
import importlib
import queue
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ============================================================
# 常量配置
# ============================================================
PANEL_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_config.json")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
QUICK_PATH = os.path.join(PROJECT_ROOT, "services", "game-api")
SERVER_PORT = 8443
FIREWALL_RULE = "SkyPrivateServer"
ADMIN_PASSWORD = "admin"          # 删除用户所需管理员密码
MAX_LOG_LINES = 5000
LOG_FILE = "H:/sky_server.log"  # 日志持续输出文件
def _cloudflared_path():
    """优先用与本文件同目录的 cloudflared.exe（随客户端一起分发）。"""
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "cloudflared.exe")
    if os.path.isfile(local):
        return local
    return os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "cloudflared", "cloudflared.exe")


CLOUDFLARED_PATH = _cloudflared_path()

# UDP(xysky)联机服务器源码目录: 项目 services/realtime-server。
# 私服启停时一并启停它; 入口 src/main.js, 依赖已随源码一起放在该目录。
UDP_SRC_DIR = os.path.join(PROJECT_ROOT, "services", "realtime-server")
UDP_ENTRY = os.path.join("src", "main.js")
UDP_HTTP_PORT = 8080  # xysky HTTP 接口端口(查看器与就绪探测用)

# 源码目录缺少 sky_boot.py 时自动补一份引导脚本（旧源码快照只有 index.py）。
SKY_BOOT_TEMPLATE = '''# -*- coding: utf-8 -*-
"""sky_boot.py —— 启动私服(index.py)的引导脚本(2026-10-05 新增)。

为什么需要它(本机实测): 这台机器上 WMI 查询不返回。而 sqlalchemy 被 import 时会调
platform.machine() -> platform.uname() -> platform.win32_ver() -> _win32_ver()
-> _wmi_query(); _wmi_query 用的是 CPython 自带的 _wmi C 扩展(COM 查询), WMI 不响应时
它**永远不返回**(C 层阻塞, 连超时都用不上)。后果:
    * 手动 `python -u index.py` -> 不打印任何东西、不监听端口, 就那样挂着;
    * 面板点"启动" -> 只看到"启动中", 永远等不到 SKY_LISTEN_PORT;
    * find_python.ps1 / 面板的导入测试也一样挂住(bat 会停在"正在查找可用的 Python 解释器...")。

这里的做法: 在 import sqlalchemy **之前**, 把 platform 的三个 WMI 入口换成不查 WMI 的实现
(值取自 sys.getwindowsversion() 和 PROCESSOR_ARCHITECTURE 环境变量; 私服不依赖精确的
Windows 版本号, 没有任何副作用), 然后原样跑 index.py。

用法(管理面板已改成用它启动; 手动启动完全一样, 环境变量照旧生效):
    python -u sky_boot.py
(工作目录无所谓, 里面会自己切到本文件所在的源码根目录)
"""
import os
import platform
import sys

_win = sys.getwindowsversion()
_ver = "%d.%d.%d" % (_win.major, _win.minor, _win.build)
platform.machine = lambda: (os.environ.get("PROCESSOR_ARCHITECTURE") or "AMD64")
platform.processor = lambda: platform.machine()
platform.win32_ver = lambda *a, **k: (_ver, _ver, "", "Multiprocessor Free")

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.chdir(HERE)
sys.argv = ["index.py"]

import runpy

runpy.run_path(os.path.join(HERE, "index.py"), run_name="__main__")'''

# 封号预设时长(秒)，None 表示永久
BAN_PRESETS = [
    ("1 小时", 3600),
    ("6 小时", 6 * 3600),
    ("12 小时", 12 * 3600),
    ("1 天", 24 * 3600),
    ("3 天", 3 * 24 * 3600),
    ("7 天", 7 * 24 * 3600),
    ("30 天", 30 * 24 * 3600),
    ("永久", None),
]

# 删除用户时级联清理的关联表 (表名, 关联列名 列表)
CASCADE_TABLES = [
    ("friend_status",       ["user_id", "friend_id"]),
    ("friendship",          ["user_id", "friend_id"]),
    ("invite",              ["inviter_user_id", "friend_id"]),
    ("user_online_status",  ["user_id"]),
    ("user_initialization", ["user_id"]),
    ("message",             ["user_id"]),
    ("social_feed_post",    ["user_id"]),
    ("social_feed_like",    ["user_id"]),
    ("collectible",         ["user_id"]),
    ("user_currency",       ["user_id"]),
    ("user_wing_buff",      ["user_id"]),
    ("user_outfit",         ["user_id"]),
    ("user_consumable",     ["user_id"]),
    ("user_buff",           ["user_id"]),
]

# 日志关键字 -> 级别
LOG_KEYWORDS = [
    ("error",   ["error", "exception", "traceback", "failed", "错误", "异常"]),
    ("warn",    ["warn", "warning", "警告"]),
    ("success", ["success", "started", "running", "ok", "成功", "ready"]),
]

# 颜色主题(仿 VS Code 暗色)
LOG_BG       = "#1e1e1e"
LOG_FG       = "#d4d4d4"
COLOR_INFO   = "#569cd6"
COLOR_WARN   = "#d7ba7d"
COLOR_ERROR  = "#f44747"
COLOR_OK     = "#6a9955"
COLOR_TS     = "#808080"

# ============================================================
# 配置持久化
# ============================================================
def load_panel_config():
    try:
        with open(PANEL_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_panel_config(cfg):
    try:
        with open(PANEL_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ============================================================
# 网络工具
# ============================================================
def get_lan_ips():
    """返回 (主IPv4, [全部IPv4], [全部IPv6])"""
    ipv4_list, ipv6_list = [], []
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None):
            family, _, _, _, sockaddr = info
            addr = sockaddr[0]
            if family == socket.AF_INET and addr not in ipv4_list:
                ipv4_list.append(addr)
            elif family == socket.AF_INET6 and addr not in ipv6_list:
                ipv6_list.append(addr.split("%")[0])
    except Exception:
        pass
    primary = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
        s.close()
        if primary not in ipv4_list:
            ipv4_list.insert(0, primary)
    except Exception:
        pass
    return primary, ipv4_list, ipv6_list

# 多个公网 IP 查询端点(含国内可用源)，按顺序尝试直到成功
PUBLIC_IP_ENDPOINTS = [
    "https://www.taobao.com/help/getip.php",   # 淘宝(国内快)
    "https://myip.ipip.net",                   # ipip.net(国内)
    "http://ip-api.com/line/?fields=query",    # ip-api 纯文本
    "https://api.ipify.org",                   # ipify(国外)
    "https://ifconfig.me/ip",                  # ifconfig(国外)
    "https://ipinfo.io/ip",                    # ipinfo(国外)
    "http://ip.3322.net",                      # 3322(国内)
]

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

def get_public_ip():
    """依次尝试多个端点获取公网 IPv4，任一成功即返回。

    - 关闭 SSL 证书校验，避免自签/缺证书环境报错
    - 用正则从任意响应文本中提取首个合法 IPv4
    - 单个端点超时 5 秒，全部失败才返回 None
    """
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for url in PUBLIC_IP_ENDPOINTS:
        try:
            req = urllib.request.Request(url, headers=headers)
            kwargs = {"timeout": 5}
            if url.lower().startswith("https"):
                kwargs["context"] = ctx
            with urllib.request.urlopen(req, **kwargs) as r:
                raw = r.read().decode("utf-8", errors="replace")
            m = _IPV4_RE.search(raw)
            if m:
                ip = m.group(0)
                # 过滤内网/无效地址
                if not (ip.startswith(("10.", "192.168.", "127.", "0."))
                        or ip == "255.255.255.255"):
                    return ip
        except Exception:
            continue
    return None

# ============================================================
# 环境配置: 端口 / 证书 / 防火墙 / config.json
# ============================================================
def kill_port(port, log):
    """释放占用端口的进程"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, shell=True,
            encoding="utf-8", errors="replace"
        ).stdout
        pids = set()
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts:
                    pids.add(parts[-1])
        for pid in pids:
            if pid and pid != "0":
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, shell=True)
                log(f"已释放端口 {port} (PID {pid})", "success")
        if not pids:
            log(f"端口 {port} 空闲", "info")
    except Exception as e:
        log(f"端口释放失败: {e}", "warn")

def ensure_ssl_cert(root, log):
    """用 cryptography 生成自签名证书(10年)"""
    cert_dir = os.path.join(root, "var", "certs")
    os.makedirs(cert_dir, exist_ok=True)
    key_path = os.path.join(cert_dir, "privkey.pem")
    cert_path = os.path.join(cert_dir, "fullchain.pem")
    if os.path.exists(key_path) and os.path.exists(cert_path):
        log("SSL 证书已存在", "info")
        return
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        host = socket.gethostname()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"SkyPrivateServer"),
        ])
        # 构建 SAN: DNS + IP
        import ipaddress
        from cryptography.x509 import IPAddress
        san_entries = [
            x509.DNSName(u"localhost"),
            x509.DNSName(host),
        ]
        # 添加本地回环
        san_entries.append(IPAddress(ipaddress.IPv4Address("127.0.0.1")))
        # 添加本机所有 IPv4 地址
        try:
            primary_ip, _, _ = get_lan_ips()
            if primary_ip and primary_ip != "127.0.0.1":
                san_entries.append(IPAddress(ipaddress.IPv4Address(primary_ip)))
        except:
            pass
        san = x509.SubjectAlternativeName(san_entries)
        now = datetime.datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(san, critical=False)
            .sign(key, hashes.SHA256())
        )
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()))
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        log("已生成自签名 SSL 证书(SAN含本机IP, 有效期10年)", "success")
    except Exception as e:
        log(f"证书生成失败: {e}", "error")

def ensure_firewall(log, port=None):
    """配置 Windows 防火墙入站规则。

    不解析 netsh 的本地化文本输出(中文系统为 GBK，易触发解码错误)，
    改为「先删除同名旧规则、再重新添加」的幂等做法：
      * 全程不使用 text=True，按字节读取，避免 UnicodeDecodeError
      * 仅依据进程返回码判断结果，与系统语言无关
    多开支持: 规则名带端口区分(各实例互不覆盖)。
    """
    port = port or SERVER_PORT
    rule = f"{FIREWALL_RULE}_{port}"
    try:
        # 先删除可能存在的同名规则(不存在时返回非零，忽略即可)
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             f"name={rule}"],
            capture_output=True, shell=True
        )
        # 重新添加入站放行规则
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule}", "dir=in", "action=allow",
             "protocol=TCP", f"localport={port}"],
            capture_output=True, shell=True
        )
        if r.returncode == 0:
            log(f"已配置防火墙规则 {rule} (TCP {port})", "success")
        else:
            log("防火墙规则配置失败(可能需要以管理员身份运行)", "warn")
    except Exception as e:
        log(f"防火墙配置失败: {e}", "warn")


def ensure_firewall_ipv6(log_cb, port=None):
    """为 IPv6 地址添加 Windows 防火墙入站规则。多开支持: 规则名带端口区分。"""
    port = port or SERVER_PORT
    rule = f"{FIREWALL_RULE}_{port}_IPv6"
    try:
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             f"name={rule}"],
            capture_output=True, shell=True
        )
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule}", "dir=in", "action=allow",
             "protocol=TCP", f"localport={port}"],
            capture_output=True, shell=True
        )
        if r.returncode == 0:
            log_cb("IPv6 防火墙规则已添加 (TCP %d)" % port, "success")
        else:
            log_cb("IPv6 防火墙配置失败(可能需要以管理员身份运行)", "warn")
    except Exception as e:
        log_cb("IPv6 防火墙异常: %s" % e, "warn")


def ensure_firewall_udp(log_cb, port):
    """放行 UDP 入站——联机(ENet/UDP)走 UDP, 上面的规则只放行了 TCP。
    规则名带端口, 多开互不覆盖。"""
    rule = f"{FIREWALL_RULE}_UDP_{port}"
    try:
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             f"name={rule}"], capture_output=True, shell=True)
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule}", "dir=in", "action=allow",
             "protocol=UDP", f"localport={port}"],
            capture_output=True, shell=True)
        if r.returncode == 0:
            log_cb(f"已配置防火墙规则 {rule} (UDP {port})", "success")
        else:
            log_cb("UDP 防火墙规则配置失败(可能需要以管理员身份运行)", "warn")
    except Exception as e:
        log_cb(f"UDP 防火墙配置失败: {e}", "warn")


def setup_upnp(port, log_cb):
    """通过 UPnP 自动映射端口，实现内网穿透。
    返回映射的公网端口号，失败返回 None。"""
    try:
        import subprocess, socket
        # 获取本机局域网 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()

        # 先删除可能存在的旧映射
        subprocess.run(
            ["upnpc", "-d", str(port), "TCP"],
            capture_output=True, shell=True, timeout=8
        )
        # 添加新映射
        r = subprocess.run(
            ["upnpc", "-a", local_ip, str(port), str(port), "TCP"],
            capture_output=True, text=True, shell=True, timeout=10
        )
        if r.returncode == 0 and "success" in r.stdout.lower():
            log_cb("UPnP 端口映射成功: %s -> %s:%s (TCP)" % (local_ip, local_ip, port), "success")
            return port
        else:
            log_cb("UPnP 映射失败，请检查路由器是否支持 UPnP 并已开启", "warn")
            if r.stdout:
                log_cb("UPnP 响应: %s" % r.stdout.strip()[:200], "info")
            return None
    except FileNotFoundError:
        log_cb("upnpc 命令不可用，跳过 UPnP 映射", "warn")
        return None
    except Exception as e:
        log_cb("UPnP 设置异常: %s" % e, "warn")
        return None


def remove_upnp(port, log_cb):
    """移除 UPnP 端口映射"""
    try:
        subprocess.run(
            ["upnpc", "-d", str(port), "TCP"],
            capture_output=True, shell=True, timeout=8
        )
        log_cb("已移除 UPnP 端口映射 %s" % port, "info")
    except Exception:
        pass


def update_server_config(root, ip, use_https, log, port=None):
    """更新源码 config.json 指向本机地址。多开支持: 端口可传入。"""
    port = port or SERVER_PORT
    cfg_path = os.path.join(root, "config", "server.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        proto = "https" if use_https else "http"
        base = f"{proto}://{ip}:{port}"
        cfg["app_store_uri"] = base
        cfg["tos_url"] = f"{base}/tos"
        cfg["pp_url"] = f"{base}/pp"
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        log(f"已更新 config.json 地址 -> {base}", "success")
    except Exception as e:
        log(f"config.json 更新失败: {e}", "warn")

# ============================================================
# 子进程解释器(启动私服时该用哪个 python.exe)
# ============================================================
# 2026-09-19 修: 以前直接拿 sys.executable 去 subprocess.Popen(index.py)。但商店版
# (MSIX)Python 的 sys.executable 其实是一条**应用执行别名**(App Execution Alias):
#   C:\Users\<你>\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_*\python.exe
# 而**管理员权限的进程无法通过别名启动商店应用** —— 子进程只会打印
#   Python was not found; run without arguments to install from the Microsoft Store, ...
# 并返回 9009, 于是"以管理员身份启动主控"时私服根本起不来(面板只看到一个秒退的子进程)。
# 所以这里先解析出**真实解释器路径**再启动子进程。启动脚本(启动主控_管理员.bat +
# find_python.ps1)已经把解析结果放进环境变量 SKY_PYTHON; 这里另带一套兜底解析,
# 保证不管用什么方式打开面板都能用对解释器。
_SERVER_PY_CACHE = None


def _is_windowsapps_alias(path):
    """路径是否落在"应用执行别名"目录(%LOCALAPPDATA%\\Microsoft\\WindowsApps\\...)。

    提权后的进程不能经别名启动商店应用, 所以这种路径要尽量避开(只作最后兜底)。
    """
    p = (path or "").replace("/", "\\").lower()
    return "\\microsoft\\windowsapps\\" in p


def _python_has_server_deps(exe):
    """真跑一遍导入测试: 面板/私服要的依赖都在才算可用。

    本机 %LOCALAPPDATA%\\Python\\pythoncore-3.14-64\\python.exe(Python 安装管理器装的
    3.14)里只有 pip, 少 flask/eventlet, 必须被剔除, 否则私服一起来就崩。

    2026-10-05: 测试前先摘掉 platform 的 WMI 入口 —— 本机 WMI 查询不返回, 而 sqlalchemy
    被 import 时会走 platform.machine() -> uname() -> win32_ver() -> _win32_ver()
    -> _wmi_query()(CPython 自带 _wmi C 扩展的 COM 查询), 不处理的话这个测试会一直挂着
    (超时之前 bat 就停在"正在查找可用的 Python 解释器...")。同一台机器上 U盘那个 python
    安装也一样中招, 所以这里是环境问题而不是解释器问题。
    """
    probe = ("import platform,os,sys;"
             "platform.machine=lambda:(os.environ.get('PROCESSOR_ARCHITECTURE') or 'AMD64');"
             "platform.processor=lambda:platform.machine();"
             "platform.win32_ver=lambda *a, **k:('10','10.0.0','','Multiprocessor Free');"
             "import flask, flask_sqlalchemy, socketio, eventlet, tkinter, cryptography;"
             "print('SKY_PYTHON_OK')")
    try:
        r = subprocess.run(
            [exe, "-c", probe],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and b"SKY_PYTHON_OK" in (r.stdout or b"")
    except Exception:
        return False


def _store_python_candidates():
    """商店版 Python 包目录里的**真实 exe**(提权后照样能用)。

    注册表 AppModel 仓库里记着包目录, 形如:
      C:\\Program Files\\WindowsApps\\PythonSoftwareFoundation.Python.3.13_3.13.3824.0_x64__qbz5n2kfra8p0\\python3.13.exe
    (同目录的 python.exe 在部分机器上是"拒绝访问"的占位, 交给导入测试筛掉)
    """
    out = []
    try:
        import winreg
        repo = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
                r"\CurrentVersion\AppModel\Repository\Packages")
        names = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, repo) as packages:
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(packages, i))
                except OSError:
                    break
                i += 1
            for name in names:
                if not name.lower().startswith("pythonsoftwarefoundation.python.3."):
                    continue
                try:
                    with winreg.OpenKey(packages, name) as pkg:
                        root = winreg.QueryValueEx(pkg, "PackageRootFolder")[0]
                except OSError:
                    continue
                tag = ".".join(name.split("_")[0].split(".")[-2:])   # -> "3.13"
                for fn in ("python%s.exe" % tag, "python3.exe", "python.exe"):
                    p = os.path.join(root, fn)
                    if os.path.isfile(p):
                        out.append(p)
    except Exception:
        pass
    return out


def ensure_boot_script(root_dir, log=None):
    """源码目录缺少 sky_boot.py 时补一个（原面板启动私服固定执行它）。

    返回 (ok, message)。已有非空 sky_boot.py 时直接返回 (True, "")。
    这样旧版源码快照（只有 index.py、没有 sky_boot.py）也能在面板里点启动，
    不会再报 "can't open file '...sky_boot.py'" 然后瞬间退出。
    """
    root = os.path.abspath(root_dir or "")
    boot = os.path.join(root, "sky_boot.py")
    index = os.path.join(root, "index.py")
    try:
        if os.path.isfile(boot) and os.path.getsize(boot) > 0:
            return True, ""
    except OSError:
        pass
    if not os.path.isfile(index):
        return False, "源码目录缺少 index.py：%s" % root
    try:
        with open(boot, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(SKY_BOOT_TEMPLATE)
    except OSError as exc:
        return False, "源码目录缺少 sky_boot.py，自动生成失败：%s" % exc
    message = "源码目录缺少 sky_boot.py，已自动生成引导脚本（内容与新版私服一致）"
    if log is not None:
        try:
            log(message, "warn")
        except TypeError:
            log(message)
    return True, message


def resolve_server_python():
    """启动私服子进程用的解释器路径(**禁止用应用执行别名**)。结果缓存, 只解析一次。

    2026-10-05: 先看缓存文件 .sky_python.txt(上次真的用过、且导入测试通过的那个解释器,
    由 find_python.ps1 写) —— 命中就直接用, 免得每次开服都重跑一遍导入实测。
    """
    global _SERVER_PY_CACHE
    if _SERVER_PY_CACHE:
        return _SERVER_PY_CACHE
    exe = sys.executable or "python"
    candidates = []
    cached = _read_python_cache()
    if cached:
        candidates.append(cached)                     # 0) 上次成功用过的(= 缓存)
    env_py = os.environ.get("SKY_PYTHON")
    if env_py and os.path.isfile(env_py):
        candidates.append(env_py)                     # 1) 启动脚本解析好的真实路径
    if not _is_windowsapps_alias(exe) and os.path.isfile(exe):
        candidates.append(exe)                        # 2) 当前解释器本身就是真解释器
    candidates.extend(_store_python_candidates())     # 3) 商店版 Python 包目录里的真 exe
    candidates.append(exe)                            # 4) 兜底(至少不比以前更差)
    seen = set()
    for c in candidates:
        if not c or c in seen or not os.path.isfile(c):
            continue
        seen.add(c)
        if _python_has_server_deps(c):
            _SERVER_PY_CACHE = c
            _write_python_cache(c)                    # 记下来, 下次(含 bat/ps1)直接用
            return c
    _SERVER_PY_CACHE = exe
    return exe


SKY_PY_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 ".sky_python.txt")


def _read_python_cache():
    """上次成功用过的解释器路径(缓存文件里就一行; 文件没了就返回 None)。"""
    try:
        with open(SKY_PY_CACHE_FILE, "r", encoding="utf-8-sig") as f:
            path = (f.readline() or "").strip()
        return path if path and os.path.isfile(path) else None
    except Exception:
        return None


def _write_python_cache(exe):
    try:
        with open(SKY_PY_CACHE_FILE, "w", encoding="ascii") as f:
            f.write(exe)
    except Exception:
        pass


# ============================================================
# 数据库访问层
# ============================================================
class UserDB:
    """对 instance/users.db 的账号读写封装"""

    def __init__(self, root):
        self.root_dir = root
        self.path = os.path.join(root, "var", "data", "instance", "users.db")

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        # 服务器在跑时也持有这个 users.db。delete 日志模式下写写/读写互斥,
        # 面板批量 INSERT(推送全解锁/全物品)很容易撞上 "database is locked"。
        # 切到 WAL + 拉长 busy_timeout, 让面板写入与服务器读写并发不打架。
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        return conn

    def available(self):
        return os.path.exists(self.path)

    def ensure_table(self):
        """自动建表(若不存在), 包含 20 个字段"""
        sql = """
        CREATE TABLE IF NOT EXISTS user (
            id VARCHAR(36) PRIMARY KEY,
            device VARCHAR(36),
            session VARCHAR(32),
            recovery_token VARCHAR(64),
            version INTEGER,
            platform VARCHAR(255),
            is_banned INTEGER DEFAULT 0,
            ban_duration INTEGER DEFAULT 0,
            ban_start_time INTEGER DEFAULT 0,
            ban_end_time INTEGER DEFAULT 0,
            is_authorized INTEGER DEFAULT 0,
            ip_address VARCHAR(50) DEFAULT '0.0.0.0',
            created_at INTEGER DEFAULT 0,
            remark TEXT DEFAULT '',
            device_identifier VARCHAR(36) DEFAULT '',
            device_name VARCHAR(255) DEFAULT '',
            device_type VARCHAR(50) DEFAULT '',
            device_model VARCHAR(100) DEFAULT '',
            device_os VARCHAR(50) DEFAULT '',
            user_agent TEXT DEFAULT ''
        )
        """
        conn = self._conn()
        try:
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def list_users(self, keyword=""):
        conn = self._conn()
        try:
            if keyword:
                kw = f"%{keyword}%"
                cur = conn.execute(
                    """SELECT * FROM user WHERE id LIKE ? OR device_name LIKE ?
                       OR ip_address LIKE ? OR platform LIKE ?
                       ORDER BY created_at DESC""",
                    (kw, kw, kw, kw))
            else:
                cur = conn.execute("SELECT * FROM user ORDER BY created_at DESC")
            rows = [dict(r) for r in cur.fetchall()]
            # 并入最后活跃时间(user_activity 表), 供判定在线/离线
            try:
                seen = {r[0]: r[1] for r in conn.execute(
                    "SELECT user_id, last_seen FROM user_activity").fetchall()}
            except Exception:
                seen = {}
            for u in rows:
                u["last_seen"] = seen.get(u.get("id"), 0)
            return rows
        finally:
            conn.close()

    def get_user(self, uid):
        conn = self._conn()
        try:
            cur = conn.execute("SELECT * FROM user WHERE id=?", (uid,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def stats(self):
        conn = self._conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            banned = conn.execute(
                "SELECT COUNT(*) FROM user WHERE is_banned=1").fetchone()[0]
            return total, banned
        finally:
            conn.close()

    def ban_user(self, uid, duration):
        """duration=None 表示永久封禁"""
        conn = self._conn()
        try:
            now = int(time.time())
            if duration is None:
                conn.execute(
                    """UPDATE user SET is_banned=1, ban_duration=0,
                       ban_start_time=?, ban_end_time=0 WHERE id=?""",
                    (now, uid))
            else:
                conn.execute(
                    """UPDATE user SET is_banned=1, ban_duration=?,
                       ban_start_time=?, ban_end_time=? WHERE id=?""",
                    (duration, now, now + duration, uid))
            conn.commit()
        finally:
            conn.close()

    def unban_user(self, uid):
        conn = self._conn()
        try:
            conn.execute(
                """UPDATE user SET is_banned=0, ban_duration=0,
                   ban_start_time=0, ban_end_time=0 WHERE id=?""", (uid,))
            conn.commit()
        finally:
            conn.close()

    def delete_user(self, uid):
        """级联删除用户及关联表记录, 返回删除统计"""
        conn = self._conn()
        report = {}
        try:
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            for table, cols in CASCADE_TABLES:
                if table not in existing:
                    continue
                where = " OR ".join(f"{c}=?" for c in cols)
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE {where}", tuple([uid] * len(cols)))
                if cur.rowcount:
                    report[table] = cur.rowcount
            cur = conn.execute("DELETE FROM user WHERE id=?", (uid,))
            report["user"] = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return report

    def set_authorized(self, uid, authorized):
        conn = self._conn()
        try:
            conn.execute("UPDATE user SET is_authorized=? WHERE id=?",
                         (1 if authorized else 0, uid))
            conn.commit()
        finally:
            conn.close()

    def push_full_unlocks(self, uid, scope="all"):
        """把"高版本全解锁"推送给指定账号(分4类写入对应按账号表)。

        - 全动作  : config/quandongzuo.json     -> collectible
        - 开门/全图: config/kaiquantu.json       -> status_unlock (高版本状态接口)
                     再加 config/kaiquantu.json+0.6.5_unlock.json -> unlock_item(type=level,
                     老版本 <0.20.0 只认这里, 例如 0.7.1; 别漏, 否则老版本开不了图)
        - 衣服/能力: config/full_unlocks_template.json -> unlock_item
        - 全光翼  : config/all_wing_buffs.json   -> user_wing_buff
        幂等(已有跳过)。与服务器是否运行无关(面板直连同一 users.db)。
        返回 dict: {emotes, maps, unlocks, wings}。
        """
        import time as _t
        import re as _re
        cfg = os.path.join(self.root_dir, "config")
        res = {"emotes": 0, "maps": 0, "unlocks": 0, "wings": 0, "errors": []}

        def _load(name, key):
            try:
                with open(os.path.join(cfg, name), encoding="utf-8") as f:
                    d = json.load(f)
                return d.get(key, []) if isinstance(d, dict) else d
            except Exception:
                return []

        conn = self._conn()
        try:
            now = int(_t.time())
            # collectible.created_at/updated_at 在模型里是 DateTime 列(SQLAlchemy 存的是
            # "YYYY-MM-DD HH:MM:SS.ffffff" 文本)。这里以前直接写 int(now) —— 读的时候
            # SQLAlchemy 的 str_to_datetime 会抛 `fromisoformat: argument must be str`,
            # /account/get_collectibles 直接 500(玩家: "接口到某个就连接失败")。
            # 新写入一律用文本时间; 库里已有的 int 行由 models.FlexibleDateTime 兼容。
            now_str = _t.strftime("%Y-%m-%d %H:%M:%S")
            # 0) 解析该账号客户端版本(从 user_agent), 用于衣服版本分流
            client_ver = None
            try:
                _row = conn.execute(
                    "SELECT user_agent FROM user WHERE id=?", (uid,)).fetchone()
                if _row and _row[0]:
                    _m = _re.search(r"/(\d+)\.(\d+)\.(\d+)", _row[0])
                    if _m:
                        client_ver = (int(_m.group(1)), int(_m.group(2)),
                                      int(_m.group(3)))
            except Exception:
                client_ver = None
            # 1) 全动作 -> collectible
            if scope in ("all", "emotes"):
              try:
                have = {r[0] for r in conn.execute(
                    "SELECT id FROM collectible WHERE user_id=?", (uid,)).fetchall()}
                for c in _load("quandongzuo.json", "collectibles"):
                    cid = c.get("id")
                    if not cid or cid in have:
                        continue
                    conn.execute(
                        "INSERT INTO collectible (id,user_id,used,candle_space,carrying,"
                        "level,permanent_level,created_at,updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (cid, uid, 1 if c.get("used", True) else 0,
                         1 if c.get("candle_space") else 0,
                         1 if c.get("carrying") else 0,
                         c.get("level", 0), c.get("permanent_level", 0),
                         now_str, now_str))
                    have.add(cid)
                    res["emotes"] += 1
              except Exception as _e:
                res["errors"].append(f"动作: {_e}")
            # 2) 开门/全图 -> status_unlock
            if scope in ("all", "maps"):
              try:
                have = {r[0] for r in conn.execute(
                    "SELECT name FROM status_unlock WHERE user_id=?", (uid,)).fetchall()}
                for s in _load("kaiquantu.json", "status_unlocks"):
                    nm = s.get("name")
                    if not nm or nm in have:
                        continue
                    conn.execute(
                        "INSERT INTO status_unlock (user_id,name,unlock_type,ack,unlocked_at)"
                        " VALUES (?,?,?,?,?)",
                        (uid, nm, s.get("type", "level"),
                         1 if s.get("ack", True) else 0, s.get("unlocked_at", now)))
                    have.add(nm)
                    res["maps"] += 1
                # 老版本通道(<0.20.0, 如 0.7.1): 没有 /service/status 接口, 地图/门
                # 状态只能从 /account/get_unlocks 里 type=level 的条目读 -> 必须同时写
                # unlock_item。少这一步就会出现"高版本能开全部地图、0.7.1 开不了"。
                try:
                    try:
                        import sys as _sys2
                        _root2 = os.path.dirname(os.path.abspath(__file__))
                        if _root2 not in _sys2.path:
                            _sys2.path.insert(0, _root2)
                        from map_unlock_helper import load_map_unlock_names
                        _lv_names = load_map_unlock_names(self.root_dir)
                    except Exception:
                        _lv_names = [s.get("name") for s in
                                     _load("kaiquantu.json", "status_unlocks")
                                     if isinstance(s, dict) and s.get("name")]
                    _lv_have = {r[0] for r in conn.execute(
                        "SELECT name FROM unlock_item WHERE user_id=?", (uid,)).fetchall()}
                    for _nm in _lv_names:
                        if not _nm or _nm in _lv_have:
                            continue
                        conn.execute(
                            "INSERT INTO unlock_item (user_id,name,unlock_type,ack,created_at)"
                            " VALUES (?,?,?,?,?)", (uid, _nm, "level", 1, now))
                        _lv_have.add(_nm)
                        res["maps"] += 1
                except Exception as _e2:
                    res["errors"].append(f"图门(老版本): {_e2}")
              except Exception as _e:
                res["errors"].append(f"图门: {_e}")
            # 3) 衣服/能力 -> unlock_item (版本分流: 按客户端版本推送对应 OutfitDefs)
            if scope in ("all", "unlocks"):
              try:
                have = {r[0] for r in conn.execute(
                    "SELECT name FROM unlock_item WHERE user_id=?", (uid,)).fetchall()}
                if client_ver is not None:
                    try:
                        import sys as _sys
                        _root = self.root_dir
                        if _root not in _sys.path:
                            _sys.path.insert(0, _root)
                        from business.helpers import outfit_version_db as _ovd
                        _names = _ovd.chars_for_version(*client_ver)
                        _items = [{"name": n, "type": "spiritshop",
                                   "ack": True, "created_at": now} for n in _names]
                        # 并入原有非 Char 项(动作/能力/传送等)
                        _seen = set(_names)
                        for _it in _load("full_unlocks_template.json", "unlocks"):
                            _nm = _it.get("name")
                            if _nm and not str(_nm).startswith("Char") and _nm not in _seen:
                                _items.append(_it)
                                _seen.add(_nm)
                    except Exception:
                        _items = _load("full_unlocks_template.json", "unlocks")
                else:
                    _items = _load("full_unlocks_template.json", "unlocks")
                for it in _items:
                    nm = it.get("name")
                    if not nm or nm in have:
                        continue
                    conn.execute(
                        "INSERT INTO unlock_item (user_id,name,unlock_type,ack,created_at)"
                        " VALUES (?,?,?,?,?)",
                        (uid, nm, it.get("type", "spiritshop"),
                         1 if it.get("ack", True) else 0, it.get("created_at", now)))
                    have.add(nm)
                    res["unlocks"] += 1
              except Exception as _e:
                res["errors"].append(f"衣服: {_e}")
            # 4) 全光翼 -> user_wing_buff
            if scope in ("all", "wings"):
              try:
                havew = {r[0] for r in conn.execute(
                    "SELECT name FROM user_wing_buff WHERE user_id=?", (uid,)).fetchall()}
                for b in _load("all_wing_buffs.json", "wing_buffs"):
                    nm = b.get("name")
                    if not nm or nm in havew:
                        continue
                    conn.execute(
                        "INSERT INTO user_wing_buff (user_id,name,collected,deposited,"
                        "last_conversion,deposit_id,created_at,updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (uid, nm, 1, 1 if b.get("deposited") else 0,
                         b.get("last_conversion", 0), b.get("deposit_id", 0), now, now))
                    havew.add(nm)
                    res["wings"] += 1
              except Exception as _e:
                res["errors"].append(f"光翼: {_e}")
            conn.commit()
        finally:
            conn.close()
        return res

    def push_all_items(self, uid):
        """全物品: 解锁数据库/配置目录里所有"服饰道具"(Char 开头即衣服/道具)。

        扫描 config 目录全部 json, 收集所有以 Char 开头的物品名(发型/身体/面具/
        斗篷/项链/道具等全部外观), 去重后写入该账号的 unlock_item 表。已有的跳过。
        与"推送全解锁"不同: 这里穷尽所有版本配置里出现过的 Char* 外观, 一件不漏。
        返回 dict: {added, total, errors}。
        """
        import time as _t
        import glob as _glob
        import re as _re
        cfg = os.path.join(self.root_dir, "config")
        res = {"added": 0, "total": 0, "errors": []}
        char_re = _re.compile(r'Char[A-Za-z0-9_]+')

        # 1) 从所有配置文件里收集 Char* 外观名(穷尽全版本)。
        names = {}  # name -> (type, ack)
        for fp in _glob.glob(os.path.join(cfg, "*.json")):
            try:
                with open(fp, encoding="utf-8") as f:
                    txt = f.read()
            except Exception as e:
                res["errors"].append(f"{os.path.basename(fp)}: {e}")
                continue
            # 先尝试结构化解析, 拿到每件衣服的 type/ack; 拿不到就用正则兜底。
            try:
                d = json.loads(txt)
                items = d.get("unlocks", []) if isinstance(d, dict) else (
                    d if isinstance(d, list) else [])
            except Exception:
                items = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                nm = it.get("name")
                if isinstance(nm, str) and nm.startswith("Char"):
                    names.setdefault(nm, (it.get("type", "spiritshop"),
                                          1 if it.get("ack", True) else 0))
            # 正则兜底: 把文件里出现过、但上面没结构化解析到的 Char* 也收进来。
            for nm in char_re.findall(txt):
                names.setdefault(nm, ("spiritshop", 1))

        res["total"] = len(names)
        if not names:
            res["errors"].append("配置目录里没有找到任何 Char* 服饰")
            return res

        # 2) 写入该账号 unlock_item(幂等, 已有跳过)。
        conn = self._conn()
        try:
            now = int(_t.time())
            have = {r[0] for r in conn.execute(
                "SELECT name FROM unlock_item WHERE user_id=?", (uid,)).fetchall()}
            for nm, (typ, ack) in names.items():
                if nm in have:
                    continue
                try:
                    conn.execute(
                        "INSERT INTO unlock_item (user_id,name,unlock_type,ack,created_at)"
                        " VALUES (?,?,?,?,?)", (uid, nm, typ, ack, now))
                    have.add(nm)
                    res["added"] += 1
                except Exception as e:
                    res["errors"].append(f"{nm}: {e}")
            conn.commit()
        finally:
            conn.close()
        return res


    def transfer_user(self, source_uid, target_uid):
        """将 source 的所有数据转移到 target，覆盖 target 数据，删除 source"""
        conn = self._conn()
        try:
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            for table, cols in CASCADE_TABLES:
                if table not in existing:
                    continue
                for col in cols:
                    try:
                        conn.execute(f"DELETE FROM {table} WHERE {col}=?", (target_uid,))
                    except:
                        pass
                    try:
                        conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?",
                                     (target_uid, source_uid))
                    except:
                        pass
            conn.execute("DELETE FROM user WHERE id=?", (source_uid,))
            conn.commit()
        finally:
            conn.close()

    # ---- 用户数据浏览器 ----
    _USER_COLUMNS = {"user_id", "friend_id", "inviter_user_id", "id", "uid"}

    def get_user_tables_data(self, uid):
        """扫描数据库中所有可能关联该用户的表，返回 {表名: [(列名列表), [行数据列表]]}"""
        conn = self._conn()
        result = {}
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for table in tables:
                cols_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
                col_names = [c[1] for c in cols_info]
                match_cols = [c for c in col_names if c.lower() in self._USER_COLUMNS]
                if not match_cols:
                    continue
                rows = []
                for col in match_cols:
                    try:
                        cur = conn.execute(
                            f"SELECT * FROM {table} WHERE {col}=?", (uid,))
                        for r in cur.fetchall():
                            row = dict(r)
                            row["_match_column"] = col
                            if row not in rows:
                                rows.append(row)
                    except Exception:
                        pass
                if rows:
                    result[table] = (col_names, rows)
        finally:
            conn.close()
        return result

    def update_table_row(self, table, uid, col, row_data):
        """更新表中匹配 uid 的某一行，row_data 为 {列名: 新值}"""
        conn = self._conn()
        try:
            sets = ", ".join(f"{k}=?" for k in row_data.keys())
            values = list(row_data.values()) + [uid]
            conn.execute(f"UPDATE {table} SET {sets} WHERE {col}=?", values)
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()


# ============================================================
# 源码目录选择对话框
# ============================================================
class DirSelectDialog(tk.Toplevel):
    def __init__(self, master, default=""):
        super().__init__(master)
        self.configure()
        self.title("选择私服源码目录")
        self.resizable(False, False)
        self.result = None
        self.grab_set()

        tk.Label(self, text="请选择包含 index.py 的源码根目录:",
                 font=("微软雅黑", 10)).grid(row=0, column=0, columnspan=3,
                                              padx=12, pady=(12, 6), sticky="w")
        self.var = tk.StringVar(value=default)
        ent = tk.Entry(self, textvariable=self.var, width=46)
        ent.grid(row=1, column=0, padx=(12, 4), pady=4)
        tk.Button(self, text="浏览...", command=self._browse).grid(
            row=1, column=1, padx=4, pady=4)
        tk.Button(self, text=f"快捷路径", command=self._quick).grid(
            row=1, column=2, padx=(4, 12), pady=4)

        bar = tk.Frame(self)
        bar.grid(row=2, column=0, columnspan=3, pady=(8, 12))
        tk.Button(bar, text="确定", width=10, command=self._ok).pack(
            side="left", padx=6)
        tk.Button(bar, text="退出", width=10, command=self._cancel).pack(
            side="left", padx=6)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        self._center()

    def _center(self):
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"+{x}+{y}")

    def _browse(self):
        d = filedialog.askdirectory(title="选择源码目录")
        if d:
            self.var.set(d)

    def _quick(self):
        self.var.set(QUICK_PATH)

    def _ok(self):
        path = self.var.get().strip()
        if not path or not os.path.isfile(os.path.join(path, "index.py")):
            messagebox.showerror("错误", "该目录下未找到 index.py，请重新选择！",
                                  parent=self)
            return
        self.result = path
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

# ============================================================
# 主面板
# ============================================================

# 数据表 -> 中文接口/数据名 对照(用户信息窗口左侧显示)
TABLE_CN_NAMES = {
    "achievement": "成就",
    "achievement_def": "成就定义",
    "achievement_stat": "成就统计",
    "achievement_stats": "成就统计(汇总)",
    "app_badge": "应用角标",
    "collectible": "收藏品",
    "constellation_friend_page": "星盘好友页",
    "consumable_def": "消耗品定义",
    "default_unlock_config": "默认解锁配置",
    "event_currency_def": "活动货币定义",
    "event_schedule": "活动日程",
    "external_account_friend": "外部平台好友",
    "follow_relationship": "关注关系",
    "forge_rate": "锻造汇率",
    "free_gift": "免费礼物",
    "friend_status": "好友状态",
    "friendship": "好友关系",
    "game_server": "游戏服务器",
    "generic_shop_def": "通用商店定义",
    "iap_product": "内购商品",
    "infraction": "违规记录",
    "invite": "邀请",
    "level_pickup": "关卡掉落物",
    "lootbox": "宝箱",
    "map_definition": "地图定义",
    "message": "消息",
    "player_quest": "玩家任务",
    "quest_stat": "任务统计",
    "resource_token": "资源令牌",
    "season_quest": "季节任务",
    "season_quest_reward": "季节任务奖励",
    "season_unlock": "季节解锁",
    "serendipity_match": "奇遇匹配",
    "server_assignment": "服务器分配",
    "shop_cost_currency": "商店货币花费",
    "shop_item": "商店物品",
    "shop_unlock_requirement": "商店解锁条件",
    "social_feed_like": "动态点赞",
    "social_feed_post": "动态发布",
    "sqlite_sequence": "(SQLite内部序列)",
    "sqlite_stat1": "(SQLite内部统计)",
    "stage": "舞台",
    "star_tag_def": "星标标签定义",
    "star_tag_link": "星标标签关联",
    "status_unlock": "状态解锁",
    "survey": "调查问卷",
    "trust_activity_def": "信任活动定义",
    "trust_group_def": "信任组定义",
    "unlock_item": "解锁项(动作/外观)",
    "user": "用户主信息",
    "user_buff": "用户增益",
    "user_checkpoint": "用户存档点",
    "user_checkpoints": "用户存档点(汇总)",
    "user_consumable": "用户消耗品",
    "user_currency": "用户货币",
    "user_initialization": "用户初始化标记",
    "user_online_status": "用户在线状态",
    "user_outfit": "用户装扮",
    "user_pickup": "用户掉落物",
    "user_rank": "用户排名",
    "user_survey": "用户问卷",
    "user_trust_group": "用户信任组",
    "user_unlocks": "用户解锁(整段)",
    "user_wing_buff": "用户光翼(翼之净化)",
    "users": "用户(旧表)",
    "version": "版本",
    "video_definition": "视频定义",
    "video_schedule": "视频日程",
}

class UserDataEditor(tk.Toplevel):
    """显示指定用户在数据库所有表中的数据，左侧列表右侧编辑"""

    def __init__(self, master, root_dir, uid):
        super().__init__(master)
        self.root_dir = root_dir
        self.uid = uid
        self.db_path = os.path.join(root_dir, "var", "data", "instance", "users.db")
        self.current_table = None
        self.current_rows = []

        self.title(f"用户数据 - {uid}")
        self.geometry("1200x700")
        self.minsize(900, 500)

        self._build_ui()
        self._load_tables()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _build_ui(self):
        # 顶部信息栏
        top = tk.Frame(self, height=32)
        top.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(top, text=f"用户ID: {self.uid}", font=("Consolas", 11, "bold")).pack(side=tk.LEFT)
        tk.Button(top, text="刷新", command=self._load_tables).pack(side=tk.RIGHT, padx=4)
        tk.Button(top, text="关闭", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        # 主体 PanedWindow
        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=4)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 左侧：表/接口列表（带中文名）
        left = tk.Frame(paned)
        paned.add(left, minsize=200, width=260)

        tk.Label(left, text="接口列表 (数据表)", font=("微软雅黑", 9, "bold")).pack(anchor=tk.W, padx=4)
        list_frame = tk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.table_listbox = tk.Listbox(list_frame, font=("Consolas", 9))
        sb = ttk.Scrollbar(list_frame, command=self.table_listbox.yview)
        self.table_listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.table_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.table_listbox.bind("<<ListboxSelect>>", self._on_table_select)

        # 右侧：数据详情+编辑
        right = tk.Frame(paned)
        paned.add(right, minsize=500)

        # 右上：表信息和操作
        info_bar = tk.Frame(right)
        info_bar.pack(fill=tk.X, padx=4, pady=2)
        self.table_info_var = tk.StringVar(value="请在左侧选择一个表")
        tk.Label(info_bar, textvariable=self.table_info_var,
                 font=("微软雅黑", 9, "bold")).pack(side=tk.LEFT)
        self.btn_copy = tk.Button(info_bar, text="复制JSON", state=tk.DISABLED,
                                   command=self._copy_as_json)
        self.btn_copy.pack(side=tk.RIGHT, padx=4)
        self.btn_delete_row = tk.Button(info_bar, text="删除选中行", state=tk.DISABLED,
                                         command=self._delete_selected_row)
        self.btn_delete_row.pack(side=tk.RIGHT, padx=4)
        self.btn_save = tk.Button(info_bar, text="保存修改", state=tk.DISABLED,
                                   command=self._save_changes)
        self.btn_save.pack(side=tk.RIGHT, padx=4)

        # 右中：数据表格
        tree_frame = tk.Frame(right)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.data_tree = ttk.Treeview(tree_frame, show="headings", selectmode="browse")
        ysb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.data_tree.yview)
        xsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.data_tree.xview)
        self.data_tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.data_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.data_tree.bind("<<TreeviewSelect>>", self._on_row_select)

        # 右下：JSON 编辑区
        edit_label = tk.Frame(right)
        edit_label.pack(fill=tk.X, padx=4)
        tk.Label(edit_label, text="JSON 原数据 (选中行后可编辑修改):",
                 font=("微软雅黑", 9)).pack(side=tk.LEFT)
        self.btn_copy_field = tk.Button(edit_label, text="复制内容",
                                         command=self._copy_field_content)
        self.btn_copy_field.pack(side=tk.RIGHT, padx=4)

        edit_frame = tk.Frame(right, height=180)
        edit_frame.pack(fill=tk.BOTH, padx=4, pady=(0, 4))
        edit_frame.pack_propagate(False)

        self.edit_text = tk.Text(edit_frame, font=("Consolas", 9), wrap=tk.WORD, undo=True)
        esb = ttk.Scrollbar(edit_frame, command=self.edit_text.yview)
        self.edit_text.configure(yscrollcommand=esb.set)
        esb.pack(side=tk.RIGHT, fill=tk.Y)
        self.edit_text.pack(fill=tk.BOTH, expand=True)

        # 右键菜单
        self.ctx_menu = tk.Menu(self.edit_text, tearoff=0)
        self.ctx_menu.add_command(label="复制", command=self._copy_field_content)
        self.ctx_menu.add_command(label="全选", command=lambda: self.edit_text.tag_add("sel", "1.0", "end"))
        self.ctx_menu.add_command(label="粘贴", command=lambda: self.edit_text.event_generate("<<Paste>>"))
        self.edit_text.bind("<Button-3>", lambda e: self.ctx_menu.tk_popup(e.x_root, e.y_root))
        self.edit_text.bind("<Control-a>", lambda e: (self.edit_text.tag_add("sel", "1.0", "end"), "break")[1])


        # ???????
        bottom = tk.Frame(self)
        bottom.pack(fill=tk.X, padx=8, pady=(2, 4))
        tk.Button(bottom, text="\u5168\u52a8\u4f5c", width=14, bg="#0078d7", fg="white",
                  font=("\u5fae\u8f6f\u96c5\u9ed1", 9, "bold"),
                  command=self._unlock_all_emotes).pack(side=tk.LEFT)
        tk.Button(bottom, text="\u5f00\u5168\u95e8", width=14, bg="#e67e22", fg="white",
                  font=("\u5fae\u8f6f\u96c5\u9ed1", 9, "bold"),
                  command=self._unlock_all_levels).pack(side=tk.LEFT, padx=4)
        tk.Button(bottom, text="\u5168\u5149\u7ffc", width=14, bg="#00897b", fg="white",
                  font=("\u5fae\u8f6f\u96c5\u9ed1", 9, "bold"),
                  command=self._unlock_all_wings).pack(side=tk.LEFT, padx=4)
        tk.Button(bottom, text="\u63a5\u53e3\u54cd\u5e94\u7ba1\u7406", width=14, bg="#5a3d8a", fg="white",
                  font=("\u5fae\u8f6f\u96c5\u9ed1", 9, "bold"),
                  command=self._open_api_response).pack(side=tk.LEFT, padx=6)

        # 状态栏
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(self, textvariable=self.status_var, anchor=tk.W,
                 font=("微软雅黑", 8), fg="#666").pack(fill=tk.X, padx=8)

    def _open_api_response(self):
        """打开该用户的接口响应管理窗口"""
        try:
            from api_response_manager import ApiResponseManager
            ApiResponseManager(self, self.root_dir, self.uid)
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开接口响应管理:{e}", parent=self)

    def _unlock_all_emotes(self):
        self._set_user_override(
            "/account/get_collectibles",
            os.path.join(self.root_dir, "config", "quandongzuo.json"),
            "\u5168\u52a8\u4f5c")

    def _unlock_all_levels(self):
        """\u5f00\u5168\u95e8: \u5f00\u542f\u5168\u90e8\u5730\u56fe/\u95e8(\u53ea\u5f71\u54cd\u8be5\u7528\u6237)\u3002

        \u5fc5\u987b\u5199**\u4e24\u4e2a**\u901a\u9053, \u5c11\u4e00\u4e2a\u5c31\u4f1a\u51fa\u73b0"\u9ad8\u7248\u672c\u80fd\u5f00\u5168\u90e8\u5730\u56fe\u30010.7.1 \u5f00\u4e0d\u4e86":

          \u00b7 \u9ad8\u7248\u672c(>=0.20.0): \u5ba2\u6237\u7aef\u4f1a\u62c9 /service/status/api/v1/get_unlocks\u3002
            \u5199\u8be5\u8d26\u53f7\u7684\u63a5\u53e3\u8986\u76d6\u4f53(config/kaiquantu.json) -> \u7acb\u5373\u5168\u5f00\u56fe\u3002
          \u00b7 \u8001\u7248\u672c(0.7.1 / 0.12 \u7b49): \u90a3\u4e2a\u5e74\u4ee3\u7684\u5ba2\u6237\u7aef**\u6ca1\u6709** status \u63a5\u53e3,
            \u5730\u56fe/\u95e8\u7684\u89e3\u9501\u72b6\u6001\u6765\u81ea /account/get_unlocks \u91cc type=level \u7684\u6761\u76ee\u3002
            \u6240\u4ee5\u5fc5\u987b\u628a\u8fd9\u4e9b\u540d\u5b57\u5199\u8fdb unlock_item \u8868, \u5426\u5219\u670d\u52a1\u7aef\u6839\u672c\u6ca1\u6709\u6570\u636e\u53ef\u4e0b\u53d1
            \u2014\u2014 \u8fd9\u6b63\u662f"\u8d26\u53f7\u7ba1\u7406\u91cc\u7684\u5f00\u5168\u95e8\u5bf9 0.7.1 \u65e0\u6548"\u7684\u539f\u56e0\u3002
        """
        if not messagebox.askyesno(
                "\u5f00\u5168\u95e8",
                "\u4e3a\u8be5\u7528\u6237\u5f00\u542f\u5168\u90e8\u5730\u56fe/\u95e8?\n"
                "\u00b7 \u9ad8\u7248\u672c(\u22650.20.0): \u8986\u76d6\u72b6\u6001\u63a5\u53e3\u54cd\u5e94\n"
                "\u00b7 \u8001\u7248\u672c(0.7.1 \u7b49): \u5199\u5165 unlock_item \u5730\u56fe\u89e3\u9501\u6761\u76ee\n"
                "(\u53ea\u5f71\u54cd\u8be5\u7528\u6237)"):
            return
        # 1) \u9ad8\u7248\u672c\u901a\u9053: /service/status/api/v1/get_unlocks \u8986\u76d6\u4f53
        if not self._set_user_override(
                "/service/status/api/v1/get_unlocks",
                os.path.join(self.root_dir, "config", "kaiquantu.json"),
                "\u5168\u5f00\u95e8/\u5168\u5730\u56fe", silent=True):
            return
        # 2) \u8001\u7248\u672c\u901a\u9053: \u5730\u56fe\u89e3\u9501\u5199\u8fdb\u8be5\u8d26\u53f7\u7684 unlock_item(/account/get_unlocks \u6570\u636e\u6e90)
        added, errs = self._push_level_unlocks()
        self.status_var.set("\u5df2\u4e3a\u8be5\u7528\u6237\u5f00\u542f\u5168\u5f00\u95e8/\u5168\u5730\u56fe(\u65b0\u589e\u5730\u56fe\u89e3\u9501 %d \u9879)" % added)
        note = ("\n\n\u00b7 \u9ad8\u7248\u672c: \u72b6\u6001\u63a5\u53e3\u8986\u76d6\u5df2\u8bbe\u7f6e(\u4e0b\u6b21\u8bf7\u6c42\u751f\u6548)\n"
                "\u00b7 \u8001\u7248\u672c(0.7.1 \u7b49): \u65b0\u589e\u5730\u56fe\u89e3\u9501 %d \u9879, \u73a9\u5bb6\u91cd\u65b0\u767b\u5f55\u540e\u751f\u6548" % added)
        if errs:
            note += "\n\n\u90e8\u5206\u51fa\u9519:\n" + "\n".join(errs)
        messagebox.showinfo("\u5b8c\u6210", "\u5df2\u4e3a\u8be5\u7528\u6237\u5f00\u542f\u5168\u5f00\u95e8/\u5168\u5730\u56fe" + note, parent=self)
        self._load_tables()

    def _level_unlock_names(self):
        """"\u5168\u90e8\u5730\u56fe/\u95e8"\u7684\u89e3\u9501\u540d\u6e05\u5355(\u4fdd\u5e8f\u53bb\u91cd)\u3002

        \u6765\u6e90\u4e24\u5904\u5e76\u96c6:
          \u00b7 config/kaiquantu.json  -> status_unlocks(\u5168\u5f00\u56fe\u5feb\u7167, 625 \u4e2a\u552f\u4e00\u540d)
          \u00b7 config/0.6.5_unlock.json -> type=level \u7684\u8001\u7248\u672c\u5730\u56fe\u6807\u8bb0(3 \u4e2a\u4e0d\u5728\u4e0a\u9762\u7684)
        """
        names, seen = [], set()
        cfg = os.path.join(self.root_dir, "config")

        def _add(n):
            if n and n not in seen:
                seen.add(n)
                names.append(n)

        try:
            with open(os.path.join(cfg, "kaiquantu.json"), encoding="utf-8") as f:
                data = json.load(f) or {}
            items = data.get("status_unlocks") if isinstance(data, dict) else None
            for it in items or []:
                _add(it.get("name") if isinstance(it, dict) else it)
        except Exception:
            pass
        try:
            with open(os.path.join(cfg, "0.6.5_unlock.json"), encoding="utf-8") as f:
                d = json.load(f)
            items = d if isinstance(d, list) else (d or {}).get("unlocks", [])
            for it in items or []:
                if isinstance(it, dict) and str(it.get("type")) == "level":
                    _add(it.get("name"))
        except Exception:
            pass
        return names

    def _push_level_unlocks(self):
        """\u628a\u5168\u90e8\u5730\u56fe/\u95e8\u89e3\u9501\u5199\u8fdb\u8be5\u8d26\u53f7\u7684 unlock_item \u8868(type=level)\u3002

        \u8fd9\u662f /account/get_unlocks \u7684\u6570\u636e\u6e90: \u6240\u6709\u7248\u672c\u5ba2\u6237\u7aef(\u542b 0.7.1)\u90fd\u80fd\u8bfb\u5230;
        \u8001\u5ba2\u6237\u7aef\u6ca1\u6709 status \u63a5\u53e3, \u5168\u5730\u56fe\u5fc5\u987b\u9760\u8fd9\u91cc\u3002\u5df2\u6709\u7684\u8df3\u8fc7(\u5e42\u7b49)\u3002
        \u8fd4\u56de (\u65b0\u589e\u6761\u6570, \u9519\u8bef\u4fe1\u606f\u5217\u8868)\u3002
        """
        import sqlite3 as _sql
        import time as _t
        names = self._level_unlock_names()
        if not names:
            return 0, ["kaiquantu.json / 0.6.5_unlock.json \u91cc\u6ca1\u6709\u8bfb\u5230\u5730\u56fe\u89e3\u9501\u540d"]
        added, errs = 0, []
        conn = None
        try:
            conn = _sql.connect(self.db_path)
            have = {r[0] for r in conn.execute(
                "SELECT name FROM unlock_item WHERE user_id=?",
                (self.uid,)).fetchall()}
            now = int(_t.time())
            for nm in names:
                if nm in have:
                    continue
                conn.execute(
                    "INSERT INTO unlock_item (user_id,name,unlock_type,ack,created_at)"
                    " VALUES (?,?,?,?,?)", (self.uid, nm, "level", 1, now))
                have.add(nm)
                added += 1
            conn.commit()
        except Exception as e:
            errs.append(str(e))
        finally:
            if conn is not None:
                conn.close()
        return added, errs

    def _set_user_override(self, path, src_file, label, silent=False):
        import sqlite3 as _sql, datetime
        if not os.path.exists(src_file):
            messagebox.showerror("\u5931\u8d25", "%s \u4e0d\u5b58\u5728" % os.path.basename(src_file), parent=self)
            return False
        if not silent and not messagebox.askyesno("\u786e\u8ba4", "\u4e3a\u8be5\u7528\u6237\u5f00\u542f%s?\n(\u53ea\u5f71\u54cd\u8be5\u7528\u6237)" % label, parent=self):
            return False
        try:
            content = open(src_file, "r", encoding="utf-8").read()
            conn = _sql.connect(self.db_path)
            conn.execute("CREATE TABLE IF NOT EXISTS response_override (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT, UNIQUE(user_id, path))")
            conn.execute("INSERT INTO response_override (user_id,path,body,enabled,updated_at) VALUES (?,?,?,1,?) ON CONFLICT(user_id,path) DO UPDATE SET body=excluded.body,enabled=1,updated_at=excluded.updated_at", (self.uid, path, content, datetime.datetime.now().isoformat()))
            conn.commit(); conn.close()
        except Exception as exc:
            messagebox.showerror("\u5931\u8d25", str(exc), parent=self)
            return False
        if silent:
            # 静默模式: 由调用方自己汇总提示, 避免弹两个框
            return True
        self.status_var.set("\u5df2\u4e3a\u8be5\u7528\u6237\u5f00\u542f%s" % label)
        messagebox.showinfo("\u5b8c\u6210", "\u5df2\u4e3a\u8be5\u7528\u6237\u8bbe\u7f6e%s\u8986\u76d6\n\u4e0b\u6b21\u8bf7\u6c42\u751f\u6548" % label, parent=self)
        self._load_tables()
        return True

    def _unlock_all_wings(self):
        import json as _json, sqlite3 as _sql
        cfg = os.path.join(self.root_dir, "config", "all_wing_buffs.json")
        if not os.path.exists(cfg):
            messagebox.showerror("\u5931\u8d25", "all_wing_buffs.json \u4e0d\u5b58\u5728", parent=self)
            return
        if not messagebox.askyesno("\u786e\u8ba4", "\u4e3a\u8be5\u7528\u6237\u89e3\u9501\u5168\u90e8\u5149\u7ffc?\n(\u53ea\u5f71\u54cd\u8be5\u7528\u6237)", parent=self):
            return
        try:
            buffs = _json.load(open(cfg, encoding="utf-8")).get("wing_buffs", [])
            conn = _sql.connect(self.db_path)
            owned = set(r[0] for r in conn.execute(
                "SELECT name FROM user_wing_buff WHERE user_id=?", (self.uid,)).fetchall())
            added = 0
            for b in buffs:
                nm = b.get("name", "")
                if not nm or nm in owned:
                    continue
                conn.execute(
                    "INSERT INTO user_wing_buff (user_id,name,collected,deposited,last_conversion,deposit_id) VALUES (?,?,?,?,?,?)",
                    (self.uid, nm, 1, 0, b.get("last_conversion",0), b.get("deposit_id",0)))
                owned.add(nm); added += 1
            conn.commit(); conn.close()
        except Exception as exc:
            messagebox.showerror("\u5931\u8d25", str(exc), parent=self)
            return
        self.status_var.set("\u5df2\u89e3\u9501\u5168\u5149\u7ffc: \u65b0\u589e %d \u4e2a" % added)
        messagebox.showinfo("\u5b8c\u6210", "\u5df2\u4e3a\u8be5\u7528\u6237\u89e3\u9501\u5168\u90e8\u5149\u7ffc\n\u65b0\u589e %d \u4e2a" % added, parent=self)
        self._load_tables()

    def _load_tables(self):
        """加载所有包含该用户数据的表，左侧显示中文名"""
        self.table_listbox.delete(0, tk.END)
        self.table_listbox_indices = {}
        self.table_data = {}
        idx = 0

        conn = self._get_conn()
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]

            for table in tables:
                try:
                    cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                    uid_cols = [c for c in cols if c in ("user_id", "sender_id", "receiver_id",
                                                        "inviter_user_id", "follower_id",
                                                        "following_id", "matched_user_id",
                                                        "friend_user_id")]
                    rows = []
                    if "user_id" in cols:
                        rows = [dict(r) for r in conn.execute(
                            f"SELECT * FROM {table} WHERE user_id=?", (self.uid,)).fetchall()]
                    elif table == "user":
                        rows = [dict(r) for r in conn.execute(
                            f"SELECT * FROM {table} WHERE id=?", (self.uid,)).fetchall()]
                    elif table == "user_checkpoint":
                        rows = [dict(r) for r in conn.execute(
                            f"SELECT * FROM {table} WHERE user_id=?", (self.uid,)).fetchall()]
                    elif table == "user_unlocks":
                        rows = [dict(r) for r in conn.execute(
                            f"SELECT * FROM {table} WHERE user_id=?", (self.uid,)).fetchall()]
                    elif table == "user_pickup":
                        rows = [dict(r) for r in conn.execute(
                            f"SELECT * FROM {table} WHERE user_id=?", (self.uid,)).fetchall()]
                    else:
                        for col in uid_cols:
                            found = [dict(r) for r in conn.execute(
                                f"SELECT * FROM {table} WHERE {col}=?", (self.uid,)).fetchall()]
                            rows.extend(found)

                    # 用中文名显示
                    cn = TABLE_CN_NAMES.get(table, table)
                    if rows:
                        self.table_data[table] = rows
                        self.table_listbox.insert(tk.END, f"  {cn}  [{table}]  ({len(rows)} 条)")
                    else:
                        self.table_listbox.insert(tk.END, f"  {cn}  [{table}]  (空)")
                        self.table_listbox.itemconfig(idx, fg="#888")
                    self.table_listbox_indices[idx] = table
                    idx += 1
                except Exception:
                    continue
        finally:
            conn.close()

        self.status_var.set(f"共 {len(self.table_data)} 个包含该用户数据的表")

    def _on_table_select(self, event=None):
        sel = self.table_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        table_name = self.table_listbox_indices.get(idx)
        if not table_name or table_name not in self.table_data:
            return

        self.current_table = table_name
        self.current_rows = self.table_data[table_name]
        self._display_table_data()

    def _display_table_data(self):
        self.data_tree.delete(*self.data_tree.get_children())
        for col in self.data_tree["columns"]:
            self.data_tree.heading(col, text="")

        if not self.current_rows:
            cn = TABLE_CN_NAMES.get(self.current_table, self.current_table)
            self.table_info_var.set(f"{cn} [{self.current_table}]: 无数据")
            self.btn_save.config(state=tk.DISABLED)
            self.btn_delete_row.config(state=tk.DISABLED)
            self.btn_copy.config(state=tk.DISABLED)
            return

        cols = list(self.current_rows[0].keys())
        self.data_tree["columns"] = cols
        for c in cols:
            self.data_tree.heading(c, text=c, anchor=tk.W)
            self.data_tree.column(c, width=max(80, min(200, len(c) * 10)), anchor=tk.W)

        for i, row in enumerate(self.current_rows):
            values = []
            for c in cols:
                v = row.get(c, "")
                if v is None:
                    v = "NULL"
                else:
                    v = str(v)
                    if len(v) > 100:
                        v = v[:100] + "..."
                values.append(v)
            self.data_tree.insert("", tk.END, iid=str(i), values=values)

        cn = TABLE_CN_NAMES.get(self.current_table, self.current_table)
        self.table_info_var.set(f"{cn} [{self.current_table}]: {len(self.current_rows)} 条, {len(cols)} 字段")
        self.btn_save.config(state=tk.NORMAL)
        self.btn_delete_row.config(state=tk.NORMAL)
        self.btn_copy.config(state=tk.NORMAL)

    def _on_row_select(self, event=None):
        sel = self.data_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx >= len(self.current_rows):
            return
        row = self.current_rows[idx]
        self.edit_text.delete("1.0", tk.END)
        try:
            formatted = json.dumps(row, indent=2, ensure_ascii=False, default=str)
            self.edit_text.insert("1.0", formatted)
        except Exception:
            for k, v in row.items():
                self.edit_text.insert(tk.END, repr({k: v}))

    def _save_changes(self):
        sel = self.data_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中要修改的行", parent=self)
            return
        idx = int(sel[0])
        if idx >= len(self.current_rows):
            return

        old_row = self.current_rows[idx]
        text = self.edit_text.get("1.0", tk.END).strip()
        try:
            new_data = json.loads(text)
        except json.JSONDecodeError as e:
            messagebox.showerror("JSON 解析错误", f"请确保编辑框中是有效的 JSON:{e}", parent=self)
            return

        if not isinstance(new_data, dict):
            messagebox.showerror("错误", "数据必须是 JSON 对象", parent=self)
            return

        table = self.current_table
        cols = list(old_row.keys())
        if "id" in old_row and old_row["id"] is not None:
            where_clause = "id=?"
            where_params = [old_row["id"]]
        elif "user_id" in old_row:
            where_clause = "user_id=?"
            where_params = [old_row["user_id"]]
        else:
            messagebox.showerror("错误", "无法确定行的主键", parent=self)
            return

        set_cols = []
        set_vals = []
        for k, v in new_data.items():
            if k in cols and k not in ("id",):
                set_cols.append(f"{k}=?")
                set_vals.append(v)

        if not set_cols:
            messagebox.showinfo("提示", "无可更新的字段", parent=self)
            return

        sql = f"UPDATE {table} SET {', '.join(set_cols)} WHERE {where_clause}"
        params = set_vals + where_params

        try:
            conn = self._get_conn()
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            cn = TABLE_CN_NAMES.get(table, table)
            self.status_var.set(f"已保存: {cn} 更新 {len(set_cols)} 个字段")
            self._load_tables()
            for i in range(self.table_listbox.size()):
                if self.table_listbox_indices.get(i) == table:
                    self.table_listbox.selection_set(i)
                    self._on_table_select()
                    break
        except Exception as e:
            messagebox.showerror("保存失败", f"数据库错误:{e}", parent=self)

    def _delete_selected_row(self):
        sel = self.data_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中要删除的行", parent=self)
            return
        idx = int(sel[0])
        if idx >= len(self.current_rows):
            return
        row = self.current_rows[idx]
        table = self.current_table

        cn = TABLE_CN_NAMES.get(table, table)
        if not messagebox.askyesno("确认删除", f"确定从 {cn} [{table}] 中删除该行? 此操作不可恢复!", parent=self):
            return

        if "id" in row and row["id"] is not None:
            where = "id=?"
            params = [row["id"]]
        elif "user_id" in row:
            where = "user_id=?"
            params = [row["user_id"]]
        else:
            messagebox.showerror("错误", "无法定位行", parent=self)
            return

        try:
            conn = self._get_conn()
            conn.execute(f"DELETE FROM {table} WHERE {where}", params)
            conn.commit()
            conn.close()
            self.status_var.set(f"已删除: {cn} 中 1 行")
            self._load_tables()
        except Exception as e:
            messagebox.showerror("删除失败", str(e), parent=self)

    def _copy_as_json(self):
        if not self.current_rows:
            return
        try:
            text = json.dumps(self.current_rows, indent=2, ensure_ascii=False, default=str)
            self.clipboard_clear()
            self.clipboard_append(text)
            cn = TABLE_CN_NAMES.get(self.current_table, self.current_table)
            self.status_var.set(f"已复制 {cn} 的 {len(self.current_rows)} 条记录 JSON")
        except Exception as e:
            messagebox.showerror("复制失败", str(e), parent=self)

    def _copy_field_content(self):
        try:
            sel = self.edit_text.get("sel.first", "sel.last")
        except tk.TclError:
            sel = self.edit_text.get("1.0", tk.END).strip()
        if sel:
            self.clipboard_clear()
            self.clipboard_append(sel)
            self.status_var.set("已复制内容到剪贴板")


# ============================================================
# UDP 联机接口响应查看窗口
# ------------------------------------------------------------
# 原理与"接口请求响应查看"一致(左列表 + 右详情, 轮询取数, 彩色状态),
# 但数据源是 xysky(UDP 联机服务器)对外暴露的 HTTP 接口。每个轮询周期
# 依次请求这些接口, 把每次"响应"作为一条记录展示。只显示 UDP 联机接口的
# 响应数据, 不涉及 Flask 私服的 HTTP 业务接口。
#
# 与请求监控的不同点: 请求监控的窗口跑在服务器(index.py)进程里, 靠进程内
# 队列拿数据; 而本窗口直接跑在管理面板进程里, 用后台线程主动轮询 xysky 的
# HTTP API, 因此自带 Toplevel, 无需跨进程标记文件唤起。
# ============================================================
UDP_BG_DARK = "#1e1e1e"
UDP_BG_PANEL = "#252526"
UDP_BG_LIST = "#1e1e1e"
UDP_FG_DEFAULT = "#d4d4d4"
UDP_FG_DIM = "#808080"
UDP_SEP = "#3c3c3c"
UDP_SEL = "#094771"
UDP_MAX_ITEMS = 200000     # 内存里最多保留多少条(服务端磁盘缓存里才是全量)
UDP_DISPLAY_MAX = 20000    # 列表最多显示多少条(筛选在全部记录上做)
UDP_POLL_MS = 100          # UI 轮询内部队列间隔
UDP_HISTORY_TIMEOUT = 30   # 首次全量拉取服务端磁盘缓存的超时(秒)

# UDP 联机监控的数据源: xysky 的 /packets 增量接口。
# 每条记录 = 一个**真实收发**的 ENet 包(客户端->服务器 / 服务器->客户端),
# 由服务端 PacketLog 记录, 面板按 seq 增量拉取。
# (旧实现是定时轮询 /health /players /rooms 这几个固定状态接口, 显示的是
#  "服务器当前状态"的固定内容, 与真实联机数据无关, 已废弃。)
UDP_PACKETS_PATH = "/packets"
UDP_STATS_PATH = "/packets/stats"


class UdpMonitorWindow(tk.Toplevel):
    """轮询 xysky HTTP 接口并以请求/响应风格展示 UDP 联机接口响应。"""

    def __init__(self, master, base_url="http://127.0.0.1:8080", interval_sec=2.0):
        super().__init__(master)
        self.base_url = base_url.rstrip("/")
        self.interval_sec = float(interval_sec)
        # 后台线程只读这两个纯属性(tk 变量不能跨线程访问), 由 UI 线程在 _drain_queue 里同步
        self._base_url = self.base_url
        self._interval = self.interval_sec
        self.records = []
        self.filtered = []
        self.selected_index = -1
        self.filter_text = ""
        self.versions = {}
        self._filter_job = None
        # 已经拉到的最新 seq(-1 = 还没拉过)。暂停后继续时从这里接着拉,
        # 否则会再把磁盘里的历史全拉一遍, 列表里全是重复项。
        self._last_seq = -1
        self._queue = __import__("queue").Queue(maxsize=5000)
        self._stop = threading.Event()
        self._poll_thread = None

        self.title("Sky 私服 - UDP 联机接口响应查看")
        self.geometry("1280x760")
        self.minsize(900, 520)
        self.configure(bg=UDP_BG_DARK)

        self.mono_font = ("Consolas", 10)
        self.list_font = ("Consolas", 9)
        self.title_font = ("Microsoft YaHei UI", 10, "bold")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_polling()
        self._drain_queue()

    # ---------------- UI ----------------
    def _build_ui(self):
        bar = tk.Frame(self, bg=UDP_BG_PANEL, height=38)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.pack_propagate(False)

        tk.Label(bar, text=" 地址:", bg=UDP_BG_PANEL, fg=UDP_FG_DEFAULT,
                 font=self.title_font).pack(side=tk.LEFT, padx=(8, 2))
        self.url_var = tk.StringVar(value=self.base_url)
        tk.Entry(bar, textvariable=self.url_var, width=26, font=self.mono_font,
                 bg=UDP_BG_DARK, fg=UDP_FG_DEFAULT, insertbackground=UDP_FG_DEFAULT,
                 relief=tk.FLAT, highlightthickness=1,
                 highlightcolor="#007acc").pack(side=tk.LEFT, padx=2, pady=5)

        tk.Label(bar, text=" 间隔(秒):", bg=UDP_BG_PANEL, fg=UDP_FG_DEFAULT,
                 font=self.title_font).pack(side=tk.LEFT, padx=(8, 2))
        self.interval_var = tk.StringVar(value=str(self.interval_sec))
        tk.Entry(bar, textvariable=self.interval_var, width=5, font=self.mono_font,
                 bg=UDP_BG_DARK, fg=UDP_FG_DEFAULT, insertbackground=UDP_FG_DEFAULT,
                 relief=tk.FLAT, highlightthickness=1,
                 highlightcolor="#007acc").pack(side=tk.LEFT, padx=2, pady=5)

        self.toggle_btn = tk.Button(bar, text="⏸ 暂停", command=self._toggle,
                                    bg="#3c3c3c", fg=UDP_FG_DEFAULT, relief=tk.FLAT,
                                    font=self.list_font, padx=8)
        self.toggle_btn.pack(side=tk.LEFT, padx=8, pady=4)

        tk.Label(bar, text=" 🔍 筛选:", bg=UDP_BG_PANEL, fg=UDP_FG_DEFAULT,
                 font=self.title_font).pack(side=tk.LEFT, padx=(8, 2))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._on_filter_change())
        tk.Entry(bar, textvariable=self.filter_var, width=20, font=self.mono_font,
                 bg=UDP_BG_DARK, fg=UDP_FG_DEFAULT, insertbackground=UDP_FG_DEFAULT,
                 relief=tk.FLAT, highlightthickness=1,
                 highlightcolor="#007acc").pack(side=tk.LEFT, padx=2, pady=5)

        self.autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="自动滚动", variable=self.autoscroll_var,
                       bg=UDP_BG_PANEL, fg=UDP_FG_DEFAULT, selectcolor=UDP_BG_DARK,
                       activebackground=UDP_BG_PANEL, activeforeground=UDP_FG_DEFAULT,
                       font=self.list_font).pack(side=tk.LEFT, padx=10)

        # 客户端版本号缓存(服务端 join 事件 + 每个包的 ver 统计), 供按版本排查。
        self.ver_label = tk.Label(bar, text="版本缓存: -", bg=UDP_BG_PANEL,
                                  fg=UDP_FG_DIM, font=self.list_font)
        self.ver_label.pack(side=tk.LEFT, padx=10)

        tk.Button(bar, text="🗑 清除", command=self._clear_all,
                  bg="#3c3c3c", fg=UDP_FG_DEFAULT, relief=tk.FLAT,
                  font=self.list_font, padx=8).pack(side=tk.RIGHT, padx=8, pady=4)
        self.count_label = tk.Label(bar, text="0 条", bg=UDP_BG_PANEL,
                                    fg=UDP_FG_DIM, font=self.list_font)
        self.count_label.pack(side=tk.RIGHT, padx=8)

        paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=UDP_SEP, sashwidth=3)
        paned.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(paned, bg=UDP_BG_DARK)
        paned.add(left, minsize=320, width=460)
        header = tk.Frame(left, bg=UDP_BG_PANEL, height=22)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="  时间        状态  耗时     接口",
                 bg=UDP_BG_PANEL, fg=UDP_FG_DIM, font=self.list_font,
                 anchor=tk.W).pack(fill=tk.X, padx=4)

        list_wrap = tk.Frame(left, bg=UDP_BG_DARK)
        list_wrap.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(list_wrap, bg=UDP_BG_LIST, fg=UDP_FG_DEFAULT,
                                  font=self.list_font, relief=tk.FLAT,
                                  selectbackground=UDP_SEL, selectforeground="#ffffff",
                                  activestyle="none", highlightthickness=0)
        sb = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        right = tk.Frame(paned, bg=UDP_BG_DARK)
        paned.add(right, minsize=400)
        tk.Label(right, text=" 响应详情", bg=UDP_BG_PANEL, fg=UDP_FG_DEFAULT,
                 font=self.title_font, anchor=tk.W).pack(fill=tk.X)
        detail_wrap = tk.Frame(right, bg=UDP_BG_DARK)
        detail_wrap.pack(fill=tk.BOTH, expand=True)
        self.detail = tk.Text(detail_wrap, bg=UDP_BG_DARK, fg=UDP_FG_DEFAULT,
                              font=self.mono_font, relief=tk.FLAT, wrap=tk.NONE,
                              insertbackground=UDP_FG_DEFAULT)
        dsb = ttk.Scrollbar(detail_wrap, orient=tk.VERTICAL, command=self.detail.yview)
        dxsb = ttk.Scrollbar(right, orient=tk.HORIZONTAL, command=self.detail.xview)
        self.detail.configure(yscrollcommand=dsb.set, xscrollcommand=dxsb.set)
        dxsb.pack(side=tk.BOTTOM, fill=tk.X)
        dsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.detail.tag_configure("err", foreground="#f44747")
        self.detail.tag_configure("ok", foreground="#6a9955")
        self.detail.tag_configure("dim", foreground=UDP_FG_DIM)

    # ---------------- 轮询(后台线程) ----------------
    def _start_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        """先全量拉取服务端磁盘缓存(本次运行期间的所有包), 再按 seq 增量拉取。

        服务端 PacketLog 把每个真实收发的包都追加写进磁盘(JSONL),
        /packets?all=1 会把缓存里的全部包一次返回, 所以打开窗口后能直接看到
        入房 / JoinGame / EnterGame 这些"窗口打开之前"发生的包, 而不是从当前
        序号开始只看新包。
        """
        import urllib.request
        import urllib.error

        since = self._last_seq if self._last_seq >= 0 else 0
        history_done = False
        last_err_notice = 0.0
        while not self._stop.is_set():
            base = (self._base_url or "").rstrip("/") or self.base_url
            if not history_done:
                # 1) 全量历史(服务端磁盘缓存); 继续时只补暂停期间的新包
                try:
                    url = "%s%s?all=1&since=%d" % (base, UDP_PACKETS_PATH, since)
                    with urllib.request.urlopen(url, timeout=UDP_HISTORY_TIMEOUT) as resp:
                        data = json.loads(resp.read().decode("utf-8", "replace"))
                    pkts = data.get("packets", []) or []
                    records = [self._packet_to_record(pkt, base) for pkt in pkts]
                    since = int(data.get("seq", 0) or 0)
                    for pkt in pkts:
                        try:
                            since = max(since, int(pkt.get("seq", since)))
                        except Exception:
                            pass
                    self._enqueue({
                        "kind": "history",
                        "records": records,
                        "stats": data.get("stats") or {},
                        "versions": data.get("versions") or {},
                        "joins": data.get("joins") or [],
                    })
                    self._last_seq = since
                    history_done = True
                    continue        # 立刻进入增量阶段, 不用等一个轮询间隔
                except Exception as e:
                    # 旧 node 进程没有 all=1: 退回旧行为(对齐当前 seq, 只看新包)。
                    try:
                        with urllib.request.urlopen(base + UDP_STATS_PATH, timeout=3) as resp:
                            since = int(json.loads(resp.read().decode("utf-8", "replace")).get("seq", 0))
                        self._last_seq = since
                        history_done = True
                        self._enqueue(self._error_record(base, e))
                    except Exception:
                        now = time.time()
                        if now - last_err_notice > 5.0:
                            last_err_notice = now
                            self._enqueue(self._error_record(base, e))
                        iv = min(max(self._interval, 0.2), 60.0)
                        self._stop.wait(iv)
                        continue
            # 2) 增量: 只取 since 之后的新包
            url = "%s%s?since=%d" % (base, UDP_PACKETS_PATH, since)
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                newrecs = []
                for pkt in data.get("packets", []) or []:
                    try:
                        since = max(since, int(pkt.get("seq", since)))
                    except Exception:
                        pass
                    newrecs.append(self._packet_to_record(pkt, base))
                if newrecs:
                    # 整批入队(一个轮询间隔可能有上千个包), 避免队列顶满丢包
                    self._enqueue({"kind": "append", "records": newrecs})
                self._last_seq = since
                if data.get("versions") or data.get("joins"):
                    self._enqueue({
                        "kind": "versions",
                        "versions": data.get("versions") or {},
                        "joins": data.get("joins") or [],
                    })
            except Exception as e:
                # 服务端还没有 /packets(旧 node 进程) 或没起来: 提示一次, 别刷屏。
                now = time.time()
                if now - last_err_notice > 5.0:
                    last_err_notice = now
                    self._enqueue(self._error_record(base, e))
            # 间隔: 用主线程同步过来的纯属性, 范围 0.2 ~ 60 秒
            iv = min(max(self._interval, 0.2), 60.0)
            self._stop.wait(iv)

    def _enqueue(self, rec):
        try:
            self._queue.put_nowait(rec)
        except Exception:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(rec)
            except Exception:
                pass

    def _error_record(self, base, err):
        return {
            "timestamp": time.time(),
            "method": "UDP",
            "path": "/packets 取数失败",
            "url": base + UDP_PACKETS_PATH,
            "status_code": 0,
            "headers": {"提示": "UDP 服务器没有 /packets 接口(多是 node 进程还是旧代码)。"
                             "在面板点一次「重启 UDP 服务器/热更新」再打开本窗口。"},
            "body": ("%s\n\n请在主控面板重启 UDP 联机服务器, 让 node 加载新的 PacketLog 代码。"
                     % err).encode("utf-8", "replace"),
            "elapsed_ms": 0.0,
        }

    def _packet_to_record(self, pkt, base):
        """把服务端的一条包记录转成与本窗口既有 UI 兼容的 record(dict)。

        只保留原始 pkt, 详情里的 hexdump 等文本在选中该行时才生成:
        全量历史一次可能有几万条, 逐条拼 hexdump 会把 UI 卡住。
        """
        try:
            ts = float(pkt.get("ts", 0)) / 1000.0 or time.time()
        except Exception:
            ts = time.time()
        direction = pkt.get("dir")
        is_in = (direction == "in")
        name = pkt.get("name") or ("id#%s" % pkt.get("packetId"))
        try:
            nbytes = int(pkt.get("len", 0))
        except Exception:
            nbytes = 0
        ver = pkt.get("ver") or ""

        # 颜色: 解包失败=红(500), 该版本不支持的包=黄(400), 正常=绿(200)
        if name == "decode_error":
            code = 500
        elif name == "unsupported":
            code = 400
        else:
            code = 200

        headers = {
            "方向": "客户端 → 服务器" if is_in else "服务器 → 客户端",
            "包名": name,
            "包ID": str(pkt.get("packetId")),
            "负载长度": "%d 字节" % nbytes,
            "ENet peer": str(pkt.get("peerId")),
            "玩家ID": "-" if pkt.get("playerId") is None else str(pkt.get("playerId")),
        }
        if ver:
            headers["客户端版本"] = ver
        if pkt.get("note"):
            headers["说明"] = pkt["note"]
        if pkt.get("truncated"):
            headers["原始数据"] = "仅记录前 %d 字节(省略 %d 字节)" % (
                len(pkt.get("hex", "")) // 2, pkt["truncated"])

        return {
            "timestamp": ts,
            "method": "UDP",
            "path": "%s %s%s  (%dB)" % (
                "← 收" if is_in else "→ 发",
                ("[%s] " % ver) if ver else "",
                name, nbytes),
            "url": base + UDP_PACKETS_PATH,
            "status_code": code,
            "headers": headers,
            "pkt": pkt,
            "elapsed_ms": 0.0,
        }

    def _packet_body(self, pkt):
        """选中行时才生成详情文本(含 hexdump), 返回 bytes 以兼容 _render_detail。"""
        direction = pkt.get("dir")
        is_in = (direction == "in")
        name = pkt.get("name") or ("id#%s" % pkt.get("packetId"))
        try:
            nbytes = int(pkt.get("len", 0))
        except Exception:
            nbytes = 0
        lines = ["%s  %s  %d 字节" % ("← 收到" if is_in else "→ 发出", name, nbytes)]
        if pkt.get("ver"):
            lines.append("客户端版本: %s" % pkt["ver"])
        if pkt.get("note"):
            lines.append("说明: %s" % pkt["note"])
        lines.append("")
        lines.append("原始字节 (hexdump):")
        lines.extend(self._hexdump(pkt.get("hex", "")))
        return "\n".join(lines).encode("utf-8", "replace")

    @staticmethod
    def _hexdump(hexstr, per_line=16):
        """把十六进制串排成 偏移 + hex + ASCII 三栏, 和抓包工具一样。"""
        if not hexstr:
            return ["(无负载)"]
        try:
            raw = bytes.fromhex(hexstr)
        except Exception:
            return [hexstr]
        out = []
        for off in range(0, len(raw), per_line):
            chunk = raw[off:off + per_line]
            hexpart = " ".join("%02x" % b for b in chunk).ljust(per_line * 3 - 1)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            out.append("%04x  %s  |%s|" % (off, hexpart, ascii_part))
        return out

    # ---------------- UI 轮询内部队列 ----------------
    def _drain_queue(self):
        import queue as _q
        # 把 UI 线程的输入框值同步给后台线程读取的纯属性(避免跨线程访问 tk 变量)
        try:
            self._base_url = self.url_var.get().strip()
        except Exception:
            pass
        try:
            self._interval = float(self.interval_var.get())
        except Exception:
            pass
        batch = []
        try:
            while True:
                item = self._queue.get_nowait()
                if isinstance(item, dict) and item.get("kind") == "history":
                    batch.extend(item.get("records") or [])
                    self._apply_versions(item.get("versions") or {},
                                         item.get("joins") or [])
                elif isinstance(item, dict) and item.get("kind") == "append":
                    batch.extend(item.get("records") or [])
                elif isinstance(item, dict) and item.get("kind") == "versions":
                    self._apply_versions(item.get("versions") or {},
                                         item.get("joins") or [])
                else:
                    batch.append(item)
        except _q.Empty:
            pass
        added = self._add_records(batch) if batch else 0
        if added and self.autoscroll_var.get():
            self.listbox.see(tk.END)
        try:
            self.after(UDP_POLL_MS, self._drain_queue)
        except Exception:
            pass

    def _add_records(self, recs):
        """把新记录追加到列表。只插入新行, 不重建整个列表(历史可能几万条)。"""
        if not recs:
            return 0
        self.records.extend(recs)
        trimmed = False
        if len(self.records) > UDP_MAX_ITEMS:
            drop = len(self.records) - UDP_MAX_ITEMS
            removed = self.records[:drop]
            del self.records[:drop]
            trimmed = bool(removed)
        new_rows = [r for r in recs if self._match(r)]
        if not new_rows:
            self._update_count()
            return 0
        if len(new_rows) >= UDP_DISPLAY_MAX or trimmed:
            # 一次来得太多(打开窗口时拉到的全量历史), 或内存超上限裁掉了旧记录:
            # 整体重建, 只显示最后若干条, 保证 listbox 的行与 self.filtered 一一对应。
            self._rebuild_list()
            return len(new_rows)
        self.filtered.extend(new_rows)
        overflow = len(self.filtered) - UDP_DISPLAY_MAX
        if overflow > 0:
            del self.filtered[:overflow]
            self.listbox.delete(0, overflow - 1)
        start = self.listbox.size()
        for i, r in enumerate(new_rows):
            self._insert_row(r, start + i)
        self._update_count()
        return len(new_rows)

    def _redraw_listbox(self):
        self.listbox.delete(0, tk.END)
        for r in self.filtered:
            self._insert_row(r)

    def _insert_row(self, r, index=None):
        ts = datetime.datetime.fromtimestamp(r["timestamp"]).strftime("%H:%M:%S")
        code = r["status_code"]
        code_s = str(code) if code else "ERR"
        line = "  %s   %-4s  %6.1fms  %s" % (ts, code_s, r["elapsed_ms"], r["path"])
        if index is None:
            self.listbox.insert(tk.END, line)
            index = self.listbox.size() - 1
        else:
            self.listbox.insert(index, line)
        if code == 0 or code >= 500:
            self.listbox.itemconfig(index, fg="#f44747")
        elif code >= 400:
            self.listbox.itemconfig(index, fg="#d7ba7d")
        else:
            self.listbox.itemconfig(index, fg="#6a9955")

    def _update_count(self):
        text = "%d/%d 条" % (len(self.filtered), len(self.records))
        if len(self.filtered) >= UDP_DISPLAY_MAX:
            text += " (列表只保留最近 %d 条, 上方过滤可用)" % UDP_DISPLAY_MAX
        self.count_label.configure(text=text)

    def _apply_versions(self, versions, joins=None):
        """缓存并显示各客户端版本号(供测试判断哪个版本连上来了)。"""
        if not isinstance(versions, dict):
            versions = {}
        self.versions.update(versions)
        items = sorted(self.versions.items(), key=lambda kv: (-int(kv[1] or 0), str(kv[0])))
        parts = ["%s×%s" % (k, v) for k, v in items[:4]]
        text = "版本缓存: " + (", ".join(parts) if parts else "-")
        last_join = None
        if joins:
            last_join = joins[-1]
        if isinstance(last_join, dict):
            ver = last_join.get("ver") or ""
            name = last_join.get("name") or ""
            if ver or name:
                text += "    最近入房: %s" % (("%s " % name) + ver).strip()
        try:
            self.ver_label.configure(text=text)
        except Exception:
            pass

    def _match(self, rec):
        if not self.filter_text:
            return True
        f = self.filter_text.lower()
        if f in rec["path"].lower():
            return True
        pkt = rec.get("pkt")
        if pkt is not None:
            for key in ("name", "note", "ver", "dir", "hex"):
                val = pkt.get(key)
                if val and f in str(val).lower():
                    return True
            return False
        try:
            return f in rec.get("body", b"").decode("utf-8", "replace").lower()
        except Exception:
            return False

    def _rebuild_list(self):
        selected = self._selected_record()
        matches = [r for r in self.records if self._match(r)]
        self.filtered = matches[-UDP_DISPLAY_MAX:] if len(matches) > UDP_DISPLAY_MAX else matches
        self._redraw_listbox()
        self._update_count()
        if selected is not None:
            for i, r in enumerate(self.filtered):
                if r is selected:
                    self.listbox.selection_set(i)
                    self.listbox.see(i)
                    break

    def _selected_record(self):
        try:
            sel = self.listbox.curselection()
        except Exception:
            return None
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self.filtered):
            return self.filtered[idx]
        return None

    def _on_filter_change(self):
        self.filter_text = self.filter_var.get().strip()
        # 输入时防抖: 历史可能有几万条, 不要每个按键都全量重建列表
        if self._filter_job is not None:
            try:
                self.after_cancel(self._filter_job)
            except Exception:
                pass
            self._filter_job = None
        try:
            self._filter_job = self.after(350, self._rebuild_list)
        except Exception:
            self._rebuild_list()

    def _on_select(self, _evt=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self.filtered):
            return
        self._render_detail(self.filtered[idx])

    def _render_detail(self, rec):
        self.detail.delete("1.0", tk.END)
        ts = datetime.datetime.fromtimestamp(rec["timestamp"]).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        code = rec["status_code"]
        tag = "ok" if (code and 200 <= code < 400) else "err"
        self.detail.insert(tk.END, "%s %s\n" % (rec["method"], rec["url"]))
        self.detail.insert(tk.END, "状态: ")
        self.detail.insert(tk.END, "%s\n" % (code if code else "连接失败"), tag)
        self.detail.insert(tk.END, "时间: %s    耗时: %sms\n" % (ts, rec["elapsed_ms"]), "dim")
        if rec.get("headers"):
            self.detail.insert(tk.END, "\n--- 响应头 ---\n", "dim")
            for k, v in rec["headers"].items():
                self.detail.insert(tk.END, "%s: %s\n" % (k, v))
        self.detail.insert(tk.END, "\n--- 响应体 ---\n", "dim")
        body = rec.get("body")
        if body is None:
            pkt = rec.get("pkt")
            body = self._packet_body(pkt) if pkt is not None else b""
        try:
            text = body.decode("utf-8", "replace")
        except Exception:
            text = repr(body)
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except Exception:
            pass
        self.detail.insert(tk.END, text or "(空)")

    def _toggle(self):
        if self._poll_thread and self._poll_thread.is_alive():
            self._stop.set()
            self.toggle_btn.configure(text="▶ 继续")
        else:
            self._start_polling()
            self.toggle_btn.configure(text="⏸ 暂停")

    def _clear_all(self):
        self.records.clear()
        self.filtered.clear()
        self.listbox.delete(0, tk.END)
        self.detail.delete("1.0", tk.END)
        self.count_label.configure(text="0 条")

    def _on_close(self):
        self._stop.set()
        try:
            self.destroy()
        except Exception:
            pass


class AdminPanel(tk.Tk):
    def __init__(self, root_dir):
        super().__init__()
        self._setup_global_copy_paste()
        self.root_dir = root_dir
        self.db = UserDB(root_dir)
        # 多开支持: 本实例端口。优先级:
        #   1) 源码目录下的 .sky_port 文件(每个私服各自一个, 多开时填不同端口)
        #   2) panel_config.json 的 server_port
        #   3) 默认 8443
        # 多开方法: 复制多份源码目录, 各自在目录里放 .sky_port 写不同端口(如 8443/8444),
        # 再开多个面板分别选这些目录, 即可各开各的私服 + 各自的临时公网隧道。
        self.server_port = self._resolve_port(root_dir)
        self.proc = None                 # 服务器子进程
        self.udp_proc = None             # UDP(xysky)联机服务器子进程, 跟随私服启停
        self.udp_procs = []              # 多房间: 每个房间一个 node 进程
        self._balancer_thread = None      # 串房线程
        self._balancer_stop = None
        self.server_state = "stopped"    # stopped / starting / running
        self.users_cache = []
        self.selected_uid = None
        self.use_https = tk.BooleanVar(value=load_panel_config().get("use_https", True))
        self.autoscroll = tk.BooleanVar(value=True)
        self.public_ip = None
        self.primary_ip = "127.0.0.1"
        self.v6_var = None  # will be created in _build_server_tab
        self._ddns_thread = None
        self._ddns_stop = None
        self.cf_proc = None             # cloudflared 隧道子进程(仅本面板亲自启动时持有)
        self.cf_url = ""                # 当前隧道公网地址
        self._cf_pid = None             # 隧道进程 PID(脱离面板后台运行, 重开面板可附着)
        self._cf_tail = False           # 隧道日志 tail 线程是否已在运行

        self.title("Sky 私服管理面板 - 端口 %d" % self.server_port)
        self.geometry("1080x720")
        self.minsize(960, 640)

        self._build_toolbar()
        self._build_notebook()
        self._build_statusbar()

        self._push_dialog = None
        if getattr(self, "enable_push_bar", True):
            self._install_push_bar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self.refresh_network)
        self.after(300, self.refresh_users)
        self.after(30000, self._auto_refresh_online)
        self.after(600, self._attach_background_server)

    def _attach_background_server(self):
        """面板启动时检测是否有挂在后台运行的私服(PID 文件存活), 有则附着:
        恢复 running 状态、继续 tail 运行日志, 而不是重新启动。"""
        info = self._read_pidfile()
        if not info:
            return
        pid = info.get("pid")
        port = info.get("port") or self.server_port
        if not self._pid_alive(pid):
            self._clear_pidfile()
            return
        # 附着到后台进程
        self.server_port = port
        self._bg_pid = pid

        class _AttachedProc:
            """轻量包装: 让现有逻辑能 poll()/terminate() 后台进程。"""
            def __init__(self, pid):
                self.pid = pid
            def poll(self):
                return None if AdminPanel._pid_alive(self.pid) else 0
            def terminate(self):
                subprocess.run(["taskkill", "/PID", str(self.pid)],
                               capture_output=True, shell=True)
            def wait(self, timeout=None):
                return 0
            def kill(self):
                subprocess.run(["taskkill", "/F", "/PID", str(self.pid)],
                               capture_output=True, shell=True)

        self.proc = _AttachedProc(pid)
        self._runlog_path = self._runtime_log()
        self._ready_fired = False
        self._port_confirmed = False
        self._set_server_state("running")
        self.log("检测到后台运行的私服(PID %s, 端口 %d), 已附着。" % (pid, port), "success")
        threading.Thread(target=self._tail_runtime_log, daemon=True).start()
        threading.Thread(target=self._probe_ready, daemon=True).start()
        # 附着后台私服时, 一并附着仍存活的隧道(地址锁定): 只要服务器没停, 沿用旧地址。
        self.after(800, self._start_cloudflared)
        # UDP 跟随私服: 若后台 UDP 进程仍存活则附着, 否则补启动一个(保证联机接口在)
        try:
            recs = self._alive_udp_records()
            if recs:
                self.udp_procs = []
                self.log("检测到后台 UDP 联机服务器(%d 个房间), 已附着。" % len(recs), "success")
                self._start_room_balancer()
            else:
                self._clear_udp_pidfile()
                self.start_udp_server()
        except Exception:
            pass

    def _auto_refresh_online(self):
        """周期性刷新账号列表(每30秒), 让在线/离线状态保持最新。"""
        try:
            if self.db.available():
                self.refresh_users()
        except Exception:
            pass
        self.after(30000, self._auto_refresh_online)

    # ---------------- 工具栏 ----------------
    def _build_toolbar(self):
        bar = tk.Frame(self, height=46)
        bar.pack(side="top", fill="x")

        self.btn_start = tk.Button(bar, text="▶ 启动服务器", width=14,
                                   command=self.start_server)
        self.btn_start.pack(side="left", padx=(10, 4), pady=8)

        self.btn_stop = tk.Button(bar, text="■ 停止服务器", width=14,
                                  state="disabled", command=self.stop_server)
        self.btn_stop.pack(side="left", padx=4, pady=8)

        self.btn_chdir = tk.Button(bar, text="切换源码目录", width=12, command=self.change_dir)
        self.btn_chdir.pack(side="left", padx=4, pady=8)

        self.btn_upnp = tk.Button(bar, text="UPnP 映射", width=12,
                                  command=self._retry_upnp)
        self.btn_upnp.pack(side="left", padx=4, pady=8)

        self.btn_stream = tk.Button(bar, text="接口请求响应查看", width=16,
                                    command=self.open_stream_viewer)
        self.btn_stream.pack(side="left", padx=4, pady=8)

        # 热更新: 运行中重载接口源码, 玩家不断连(后台源码更新)
        self.btn_reload = tk.Button(bar, text="热更新接口", width=12,
                                    command=self._hot_reload)
        self.btn_reload.pack(side="left", padx=4, pady=8)

        # UDP 联机接口响应查看: 轮询 xysky(UDP 服务器)的 HTTP 接口, 只显示联机接口响应
        self.btn_udp = tk.Button(bar, text="UDP接口响应查看", width=16,
                                 command=self.open_udp_viewer)
        self.btn_udp.pack(side="left", padx=4, pady=8)

        # 局域网 UDP 联机总开关:
        #   开 -> find_previous_or_empty 下发本机"真房间"(ip_host:realtime_port),
        #         并启动 UDP(ENet) 联机服务器;
        #   关 -> 只回"原来的假房间"(房间池), 客户端拿到的地址后面没有真服务器,
        #         同时停掉 UDP 联机服务器。
        self.btn_lanudp = tk.Button(bar, text="🌐 局域网UDP联机: --", width=18,
                                    command=self.toggle_lan_udp)
        self.btn_lanudp.pack(side="left", padx=4, pady=8)
        self._refresh_lanudp_btn()

        # 状态指示器
        self.state_dot = tk.Canvas(bar, width=16, height=16,
                                   highlightthickness=0)
        self.state_dot.pack(side="right", padx=(4, 6))
        self.state_oval = self.state_dot.create_oval(2, 2, 14, 14, fill="#9e9e9e",
                                                     outline="")
        self.state_label = tk.Label(bar, text="已停止",
                                    font=("微软雅黑", 10))
        self.state_label.pack(side="right")

    # ---------------- 选项卡 ----------------
    def _build_notebook(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=4)
        self.tab_server = tk.Frame(self.nb)
        self.tab_account = tk.Frame(self.nb)
        self.tab_game_control = tk.Frame(self.nb)
        self.tab_motd = tk.Frame(self.nb)
        self.tab_ipban = tk.Frame(self.nb)
        self.tab_magic = tk.Frame(self.nb)
        self.tab_ugc = tk.Frame(self.nb)
        self.tab_other = tk.Frame(self.nb)
        self.tab_friend = tk.Frame(self.nb)
        self.nb.add(self.tab_server, text="  服务器 / 日志  ")
        self.nb.add(self.tab_motd, text="  公告  ")
        self.nb.add(self.tab_account, text="  账号管理  ")
        self.nb.add(self.tab_friend, text="  好友控制  ")
        self.nb.add(self.tab_game_control, text="  游戏控制  ")
        self.nb.add(self.tab_magic, text="  魔法控制  ")
        self.nb.add(self.tab_ugc, text="  纸船/留影/空间  ")
        self.nb.add(self.tab_ipban, text="  IP封禁  ")
        self.nb.add(self.tab_other, text="  其他  ")
        self._build_server_tab()
        self._build_motd_tab()
        self._build_account_tab()
        if build_friend_control_tab is not None:
            try:
                build_friend_control_tab(self, self.tab_friend)
            except Exception as _e:
                tk.Label(self.tab_friend, text="好友控制页加载失败: %s" % _e).pack()
        else:
            tk.Label(self.tab_friend, text="friend_control_panel.py 未找到").pack()
        build_game_control_tab(self, self.tab_game_control)
        if build_magic_tab is not None:
            try:
                build_magic_tab(self, self.tab_magic)
            except Exception as _e:
                tk.Label(self.tab_magic, text="魔法控制页加载失败: %s" % _e).pack()
        else:
            tk.Label(self.tab_magic, text="magic_control_panel.py 未找到").pack()
        if build_ugc_tab is not None:
            try:
                build_ugc_tab(self, self.tab_ugc)
            except Exception as _e:
                tk.Label(self.tab_ugc, text="纸船/留影/空间页加载失败: %s" % _e).pack()
        else:
            tk.Label(self.tab_ugc, text="ugc_control_panel.py 未找到").pack()
        self._build_ipban_tab()
        if build_other_tab is not None:
            try:
                build_other_tab(self, self.tab_other)
            except Exception as _e:
                tk.Label(self.tab_other, text="其他页加载失败: %s" % _e).pack()
        else:
            tk.Label(self.tab_other, text="other_control_panel.py 未找到").pack()

    # ---------------- 状态栏 ----------------
    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="就绪")
        bar = tk.Frame(self, bd=1, relief="sunken")
        bar.pack(side="bottom", fill="x")
        tk.Label(bar, textvariable=self.status_var, anchor="w",
                 font=("微软雅黑", 9)).pack(side="left", padx=8)
        self.dir_var = tk.StringVar(value=f"源码: {self.root_dir}")
        tk.Label(bar, textvariable=self.dir_var, anchor="e",
                 font=("微软雅黑", 9)).pack(side="right", padx=8)
        pending = getattr(self, '_pending_status', None)
        if pending:
            self.status_var.set(pending)

    def set_status(self, text):
        if hasattr(self, 'status_var'):
            self.status_var.set(text)
        else:
            self._pending_status = text

    # ---------------- 全局复制/粘贴 ----------------
    def _setup_global_copy_paste(self):
        """为所有 Entry / Text / ttk.Entry / Combobox / ScrolledText
        统一绑定 Ctrl+C/V/X/A (含中文输入法状态) 与右键菜单。"""
        def _ctx_menu(event):
            w = event.widget
            try:
                w.focus_set()
            except Exception:
                pass
            m = tk.Menu(w, tearoff=0)
            m.add_command(label="剪切",
                          command=lambda: w.event_generate("<<Cut>>"))
            m.add_command(label="复制",
                          command=lambda: w.event_generate("<<Copy>>"))
            m.add_command(label="粘贴",
                          command=lambda: w.event_generate("<<Paste>>"))
            m.add_separator()
            m.add_command(label="全选",
                          command=lambda: self._select_all(w))
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                m.grab_release()

        def _select_all(event):
            self._select_all(event.widget)
            return "break"

        for cls in ("Entry", "TEntry", "Text", "TCombobox"):
            self.bind_class(cls, "<Button-3>", _ctx_menu, add="+")
            self.bind_class(cls, "<Control-a>", _select_all, add="+")
            self.bind_class(cls, "<Control-A>", _select_all, add="+")
            # 兼容部分环境下 Ctrl 组合未触发虚拟事件
            self.bind_class(cls, "<Control-c>",
                            lambda e: (e.widget.event_generate("<<Copy>>"), "break")[1], add="+")
            self.bind_class(cls, "<Control-v>",
                            lambda e: (e.widget.event_generate("<<Paste>>"), "break")[1], add="+")
            self.bind_class(cls, "<Control-x>",
                            lambda e: (e.widget.event_generate("<<Cut>>"), "break")[1], add="+")

    def _select_all(self, w):
        try:
            cls = w.winfo_class()
            if cls in ("Text",):
                w.tag_add("sel", "1.0", "end-1c")
                w.mark_set("insert", "1.0")
            else:
                w.select_range(0, "end")
                w.icursor("end")
        except Exception:
            pass

    # ---------------- 公告 (MOTD) ----------------
    def _motd_config_path(self):
        return os.path.join(self.root_dir, "config", "motd_config.json")

    def _build_motd_tab(self):
        import tkinter.scrolledtext as st
        wrap = tk.Frame(self.tab_motd)
        wrap.pack(fill="both", expand=True, padx=10, pady=8)

        tk.Label(wrap, text="公告标题:", font=("微软雅黑", 9, "bold")).pack(anchor="w")
        self.motd_title_var = tk.StringVar()
        tk.Entry(wrap, textvariable=self.motd_title_var,
                 font=("微软雅黑", 10)).pack(fill="x", pady=(2, 8))

        body_bar = tk.Frame(wrap)
        body_bar.pack(fill="x")
        tk.Label(body_bar, text="公告正文 (支持换行):",
                 font=("微软雅黑", 9, "bold")).pack(side="left")
        tk.Label(body_bar, text="get_motd 接口实时读取 config/motd_config.json",
                 font=("微软雅黑", 8), fg="#888").pack(side="right")
        self.motd_body_text = st.ScrolledText(wrap, font=("微软雅黑", 10),
                                               height=7, wrap="word", undo=True)
        self.motd_body_text.pack(fill="x", pady=(2, 8))

        ver_bar = tk.Frame(wrap)
        ver_bar.pack(fill="x", pady=(0, 6))
        tk.Label(ver_bar, text="公告版本号 (motd_version):",
                 font=("微软雅黑", 9)).pack(side="left")
        self.motd_version_var = tk.StringVar()
        tk.Entry(ver_bar, textvariable=self.motd_version_var, width=14).pack(side="left", padx=6)

        # ---- 季节时间 (登录界面倒计时 / 邮件时间署名) ----
        se_bar = tk.Frame(wrap)
        se_bar.pack(fill="x", pady=(0, 6))
        tk.Label(se_bar, text="季节结束 (motd_season_end, 高版本0.19+):",
                 font=("微软雅黑", 9)).pack(side="left")
        self.motd_season_end_var = tk.StringVar()
        se_entry = tk.Entry(se_bar, textvariable=self.motd_season_end_var, width=14)
        se_entry.pack(side="left", padx=6)
        self.motd_season_preview = tk.Label(se_bar, text="", font=("微软雅黑", 8), fg="#0a7")
        self.motd_season_preview.pack(side="left", padx=4)
        self.motd_season_end_var.trace_add("write", lambda *a: self._update_season_preview())
        tk.Button(se_bar, text="设为已结束(现在)",
                  command=self._season_end_now,
                  font=("微软雅黑", 8)).pack(side="right", padx=2)

        ss_bar = tk.Frame(wrap)
        ss_bar.pack(fill="x", pady=(0, 6))
        tk.Label(ss_bar, text="季节开始 (motd_season_start, 低版本0.13需要):",
                 font=("微软雅黑", 9)).pack(side="left")
        self.motd_season_start_var = tk.StringVar()
        tk.Entry(ss_bar, textvariable=self.motd_season_start_var, width=14).pack(side="left", padx=6)
        tk.Label(ss_bar, text="低版本(0134)缺此字段会导致邮件不显示内容/时间/署名",
                 font=("微软雅黑", 8), fg="#c60").pack(side="left", padx=6)

        # ---- 邮件时间显示模式 ----
        tm_bar = tk.Frame(wrap)
        tm_bar.pack(fill="x", pady=(0, 8))
        tk.Label(tm_bar, text="邮件时间 (motd_timestamp):",
                 font=("微软雅黑", 9)).pack(side="left")
        self.motd_time_mode = tk.StringVar(value="now")
        tk.Radiobutton(tm_bar, text='显示"刚刚"(实时)', value="now",
                       variable=self.motd_time_mode, command=self._toggle_time_mode,
                       font=("微软雅黑", 9)).pack(side="left", padx=4)
        tk.Radiobutton(tm_bar, text="固定时间戳", value="fixed",
                       variable=self.motd_time_mode, command=self._toggle_time_mode,
                       font=("微软雅黑", 9)).pack(side="left", padx=4)
        self.motd_timestamp_var = tk.StringVar()
        self.motd_ts_entry = tk.Entry(tm_bar, textvariable=self.motd_timestamp_var, width=14)
        self.motd_ts_entry.pack(side="left", padx=6)

        # ---- 公告按钮 (网站 url + UI 图标 img) ----
        self._build_motd_buttons_ui(wrap)

        btn_bar = tk.Frame(wrap)
        btn_bar.pack(fill="x")
        tk.Button(btn_bar, text="重新载入", width=12,
                  command=self._load_motd).pack(side="left")
        tk.Button(btn_bar, text="保存公告", width=14,
                  bg="#0078d7", fg="white", activebackground="#106ebe",
                  font=("微软雅黑", 9, "bold"),
                  command=self._save_motd).pack(side="left", padx=8)
        tk.Label(btn_bar, text="保存后玩家下次拉取 get_motd 即生效",
                 font=("微软雅黑", 8), fg="#888").pack(side="left")

        self._load_motd()

    def _update_season_preview(self):
        import datetime
        raw = self.motd_season_end_var.get().strip()
        if not raw:
            self.motd_season_preview.configure(text="", fg="#888")
            return
        try:
            ts = int(float(raw))
        except ValueError:
            self.motd_season_preview.configure(text="(非数字)", fg="#c00")
            return
        now = int(time.time())
        try:
            dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except (OSError, ValueError, OverflowError):
            self.motd_season_preview.configure(text="(时间戳超范围)", fg="#c00")
            return
        days = (ts - now) / 86400.0
        if days > 0:
            self.motd_season_preview.configure(
                text=f"→ {dt} (还有 {days:.0f} 天结束)", fg="#0a7")
        else:
            self.motd_season_preview.configure(
                text=f"→ {dt} (季节已结束)", fg="#c60")

    def _season_end_now(self):
        # 将季节结束时间设为当前时间, 登录界面即显示季节已结束/无倒计时
        self.motd_season_end_var.set(str(int(time.time())))

    def _toggle_time_mode(self):
        mode = self.motd_time_mode.get()
        try:
            if mode == "fixed":
                self.motd_ts_entry.configure(state="normal")
                if not self.motd_timestamp_var.get().strip():
                    self.motd_timestamp_var.set(str(int(time.time())))
            else:
                self.motd_ts_entry.configure(state="disabled")
        except Exception:
            pass

    # 常见 UI 图标 (img 字段) 建议值
    MOTD_ICON_PRESETS = [
        "Icon_tiktok", "Icon_instagram", "Icon_facebook",
        "Icon_twitter", "Icon_discord", "Icon_youtube", "Icon_weibo",
        "UiOutfitHairWitchHat", "UiOutfitBodyAP08BaggyPants",
        "UiOutfitHairAP05Director",
    ]

    def _build_motd_buttons_ui(self, parent):
        box = tk.LabelFrame(parent, text="公告按钮 (网站 + UI图标)",
                            font=("微软雅黑", 9, "bold"))
        box.pack(fill="both", expand=True, pady=(2, 8))

        hint = tk.Frame(box)
        hint.pack(fill="x", padx=6, pady=(4, 2))
        tk.Label(hint, text="每个按钮: 网站(url) + UI图标(img)。img 是游戏内图标资源名。",
                 font=("微软雅黑", 8), fg="#888").pack(side="left")
        tk.Button(hint, text="+ 添加按钮", command=lambda: self._add_motd_button_row(),
                  bg="#2e8b57", fg="white", font=("微软雅黑", 8, "bold")).pack(side="right")

        # 列标题
        head = tk.Frame(box)
        head.pack(fill="x", padx=6)
        tk.Label(head, text="#", width=3, font=("微软雅黑", 8, "bold")).pack(side="left")
        tk.Label(head, text="网站 (url)", font=("微软雅黑", 8, "bold")).pack(side="left", padx=(4, 0))
        tk.Label(head, text="UI图标 (img)", width=24,
                 font=("微软雅黑", 8, "bold")).pack(side="right", padx=(0, 34))

        # 可滚动行容器
        canvas_wrap = tk.Frame(box, height=150)
        canvas_wrap.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        canvas_wrap.pack_propagate(False)
        self.motd_btn_canvas = tk.Canvas(canvas_wrap, highlightthickness=0)
        vsb = ttk.Scrollbar(canvas_wrap, orient="vertical",
                            command=self.motd_btn_canvas.yview)
        self.motd_btn_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.motd_btn_canvas.pack(side="left", fill="both", expand=True)
        self.motd_btns_container = tk.Frame(self.motd_btn_canvas)
        self._motd_btns_win = self.motd_btn_canvas.create_window(
            (0, 0), window=self.motd_btns_container, anchor="nw")

        def _on_cfg(event):
            self.motd_btn_canvas.configure(
                scrollregion=self.motd_btn_canvas.bbox("all"))
            self.motd_btn_canvas.itemconfigure(self._motd_btns_win,
                                               width=self.motd_btn_canvas.winfo_width())
        self.motd_btns_container.bind("<Configure>", _on_cfg)
        self.motd_btn_canvas.bind(
            "<Configure>",
            lambda e: self.motd_btn_canvas.itemconfigure(self._motd_btns_win, width=e.width))

        self.motd_button_rows = []

    def _add_motd_button_row(self, url="", img=""):
        if not hasattr(self, "motd_button_rows"):
            self.motd_button_rows = []
        row_frame = tk.Frame(self.motd_btns_container)
        row_frame.pack(fill="x", pady=1)

        idx_lbl = tk.Label(row_frame, width=3, font=("微软雅黑", 8))
        idx_lbl.pack(side="left")

        url_var = tk.StringVar(value=url)
        img_var = tk.StringVar(value=img)

        del_btn = tk.Button(row_frame, text="删除", width=5, fg="#c00",
                            font=("微软雅黑", 8))
        del_btn.pack(side="right", padx=(4, 0))
        img_box = ttk.Combobox(row_frame, textvariable=img_var, width=22,
                               values=self.MOTD_ICON_PRESETS)
        img_box.pack(side="right")
        tk.Entry(row_frame, textvariable=url_var,
                 font=("微软雅黑", 9)).pack(side="left", fill="x", expand=True, padx=4)

        row = {"frame": row_frame, "url": url_var, "img": img_var, "idx": idx_lbl}
        del_btn.configure(command=lambda r=row: self._remove_motd_button_row(r))
        self.motd_button_rows.append(row)
        self._renumber_motd_button_rows()

    def _remove_motd_button_row(self, row):
        try:
            row["frame"].destroy()
        except Exception:
            pass
        if row in self.motd_button_rows:
            self.motd_button_rows.remove(row)
        self._renumber_motd_button_rows()

    def _renumber_motd_button_rows(self):
        for i, r in enumerate(self.motd_button_rows, 1):
            r["idx"].configure(text=str(i))

    def _clear_motd_button_rows(self):
        for r in list(getattr(self, "motd_button_rows", [])):
            try:
                r["frame"].destroy()
            except Exception:
                pass
        self.motd_button_rows = []

    def _load_motd(self):
        path = self._motd_config_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            self.motd_title_var.set("")
            self.motd_body_text.delete("1.0", tk.END)
            self.set_status(f"读取公告失败: {e}")
            return
        self._motd_cfg_cache = cfg
        self.motd_title_var.set(cfg.get("motd_title", ""))
        self.motd_body_text.delete("1.0", tk.END)
        self.motd_body_text.insert("1.0", cfg.get("motd", ""))
        self.motd_version_var.set(str(cfg.get("motd_version", 0)))
        # 季节结束时间戳 (登录界面倒计时)
        self.motd_season_end_var.set(str(cfg.get("motd_season_end", "")))
        self._update_season_preview()
        # 季节开始时间戳 (低版本邮件渲染)
        self.motd_season_start_var.set(str(cfg.get("motd_season_start", "")))
        # 邮件时间模式: 配置里有固定时间戳则为 fixed, 否则 now(实时"刚刚")
        ts_mode = cfg.get("motd_timestamp_mode", "now")
        self.motd_time_mode.set("fixed" if ts_mode == "fixed" else "now")
        self.motd_timestamp_var.set(str(cfg.get("motd_timestamp", "")) if ts_mode == "fixed" else "")
        self._toggle_time_mode()
        # 载入按钮 (网站 url + UI图标 img)
        self._clear_motd_button_rows()
        for btn in cfg.get("motd_buttons", []) or []:
            if isinstance(btn, dict):
                self._add_motd_button_row(btn.get("url", ""), btn.get("img", ""))
        self.set_status("公告已载入")

    def _save_motd(self):
        path = self._motd_config_path()
        cfg = getattr(self, "_motd_cfg_cache", None)
        if cfg is None:
            cfg = {}
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception:
                    cfg = {}
        cfg["motd_title"] = self.motd_title_var.get()
        cfg["motd"] = self.motd_body_text.get("1.0", "end-1c")
        try:
            cfg["motd_version"] = int(self.motd_version_var.get())
        except ValueError:
            messagebox.showwarning("提示", "版本号需为整数", parent=self)
            return
        # 季节结束时间戳 (登录界面"本季节还有N天结束")
        se_raw = self.motd_season_end_var.get().strip()
        if se_raw:
            try:
                cfg["motd_season_end"] = int(float(se_raw))
            except ValueError:
                messagebox.showwarning("提示", "季节结束时间戳需为数字(Unix秒)", parent=self)
                return
        else:
            cfg.pop("motd_season_end", None)
        # 季节开始时间戳 (低版本0134邮件渲染所需)
        ss_raw = self.motd_season_start_var.get().strip()
        if ss_raw:
            try:
                cfg["motd_season_start"] = int(float(ss_raw))
            except ValueError:
                messagebox.showwarning("提示", "季节开始时间戳需为数字(Unix秒)", parent=self)
                return
        else:
            cfg.pop("motd_season_start", None)
        # 邮件时间模式
        if self.motd_time_mode.get() == "fixed":
            ts_raw = self.motd_timestamp_var.get().strip()
            try:
                cfg["motd_timestamp"] = int(float(ts_raw))
            except ValueError:
                messagebox.showwarning("提示", "固定邮件时间戳需为数字(Unix秒)", parent=self)
                return
            cfg["motd_timestamp_mode"] = "fixed"
        else:
            cfg["motd_timestamp_mode"] = "now"
        # 收集按钮 (网站 url + UI图标 img), url 和 img 都为空的行跳过
        buttons = []
        for r in getattr(self, "motd_button_rows", []):
            url = r["url"].get().strip()
            img = r["img"].get().strip()
            if not url and not img:
                continue
            buttons.append({"url": url, "img": img})
        cfg["motd_buttons"] = buttons
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self)
            return
        self._motd_cfg_cache = cfg
        self.set_status("公告已保存，玩家下次拉取 get_motd 生效")
        messagebox.showinfo("完成", "公告已保存", parent=self)

    # ---------------- 服务器选项卡 ----------------
    def _build_server_tab(self):
        # 地址展示区
        addr = tk.LabelFrame(self.tab_server, text="网络地址 (点击复制)",
                             font=("微软雅黑", 10))
        addr.pack(fill="x", padx=8, pady=6)

        tk.Label(addr, text="局域网:", font=("微软雅黑", 9)).grid(
            row=0, column=0, sticky="w", padx=(8, 4), pady=4)
        self.lan_var = tk.StringVar(value="检测中...")
        self.lan_lbl = tk.Label(addr, textvariable=self.lan_var,
                                cursor="hand2", font=("Consolas", 10, "underline"))
        self.lan_lbl.grid(row=0, column=1, sticky="w", pady=4)
        self.lan_lbl.bind("<Button-1>", lambda e: self._copy(self.lan_var.get()))

        tk.Label(addr, text="IPv6:", font=("微软雅黑", 9)).grid(
            row=1, column=0, sticky="w", padx=(8, 4), pady=4)
        self.v6_var = tk.StringVar(value="检测中...")
        self.v6_lbl = tk.Label(addr, textvariable=self.v6_var,
                               cursor="hand2", font=("Consolas", 10, "underline"))
        self.v6_lbl.grid(row=1, column=1, sticky="w", pady=4)
        self.v6_lbl.bind("<Button-1>", lambda e: self._copy(self.v6_var.get()))

        tk.Label(addr, text="公网:", font=("微软雅黑", 9)).grid(
            row=2, column=0, sticky="w", padx=(8, 4), pady=4)
        self.wan_var = tk.StringVar(value="检测中...")
        self.wan_lbl = tk.Label(addr, textvariable=self.wan_var,
                                cursor="hand2", font=("Consolas", 10, "underline"))
        self.wan_lbl.grid(row=2, column=1, sticky="w", pady=4)
        self.wan_lbl.bind("<Button-1>", lambda e: self._copy(self.wan_var.get()))

        tk.Label(addr, text="隧道:", font=("微软雅黑", 9)).grid(
            row=3, column=0, sticky="w", padx=(8, 4), pady=4)
        self.cf_var = tk.StringVar(value="未启动")
        self.cf_lbl = tk.Label(addr, textvariable=self.cf_var,
                               cursor="hand2", font=("Consolas", 10, "underline"))
        self.cf_lbl.grid(row=3, column=1, sticky="w", pady=4)
        self.cf_lbl.bind("<Button-1>", lambda e: self._copy(self.cf_var.get()))

        tk.Checkbutton(addr, text="强制 HTTPS", variable=self.use_https,
                       command=self._toggle_https).grid(
            row=0, column=2, padx=20)
        tk.Button(addr, text="刷新 IP", command=self.refresh_network).grid(
            row=3, column=2, padx=20)

        # DDNS 配置区: 让域名 AAAA 始终自动跟随当前稳定 IPv6
        ddns = tk.LabelFrame(self.tab_server,
                             text="DDNS 自动更新 (ClouDNS — 解决 IPv6 地址漂移)",
                             font=("微软雅黑", 10))
        ddns.pack(fill="x", padx=8, pady=(0, 6))
        _cfg = load_panel_config()
        tk.Label(ddns, text="Dynamic URL:", font=("微软雅黑", 9)).grid(
            row=0, column=0, sticky="w", padx=(8, 4), pady=4)
        self.ddns_url_var = tk.StringVar(value=_cfg.get("ddns_dynamic_url", ""))
        tk.Entry(ddns, textvariable=self.ddns_url_var, width=64,
                 font=("Consolas", 9)).grid(row=0, column=1, sticky="we",
                                            pady=4, padx=(0, 6))
        tk.Button(ddns, text="保存并立即更新",
                  command=self._save_and_run_ddns).grid(row=0, column=2,
                                                        padx=8)
        tk.Label(ddns, font=("微软雅黑", 8), fg="#888",
                 text="在 ClouDNS 的 AAAA 记录上启用 Dynamic URL, 复制其链接粘贴到此处。"
                      "服务器启动后会自动周期更新, 域名永远指向真实 IPv6。").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 4))
        ddns.columnconfigure(1, weight=1)

        # 日志控制条
        ctrl = tk.Frame(self.tab_server)
        ctrl.pack(fill="x", padx=8)
        tk.Label(ctrl, text="服务器日志", font=("微软雅黑", 10, "bold")).pack(side="left")
        tk.Checkbutton(ctrl, text="自动滚动", variable=self.autoscroll).pack(
            side="right")
        tk.Button(ctrl, text="清空", command=self._clear_log).pack(side="right", padx=4)

        # 日志查看器
        wrap = tk.Frame(self.tab_server)
        wrap.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.log_text = tk.Text(wrap, bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG,
                                font=("Consolas", 9), wrap="none", undo=False)
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ysb.set)
        ysb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        for tag, color in [("info", COLOR_INFO), ("warn", COLOR_WARN),
                           ("error", COLOR_ERROR), ("success", COLOR_OK),
                           ("timestamp", COLOR_TS)]:
            self.log_text.tag_configure(tag, foreground=color)

        # 只读 + 快捷键
        self.log_text.bind("<Key>", self._block_key)
        menu = tk.Menu(self.log_text, tearoff=0)
        menu.add_command(label="复制", command=self._log_copy)
        menu.add_command(label="全选", command=self._log_selectall)
        self.log_text.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        self.log_text.bind("<Control-a>", lambda e: self._log_selectall() or "break")
        self.log_text.bind("<Control-c>", lambda e: self._log_copy() or "break")

    # ---------------- 账号选项卡 ----------------
    def _build_account_tab(self):
        top = tk.Frame(self.tab_account)
        top.pack(fill="x", padx=8, pady=6)
        tk.Label(top, text="搜索:", font=("微软雅黑", 9)).pack(side="left")
        self.search_var = tk.StringVar()
        ent = tk.Entry(top, textvariable=self.search_var, width=30)
        ent.pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: self.refresh_users())
        tk.Button(top, text="搜索", command=self.refresh_users).pack(side="left", padx=2)
        tk.Button(top, text="刷新", command=lambda: (self.search_var.set(""),
                  self.refresh_users())).pack(side="left", padx=2)
        # 自动授权开关: 开启后此刻之后新建的账号自动授权并标黄(任务2)
        # 放在顶部一直可见, 避免被下方操作按钮行挤出窗口
        self.btn_auto_auth = tk.Button(top, text="自动授权: 关", width=12,
                                       command=self._toggle_auto_auth)
        self.btn_auto_auth.pack(side="left", padx=(12, 2))
        # 无库就建号开关(用户 2026-10-05): 客户端拿着账号库里没有的 id 来请求时,
        # 服务器用**同一个 id** 建一行并继续登录 —— 清空/换账号库之后客户端不用
        # 重装、不用换号就能直接进游戏。放在账号管理页(和"自动授权"相邻: 新号能不能
        # 立刻进游戏由"自动授权"决定)。配置写在 config/friend_config.json 的
        # auto_create_account, 服务端每次调用都重读 -> 这里改完立刻生效。
        self.btn_auto_create = tk.Button(top, text="无库就建号: 开", width=15,
                                         command=self._toggle_auto_create_account)
        self.btn_auto_create.pack(side="left", padx=(8, 2))
        self.stat_var = tk.StringVar(value="总数: 0  封禁: 0")
        tk.Label(top, textvariable=self.stat_var,
                 font=("微软雅黑", 9)).pack(side="right")

        # 用户表格
        cols = ("id", "device_name", "platform", "version", "online", "banned", "ban_end", "created")
        heads = ("用户ID", "设备名", "平台", "版本", "在线状态", "封号状态", "截止时间", "注册时间")
        widths = (230, 130, 80, 70, 100, 80, 150, 150)
        frame = tk.Frame(self.tab_account)
        frame.pack(fill="both", expand=True, padx=8)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center")
        ysb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        ysb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("banned", foreground="#d32f2f")
        self.tree.tag_configure("unauthorized", foreground="#e67e22")
        # 自动授权账号: 黄色高亮背景(任务2)
        self.tree.tag_configure("auto_auth", background="#fff7cc")
        # 在线状态绿色文字(在 values 里单独标, 这里仅备用)
        self.tree.tag_configure("online", foreground="#0a7d00")
        
        self.tree.bind("<<TreeviewSelect>>", self._on_select_user)
        self.tree.bind("<Double-1>", self._show_user_detail)

        # 左键框选（拖拽选择）
        self._drag_start = None
        self.tree.bind("<ButtonPress-1>", self._on_drag_start, add="+")
        self.tree.bind("<B1-Motion>", self._on_drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_drag_release, add="+")

        # 操作按钮 —— 第1行：单人操作
        ops1 = tk.Frame(self.tab_account)
        ops1.pack(fill="x", padx=8, pady=(8, 2))
        self.btn_ban = tk.Button(ops1, text="封号", width=12, state="disabled",
                                 command=self._ban_dialog)
        self.btn_ban.pack(side="left", padx=4)
        self.btn_unban = tk.Button(ops1, text="解封", width=12, state="disabled",
                                   command=self._unban)
        self.btn_unban.pack(side="left", padx=4)
        self.btn_del = tk.Button(ops1, text="删除", width=10, state="disabled",
                                 command=self._delete)
        self.btn_del.pack(side="left", padx=4)
        self.btn_auth = tk.Button(ops1, text="授权", width=10, state="disabled",
                                  command=self._auth)
        self.btn_auth.pack(side="left", padx=4)
        self.btn_unauth = tk.Button(ops1, text="取消授权", width=10, state="disabled",
                                    command=self._unauth)
        self.btn_unauth.pack(side="left", padx=4)
        self.btn_transfer = tk.Button(ops1, text="转移数据", width=10, state="disabled",
                                      command=self._transfer)
        self.btn_transfer.pack(side="left", padx=4)
        self.btn_push_unlocks = tk.Button(ops1, text="推送全解锁", width=12, state="disabled",
                                          command=self._push_full_unlocks)
        self.btn_push_unlocks.pack(side="left", padx=4)
        self.btn_push_items = tk.Button(ops1, text="全物品", width=10, state="disabled",
                                        command=self._push_all_items)
        self.btn_push_items.pack(side="left", padx=4)
        tk.Button(ops1, text="数据浏览", width=10, command=self._show_data_browser).pack(
            side="left", padx=4)
        tk.Button(ops1, text="后台接口管理", width=12, command=self._open_api_response).pack(
            side="left", padx=4)

        # 操作按钮 —— 第2行：批量操作
        ops2 = tk.Frame(self.tab_account)
        ops2.pack(fill="x", padx=8, pady=(2, 8))
        self.btn_batch_ban = tk.Button(ops2, text="批量封号", width=12,
                                       state="disabled", command=self._batch_ban)
        self.btn_batch_ban.pack(side="left", padx=4)
        self.btn_batch_del = tk.Button(ops2, text="批量删除", width=12,
                                       state="disabled", command=self._batch_delete)
        self.btn_batch_del.pack(side="left", padx=4)
        self.btn_batch_auth = tk.Button(ops2, text="批量授权", width=12,
                                        state="disabled", command=self._batch_auth)
        self.btn_batch_auth.pack(side="left", padx=4)
        self.btn_batch_unauth = tk.Button(ops2, text="批量取消授权", width=14,
                                          state="disabled", command=self._batch_unauth)
        self.btn_batch_unauth.pack(side="left", padx=4)
        # 新建账号全解锁开关(非单账号控制): 开启后, 此刻之后新建的账号自动推送全解锁
        self.btn_auto_full_unlock = tk.Button(ops2, text="新建账号全解锁: 关", width=17,
                                              command=self._toggle_auto_full_unlock)
        self.btn_auto_full_unlock.pack(side="left", padx=(12, 4))

        # 初始化自动授权按钮状态(任务2)
        self._refresh_auto_auth_btn()
        # 初始化"无库就建号"按钮状态(2026-10-05)
        self._refresh_auto_create_btn()
        self._refresh_auto_full_unlock_btn()

    # ---------------- IP封禁 ----------------
    def _ipban_path(self):
        return os.path.join(self.root_dir, "config", "banned_ips.json")

    @staticmethod
    def _normalize_ip_entry(s):
        """校验并规范化 IP 封禁条目。支持:
        - IPv4 / IPv6 精确地址(自动规范化大小写/前导零/:: 压缩)
        - CIDR 网段, 如 2409:8a28::/32、192.168.0.0/24
        非法条目返回 None。"""
        import ipaddress as _ipa
        s = (s or "").strip()
        if not s:
            return None
        try:
            if "/" in s:
                return str(_ipa.ip_network(s, strict=False))
            return str(_ipa.ip_address(s))
        except ValueError:
            return None

    def _load_banned_ips(self):
        """读取手动封禁 IP 列表, 返回 list[str]。"""
        try:
            with open(self._ipban_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
            if isinstance(data, dict):
                return [str(x).strip() for x in data.get("banned_ips", [])
                        if str(x).strip()]
        except Exception:
            pass
        return []

    def _save_banned_ips(self, ips):
        path = self._ipban_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"banned_ips": list(ips)}, f, ensure_ascii=False, indent=2)

    def _build_ipban_tab(self):
        top = tk.Frame(self.tab_ipban)
        top.pack(fill="x", padx=8, pady=6)
        tk.Label(top, text="封禁 IP 列表(请求时检测真实客户端 IP, 命中返回 405 不给数据)",
                 font=("微软雅黑", 9)).pack(side="left")

        # 列表
        frame = tk.Frame(self.tab_ipban)
        frame.pack(fill="both", expand=True, padx=8)
        self.ipban_list = tk.Listbox(frame, font=("Consolas", 11))
        self.ipban_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.ipban_list.yview)
        self.ipban_list.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        # 操作区: 输入 + 添加 + 删除
        ops = tk.Frame(self.tab_ipban)
        ops.pack(fill="x", padx=8, pady=(8, 8))
        tk.Label(ops, text="IP:", font=("微软雅黑", 9)).pack(side="left")
        self.ipban_var = tk.StringVar()
        ent = tk.Entry(ops, textvariable=self.ipban_var, width=30)
        ent.pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: self._add_banned_ip())
        tk.Button(ops, text="添加", width=10, command=self._add_banned_ip).pack(side="left", padx=4)
        tk.Button(ops, text="删除选中", width=10, command=self._remove_banned_ip).pack(side="left", padx=4)
        tk.Button(ops, text="刷新", width=8, command=self._refresh_ipban_list).pack(side="left", padx=4)

        self._refresh_ipban_list()

    def _refresh_ipban_list(self):
        if not hasattr(self, "ipban_list"):
            return
        self.ipban_list.delete(0, "end")
        # 显示时规范化(IPv6 大小写/压缩统一), 非法原样显示
        shown = []
        for ip in self._load_banned_ips():
            norm = self._normalize_ip_entry(ip)
            shown.append(norm if norm is not None else ip)
        for ip in sorted(shown):
            self.ipban_list.insert("end", ip)
        self.set_status(f"IP封禁: 共 {self.ipban_list.size()} 个")

    def _add_banned_ip(self):
        ip = self.ipban_var.get().strip()
        if not ip:
            return
        norm = self._normalize_ip_entry(ip)
        if norm is None:
            messagebox.showwarning(
                "无效的 IP/网段",
                f"“{ip}”不是有效的 IP 或网段。\n\n"
                "支持:\n"
                "  IPv4   如 192.168.1.5\n"
                "  IPv6   如 2409:8a28:5672::1\n"
                "  网段   如 2409:8a28::/32、192.168.0.0/24",
                parent=self)
            return
        ips = self._load_banned_ips()
        # 去重: 已存在同规范化值则跳过
        if norm not in ips:
            ips.append(norm)
            try:
                self._save_banned_ips(ips)
            except Exception as e:
                messagebox.showerror("错误", f"写入配置失败: {e}", parent=self)
                return
            self.log(f"IP封禁: 添加 {norm}", "success")
        self.ipban_var.set("")
        self._refresh_ipban_list()

    def _remove_banned_ip(self):
        sel = self.ipban_list.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中选择要删除的 IP")
            return
        ip = self.ipban_list.get(sel[0])
        if not messagebox.askyesno("删除 IP", f"解封 IP {ip} ?"):
            return
        # 列表显示的是规范化形式, 存储可能是旧写法, 按规范化后匹配删除
        def _norm(x):
            n = self._normalize_ip_entry(x)
            return n if n is not None else x
        ips = [x for x in self._load_banned_ips() if _norm(x) != ip]
        try:
            self._save_banned_ips(ips)
        except Exception as e:
            messagebox.showerror("错误", f"写入配置失败: {e}")
            return
        self.log(f"IP封禁: 删除 {ip}", "warn")
        self._refresh_ipban_list()

    # ---------------- 账号数据刷新 ----------------
    def refresh_users(self):
        # 每次刷新账号列表时同步"新建账号全解锁"按钮的显示, 使其永远等于
        # config/auto_full_unlock.json 的真实状态: 面板多开、或外部(服务端/人)改了
        # 该配置文件时, 按钮不会停留在旧状态上骗人(旧状态下点一下会把"关"变成"开")。
        try:
            self._refresh_auto_full_unlock_btn()
        except Exception:
            pass
        if not self.db.available():
            self.set_status("数据库不存在，启动服务器后将自动创建")
            self.tree.delete(*self.tree.get_children())
            return
        try:
            self.db.ensure_table()
            self.users_cache = self.db.list_users(self.search_var.get().strip())
        except Exception as e:
            self.set_status(f"读取数据库失败: {e}")
            return
        self.tree.delete(*self.tree.get_children())
        unauth_count = 0
        online_count = 0
        now_ts = int(time.time())
        # 优先显示在线账号: 先按"是否在线"(最后请求 5 分钟内)分组, 在线的排前面;
        # 同组内再按最后活跃时间降序(最近活跃的更靠前)。不改动 users_cache 本体的
        # 其它用途, 仅用于本次列表渲染顺序。
        def _last_seen(u):
            return int(u.get("last_seen") or 0)
        def _online_key(u):
            ls = _last_seen(u)
            is_online = 1 if (ls > 0 and (now_ts - ls) <= 300) else 0
            # 在线优先(is_online 降序) + 最近活跃优先(last_seen 降序)
            return (-is_online, -ls)
        ordered_users = sorted(self.users_cache, key=_online_key)
        for u in ordered_users:
            banned = u.get("is_banned")
            authorized = u.get("is_authorized")
            if banned:
                end = u.get("ban_end_time") or 0
                ban_txt = "永久" if not end else "封禁中"
                end_txt = "永久" if not end else self._fmt_ts(end)
            else:
                ban_txt, end_txt = "正常", "-"
            if banned:
                tags = ("banned",)
            elif not authorized:
                tags = ("unauthorized",)
                unauth_count += 1
            elif (u.get("remark") or "") == "AUTO_AUTH":
                # 自动授权新建账号: 标黄区分(任务2)
                tags = ("auto_auth",)
            else:
                tags = ()
            # 在线状态: 最后请求时间在 5 分钟内视为在线
            last_seen = int(u.get("last_seen") or 0)
            online = (last_seen > 0 and (now_ts - last_seen) <= 300)
            online_txt = "在线" if online else "离线"
            if online:
                online_count += 1
            self.tree.insert("", "end", iid=u["id"], values=(
                u["id"], u.get("device_name") or "-", u.get("platform") or self._parse_platform(u.get("user_agent","")),
                u.get("version") or self._parse_version(u.get("user_agent","")),
                online_txt, ban_txt, end_txt,
                self._fmt_ts(u.get("created_at"))),
                tags=tags)
        total, banned = self.db.stats()
        self.stat_var.set("总数: %d  封禁: %d  未授权: %d  在线: %d" % (
            total, banned, unauth_count, online_count))
        self.set_status(f"已加载 {len(self.users_cache)} 个账号")

    @staticmethod
    def _parse_version(ua):
        import re
        if not ua:
            return "-"
        # 兼容高低版本: Sky-Live / Sky-Test-com.tgc.sky.android.test.gold 等
        # UA: Sky-<平台>/<版本> (...), 取斜杠后的版本号
        m = re.search(r"Sky-[^/\s]+/([\d.]+)", ua)
        if m:
            return m.group(1)
        # 兜底: 任意 "/版本号(含至少一个点)" 形式
        m = re.search(r"/([\d]+(?:\.[\d]+)+)", ua)
        return m.group(1) if m else "-"

    @staticmethod
    def _parse_platform(ua):
        import re
        if not ua:
            return "-"
        # 兼容高低版本: 取斜杠前的整个 Sky-* 平台标识
        # 高版本: Sky-Test-com.tgc.sky.android.test.gold
        # 低版本: Sky-Live-com.tgc.sky.android 等
        m = re.search(r"(Sky-[^/\s]+)/", ua)
        if m:
            return m.group(1)
        # 兜底: 没有斜杠时取 Sky- 开头到空白
        m = re.search(r"(Sky-[^\s]+)", ua)
        return m.group(1) if m else "-"

    @staticmethod
    def _fmt_ts(ts):
        try:
            ts = int(ts)
            if ts <= 0:
                return "-"
            if ts > 1e11:
                ts /= 1000
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "-"

    def _get_selected_uids(self):
        return list(self.tree.selection())

    def _on_select_user(self, event=None):
        uids = self._get_selected_uids()
        count = len(uids)
        if count == 0:
            self.selected_uid = None
            singles = (self.btn_ban, self.btn_unban, self.btn_del,
                       self.btn_auth, self.btn_unauth, self.btn_transfer,
                       self.btn_push_unlocks, self.btn_push_items)
            batches = (self.btn_batch_ban, self.btn_batch_del,
                       self.btn_batch_auth, self.btn_batch_unauth)
            for b in singles + batches:
                b.config(state="disabled")
            return

        self.selected_uid = uids[0]
        user = next((u for u in self.users_cache if u["id"] == self.selected_uid), None)

        # 单选按钮
        if count == 1:
            self.btn_del.config(state="normal")
            self.btn_transfer.config(state="normal")
            self.btn_push_unlocks.config(state="normal")
            self.btn_push_items.config(state="normal")
            if user:
                self.btn_ban.config(state="disabled" if user.get("is_banned") else "normal")
                self.btn_unban.config(state="normal" if user.get("is_banned") else "disabled")
                self.btn_auth.config(state="disabled" if user.get("is_authorized") else "normal")
                self.btn_unauth.config(state="normal" if user.get("is_authorized") else "disabled")
        else:
            for b in (self.btn_ban, self.btn_unban, self.btn_del,
                      self.btn_auth, self.btn_unauth, self.btn_transfer,
                      self.btn_push_unlocks, self.btn_push_items):
                b.config(state="disabled")

        # 批量按钮
        if count >= 2:
            for b in (self.btn_batch_ban, self.btn_batch_del,
                      self.btn_batch_auth, self.btn_batch_unauth):
                b.config(state="normal")
        else:
            for b in (self.btn_batch_ban, self.btn_batch_del,
                      self.btn_batch_auth, self.btn_batch_unauth):
                b.config(state="disabled")

    # ---------------- 框选支持 ----------------
    def _on_drag_start(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            if not (event.state & 0x0004):  # 不按 Ctrl
                self.tree.selection_set(item)
            self._drag_start = item

    def _on_drag_motion(self, event):
        if not self._drag_start:
            return
        current = self.tree.identify_row(event.y)
        if current and current != self._drag_start:
            # 选中从 _drag_start 到 current 之间的所有行
            all_items = self.tree.get_children()
            try:
                start_idx = all_items.index(self._drag_start)
                end_idx = all_items.index(current)
                if start_idx > end_idx:
                    start_idx, end_idx = end_idx, start_idx
                for i in range(start_idx, end_idx + 1):
                    self.tree.selection_add(all_items[i])
            except ValueError:
                pass

    def _on_drag_release(self, event):
        self._drag_start = None

    def _show_user_detail(self, event=None):
        """\u53cc\u51fb\u7528\u6237 \u2192 \u6253\u5f00\u7528\u6237\u6570\u636e\u7f16\u8f91\u5668"""
        if not self.selected_uid:
            return
        user = self.db.get_user(self.selected_uid)
        if not user:
            return
        try:
            UserDataEditor(self, self.root_dir, self.selected_uid)
        except Exception as e:
            messagebox.showerror("\u6253\u5f00\u5931\u8d25", str(e), parent=self)

    def _show_data_browser(self):
        """打开数据浏览器（数据库表查看/编辑）"""
        if not self.selected_uid:
            return
        user = self.db.get_user(self.selected_uid)
        if not user:
            messagebox.showwarning("提示", "用户不存在")
            return
        UserDataEditor(self, self.root_dir, self.selected_uid)

    def _push_full_unlocks(self):
        """推送高版本全解锁(全动作/全光翼/全门先祖能力)给选中账号。"""
        if not self.selected_uid:
            return
        uid = self.selected_uid
        if not messagebox.askyesno(
                "推送全解锁",
                f"将给账号 {uid} 推送高版本全解锁:\n"
                "· 全部动作(emote)\n· 全部地图/开门\n"
                "· 全部衣服/先祖能力\n· 全部光翼(光翼数量)\n\n"
                "已拥有的会自动跳过。确定?"):
            return
        try:
            r = self.db.push_full_unlocks(uid, scope="all")
            summary = (f"动作+{r['emotes']} 图门+{r['maps']} "
                       f"衣服+{r['unlocks']} 光翼+{r['wings']}")
            self.set_status(f"已推送全解锁给 {uid}: {summary}")
            self.log(f"推送全解锁 {uid}: {summary}", "success")
            errs = r.get("errors") or []
            extra = ("\n\n部分类别出错(其余已成功):\n" + "\n".join(errs)) if errs else ""
            if errs:
                for e in errs:
                    self.log(f"推送全解锁部分失败 {uid}: {e}", "warn")
            messagebox.showinfo(
                "推送完成",
                f"账号 {uid}\n新增动作: {r['emotes']}\n新增图门: {r['maps']}\n"
                f"新增衣服: {r['unlocks']}\n新增光翼: {r['wings']}\n\n"
                "玩家重新登录/同步后生效。" + extra)
        except Exception as e:
            self.log(f"推送全解锁失败 {uid}: {e}", "error")
            messagebox.showerror("推送失败", str(e))

    def _push_all_items(self):
        """全物品: 给选中账号解锁配置目录里所有 Char* 服饰/道具(全外观)。"""
        if not self.selected_uid:
            return
        uid = self.selected_uid
        if not messagebox.askyesno(
                "全物品",
                f"将给账号 {uid} 解锁\"全部服饰道具\":\n"
                "· 所有发型 / 身体 / 面具 / 斗篷 / 项链 / 道具等\n"
                "  (配置里所有 Char 开头的外观, 一件不漏)\n\n"
                "已拥有的会自动跳过。确定?"):
            return
        try:
            r = self.db.push_all_items(uid)
            self.set_status(f"已给 {uid} 解锁全部服饰: 新增 {r['added']}/{r['total']}")
            self.log(f"全物品 {uid}: 新增 {r['added']}/{r['total']}", "success")
            errs = r.get("errors") or []
            if errs:
                for e in errs[:20]:
                    self.log(f"全物品部分失败 {uid}: {e}", "warn")
            extra = ("\n\n部分项出错(其余已成功):\n" + "\n".join(errs[:10])) if errs else ""
            messagebox.showinfo(
                "全物品完成",
                f"账号 {uid}\n配置里共发现服饰道具: {r['total']}\n"
                f"本次新增解锁: {r['added']}\n\n"
                "玩家重新登录/同步后生效。" + extra)
        except Exception as e:
            self.log(f"全物品失败 {uid}: {e}", "error")
            messagebox.showerror("全物品失败", str(e))

    def _open_api_response(self, uid=None):
        target_uid = uid or self.selected_uid
        if not target_uid:
            target_uid = ""
        try:
            ApiResponseManager(self, self.root_dir, target_uid)
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开接口响应管理:{e}", parent=self)


    # ================= 服务器启停 =================
    def _set_server_state(self, state):
        self.server_state = state
        colors = {"stopped": ("#9e9e9e", "已停止"),
                  "starting": ("#ff9800", "启动中"),
                  "running": ("#4caf50", "运行中")}
        color, text = colors.get(state, ("#9e9e9e", "未知"))
        self.state_dot.itemconfig(self.state_oval, fill=color)
        self.state_label.config(text=text)
        if state == "stopped":
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            self.btn_chdir.config(state="normal")
        elif state == "starting":
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.btn_chdir.config(state="disabled")
        else:
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.btn_chdir.config(state="disabled")
        self.set_status(text)

    def start_server(self):
        if self.proc and self.proc.poll() is None:
            return
        # 旧源码快照可能没有 sky_boot.py：原来会直接 Popen 一个不存在的文件，
        # 报 "can't open file '...sky_boot.py'" 后瞬间退出。这里先校验并补齐。
        root = os.path.abspath(self.root_dir or "")
        if not os.path.isfile(os.path.join(root, "index.py")):
            messagebox.showerror(
                "源码目录无效",
                "当前源码目录里找不到 index.py：\n%s\n\n请点『切换源码目录』重新选择。" % root,
                parent=self,
            )
            return
        ok, message = ensure_boot_script(root, self.log)
        if not ok:
            messagebox.showerror("无法启动私服", message, parent=self)
            return
        self._set_server_state("starting")
        threading.Thread(target=self._start_worker, daemon=True).start()

    # ================= UDP(xysky)联机服务器: 多房间 + 串房 =================
    UDP_ROOM_COUNT_DEFAULT = 3
    UDP_ROOM_MAX = 8

    def _udp_room_count(self):
        """房间数(room_config.json 的 room_count, 默认 3, 最多 8)。

        运行时补房会把 room_count 往上写, 所以这里同时参考 pidfile 里活着的
        房间数, 避免面板重启后把已有的第 4/5 个房间当成不存在。
        """
        try:
            c = self._load_room_config()
            n = int(c.get("room_count", self.UDP_ROOM_COUNT_DEFAULT) or self.UDP_ROOM_COUNT_DEFAULT)
        except Exception:
            n = self.UDP_ROOM_COUNT_DEFAULT
        try:
            alive = len(self._alive_udp_records())
            if alive:
                n = max(n, alive)
        except Exception:
            pass
        return max(1, min(n, self.UDP_ROOM_MAX))

    def _udp_room_ip_host(self):
        """房间地址里给客户端的 host: 优先配置的 ip_host(公网/局域网), 否则探测。"""
        try:
            c = self._load_room_config()
            ip = str(c.get("ip_host") or "").strip()
        except Exception:
            ip = ""
        return ip or self._detect_lan_ip() or "127.0.0.1"

    @classmethod
    def _udp_room_targets(cls, count, base_port, ip_host):
        """每个房间都认识所有「可能」房间的地址(MoveGame 目标)。

        这里固定生成 UDP_ROOM_MAX 个目标, 而不是只生成当前房间数: 面板在运行
        中补房时, 已经在跑的房间不需要重启就能把人 MoveGame 到新房间; 客户端
        也只会被送到真实存在的房间(串房线程只选活着的)。
        """
        return [{"id": "sky-room-%d" % (i + 1), "host": ip_host,
                 "port": base_port + i, "capacity": 8, "players": 0, "levels": []}
                for i in range(cls.UDP_ROOM_MAX)]

    def _udp_pidfile_records(self, info=None):
        info = info if info is not None else self._read_udp_pidfile()
        if not info:
            return []
        if isinstance(info.get("procs"), list):
            return [r for r in info["procs"] if isinstance(r, dict) and r.get("pid")]
        if info.get("pid"):
            return [{"pid": info["pid"], "port": None, "id": "sky-room-1"}]
        return []

    def _alive_udp_records(self):
        return [r for r in self._udp_pidfile_records() if self._pid_alive(r.get("pid"))]

    def _kill_udp_records(self, records):
        for rec in records or []:
            pid = rec.get("pid")
            if not pid:
                continue
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, shell=True)
            except Exception:
                pass

    def _spawn_udp_room(self, index, base_port, ip_host, targets, records):
        """启动一个房间 node 进程(index 0-based), 返回 pidfile 记录。

        端口规则: UDP = realtime_port + index, HTTP = 8080 + index。运行中补房
        也走这里, 所以启动流程与补房流程完全一致。
        """
        node = self._find_node()
        if not node:
            raise RuntimeError("未找到 node, 无法启动 UDP 房间")
        port = int(base_port) + int(index)
        http_port = UDP_HTTP_PORT + int(index)
        env = dict(os.environ)
        env["ROOM_ID"] = "sky-room-%d" % (int(index) + 1)
        env["ROOM_PORT"] = str(port)
        env["HTTP_PORT"] = str(http_port)
        env["MOVE_TARGETS"] = json.dumps(targets, ensure_ascii=False)
        flags = subprocess.CREATE_NO_WINDOW
        try:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        except Exception:
            pass
        try:
            lf = open(self._udp_runtime_log(port), "w", encoding="utf-8", errors="replace")
        except Exception:
            lf = subprocess.DEVNULL
        try:
            proc = subprocess.Popen(
                [node, UDP_ENTRY], cwd=UDP_SRC_DIR, stdout=lf,
                stderr=subprocess.STDOUT, creationflags=flags, env=env)
        finally:
            if lf is not subprocess.DEVNULL:
                try:
                    lf.close()
                except Exception:
                    pass
        ensure_firewall_udp(self.log, port)
        procs = getattr(self, "udp_procs", None)
        if procs is None:
            procs = self.udp_procs = []
        procs.append(proc)
        rec = {"pid": proc.pid, "port": port, "http": http_port,
               "id": env["ROOM_ID"], "index": int(index)}
        records.append(rec)
        self.log("UDP 房间 %d 已启动 (pid=%d, udp=:%d, http=:%d)"
                 % (int(index) + 1, proc.pid, port, http_port), "success")
        return rec

    def _room_record_index(self, rec):
        """从 pidfile 记录里取 0-based 房间下标(兼容旧记录: 只有 pid/port)。"""
        try:
            idx = rec.get("index")
            if idx is not None:
                return int(idx)
        except Exception:
            pass
        rid = str(rec.get("id") or "")
        m = re.search(r"(\d+)$", rid)
        if m:
            return int(m.group(1)) - 1
        try:
            base = int(self._load_room_config().get("realtime_port", 25565) or 25565)
            return int(rec.get("port")) - base
        except Exception:
            return 0

    def start_udp_server(self):
        """启动 UDP 联机服务器(xysky 多房间)。随私服一起启动。

        每个房间 = 一个独立 node 进程: ROOM_PORT=realtime_port+i, HTTP_PORT=8080+i;
        所有房间通过 MOVE_TARGETS 互相认识, 面板串房线程用 MoveGame 把玩家从空房/
        少人房并到人多房(或按游戏机制反向分流), 客户端不用重开。
        """
        try:
            if getattr(self, "udp_procs", None) and any(p.poll() is None for p in self.udp_procs):
                return
            alive = self._alive_udp_records()
            expected = self._udp_room_count()
            if alive:
                if len(alive) >= expected and self._udp_has_history_api():
                    self.udp_procs = []
                    self.log("UDP 联机服务器已在运行(%d 个房间), 跳过启动" % len(alive), "info")
                    self._start_room_balancer()
                    return
                self.log("检测到旧 UDP 进程(%d 个), 重启为多房间版本..." % len(alive), "warn")
                self._kill_udp_records(alive)
                self._clear_udp_pidfile()
                time.sleep(0.8)
            entry = os.path.join(UDP_SRC_DIR, UDP_ENTRY)
            if not os.path.isfile(entry):
                self.log("UDP 源码缺失, 跳过启动: %s" % entry, "warn")
                return
            node = self._find_node()
            if not node:
                self.log("未找到 node, 无法启动 UDP 服务器(请安装 Node.js)", "warn")
                return
            cfg = self._load_room_config()
            base_port = int(cfg.get("realtime_port", 25565) or 25565)
            count = self._udp_room_count()
            ip_host = self._udp_room_ip_host()
            targets = self._udp_room_targets(count, base_port, ip_host)
            self.udp_procs = []
            records = []
            for i in range(count):
                try:
                    self._spawn_udp_room(i, base_port, ip_host, targets, records)
                except Exception as e:
                    self.log("UDP 房间 %d 启动失败: %s" % (i + 1, e), "error")
            self._write_udp_pidfile(records)
            self._start_room_balancer()
            self._ensure_public_access()
        except Exception as e:
            self.log("UDP 服务器启动失败: %s" % e, "error")

    def stop_udp_server(self):
        """停止全部 UDP 房间进程。随私服一起停止。"""
        procs = list(getattr(self, "udp_procs", []) or [])
        try:
            for proc in procs:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except Exception:
                    pass
            for proc in procs:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            if procs:
                self.log("UDP 联机服务器已停止(%d 个房间)" % len(procs), "info")
        except Exception as e:
            self.log("停止 UDP 服务器异常: %s" % e, "warn")
        finally:
            self.udp_procs = []
            records = self._alive_udp_records()
            if records:
                self._kill_udp_records(records)
            self._clear_udp_pidfile()
            self._stop_room_balancer()

    def restart_udp_server(self):
        """重启 UDP 联机服务器(热更新 Node 源码用)。"""
        self.stop_udp_server()
        time.sleep(0.6)
        self.start_udp_server()

    # ---------- 串房: 空房/少人房 -> 人多房, 按游戏机制合并; 也可反向分流 ----------
    def _start_room_balancer(self):
        th = getattr(self, "_balancer_thread", None)
        if th and th.is_alive():
            return
        self._balancer_stop = threading.Event()
        self._balancer_thread = threading.Thread(
            target=self._room_balancer_loop, daemon=True)
        self._balancer_thread.start()

    def _stop_room_balancer(self):
        ev = getattr(self, "_balancer_stop", None)
        if ev is not None:
            ev.set()
        self._balancer_thread = None

    def _http_json(self, url, payload=None, timeout=2.0):
        import urllib.request
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=data, headers=headers,
            method="POST" if payload is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")

    def _room_balancer_loop(self):
        stable = {}
        stop = self._balancer_stop
        while not stop.wait(3):
            try:
                self._ensure_empty_rooms()
            except Exception as e:
                self.log("自动补房异常: %s" % e, "warn")
            try:
                self._balance_rooms_once(stable)
            except Exception as e:
                self.log("串房线程异常: %s" % e, "warn")

    def _room_records_with_occupancy(self, timeout=1.5):
        """枚举活着房间 + 各自的 occupancy(用于补房/串房决策)。"""
        rooms = []
        for rec in self._alive_udp_records():
            http = rec.get("http")
            if not http:
                try:
                    http = UDP_HTTP_PORT + self._room_record_index(rec)
                except Exception:
                    continue
            try:
                d = self._http_json("http://127.0.0.1:%d/occupancy" % int(http),
                                    timeout=timeout)
            except Exception:
                continue
            d["_index"] = self._room_record_index(rec)
            d["_http"] = int(http)
            d["_pid"] = rec.get("pid")
            rooms.append(d)
        return rooms

    def _ensure_empty_rooms(self):
        """空房间不足时立刻补房。

        规则(可在 room_config.json 调): 空房间(在线 0 人)少于 `min_empty_rooms`
        (默认 2)时, 立即新建房间, 补到 `keep_empty_rooms`(默认 3)个空房。
        房号/端口沿用 index 规则: UDP=realtime_port+index, HTTP=8080+index;
        已在跑的房间开局就持有全部 8 个 MOVE_TARGETS, 所以补房后无需重启,
        串房线程可以直接把玩家 MoveGame 到新房间。
        """
        cfg = self._load_room_config()
        if not cfg.get("auto_scale_rooms", True):
            return
        if not cfg.get("single_shared_room", True) or not cfg.get("lan_udp_enabled", True):
            return
        try:
            min_empty = int(cfg.get("min_empty_rooms", 2) or 2)
        except Exception:
            min_empty = 2
        try:
            keep_empty = int(cfg.get("keep_empty_rooms", 3) or 3)
        except Exception:
            keep_empty = 3
        keep_empty = max(keep_empty, min_empty + 1)
        rooms = self._room_records_with_occupancy()
        if not rooms:
            return
        alive_n = len(self._alive_udp_records())
        # 刚补的 node 还没监听时 occupancy 拉不到, 按「活着但未响应=空房」算,
        # 防止 3 秒后重复补房把房间数冲高。
        empty = sum(1 for r in rooms if int(r.get("players") or 0) == 0)
        empty += max(0, alive_n - len(rooms))
        if empty >= min_empty:
            return
        base_port = int(cfg.get("realtime_port", 25565) or 25565)
        ip_host = self._udp_room_ip_host()
        targets = self._udp_room_targets(0, base_port, ip_host)
        recs = self._alive_udp_records()
        used = set()
        for rec in recs:
            try:
                used.add(self._room_record_index(rec))
            except Exception:
                pass
        added = 0
        idx = 0
        while empty + added < keep_empty and len(recs) < self.UDP_ROOM_MAX:
            while idx in used:
                idx += 1
            if idx >= self.UDP_ROOM_MAX:
                break
            try:
                self._spawn_udp_room(idx, base_port, ip_host, targets, recs)
            except Exception as e:
                self.log("自动补房失败(房间 %d): %s" % (idx + 1, e), "warn")
                break
            used.add(idx)
            added += 1
            idx += 1
        if added:
            self._write_udp_pidfile(recs)
            cfg["room_count"] = max(int(cfg.get("room_count") or 3), len(recs))
            self._save_room_config(cfg)
            self.log("空房间只剩 %d 个, 已立刻补 %d 个空房间 (当前共 %d 个, room_count=%d)"
                     % (empty, added, len(recs), cfg["room_count"]), "success")

    def _balance_rooms_once(self, stable):
        cfg = self._load_room_config()
        if not cfg.get("auto_merge_rooms", True):
            return
        if not cfg.get("single_shared_room", True) or not cfg.get("lan_udp_enabled", True):
            return
        rooms = self._room_records_with_occupancy()
        if len(rooms) < 2:
            return
        try:
            cap = int(cfg.get("room_capacity_players", 8) or 8)
        except Exception:
            cap = 8
        by_level = {}
        for room in rooms:
            for lv, players in (room.get("levels") or {}).items():
                if players:
                    by_level.setdefault(str(lv), []).append((room, players))
        now = time.time()
        merge_after = float(cfg.get("room_merge_after_seconds", 25) or 25)
        for lv, entries in by_level.items():
            if len(entries) < 2:
                continue
            entries.sort(key=lambda item: -len(item[1]))
            target_room, _target_players = entries[0]
            total = sum(len(p) for _, p in entries)
            if total > cap:
                continue  # 目标房容量
            for room, players in entries[1:]:
                key = (str(room.get("roomId")), lv)
                since = stable.get(key)
                if since is None:
                    stable[key] = now
                    continue
                if now - since < merge_after:
                    continue
                stable.pop(key, None)
                try:
                    res = self._http_json(
                        "http://127.0.0.1:%d/move/level" % room["_http"],
                        {"levelId": int(lv), "roomId": target_room.get("roomId")})
                    self.log("串房: 关卡 %s 的 %d 人从 %s 并到 %s (%s)"
                             % (lv, len(players), room.get("roomId"),
                                target_room.get("roomId"), res), "info")
                    self._remember_room_assign(players, target_room.get("_index", 0))
                except Exception as e:
                    self.log("串房失败(关卡 %s -> %s): %s"
                             % (lv, target_room.get("roomId"), e), "warn")

    def _remember_room_assign(self, players, index):
        """把被串房搬走的 uuid -> 房间下标 写进 config/room_assign.json,
        客户端重新请求 find_previous_or_empty 时仍回同一个房间, 避免来回串。"""
        path = os.path.join(self.root_dir, "config", "room_assign.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
        assign = data.get("assign") or {}
        now = int(time.time())
        for p in players or []:
            uuid = str(p.get("uuid") or "").strip()
            if uuid:
                assign[uuid] = [int(index), now]
        assign = {k: v for k, v in assign.items()
                  if isinstance(v, list) and (now - (v[1] if len(v) > 1 else 0)) < 7200}
        data["assign"] = assign
        data["updated"] = now
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            self.log("写入房间归属失败: %s" % e, "warn")

    # ============ 局域网 UDP 联机总开关 (config/room_config.json) ============
    def _room_config_path(self):
        return os.path.join(self.root_dir, "config", "room_config.json")

    def _load_room_config(self):
        try:
            with open(self._room_config_path(), encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _save_room_config(self, cfg):
        try:
            with open(self._room_config_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.log("写入 room_config.json 失败: %s" % e, "error")
            return False

    def _lan_udp_enabled(self):
        """当前"局域网UDP联机"是否开启(两个键都为真才算开)。"""
        c = self._load_room_config()
        return bool(c.get("lan_udp_enabled", True)) and bool(c.get("single_shared_room", True))

    def _refresh_lanudp_btn(self):
        try:
            if not hasattr(self, "btn_lanudp"):
                return
            on = self._lan_udp_enabled()
            self.btn_lanudp.config(
                text=("🌐 局域网UDP联机: 开" if on else "🌐 局域网UDP联机: 关"))
        except Exception:
            pass

    def _detect_lan_ip(self):
        """探测本机局域网 IPv4(同 WiFi 玩家能直连的那个)。失败返回空串。"""
        try:
            import socket as _sock
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
            finally:
                s.close()
            return ip or ""
        except Exception:
            return ""

    def _local_ipv4_addrs(self):
        """本机所有 IPv4 地址(含探测到的默认出口地址)。"""
        import socket as _sock
        addrs = set()
        for probe in (lambda: _sock.gethostbyname_ex(_sock.gethostname())[2],
                      lambda: [i[4][0] for i in _sock.getaddrinfo(
                          _sock.gethostname(), None, _sock.AF_INET)]):
            try:
                addrs.update(a for a in probe() if a)
            except Exception:
                pass
        d = self._detect_lan_ip()
        if d:
            addrs.add(d)
        addrs.discard("127.0.0.1")
        return addrs

    def _auto_lan_ip(self, cur):
        """把配置里的 ip_host 自愈为当前可达地址。

        私网 IP 且已不属于本机(换 WiFi / DHCP 改址) -> 换成探测值; 公网 IP
        (内网穿透 / VPS 转发) 与仍有效的本机地址原样保留。
        """
        det = self._detect_lan_ip()
        cur = str(cur or "").strip()
        if not det:
            return cur
        if not cur:
            return det
        try:
            a, b = [int(x) for x in cur.split(".")[:2]]
        except Exception:
            return cur
        is_private = (a == 10 or (a == 172 and 16 <= b <= 31)
                      or (a == 192 and b == 168))
        if is_private and cur not in self._local_ipv4_addrs():
            return det
        return cur

    def toggle_lan_udp(self):
        """局域网 UDP 联机总开关。
        开 -> 下发真房间(ip_host:realtime_port) + 启动 UDP 联机服务器;
        关 -> 只回原来的假房间(房间池) + 停掉 UDP 联机服务器。
        房间地址是客户端每次联网时实时读取的, 立即对新连接生效。
        """
        new = not self._lan_udp_enabled()
        c = self._load_room_config()
        c["lan_udp_enabled"] = new
        c["single_shared_room"] = new
        # 开启时把残留的旧局域网 IP 自愈成当前本机 IP(否则同一 WiFi 的玩家连不上)。
        if new:
            c["ip_host"] = self._auto_lan_ip(c.get("ip_host"))
        if not self._save_room_config(c):
            return
        self._refresh_lanudp_btn()
        if new:
            self.start_udp_server()
            self.log("局域网UDP联机: 开 —— find_previous_or_empty 下发真房间 %s:%s, 并启动 UDP(ENet) 联机服务器"
                     % (c.get("ip_host"), c.get("realtime_port")), "success")
        else:
            self.stop_udp_server()
            self.log("局域网UDP联机: 关 —— 只回原来的假房间(房间池), 已停止 UDP 联机服务器", "warn")
        try:
            self.set_status("局域网UDP联机已%s" % ("开启" if new else "关闭"))
        except Exception:
            pass

    def _find_node(self):
        """定位 node 可执行文件: 优先 PATH, 退而求其次常见安装路径。"""
        import shutil as _sh
        node = _sh.which("node")
        if node:
            return node
        for cand in (r"C:\Program Files\nodejs\node.exe",
                     r"C:\Program Files (x86)\nodejs\node.exe",
                     os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                  "Programs", "nodejs", "node.exe")):
            if os.path.isfile(cand):
                return cand
        return None

    def _udp_runtime_log(self, port=None):
        log_dir = os.path.join(UDP_SRC_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        if port is None:
            return os.path.join(log_dir, "_udp_runtime_%d.log" % self.server_port)
        return os.path.join(log_dir, "_udp_runtime_%d_room%d.log" % (self.server_port, port))

    def _udp_pidfile(self):
        return os.path.join(UDP_SRC_DIR, ".udp_server_%d.pid" % self.server_port)

    def _write_udp_pidfile(self, records):
        try:
            with open(self._udp_pidfile(), "w", encoding="utf-8") as f:
                json.dump({"procs": list(records), "started": int(time.time())}, f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _read_udp_pidfile(self):
        try:
            with open(self._udp_pidfile(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _clear_udp_pidfile(self):
        try:
            os.remove(self._udp_pidfile())
        except Exception:
            pass

    def _udp_has_history_api(self, timeout=2.0):
        """探测正在运行的 UDP 服务器是否带磁盘缓存接口(/packets?all=1)。

        旧 node 进程没有这个接口: 监控窗口拉不到本次运行期间的历史包,
        也没有客户端版本号。启动时据此判断要不要重启成新代码。
        """
        import urllib.request
        try:
            url = "http://127.0.0.1:%d/packets?all=1&since=0&limit=1" % UDP_HTTP_PORT
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            return bool(data.get("all"))
        except Exception:
            return False

    def open_stream_viewer(self):
        """唤起/聚焦私服启动时自动弹出的"请求监控面板"窗口。

        该面板由服务器进程(index.py)内的 request_monitor_gui.start_monitor_gui()
        启动, 通过进程内队列实时接收请求/响应。主控是独立进程, 无法直接操作它,
        因此这里用 Win32 API 按窗口标题把它带到前台。
        """
        if not (self.proc and self.proc.poll() is None):
            messagebox.showinfo(
                "请求监控面板",
                "请求监控面板会在私服启动时自动弹出。\n请先启动服务器。",
                parent=self)
            return
        # 首选: 写 show 标记文件, 监控面板(在服务器进程内)轮询到后会自己 deiconify
        # 重新显示, 并带着关闭前的全部数据。这种方式 tkinter 状态一致, 最可靠。
        try:
            marker = os.path.join(self._state_dir(), ".show_monitor")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("1")
            self.set_status("已请求唤起请求监控面板")
        except Exception:
            pass
        # 兜底: 同时用 Win32 把窗口带到前台(若它只是被压在后面)
        # 多开时监控窗口标题带端口后缀, 这里按本实例端口匹配, 只唤起自己的窗口。
        title = "Sky 私服 - 请求监控面板 :%d" % self.server_port
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                SW_RESTORE = 9
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def open_udp_viewer(self):
        """打开"UDP 联机接口响应查看"窗口。

        原理与"接口请求响应查看"一致(左列表+右详情、彩色状态、实时刷新),
        但数据源是 xysky(UDP 联机服务器)对外的 HTTP 接口, 只显示 UDP 联机
        相关接口(/health、/players、/rooms、/move/*、/levels)的响应数据。
        该窗口直接跑在面板进程里, 用后台线程轮询, 重复点击只聚焦已有窗口。
        """
        try:
            win = getattr(self, "_udp_win", None)
            if win is not None and win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_force()
                return
        except Exception:
            pass
        try:
            self._udp_win = UdpMonitorWindow(self, base_url="http://127.0.0.1:8080")
            self.set_status("已打开 UDP 接口响应查看")
        except Exception as e:
            messagebox.showerror("UDP 接口响应查看", "打开失败: %s" % e, parent=self)

    def _log_file(self):
        """本实例的日志文件路径(按端口区分, 多开时互不覆盖)。

        原来写死 H:/sky_server_%d.log —— 那是开发机上的盘符。用户机器上没有 H: 盘,
        每次 open(..., 'a') 都抛 FileNotFoundError, 于是"日志持续写文件"整体失效:
        面板界面里的日志框还在, 但一关面板历史就没了, 出问题没法回溯。
        改为写在本机源码目录下(root_dir), 保证一定可写; 仍带端口号以区分多开。
        """
        try:
            return self._sky_log_path("panel_%d.log" % self.server_port)
        except Exception:
            return LOG_FILE

    def _port_in_use(self, port):
        """检测端口是否已被监听。用 netstat 查 LISTENING 状态(最可靠)。

        之前用 connect/bind 探测在 Windows 下都不可靠: eventlet 的 listen() 设了
        SO_REUSEADDR, Windows 允许重复 bind, 导致试绑定误判为空闲, 选回同一端口后
        第二个服务器 listen 仍冲突报 10048。改为解析 netstat 的 LISTENING 行,
        按"本地地址末尾 :端口"精确匹配, 与系统语言无关。
        """
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, shell=True,
                encoding="utf-8", errors="replace"
            ).stdout or ""
        except Exception:
            return False
        target = ":%d" % port
        for line in out.splitlines():
            up = line.upper()
            if "LISTENING" not in up:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            local = parts[1]  # 形如 0.0.0.0:8443 / [::]:8443 / [2409:..]:8443
            # 取最后一个冒号后的端口段, 精确比对(避免 8443 命中 84431)
            if local.rsplit(":", 1)[-1] == str(port) and local.endswith(target):
                return True
        return False

    def _pick_free_port(self, base):
        """从 base 起找一个空闲端口(最多向上找 30 个)。多开时自动避让。"""
        for p in range(int(base), int(base) + 30):
            if 1 <= p <= 65535 and not self._port_in_use(p):
                return p
        return int(base)

    def _resolve_port(self, root_dir):
        """解析本实例端口: <源码目录>/.sky_port > panel_config.server_port > 8443。"""
        try:
            p = os.path.join(root_dir, "var", "data", "runtime", ".sky_port")
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    v = int((f.read() or "").strip())
                if 1 <= v <= 65535:
                    return v
        except Exception:
            pass
        try:
            return int(load_panel_config().get("server_port", SERVER_PORT))
        except Exception:
            return SERVER_PORT

    def _sky_log_path(self, filename):
        log_dir = os.path.join(self.root_dir, "var", "logs")
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, filename)

    def _runtime_log(self):
        """服务器运行日志文件(按端口区分), 后台模式下面板用它读日志。"""
        return self._sky_log_path("server_runtime_%d.log" % self.server_port)

    def _state_dir(self):
        directory = os.path.join(self.root_dir, "var", "data", "runtime")
        os.makedirs(directory, exist_ok=True)
        return directory

    def _pidfile(self):
        """PID 文件路径(按端口区分, 多开各一份)。"""
        return os.path.join(self._state_dir(), ".sky_server_%d.pid" % self.server_port)

    def _write_pidfile(self, pid, port):
        try:
            with open(self._pidfile(), "w", encoding="utf-8") as f:
                json.dump({"pid": pid, "port": port, "started": int(time.time())}, f)
        except Exception:
            pass

    def _read_pidfile(self):
        try:
            with open(self._pidfile(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _clear_pidfile(self):
        try:
            p = self._pidfile()
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    @staticmethod
    def _pid_alive(pid):
        """判断 PID 是否存活(Windows: tasklist 过滤)。"""
        if not pid:
            return False
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                capture_output=True, text=True, shell=True,
                encoding="utf-8", errors="replace").stdout or ""
            return str(pid) in out
        except Exception:
            return False

    def _start_worker(self):
        try:
            self.log("==== 开始环境配置 ====", "info")
            # 任务3: 新会话默认关服删除接口缓存, 清掉上次的保存标记, 让用户重新决定
            try:
                keep = os.path.join(self.root_dir, "request_dumps", ".keep_cache")
                if os.path.exists(keep):
                    os.remove(keep)
                # 同时清掉可能残留的监控窗口"显示"标记
                showm = os.path.join(self._state_dir(), ".show_monitor")
                if os.path.exists(showm):
                    os.remove(showm)
            except Exception:
                pass
            # 多开支持: 端口由真正绑定的 index.py 决定 —— 面板不再预先抢占/检测端口
            # (Windows 下预检测有竞态/SO_REUSEADDR 误判)。这里把"期望端口"作为起点
            # 通过 SKY_PORT 传给 index.py, 它绑不上就自增, 绑成功后打印 SKY_LISTEN_PORT,
            # 面板从输出读到真实端口后再配防火墙/UPnP/隧道/显示。
            desired = self.server_port
            ensure_ssl_cert(self.root_dir, self.log)
            self.log("==== 环境配置完成, 启动 index.py (期望端口 %d, 实际端口由服务器确定) ===="
                     % desired, "success")

            # 通过环境变量把期望端口传给 index.py(多开时各实例用不同端口)
            _env = dict(os.environ)
            _env["SKY_PORT"] = str(desired)
            # 后台模式核心: 服务器输出重定向到运行日志文件(而非管道), 进程独立于面板,
            # 这样面板退出/更新后服务器仍存活; 面板用 tail 方式读该文件显示日志+探端口。
            runlog = self._runtime_log()
            self._runlog_path = runlog
            try:
                lf = open(runlog, "w", encoding="utf-8", errors="replace")
            except Exception:
                lf = subprocess.DEVNULL
            # DETACHED: 让服务器进程脱离面板, 面板退出不连带杀掉
            _flags = subprocess.CREATE_NO_WINDOW
            try:
                _flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            except Exception:
                pass
            # Python 3.14 + SQLAlchemy cyextension 兼容性: 禁用 C 扩展
            # 避免 str_to_datetime 在 DateTime 列为整数时崩溃
            _env["SKIP_CYEXTENSION"] = "1"
            # 解释器必须是**真实 python.exe**: 管理员进程用不了应用执行别名, 见
            # resolve_server_python 的说明。顺手写回 SKY_PYTHON, 私服再拉子进程时也用真的。
            _py = resolve_server_python()
            _env["SKY_PYTHON"] = _py
            self.log("私服解释器: %s" % _py, "info")
            # 2026-10-05: 改成用 sky_boot.py 引导, 而不是直接跑 index.py。
            # 本机 WMI 不响应时, 任何 import sqlalchemy 的进程都会挂死在
            # platform.machine() -> ... -> _wmi_query()(C 层 COM 查询, 不返回), 表现就是
            # "点启动后一直启动中 / 手动 python index.py 毫无反应"。sky_boot.py 在 import
            # sqlalchemy 之前把 platform 的 WMI 入口换掉, 然后原样跑 index.py,
            # 环境变量/命令行参数/输出日志与以前完全一致(SKY_PORT 等照旧生效)。
            # 想改回直接启动: 把 "sky_boot.py" 换回 "index.py" 即可(见同目录备份)。
            self.proc = subprocess.Popen(
                [_py, "-u", "sky_boot.py"],
                cwd=self.root_dir,
                stdout=lf,
                stderr=subprocess.STDOUT,
                env=_env,
                creationflags=_flags
            )
            try:
                if lf is not subprocess.DEVNULL:
                    lf.close()  # 子进程已持有句柄, 面板侧关闭不影响写入
            except Exception:
                pass
            # 写 PID 文件(供面板重开后附着、后台模式判断)
            self._write_pidfile(self.proc.pid, desired)
            # tail 运行日志(显示 + 探端口 + 进程退出检测)
            threading.Thread(target=self._tail_runtime_log, daemon=True).start()
            self._ready_fired = False
            self._port_confirmed = False
            threading.Thread(target=self._probe_ready, daemon=True).start()
            # UDP 联机服务器完全跟随私服: 私服启动的同时把它一起拉起来
            self.start_udp_server()
        except Exception as e:
            self.log(f"启动失败: {e}", "error")
            self.after(0, lambda: self._set_server_state("stopped"))

    def _on_listen_port(self, port):
        """收到服务器实际监听端口后: 更新端口并据此配防火墙/UPnP/config.json/显示。"""
        if getattr(self, "_port_confirmed", False) and port == self.server_port:
            return
        self._port_confirmed = True
        self.server_port = port
        self.log("服务器实际监听端口: %d" % port, "success")
        try:
            ensure_firewall(self.log, port)
            ensure_firewall_ipv6(self.log, port)
            setup_upnp(port, self.log)
            primary, _, v6_list = get_lan_ips()
            self.primary_ip = primary
            pub_v6 = [a for a in v6_list if not a.startswith("fe80:")]
            config_ip = ("[%s]" % pub_v6[0]) if pub_v6 else primary
            update_server_config(self.root_dir, config_ip,
                                 self.use_https.get(), self.log, port)
        except Exception as e:
            self.log("按实际端口配置失败: %s" % e, "warn")
        self.refresh_network()
        self._ensure_public_access()

    def _ensure_public_access(self, force=False):
        """自动公网可达: UPnP 映射 TCP/UDP + STUN 探测公网 IP, 并回写房间地址。

        * 路由器支持 UPnP: 映射 TCP server_port 与 UDP 各房间端口(25566+),
          用路由器返回的公网 IP 写 room_config.ip_host;
        * 只有 STUN: 提示手动在路由器映射端口(没有 UPnP 无法自动改 NAT);
        * 不会覆盖用户已手动配置的公网 IP/域名。
        """
        if not force and time.time() - getattr(self, "_public_mount_ts", 0) < 90:
            return
        self._public_mount_ts = time.time()

        def _worker():
            try:
                import public_net
            except Exception as e:
                self.log("公网探测模块 public_net.py 缺失: %s" % e, "warn")
                return
            cfg = self._load_room_config()
            if not cfg.get("auto_public_mount", True):
                return
            try:
                ip, source, dev = public_net.detect_public_ip()
            except Exception as e:
                self.log("公网探测失败: %s" % e, "warn")
                return
            if not ip:
                self.log("未探测到公网 IP(路由器可能屏蔽 UPnP/STUN); "
                         "可在路由器手动映射端口", "warn")
                return
            try:
                base = int(cfg.get("realtime_port", 25566) or 25566)
                udp_ports = [base + i for i in range(self._udp_room_count())]
            except Exception:
                udp_ports = []
            if dev:
                ok, fail = public_net.map_ports(
                    dev, [self.server_port], udp_ports,
                    "Sky private server %s" % self.server_port)
                for port, proto, msg in ok:
                    self.log("UPnP 已映射 %s %d (%s)" % (proto, port, msg), "success")
                for port, proto, msg in fail:
                    self.log("UPnP 映射失败 %s %d: %s" % (proto, port, msg), "warn")
            if dev:
                self.log("公网地址: %s (来源 %s, 端口已自动映射)" % (ip, source), "success")
            else:
                self.log("公网地址: %s (来源 %s); 未发现 UPnP 网关, 需在路由器手动映射 "
                         "TCP %d 与 UDP %s" % (ip, source, self.server_port,
                                               ",".join(str(p) for p in udp_ports)),
                         "warn")
            try:
                old = str(cfg.get("ip_host") or "").strip()
                if (not old) or (not public_net.is_public_ipv4(old)) or force:
                    if old != ip:
                        cfg["ip_host"] = ip
                        path = os.path.join(self.root_dir, "config", "room_config.json")
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(cfg, f, ensure_ascii=False, indent=2)
                        self.log("已把房间地址 ip_host 更新为公网 IP %s" % ip, "success")
            except Exception as e:
                self.log("写回公网地址失败: %s" % e, "warn")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_server_ready(self):
        """服务器就绪(只触发一次): 切到 running, 刷新用户, 启动 DDNS 和隧道。"""
        if getattr(self, "_ready_fired", False):
            return
        self._ready_fired = True
        self._set_server_state("running")
        self.refresh_users()
        self.refresh_network()   # 按最终选定的端口刷新地址显示
        self.start_ddns()
        self.after(500, self._start_cloudflared)

    def _tail_runtime_log(self):
        """tail 服务器运行日志(文件方式): 显示日志行 + 解析 SKY_LISTEN_PORT +
        关键字就绪 + 进程退出检测。后台模式下面板退出/重开都能继续读这个文件。"""
        path = getattr(self, "_runlog_path", None)
        ready = False
        pos = 0
        # 等文件出现
        for _ in range(50):
            if path and os.path.exists(path):
                break
            time.sleep(0.1)
        while True:
            proc = self.proc
            alive = bool(proc and proc.poll() is None)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
            except Exception:
                chunk = ""
            if chunk:
                for line in chunk.splitlines():
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    if "SKY_LISTEN_PORT=" in line:
                        try:
                            p = int(line.split("SKY_LISTEN_PORT=", 1)[1].strip().split()[0])
                            self.after(0, self._on_listen_port, p)
                        except Exception:
                            pass
                        continue
                    self.log(line, self._detect_level(line))
                    if not ready and ("wsgi starting up" in line.lower()
                                      or "running on" in line.lower()):
                        ready = True
                        self.after(0, self._on_server_ready)
            if not alive and not chunk:
                break
            time.sleep(0.4)
        # 进程结束
        self._ready_fired = False
        self._port_confirmed = False
        self._clear_pidfile()
        self.after(0, self.stop_ddns)
        self.after(0, lambda: self._set_server_state("stopped"))
        self.log("==== 服务器进程已退出 ====", "warn")

    def _probe_ready(self):
        """后台探测本机端口, 连通即视为服务器就绪(日志检测的兜底)。"""
        deadline = time.time() + 120
        while time.time() < deadline:
            # 进程已退出则停止探测
            if not self.proc or self.proc.poll() is not None:
                return
            if getattr(self, "_ready_fired", False):
                return
            # 服务器绑 ::(IPv6), 依次尝试 IPv4/IPv6 回环, 任一连通即视为就绪
            connected = False
            for _addr in (("127.0.0.1", self.server_port),
                          ("::1", self.server_port)):
                try:
                    with socket.create_connection(_addr, timeout=2):
                        pass
                    connected = True
                    break
                except OSError:
                    continue
            if connected:
                self.after(0, self._on_server_ready)
                return
            time.sleep(1.5)

    def _read_output(self):
        ready = False
        for line in iter(self.proc.stdout.readline, ""):
            line = line.rstrip("\n")
            if not line:
                continue
            # 服务器报告实际监听端口: SKY_LISTEN_PORT=<端口>
            if "SKY_LISTEN_PORT=" in line:
                try:
                    p = int(line.split("SKY_LISTEN_PORT=", 1)[1].strip().split()[0])
                    self.after(0, self._on_listen_port, p)
                except Exception:
                    pass
                continue
            level = self._detect_level(line)
            self.log(line, level)
            # 关键字命中也走统一的就绪处理(与端口探测互斥, 只触发一次)
            if not ready and ("wsgi starting up" in line.lower()
                              or "running on" in line.lower()):
                ready = True
                self.after(0, self._on_server_ready)
        # 进程结束
        self.proc.stdout.close()
        self.proc.wait()
        self._ready_fired = False  # 复位, 便于下次启动重新判定就绪
        self._port_confirmed = False
        self.after(0, self.stop_ddns)
        self.after(0, lambda: self._set_server_state("stopped"))
        self.log("==== 服务器进程已退出 ====", "warn")

    def start_ddns(self):
        """启动 DDNS 后台线程: 周期性把当前稳定 IPv6 推送到 ClouDNS。"""
        if ddns_updater is None:
            return
        cfg = load_panel_config()
        has_cfg = (cfg.get("ddns_dynamic_url") or
                   (cfg.get("ddns_auth_id") and cfg.get("ddns_zone")))
        if not has_cfg:
            self.log("DDNS 未配置(panel_config.json 缺 ddns_dynamic_url 或 "
                     "API 凭据), 跳过自动更新", "info")
            return
        if self._ddns_thread and self._ddns_thread.is_alive():
            return
        self._ddns_stop = threading.Event()
        interval = int(cfg.get("ddns_interval", 300) or 300)

        def _worker():
            self.log("==== DDNS 自动更新已启动 ====", "success")
            ddns_updater.run_forever(
                interval=interval,
                logger=lambda m: self.log(m, "info"),
                stop_event=self._ddns_stop)

        self._ddns_thread = threading.Thread(target=_worker, daemon=True)
        self._ddns_thread.start()

    def stop_ddns(self):
        if self._ddns_stop is not None:
            self._ddns_stop.set()
            self._ddns_stop = None
            self.log("DDNS 自动更新已停止", "info")

    # ── Cloudflare Tunnel ──
    def _cf_statefile(self):
        """隧道状态文件(按端口区分)。保存 {pid, url, port}, 供面板重开后附着,
        实现"隧道地址锁定": 只要服务器没停, 隧道进程就一直活着, 地址不变。"""
        return os.path.join(self._state_dir(), ".sky_tunnel_%d.json" % self.server_port)

    def _cf_logfile(self):
        """隧道进程输出日志(按端口区分)。脱离面板的 cloudflared 把输出写到这里,
        面板用 tail 方式读取以捕获 URL, 重开面板也能继续读。"""
        return self._sky_log_path(".sky_tunnel_%d.log" % self.server_port)

    def _read_cf_state(self):
        try:
            with open(self._cf_statefile(), "r", encoding="utf-8") as f:
                st = json.load(f)
            # 历史状态文件可能存了带接口路径的网址, 读出来先规范化, 避免被复用显示。
            if isinstance(st, dict) and st.get("url"):
                st["url"] = self._clean_tunnel_url(st["url"])
            return st
        except Exception:
            return None

    def _write_cf_state(self, pid, url):
        try:
            with open(self._cf_statefile(), "w", encoding="utf-8") as f:
                json.dump({"pid": pid, "url": self._clean_tunnel_url(url),
                           "port": self.server_port}, f)
        except Exception:
            pass

    def _clear_cf_state(self):
        for p in (self._cf_statefile(), self._cf_logfile()):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    # Cloudflare 官方 Anycast IP 段(只用于判断本机解析到的 api.trycloudflare.com 是否被劫持)
    _CF_IP_PREFIXES = ("104.16.", "104.17.", "104.18.", "104.19.", "104.20.",
                       "104.21.", "104.22.", "104.23.", "104.24.", "104.25.",
                       "104.26.", "104.27.", "172.64.", "172.65.", "172.66.",
                       "172.67.", "172.68.", "172.69.", "172.70.", "172.71.",
                       "162.159.", "188.114.", "197.234.", "198.41.")

    def _cf_dns_probe(self):
        """探测本机把 api.trycloudflare.com 解析到哪, 判断是否被 DNS 劫持/污染。

        cloudflared 申请 Quick Tunnel 前必须 HTTPS 访问
        https://api.trycloudflare.com/tunnel 。若本机(或路由器/运营商)DNS 把这个
        域名解析到非 Cloudflare 的 IP, TLS 校验必然失败, 报:
            failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel":
            tls: failed to verify certificate: x509: certificate is not valid for any names
        这种情况下重装/升级 cloudflared 完全没用, 唯一办法是让该域名解析回
        真实 Cloudflare IP(见同目录 修复隧道DNS.bat)。返回 (是否正常, IP或错误信息)。
        """
        try:
            ip = socket.gethostbyname("api.trycloudflare.com")
        except Exception as e:
            return False, str(e)
        return ip.startswith(self._CF_IP_PREFIXES), ip

    def _start_cloudflared(self):
        """启动 / 附着 Cloudflare Quick Tunnel。

        隧道地址锁定策略:
        - 若状态文件里记录的隧道进程仍存活 -> 直接附着, 沿用旧地址(不新建隧道)。
          这样热更新、关闭并重开主控(只要服务器没停)都不会更换隧道地址。
        - 只有服务器真正停止时才会清掉状态文件; 下次启动才生成新地址。
        cloudflared 以脱离进程组方式启动, 输出写到日志文件, 面板退出不连带杀掉。
        """
        # 1) 已在本面板内持有活动进程 -> 跳过
        if self.cf_proc and self.cf_proc.poll() is None:
            self.log("Cloudflare Tunnel 已在运行, 跳过", "info")
            return
        # 2) 状态文件里的后台隧道仍存活 -> 附着, 沿用旧地址(地址锁定核心)
        st = self._read_cf_state()
        if st and self._pid_alive(st.get("pid")):
            self._cf_pid = st.get("pid")
            old_url = st.get("url") or ""
            if old_url:
                self.cf_url = old_url
                self.after(0, self._apply_tunnel_url, old_url)
                self.log("已附着后台隧道(地址锁定): %s" % old_url, "success")
            else:
                self.log("已附着后台隧道(等待地址)...", "info")
            # 继续 tail 日志, 万一 URL 还没写入状态文件也能补上
            if not self._cf_tail:
                threading.Thread(target=self._read_cf_output, daemon=True).start()
            return
        # 3) 没有可用隧道 -> 全新启动
        if not os.path.isfile(CLOUDFLARED_PATH):
            self.log(f"cloudflared 未找到: {CLOUDFLARED_PATH}, 跳过隧道", "warn")
            return
        # 申请隧道前先看 DNS: 被劫持时直接说清原因, 免得用户以为是 cloudflared 坏了。
        _dns_ok, _dns_ip = self._cf_dns_probe()
        if not _dns_ok:
            self.log("警告: 本机把 api.trycloudflare.com 解析为 %s, 不是 Cloudflare 的 IP,"
                     " 属于 DNS 劫持/污染, cloudflared 申请隧道一定会失败。" % _dns_ip, "warn")
            self.log("请右键「以管理员身份运行」同目录的 [修复隧道DNS.bat], 把该域名固定到真实 "
                     "Cloudflare IP(104.16.230.132), 再启动服务器即可。", "warn")
        else:
            self.log("隧道前置检查: api.trycloudflare.com -> %s (DNS 正常)" % _dns_ip, "info")
        try:
            logf = open(self._cf_logfile(), "w", encoding="utf-8", errors="replace")
        except Exception:
            logf = subprocess.PIPE
        _flags = subprocess.CREATE_NO_WINDOW
        try:
            _flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        except Exception:
            pass
        try:
            # 强制 IPv4 + HTTP/2。
            # 默认(auto 选边缘 + QUIC)会优先走 IPv6 边缘节点(如 2606:4700:a8::3),
            # 本机 IPv6 出网不稳时报 "Register tunnel error ... context deadline
            # exceeded" / "initial tunnel connection failed", 隧道刚建好就退出 ->
            # 公网网址 530 (Argo Tunnel error)、主网页进不去、接口监控一片空白。
            # 实测固定 "--edge-ip-version 4 --protocol http2" 能稳定
            # "Registered tunnel connection"(region1/region2 TCP 均 PASS)。
            self.cf_proc = subprocess.Popen(
                [CLOUDFLARED_PATH, "tunnel", "--no-autoupdate",
                 "--url", f"https://localhost:{self.server_port}", "--no-tls-verify",
                 "--edge-ip-version", "4", "--protocol", "http2"],
                stdout=logf,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_flags
            )
            try:
                if logf is not subprocess.PIPE:
                    logf.close()   # 子进程已持有句柄
            except Exception:
                pass
            self._cf_pid = self.cf_proc.pid
            self.cf_url = ""
            self._write_cf_state(self._cf_pid, "")   # 先记 PID, URL 待捕获后补写
            threading.Thread(target=self._read_cf_output, daemon=True).start()
            self.log("Cloudflare Tunnel 启动中...", "info")
        except Exception as e:
            self.log(f"Cloudflare Tunnel 启动失败: {e}", "error")
            self.cf_proc = None
            self._cf_pid = None

    def _read_cf_output(self):
        """tail 隧道日志文件, 捕获 URL 并更新 UI + 写入状态文件。

        采用文件 tail 而非读管道: cloudflared 脱离面板后台运行, 面板退出/重开都能
        继续读这个日志, 从而附着到同一隧道。捕获到 URL 后写入状态文件实现地址锁定。
        """
        self._cf_tail = True
        path = self._cf_logfile()
        # 只捕获隧道主机名本身, 绝不带后面的路径/接口。
        # 之前的正则 [^\s"'<>|]* 会把日志里出现的请求路径(如
        # https://xxx.trycloudflare.com/account/purchase_unlock 或 /favicon.ico)
        # 一并吃进来, 导致"隧道:"显示成带接口的网址, 且写进状态文件被锁定复用。
        # 这里把尾部通配去掉, 只匹配到 .trycloudflare.com 为止(后面不允许再接路径字符)。
        # 重要: 必须排除 api.trycloudflare.com。
        # 它是 cloudflared "申请隧道"接口的主机名; 申请失败时错误行里会出现
        #   failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": ...
        # 旧正则会把 api.trycloudflare.com 当成隧道地址显示出来 -> 界面"公网地址"
        # 变成 https://api.trycloudflare.com (实际打不开), 用户看到的就是"隧道获取错误"。
        # 真正的隧道主机名是随机英文单词(如 wrestling-scheme-brooks-pointed)。
        url_re = re.compile(r'https?://(?!api\.)[a-z0-9-]+\.trycloudflare\.com(?![a-z0-9.-])',
                            re.IGNORECASE)
        pos = 0
        warned_timeout = False
        deadline = time.time() + 25
        # 等日志文件出现
        for _ in range(50):
            if os.path.exists(path):
                break
            time.sleep(0.1)
        try:
            while True:
                # 隧道进程已退出则停止 tail
                alive = self._pid_alive(self._cf_pid) or (
                    self.cf_proc and self.cf_proc.poll() is None)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                except Exception:
                    chunk = ""
                if chunk:
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        low = line.lower()
                        m = url_re.search(line)
                        if m:
                            captured_url = m.group(0)
                            if captured_url != getattr(self, "cf_url", ""):
                                self.cf_url = captured_url
                                self._cf_retry = 0
                                self._write_cf_state(self._cf_pid, captured_url)
                                self.after(0, self._apply_tunnel_url, captured_url)
                            continue
                        if any(kw in low for kw in (
                                "fatal", "failed to connect", "error",
                                # 申请隧道失败/证书校验失败: 让用户看到真正的原因,
                                # 而不是只看到一个假的公网地址。
                                "failed to request", "x509", "certificate",
                                "context deadline exceeded", "no such host",
                                "forcibly closed")):
                            self.after(0, lambda msg=line: self.log(f"[CF] {msg}", "error"))
                        elif "registered tunnel connection" in low:
                            self.after(0, lambda: self.log("[CF] 隧道连接已建立", "success"))
                if not self.cf_url and not warned_timeout and time.time() > deadline:
                    warned_timeout = True
                    self.after(0, lambda: self.log(
                        "Cloudflare Tunnel 仍在等待 URL（冷启动可能较慢）...", "warn"))
                if not alive and not chunk:
                    break
                time.sleep(0.5)
        except Exception as e:
            self.after(0, lambda msg=str(e): self.log(f"[CF] 读取输出异常: {msg}", "warn"))
        finally:
            self._cf_tail = False
            # 没拿到隧道地址就自动重试。
            # DNS 劫持通常是"偶发"的(路由器/运营商缓存过期时会短暂返回真实 IP),
            # 每次重试前刷新本机 DNS 缓存, 多试几次就能拿到一次正常解析; 一旦
            # cloudflared 申请成功、隧道连上, 后续就不再依赖这个解析了。
            try:
                if not getattr(self, "cf_url", "") and self.cf_proc is not None:
                    n = getattr(self, "_cf_retry", 0)
                    if n < 12:
                        self._cf_retry = n + 1
                        self.after(0, lambda n=n: self.log(
                            "隧道未取到地址, 6 秒后重试(%d/12), 并刷新本机 DNS 缓存..." % n,
                            "warn"))
                        try:
                            subprocess.run(["ipconfig", "/flushdns"],
                                           creationflags=subprocess.CREATE_NO_WINDOW,
                                           timeout=10, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                        time.sleep(6)
                        if self.cf_proc is not None:
                            self.after(0, self._start_cloudflared)
                    elif n == 12:
                        self._cf_retry = n + 1
                        self.after(0, lambda: self.log(
                            "隧道重试 12 次仍失败, 确认是 DNS 劫持: 请右键「以管理员身份运行」"
                            "同目录的 [修复隧道DNS.bat] 后, 再启动服务器。", "error"))
            except Exception:
                pass

    @staticmethod
    def _clean_tunnel_url(url):
        """规范化隧道地址: 只保留 scheme + 主机名, 去掉任何路径/查询串。
        防止历史状态文件里存了带接口的网址(如 .../account/purchase_unlock)
        在重开面板时被原样显示和复用。"""
        if not url:
            return url
        m = re.match(r'(https?://[a-z0-9-]+\.trycloudflare\.com)', url, re.IGNORECASE)
        return m.group(1) if m else url

    def _apply_tunnel_url(self, url):
        """把捕获到的隧道公网地址显示到界面(主线程调用)。"""
        url = self._clean_tunnel_url(url)
        try:
            self.cf_var.set(url)
            self.log(f"Cloudflare Tunnel 公网地址: {url}", "success")
            self.set_status("临时公网已就绪: " + url)
        except Exception as e:
            self.log(f"显示隧道地址失败: {e}", "warn")

    def _stop_cloudflared(self):
        """停止 Cloudflare Tunnel(仅服务器真正停止时调用)。

        会同时杀掉本面板进程与后台脱离的隧道进程, 并清掉状态文件,
        使下次启动服务器时生成新的隧道地址。"""
        self.cf_url = ""
        try:
            self.cf_var.set("未启动")
        except Exception:
            pass
        # 杀掉本面板亲自启动的进程
        if self.cf_proc:
            try:
                if self.cf_proc.poll() is None:
                    self.cf_proc.terminate()
                    try:
                        self.cf_proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.cf_proc.kill()
            except Exception as e:
                self.log(f"Cloudflare Tunnel 停止异常: {e}", "warn")
            finally:
                self.cf_proc = None
        # 杀掉后台脱离的隧道进程(状态文件里记录的 PID)
        st = self._read_cf_state()
        pid = (st or {}).get("pid") or self._cf_pid
        if pid and self._pid_alive(pid):
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, shell=True)
            except Exception:
                pass
        self._cf_pid = None
        self._clear_cf_state()
        self.log("Cloudflare Tunnel 已停止", "info")

    def _save_and_run_ddns(self):
        """保存 Dynamic URL 到配置, 立即执行一次更新, 并(若服务器在跑)启动后台线程。"""
        cfg = load_panel_config()
        cfg["ddns_dynamic_url"] = self.ddns_url_var.get().strip()
        save_panel_config(cfg)
        self.set_status("DDNS 配置已保存")
        if ddns_updater is None:
            messagebox.showerror("DDNS", "ddns_updater 模块加载失败", parent=self)
            return
        if not cfg["ddns_dynamic_url"]:
            messagebox.showinfo("DDNS", "已清空 Dynamic URL", parent=self)
            return

        def _once():
            ipv6, ok, info = ddns_updater.run_once(
                logger=lambda m: self.log(m, "info"))
            msg = (f"DDNS 更新{'成功' if ok else '失败'}\n地址: {ipv6}\n{info}")
            self.after(0, lambda: messagebox.showinfo("DDNS", msg, parent=self)
                       if ok else messagebox.showwarning("DDNS", msg, parent=self))
        threading.Thread(target=_once, daemon=True).start()
        # 若服务器正在运行, 顺带把周期线程拉起来
        if self.proc and self.proc.poll() is None:
            self.start_ddns()

    @staticmethod
    def _detect_level(line):
        low = line.lower()
        for level, keys in LOG_KEYWORDS:
            if any(k in low for k in keys):
                return level
        return "info"

    def stop_server(self):
        if not self.proc or self.proc.poll() is not None:
            self._set_server_state("stopped")
            return
        self.set_status("正在停止服务器...")
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _cleanup_request_dumps(self):
        """关服时清理接口监控磁盘缓存(任务3)。
        request_dumps/ 是接口监控的磁盘缓存; 默认关服即删除,
        但若监控面板里点了"保存接口缓存"(存在 .keep_cache 标记)则保留。
        """
        try:
            dump_dir = os.path.join(self.root_dir, "request_dumps")
            if not os.path.isdir(dump_dir):
                return
            keep = os.path.join(dump_dir, ".keep_cache")
            if os.path.exists(keep):
                self.log("接口缓存已标记保存, 保留 request_dumps", "info")
                return
            import shutil
            shutil.rmtree(dump_dir, ignore_errors=True)
            self.log("已删除接口监控磁盘缓存 request_dumps", "info")
        except Exception as e:
            self.log(f"清理接口缓存失败: {e}", "warn")

    def _stop_worker(self):
        try:
            self._stop_cloudflared()
            self.stop_udp_server()  # UDP 跟随私服: 一起停止
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.log("超时, 强制结束进程", "warn")
                self.proc.kill()
            remove_upnp(self.server_port, self.log)  # 清理 UPnP
            kill_port(self.server_port, self.log)
            self._clear_pidfile()  # 后台模式: 停止时清掉 PID 文件
            self._cleanup_request_dumps()  # 任务3: 关服清理接口缓存(除非已保存)
        except Exception as e:
            self.log(f"停止异常: {e}", "error")
        self.after(0, lambda: self._set_server_state("stopped"))

    # ================= 日志 =================
    def log(self, text, level="info"):
        self.after(0, self._append_log, text, level)

    def _append_log(self, text, level):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] ", "timestamp")
        self.log_text.insert("end", text + "\n", level)
        # 持续写入日志文件(每实例独立, 按端口区分, 避免多开时互相覆盖)。
        # 写文件失败(盘符不存在/被占用/无权限)绝不能影响界面日志与后续代码。
        try:
            _lf_path = self._log_file()
            if _lf_path:
                with open(_lf_path, "a", encoding="utf-8", errors="replace") as lf:
                    lf.write("[%s] %s\n" % (ts, text))
        except Exception:
            pass
        # 行数上限
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        if self.autoscroll.get():
            self.log_text.see("end")

    def _clear_log(self):
        self.log_text.delete("1.0", "end")

    def _block_key(self, event):
        if event.state & 0x4 and event.keysym.lower() in ("a", "c"):
            return
        return "break"

    def _log_copy(self):
        try:
            sel = self.log_text.get("sel.first", "sel.last")
        except tk.TclError:
            sel = self.log_text.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(sel)

    def _log_selectall(self):
        self.log_text.tag_add("sel", "1.0", "end")

    # ================= 网络 =================
    def refresh_network(self):
        primary, v4, v6 = get_lan_ips()
        self.primary_ip = primary
        proto = "https" if self.use_https.get() else "http"
        self.lan_var.set("%s://%s:%d" % (proto, primary, self.server_port))
        # 显示 IPv6
        _, _, v6_list = get_lan_ips()
        pub_v6 = [a for a in v6_list if not a.startswith("fe80:")]  # 过滤本地链路
        if pub_v6:
            self.v6_var.set("%s://[%s]:%d" % (proto, pub_v6[0], self.server_port))
        else:
            self.v6_var.set("无公网 IPv6")
        self.wan_var.set("获取中...")
        threading.Thread(target=self._wan_worker, daemon=True).start()

    def _wan_worker(self):
        ip = get_public_ip()
        self.public_ip = ip
        proto = "https" if self.use_https.get() else "http"
        if ip:
            self.after(0, lambda: self.wan_var.set(f"{proto}://{ip}:{self.server_port}"))
        else:
            self.after(0, lambda: self.wan_var.set("获取失败"))

    def _copy(self, text):
        if not text or text in ("检测中...", "获取中...", "获取失败"):
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.set_status(f"已复制: {text}")

    def _toggle_https(self):
        cfg = load_panel_config()
        cfg["use_https"] = self.use_https.get()
        save_panel_config(cfg)
        self.refresh_network()
        if self.server_state == "running":
            self.set_status("HTTPS 设置已更改，下次启动生效")

    # ================= 账号操作 =================
    def _ban_dialog(self):
        if not self.selected_uid:
            return
        win = tk.Toplevel(self)
        win.configure()
        win.title("封号设置")
        win.resizable(False, False)
        win.grab_set()
        tk.Label(win, text=f"用户: {self.selected_uid}",
                 font=("微软雅黑", 9)).grid(row=0, column=0, columnspan=2,
                                            padx=12, pady=(12, 4), sticky="w")
        tk.Label(win, text="预设时长:", font=("微软雅黑", 9)).grid(
            row=1, column=0, padx=12, sticky="w")
        choice = tk.StringVar(value=BAN_PRESETS[0][0])
        ttk.Combobox(win, textvariable=choice, state="readonly", width=18,
                     values=[p[0] for p in BAN_PRESETS]).grid(
            row=1, column=1, padx=12, pady=4, sticky="w")

        # 自定义
        cf = tk.LabelFrame(win, text="自定义", font=("微软雅黑", 9))
        cf.grid(row=2, column=0, columnspan=2, padx=12, pady=8, sticky="we")
        use_custom = tk.BooleanVar(value=False)
        tk.Checkbutton(cf, text="启用自定义时长", variable=use_custom).grid(
            row=0, column=0, columnspan=2, sticky="w")
        amount = tk.StringVar(value="1")
        unit = tk.StringVar(value="小时")
        tk.Entry(cf, textvariable=amount, width=8).grid(row=1, column=0, padx=6, pady=4)
        ttk.Combobox(cf, textvariable=unit, state="readonly", width=8,
                     values=["分钟", "小时", "天"]).grid(row=1, column=1, padx=6)

        def confirm():
            if use_custom.get():
                try:
                    amt = float(amount.get())
                except ValueError:
                    messagebox.showerror("错误", "请输入有效数字", parent=win)
                    return
                mult = {"分钟": 60, "小时": 3600, "天": 86400}[unit.get()]
                duration = int(amt * mult)
                label = f"{amount.get()} {unit.get()}"
            else:
                label = choice.get()
                duration = dict(BAN_PRESETS)[label]
            if not messagebox.askyesno("确认封号",
                    f"确定封禁用户 {self.selected_uid}\n时长: {label} ?", parent=win):
                return
            try:
                self.db.ban_user(self.selected_uid, duration)
                self.set_status(f"已封禁 {self.selected_uid} ({label})")
                self.refresh_users()
                win.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"封号失败: {e}", parent=win)

        bar = tk.Frame(win)
        bar.grid(row=3, column=0, columnspan=2, pady=(4, 12))
        tk.Button(bar, text="确定封号", width=10, command=confirm).pack(side="left", padx=6)
        tk.Button(bar, text="取消", width=10, command=win.destroy).pack(side="left", padx=6)

    def _unban(self):
        if not self.selected_uid:
            return
        if not messagebox.askyesno("确认解封",
                f"确定解封用户 {self.selected_uid} ?"):
            return
        try:
            self.db.unban_user(self.selected_uid)
            self.set_status(f"已解封 {self.selected_uid}")
            self.refresh_users()
        except Exception as e:
            messagebox.showerror("错误", f"解封失败: {e}")

    def _delete(self):
        uids = self._get_selected_uids()
        if not uids:
            return
        if not messagebox.askyesno("确认删除",
                f"将永久删除 {len(uids)} 个用户及其所有关联数据！\n"
                f"此操作不可恢复，确定继续?"):
            return
        for uid in uids:
            try:
                report = self.db.delete_user(uid)
                detail = ", ".join(f"{k}:{v}" for k, v in report.items())
                self.log(f"删除用户 {uid}: {detail}", "warn")
            except Exception as e:
                self.log(f"删除失败 {uid}: {e}", "error")
        self.set_status(f"已删除 {len(uids)} 个用户")
        self.refresh_users()

    # ---------------- 授权操作 ----------------
    # ---------------- 自动授权 (任务2) ----------------
    def _auto_auth_path(self):
        return os.path.join(self.root_dir, "config", "auto_auth.json")

    def _load_auto_auth(self):
        """读取自动授权配置, 返回 {"enabled": bool, "since": int}"""
        try:
            with open(self._auto_auth_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            return {"enabled": bool(d.get("enabled", False)),
                    "since": int(d.get("since", 0) or 0)}
        except Exception:
            return {"enabled": False, "since": 0}

    def _save_auto_auth(self, enabled, since):
        path = self._auto_auth_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"enabled": bool(enabled), "since": int(since)},
                      f, ensure_ascii=False, indent=2)

    def _refresh_auto_auth_btn(self):
        """根据当前配置刷新按钮文字/颜色"""
        if not hasattr(self, "btn_auto_auth"):
            return
        cfg = self._load_auto_auth()
        if cfg["enabled"]:
            self.btn_auto_auth.config(text="自动授权: 开", bg="#f1c40f",
                                      activebackground="#f39c12", fg="#000000")
        else:
            self.btn_auto_auth.config(text="自动授权: 关", bg="SystemButtonFace",
                                      activebackground="SystemButtonFace", fg="#000000")

    def _toggle_auto_auth(self):
        """开启/关闭自动授权。开启后, 此刻之后新建的账号会被服务端自动授权并标黄。"""
        cfg = self._load_auto_auth()
        if cfg["enabled"]:
            if not messagebox.askyesno("关闭自动授权",
                    "关闭后, 新建的账号将恢复为'未授权'(需手动授权)。\n确定关闭?"):
                return
            try:
                self._save_auto_auth(False, cfg["since"])
            except Exception as e:
                messagebox.showerror("错误", f"写入配置失败: {e}"); return
            self.set_status("已关闭自动授权")
            self.log("自动授权: 已关闭", "warn")
        else:
            now = int(time.time())
            if not messagebox.askyesno("开启自动授权",
                    "开启后, 从现在起新建的账号会被自动授权并在列表中标黄。\n"
                    "(此前已存在的账号不受影响)\n确定开启?"):
                return
            try:
                self._save_auto_auth(True, now)
            except Exception as e:
                messagebox.showerror("错误", f"写入配置失败: {e}"); return
            self.set_status(f"已开启自动授权 (生效时间: {self._fmt_ts(now)})")
            self.log(f"自动授权: 已开启, 此刻之后新建账号自动授权 ({self._fmt_ts(now)})", "success")
        self._refresh_auto_auth_btn()
        self.refresh_users()

    # ---------------- 无库就建号 (2026-10-05) ----------------
    #
    # 开关放在账号管理页(用户要求: "这个功能要在账号管理下控制")。
    # 存的是 config/friend_config.json 里的 auto_create_account, 与服务端
    # friend_helper.auto_create_account_enabled() 读的是同一份 —— 服务端每次调用都
    # 重新读文件, 所以这里点一下立刻生效, 不用重启私服、不用热更新。
    def _friend_config_path(self):
        return os.path.join(self.root_dir, "config", "friend_config.json")

    def _load_friend_config(self):
        """读 config/friend_config.json(读不到就返回空字典)"""
        try:
            with open(self._friend_config_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_friend_config(self, updates):
        """读-改-写: 只动传进来的键, 别把 auto_friend / auto_light_all 等其它开关冲掉。"""
        path = self._friend_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cur = self._load_friend_config()
        cur.update(updates or {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)

    def _auto_create_account_enabled(self):
        cfg = self._load_friend_config()
        if "auto_create_account" in cfg:
            return bool(cfg["auto_create_account"])
        return True   # 与服务端 friend_helper.auto_create_account_enabled() 的默认一致

    def _refresh_auto_create_btn(self):
        """根据当前配置刷新按钮文字/颜色"""
        if not hasattr(self, "btn_auto_create"):
            return
        if self._auto_create_account_enabled():
            self.btn_auto_create.config(text="无库就建号: 开", bg="#f1c40f",
                                        activebackground="#f39c12", fg="#000000")
        else:
            self.btn_auto_create.config(text="无库就建号: 关", bg="SystemButtonFace",
                                        activebackground="SystemButtonFace", fg="#000000")

    def _toggle_auto_create_account(self):
        """开/关"无库就建号": 账号库里没有的 id 是否用同一个 id 建号。"""
        turn_on = not self._auto_create_account_enabled()
        if turn_on:
            if not messagebox.askyesno("开启无库就建号",
                    "开启后, 客户端拿着账号库里没有的 id 来请求时\n"
                    "(登录 /account/auth/login、/service/trust/api/v1/get_capped),\n"
                    "服务器会用【同一个 id】自动建一行账号并继续登录。\n\n"
                    "清空/换账号库之后, 客户端不用重装、不用换号就能直接进游戏\n"
                    "(世界进度/物品/好友是新的, 因为库是空的)。\n\n"
                    "新号能不能马上进游戏仍然看上面的『自动授权』开关。\n\n确定开启?"):
                return
        else:
            if not messagebox.askyesno("关闭无库就建号",
                    "关闭后, 账号库里没有的 id 登录时会回『用户不存在』(老行为),\n"
                    "不会自动建号 —— 删掉的账号不会再被客户端建回来。\n\n确定关闭?"):
                return
        try:
            self._save_friend_config({"auto_create_account": bool(turn_on)})
        except Exception as e:
            messagebox.showerror("错误", f"写入 config/friend_config.json 失败: {e}")
            return
        self._refresh_auto_create_btn()
        self.set_status("无库就建号: 已%s(立刻生效)" % ("开启" if turn_on else "关闭"))
        self.log("无库就建号: 已%s (config/friend_config.json)" % ("开启" if turn_on else "关闭"),
                 "success" if turn_on else "warn")

    # ---------------- 新建账号全解锁 ----------------
    def _auto_full_unlock_path(self):
        return os.path.join(self.root_dir, "config", "auto_full_unlock.json")

    def _load_auto_full_unlock(self):
        """读取新建账号全解锁配置, 返回 {"enabled": bool, "since": int}"""
        try:
            with open(self._auto_full_unlock_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            return {"enabled": bool(d.get("enabled", False)),
                    "since": int(d.get("since", 0) or 0)}
        except Exception:
            return {"enabled": False, "since": 0}

    def _save_auto_full_unlock(self, enabled, since):
        path = self._auto_full_unlock_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"enabled": bool(enabled), "since": int(since)},
                      f, ensure_ascii=False, indent=2)

    def _refresh_auto_full_unlock_btn(self):
        """根据当前配置刷新按钮文字/颜色"""
        if not hasattr(self, "btn_auto_full_unlock"):
            return
        cfg = self._load_auto_full_unlock()
        if cfg["enabled"]:
            self.btn_auto_full_unlock.config(text="新建账号全解锁: 开", bg="#2ecc71",
                                             activebackground="#27ae60", fg="#000000")
        else:
            self.btn_auto_full_unlock.config(text="新建账号全解锁: 关", bg="SystemButtonFace",
                                             activebackground="SystemButtonFace", fg="#000000")

    def _toggle_auto_full_unlock(self):
        """开启/关闭新建账号全解锁。

        开启后, 此刻之后新建的账号会被服务端自动推送高版本全解锁
        (全动作/开全门/全光翼/全物品), 即写入与"推送全解锁"相同的账号数据。
        关闭后, 新建账号恢复为原来的空白状态。
        """
        cfg = self._load_auto_full_unlock()
        if cfg["enabled"]:
            if not messagebox.askyesno("关闭新建账号全解锁",
                    "关闭后, 新建的账号将恢复为'空白'(无全解锁数据)。\n确定关闭?"):
                return
            try:
                self._save_auto_full_unlock(False, cfg["since"])
            except Exception as e:
                messagebox.showerror("错误", f"写入配置失败: {e}"); return
            self.set_status("已关闭新建账号全解锁")
            self.log("新建账号全解锁: 已关闭", "warn")
        else:
            now = int(time.time())
            if not messagebox.askyesno("开启新建账号全解锁",
                    "开启后, 从现在起新建的账号会自动推送全解锁\n"
                    "(全动作/开全门/全光翼/全物品)。\n"
                    "(此前已存在的账号不受影响)\n确定开启?"):
                return
            try:
                self._save_auto_full_unlock(True, now)
            except Exception as e:
                messagebox.showerror("错误", f"写入配置失败: {e}"); return
            self.set_status(f"已开启新建账号全解锁 (生效时间: {self._fmt_ts(now)})")
            self.log(f"新建账号全解锁: 已开启, 此刻之后新建账号自动全解锁 ({self._fmt_ts(now)})", "success")
        self._refresh_auto_full_unlock_btn()
        self.refresh_users()

    def _auth(self):
        uid = self.selected_uid
        if not uid: return
        if not messagebox.askyesno("确认授权", f"授权用户 {uid} ?"): return
        try:
            self.db.set_authorized(uid, True)
            self.set_status(f"已授权 {uid}")
            self.log(f"授权: {uid}", "success")
            self.refresh_users()
        except Exception as e:
            messagebox.showerror("错误", f"授权失败: {e}")

    def _unauth(self):
        uid = self.selected_uid
        if not uid: return
        if not messagebox.askyesno("取消授权", f"取消授权 {uid} ?\n取消后该账号将无法登录"): return
        try:
            self.db.set_authorized(uid, False)
            self.set_status(f"已取消授权 {uid}")
            self.log(f"取消授权: {uid}", "warn")
            self.refresh_users()
        except Exception as e:
            messagebox.showerror("错误", f"取消授权失败: {e}")

    # ---------------- 批量封号/删除 ----------------
    def _batch_ban(self):
        uids = self._get_selected_uids()
        if len(uids) < 2:
            messagebox.showwarning("提示", "请至少选择 2 个用户"); return
        targets = [uid for uid in uids if not next(
            (u for u in self.users_cache if u["id"] == uid), {}).get("is_banned")]
        if not targets:
            messagebox.showinfo("提示", "所选用户均已封禁"); return
        # 复用单用户封号对话框逻辑, 简化版
        from tkinter import simpledialog
        dur_str = simpledialog.askstring("批量封号",
            f"将对 {len(targets)} 个用户封号\n请输入时长(秒, 0=永久):", parent=self)
        if dur_str is None: return
        try:
            dur = int(dur_str)
        except ValueError:
            messagebox.showerror("错误", "请输入有效数字"); return
        if not messagebox.askyesno("确认", f"封禁 {len(targets)} 个用户, 时长: {dur if dur else '永久'}秒 ?"): return
        count = 0
        for uid in targets:
            try:
                self.db.ban_user(uid, None if dur == 0 else dur)
                count += 1
            except Exception as e:
                self.log(f"封号失败 {uid}: {e}", "error")
        self.set_status(f"批量封禁 {count} 个用户")
        self.log(f"批量封号: {count} 个", "warn")
        self.refresh_users()

    def _batch_delete(self):
        uids = self._get_selected_uids()
        if len(uids) < 2:
            messagebox.showwarning("提示", "请至少选择 2 个用户"); return
        if not messagebox.askyesno("批量删除",
                f"将永久删除 {len(uids)} 个用户及所有关联数据！\n此操作不可恢复!"): return
        count = 0
        for uid in uids:
            try:
                self.db.delete_user(uid); count += 1
            except Exception as e:
                self.log(f"删除失败 {uid}: {e}", "error")
        self.set_status(f"已删除 {count} 个用户")
        self.log(f"批量删除: {count} 个", "warn")
        self.refresh_users()

    def _batch_auth(self):
        uids = self._get_selected_uids()
        if len(uids) < 2:
            messagebox.showwarning("提示", "请至少选择 2 个用户"); return
        targets = [uid for uid in uids if not next(
            (u for u in self.users_cache if u["id"] == uid), {}).get("is_authorized")]
        if not targets:
            messagebox.showinfo("提示", "所选用户均已授权"); return
        if not messagebox.askyesno("批量授权", f"为 {len(targets)} 个用户授权?"): return
        count = 0
        for uid in targets:
            try:
                self.db.set_authorized(uid, True); count += 1
            except Exception as e:
                self.log(f"授权失败 {uid}: {e}", "error")
        self.set_status(f"已授权 {count} 个用户")
        self.log(f"批量授权: {count} 个", "success")
        self.refresh_users()

    def _batch_unauth(self):
        uids = self._get_selected_uids()
        if len(uids) < 2:
            messagebox.showwarning("提示", "请至少选择 2 个用户"); return
        targets = [uid for uid in uids if next(
            (u for u in self.users_cache if u["id"] == uid), {}).get("is_authorized")]
        if not targets:
            messagebox.showinfo("提示", "所选用户均未授权"); return
        if not messagebox.askyesno("批量取消授权",
                f"为 {len(targets)} 个用户取消授权?\n取消后这些账号将无法登录"): return
        count = 0
        for uid in targets:
            try:
                self.db.set_authorized(uid, False); count += 1
            except Exception as e:
                self.log(f"取消授权失败 {uid}: {e}", "error")
        self.set_status(f"已取消 {count} 个用户授权")
        self.log(f"批量取消授权: {count} 个", "warn")
        self.refresh_users()

    # ---------------- 账号转移 ----------------
    def _transfer(self):
        source_uid = self.selected_uid
        if not source_uid:
            return
        from tkinter import simpledialog
        target_uid = simpledialog.askstring("账号转移",
            f"源账号: {source_uid}\n\n请输入目标账号 ID\n(目标账号数据将被覆盖):", parent=self)
        if not target_uid:
            return
        target_uid = target_uid.strip()
        if target_uid == source_uid:
            messagebox.showwarning("提示", "目标账号不能与源账号相同"); return
        if not messagebox.askyesno("确认转移",
                f"警告: 此操作不可恢复!\n\n"
                f"将 {source_uid} 的所有数据转移到 {target_uid}\n"
                f"目标账号现有数据将被覆盖\n源账号将被删除\n\n确定继续?"): return
        try:
            self.db.transfer_user(source_uid, target_uid)
            self.set_status(f"数据已从 {source_uid} 转移到 {target_uid}")
            self.log(f"账号转移: {source_uid} -> {target_uid}", "warn")
            messagebox.showinfo("完成", f"转移成功!\n{source_uid} -> {target_uid}")
            self.refresh_users()
        except Exception as e:
            messagebox.showerror("错误", f"转移失败: {e}")
            self.log(f"转移失败: {e}", "error")

    # ================= 目录切换 / 关闭 =================
    def _hot_reload(self):
        """调用服务器 /admin/api/hot_reload 在运行中重载接口源码(玩家不断连)。"""
        if not (self.proc and self.proc.poll() is None):
            messagebox.showinfo("热更新", "请先启动服务器。", parent=self)
            return
        if not messagebox.askyesno(
                "热更新接口",
                "将在私服运行中重新加载接口源码(修复/新增接口),\n"
                "玩家无需断连。确定?"):
            return

        def _worker():
            import urllib.request, ssl as _ssl, json as _json
            proto = "https" if self.use_https.get() else "http"
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            # 服务器绑 :: (IPv6), Windows 上 IPV6_V6ONLY 可能使 127.0.0.1 连不上,
            # 因此依次尝试多种本机地址, 哪个通用哪个。
            hosts = ["127.0.0.1", "[::1]", "localhost"]
            last_err = None
            data = None
            for h in hosts:
                url = "%s://%s:%d/admin/api/hot_reload" % (proto, h, self.server_port)
                try:
                    req = urllib.request.Request(
                        url, data=b"{}", method="POST",
                        headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                        data = _json.loads(resp.read().decode("utf-8", errors="replace"))
                    break
                except Exception as e:
                    last_err = e
                    continue
            if data is None:
                _msg = "热更新失败: %s" % last_err
                self.after(0, lambda m=_msg: self.log(m, "error"))
                return
            msg = "热更新完成: 重载模块 %s, 重注册端点 %s" % (
                data.get("reloaded_modules"), data.get("reregistered_endpoints"))
            errs = data.get("errors") or []
            if errs:
                msg += "\n警告(%d): %s" % (len(errs), "; ".join(str(e) for e in errs[:5]))
            self.after(0, lambda: self.log(msg, "success" if not errs else "warn"))
            # UDP 跟随热更新: Node 进程需整体重启才能加载 udp源码 的新代码
            self.after(0, lambda: self.log("正在重启 UDP 联机服务器以应用新源码...", "info"))
            self.restart_udp_server()
            self.after(0, lambda: self.set_status("热更新完成"))

        threading.Thread(target=_worker, daemon=True).start()
        self.set_status("正在热更新接口...")

    def _retry_upnp(self):
        """手动重试 UPnP 端口映射"""
        self.log("正在尝试 UPnP 端口映射...", "info")
        result = setup_upnp(self.server_port, self.log)
        if result:
            self.set_status("UPnP 映射成功: 端口 %d" % self.server_port)
        else:
            self.set_status("UPnP 映射失败，请检查路由器")

    def change_dir(self):
        if self.server_state != "stopped":
            messagebox.showwarning("提示", "请先停止服务器再切换目录")
            return
        dlg = DirSelectDialog(self, default=self.root_dir)
        self.wait_window(dlg)
        if dlg.result:
            self.root_dir = dlg.result
            self.db = UserDB(dlg.result)
            self.dir_var.set(f"源码: {dlg.result}")
            cfg = load_panel_config()
            cfg["source_dir"] = dlg.result
            save_panel_config(cfg)
            self.refresh_users()
            self.set_status(f"已切换源码目录: {dlg.result}")

    def _on_close(self):
        if self.server_state in ("running", "starting") and self.proc and \
                self.proc.poll() is None:
            ans = messagebox.askyesnocancel("退出",
                    "服务器正在运行。\n\n"
                    "是 = 挂到后台继续运行(面板退出, 私服不断连, 可更新主控)\n"
                    "否 = 停止服务器并退出\n"
                    "取消 = 不退出")
            if ans is None:
                return
            if ans:
                # 后台模式: 不杀服务器进程, 保留 PID 文件, 面板直接退出。
                # 隧道进程已脱离面板(独立进程组)+ 状态文件记录 PID/URL,
                # 所以这里绝不调用 _stop_cloudflared, 隧道继续在后台运行, 地址不变。
                # 服务器输出已写到运行日志文件, 重开面板会自动附着并复用同一隧道地址。
                self.log("已切换到后台运行, 面板退出。私服继续在端口 %d 运行(隧道地址保持不变)。"
                         % self.server_port, "success")
                self.destroy()
                return
            else:
                # 停止并退出: 服务器和隧道一起停, 清掉隧道状态(下次启动才换新地址)。
                self._stop_cloudflared()
                self.stop_udp_server()  # UDP 跟随私服: 一起停止
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=5)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
                self._clear_pidfile()
                self._cleanup_request_dumps()  # 任务3: 关服清理接口缓存(除非已保存)
        self.destroy()

# ---------------------------------------------------------------------------
# 协议常量 / 工具
# ---------------------------------------------------------------------------
PROTOCOL = "sky-push/1"
SERVER_APP = "sky-admin-server"
CLIENT_APP = "sky-admin-client"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sky_push_server.json")
DEFAULT_CLIENT_URL = "http://127.0.0.1:9527"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
HTTP_TIMEOUT = 6
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".svn", ".idea", ".vscode"}

# 接口分组：key, 展示名, 源码包名（UDP 单独扫描）
GROUP_ORDER = (
    ("account", "账号接口", "account"),
    ("service", "服务接口", "service"),
    ("starwatch", "观星接口", "starwatch"),
    ("admin", "后台接口", "admin"),
    ("udp", "UDP 源码", None),
)
GROUP_TITLES = {key: title for key, title, _pkg in GROUP_ORDER}

UDP_EXTS = {".js", ".mjs", ".cjs", ".json"}

SERVER_BUILD = time.strftime(
    "%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(__file__))
)


class PushError(RuntimeError):
    """推送过程中的可读错误。"""


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def human_size(num) -> str:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0 or unit == "GB":
            if unit == "B":
                return "%d B" % n
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "-"


def normalize_base_url(text: str) -> str:
    """把用户输入的域名整理成 http(s)://host[:port] 形式。"""
    base = (text or "").strip()
    if not base:
        raise PushError("请输入客户端地址，例如 http://1.2.3.4:9527")
    if "://" not in base:
        base = "http://" + base
    parts = urllib.parse.urlsplit(base)
    if parts.scheme.lower() not in ("http", "https"):
        raise PushError("只支持 http / https 地址")
    if not parts.hostname:
        raise PushError("地址格式不正确：%s" % base)
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def read_text_file(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def decode_source(raw: bytes):
    """返回 (文本, 编码)。保留 BOM 语义，客户端按同一编码写回。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030"), "gb18030"
    except UnicodeDecodeError as exc:
        raise PushError("源码文件编码无法识别（既不是 UTF-8 也不是 GB18030）") from exc


# ---------------------------------------------------------------------------
# 服务端配置
# ---------------------------------------------------------------------------
def load_server_config() -> dict:
    cfg = {"last_url": DEFAULT_CLIENT_URL, "token": "", "auto_open": True}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if v is not None})
    except (OSError, ValueError):
        pass
    return cfg


def save_server_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 接口注册表：从源码目录实时解析出「接口名 -> 源码文件」
# ---------------------------------------------------------------------------
class InterfaceRegistry:
    """扫描游戏服务端源码，得到可在界面上逐条推送的接口清单。

    account / service / starwatch 三个包：解析 __init__.py 里的
    app.add_url_rule('/path', 'endpoint', handler)，再顺着 import 找到
    handler 所在的 .py 文件 —— 一行一个接口，推送的就是这个接口的源码文件。

    admin 包：多个后台接口定义在同一个 admin/__init__.py 里，同样按
    add_url_rule 列出来（推送时发送该文件，客户端整文件替换）。

    UDP 源码：src/ 下的 .js 源码，按文件列出（接口名以 udp: 前缀展示）。
    """

    def __init__(self, root_dir: str, udp_dir: str = ""):
        self.root_dir = os.path.abspath(root_dir or "")
        self.udp_dir = self._pick_udp_dir(udp_dir)
        self.entries = []
        self.errors = []
        self.elapsed = 0.0
        self._def_cache = {}

    # -- 目录 ---------------------------------------------------------------
    def _pick_udp_dir(self, udp_dir: str) -> str:
        candidates = []
        if udp_dir:
            candidates.append(udp_dir)
        candidates.append(UDP_SRC_DIR)
        candidates.append(os.path.join(os.path.dirname(self.root_dir), "realtime-server"))
        candidates.append(os.path.join(os.path.dirname(self.root_dir), "udp源码"))
        for cand in candidates:
            if cand and os.path.isdir(cand):
                return os.path.abspath(cand)
        return ""

    def scope_root(self, scope: str) -> str:
        return self.udp_dir if scope == "udp" else self.root_dir

    # -- 扫描 ---------------------------------------------------------------
    def scan(self):
        started = time.time()
        entries = []
        self.errors = []
        for key, title, pkg in GROUP_ORDER[:-1]:
            try:
                entries.extend(self._scan_package(key, title, pkg))
            except Exception as exc:  # 单个包失败不影响其它包
                self.errors.append("%s 扫描失败：%s" % (title, exc))
        try:
            entries.extend(self._scan_udp())
        except Exception as exc:
            self.errors.append("UDP 源码扫描失败：%s" % exc)
        entries.sort(key=lambda e: (e["group_index"], e["name"].lower()))
        self.entries = entries
        self.elapsed = time.time() - started
        return entries

    def counts(self) -> dict:
        result = {key: 0 for key, _t, _p in GROUP_ORDER}
        for entry in self.entries:
            result[entry["group"]] = result.get(entry["group"], 0) + 1
        return result

    # -- HTTP 接口包 ---------------------------------------------------------
    def _scan_package(self, kind: str, title: str, pkg: str):
        pkg_dir = os.path.join(self.root_dir, "business", pkg)
        init_py = os.path.join(pkg_dir, "__init__.py")
        if not os.path.isdir(pkg_dir):
            self.errors.append("%s：目录不存在 %s" % (title, pkg_dir))
            return []
        if not os.path.isfile(init_py):
            self.errors.append("%s：缺少 %s" % (title, init_py))
            return []

        try:
            tree = ast.parse(read_text_file(init_py), filename=init_py)
        except SyntaxError as exc:
            self.errors.append("%s：__init__.py 语法错误（第 %s 行）" % (title, exc.lineno))
            return []

        imports = self._collect_imports(tree)
        routes = self._collect_routes(tree)

        entries = []
        seen = set()
        for route, endpoint, handler, methods in routes:
            if route in seen:
                continue
            seen.add(route)
            source = self._locate_handler(pkg_dir, handler, imports)
            try:
                size = os.path.getsize(source)
            except OSError:
                size = 0
            relpath = os.path.relpath(source, self.root_dir).replace("\\", "/")
            entries.append(
                {
                    "id": "server:%s" % relpath,
                    "group": kind,
                    "group_title": title,
                    "group_index": [g[0] for g in GROUP_ORDER].index(kind),
                    "kind": kind,
                    "name": route,
                    "endpoint": endpoint,
                    "handler": handler,
                    "methods": "/".join(methods),
                    "scope": "server",
                    "relpath": relpath,
                    "abspath": source,
                    "size": size,
                    "unit": "file",
                }
            )
        return entries

    @staticmethod
    def _collect_imports(tree):
        """handler 名 -> 相对包内的模块路径（不含 .py）。"""
        imports = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or (node.level or 0) < 1:
                continue
            module = (node.module or "").replace(".", "/")
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                if module:
                    imports[bound] = module + "/" + alias.name
                else:
                    imports[bound] = alias.name
        return imports

    @staticmethod
    def _collect_routes(tree):
        routes = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "add_url_rule":
                continue
            if len(node.args) < 3:
                continue
            try:
                route = ast.literal_eval(node.args[0])
                endpoint = ast.literal_eval(node.args[1])
            except (ValueError, SyntaxError):
                continue
            if not isinstance(route, str) or not route.startswith("/"):
                continue
            handler_node = node.args[2]
            if isinstance(handler_node, ast.Name):
                handler = handler_node.id
            elif isinstance(handler_node, ast.Attribute):
                handler = handler_node.attr
            else:
                handler = ""
            methods = ["POST"]
            for kw in node.keywords:
                if kw.arg == "methods":
                    try:
                        methods = [str(m).upper() for m in ast.literal_eval(kw.value)]
                    except (ValueError, SyntaxError):
                        pass
            routes.append((route, str(endpoint or ""), handler, methods))
        return routes

    def _locate_handler(self, pkg_dir: str, handler: str, imports: dict) -> str:
        if handler:
            module = imports.get(handler) or imports.get(handler.split(".")[-1])
            if module:
                found = self._module_file(pkg_dir, module)
                if found:
                    return found
            index = self._def_index(pkg_dir)
            found = index.get(handler)
            if found:
                return found
        return os.path.join(pkg_dir, "__init__.py")

    @staticmethod
    def _module_file(pkg_dir: str, rel_module: str):
        base = os.path.join(pkg_dir, *rel_module.split("/"))
        candidates = (base + ".py", os.path.join(base, "__init__.py"))
        for cand in candidates:
            if os.path.isfile(cand):
                return cand
        return None

    def _def_index(self, pkg_dir: str) -> dict:
        """函数名 -> 定义它的文件；__init__.py 优先级最低。"""
        cached = self._def_cache.get(pkg_dir)
        if cached is not None:
            return cached
        index = {}
        for dirpath, dirnames, filenames in os.walk(pkg_dir):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                priority = 1 if filename == "__init__.py" else 0
                try:
                    tree = ast.parse(read_text_file(path), filename=path)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        old = index.get(node.name)
                        if old is None or priority < old[0]:
                            index[node.name] = (priority, path)
        result = {name: item[1] for name, item in index.items()}
        self._def_cache[pkg_dir] = result
        return result

    # -- UDP 源码 -----------------------------------------------------------
    def _scan_udp(self):
        title = GROUP_TITLES["udp"]
        if not self.udp_dir or not os.path.isdir(self.udp_dir):
            self.errors.append("UDP 源码：目录不存在（可在客户端设置里指定）")
            return []
        entries = []
        for dirpath, dirnames, filenames in os.walk(self.udp_dir):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for filename in sorted(filenames):
                ext = os.path.splitext(filename)[1].lower()
                if ext not in UDP_EXTS:
                    continue
                abspath = os.path.join(dirpath, filename)
                relpath = os.path.relpath(abspath, self.udp_dir).replace("\\", "/")
                try:
                    size = os.path.getsize(abspath)
                except OSError:
                    continue
                entries.append(
                    {
                        "id": "udp:%s" % relpath,
                        "group": "udp",
                        "group_title": title,
                        "group_index": [g[0] for g in GROUP_ORDER].index("udp"),
                        "kind": "udp",
                        "name": "udp: %s" % relpath,
                        "endpoint": "",
                        "handler": "",
                        "methods": "",
                        "scope": "udp",
                        "relpath": relpath,
                        "abspath": abspath,
                        "size": size,
                        "unit": "file",
                    }
                )
        return entries

    # -- 推送时读取最新源码 -------------------------------------------------
    def read_entry_source(self, entry: dict):
        """实时读取接口源码（不缓存），返回 (path, raw, text, encoding)。"""
        scope_root = self.scope_root(entry["scope"])
        if not scope_root:
            raise PushError("UDP 源码目录不存在，无法推送 %s" % entry["name"])
        relpath = str(entry["relpath"]).replace("/", os.sep)
        path = os.path.abspath(os.path.join(scope_root, relpath))
        root = os.path.abspath(scope_root)
        if path != root and not path.startswith(root + os.sep):
            raise PushError("源码路径越界：%s" % entry["relpath"])
        if not os.path.isfile(path):
            raise PushError("源码文件不存在：%s" % path)
        size = os.path.getsize(path)
        if size > MAX_SOURCE_BYTES:
            raise PushError(
                "源码文件过大（%s > %s），请拆分后再推送"
                % (human_size(size), human_size(MAX_SOURCE_BYTES))
            )
        with open(path, "rb") as fh:
            raw = fh.read()
        text, encoding = decode_source(raw)
        return path, raw, text, encoding


# ---------------------------------------------------------------------------
# 服务端 -> 客户端 HTTP 小客户端
# ---------------------------------------------------------------------------
class ClientLink:
    def __init__(self, base_url: str, token: str = ""):
        self.base_url = normalize_base_url(base_url)
        self.token = (token or "").strip()

    @property
    def display(self) -> str:
        return self.base_url.split("//", 1)[-1]

    @staticmethod
    def _ssl_context():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    @staticmethod
    def _page_hint(html: str) -> str:
        """从返回页面里取标题/首段文字，用于区分游戏服务端、隧道错误页等。"""
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
        match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()
            if title:
                return title[:80]
        plain = re.sub(r"(?s)<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", plain).strip()[:80]

    def request(self, path: str, method: str = "GET", payload=None, timeout: float = HTTP_TIMEOUT):
        url = self.base_url + path
        data = None
        headers = {
            "User-Agent": "SkyPushServer/%s" % SERVER_BUILD,
            "Accept": "application/json, text/html;q=0.9",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self.token:
            headers["X-Sky-Token"] = self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        context = self._ssl_context() if url.lower().startswith("https://") else None
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise PushError("连接超时（%ss）：%s" % (timeout, self.base_url)) from exc
            if isinstance(reason, ssl.SSLError):
                raise PushError("TLS 握手失败：%s（%s）" % (reason, self.base_url)) from exc
            raise PushError("无法连接 %s：%s" % (self.base_url, reason)) from exc
        except ssl.SSLError as exc:
            raise PushError("TLS 握手失败：%s（%s）" % (exc, self.base_url)) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise PushError("连接超时（%ss）：%s" % (timeout, self.base_url)) from exc
        except OSError as exc:
            raise PushError("无法连接 %s：%s" % (self.base_url, exc)) from exc

    def probe(self) -> dict:
        """连通性测试。

        先访问 /api/ping 这个确定性标识，再取 HTML 主页。旧顺序是
        「主页 → ping」，中间只要多一层反向代理/自定义首页就会报
        “HTML 主页访问失败(HTTP 200)”，看不出究竟连到了什么。
        """
        started = time.time()
        info = {"ok": False, "homepage": False, "version": "",
                "status": {}, "elapsed": 0.0}

        ping = {}
        ping_error = ""
        code, body = 0, ""
        try:
            code, body = self.request("/api/ping", timeout=6)
        except PushError as exc:
            ping_error = str(exc)
        if code == 200:
            try:
                ping = json.loads(body)
            except ValueError:
                ping = {}
            if ping.get("app") == CLIENT_APP:
                info["version"] = ping.get("version") or ""
                info["ping"] = ping
            else:
                ping_error = (
                    "目标不是 Sky 更新客户端（/api/ping 返回 app=%s）"
                    % (ping.get("app") or "非 JSON")
                )
        elif code == 401:
            ping_error = "客户端拒绝了推送令牌（HTTP 401），请确认两端令牌一致"
        elif not ping_error:
            ping_error = "客户端 /api/ping 返回 HTTP %s" % code

        # 主页只用于「打开主页」和辅助诊断，不再作为连接成功的前置条件。
        home_code = 0
        html = ""
        home_error = ""
        try:
            home_code, html = self.request("/", timeout=6)
        except PushError as exc:
            home_error = str(exc)
        homepage = bool(
            home_code == 200
            and ("sky-admin-client" in html or "Sky 更新客户端" in html)
        )

        if ping.get("app") == CLIENT_APP:
            info["homepage"] = homepage
        elif homepage and code == 404:
            # 兼容没有 /api/ping 的最早期客户端（未知路径返回 404）。
            info["homepage"] = True
            info["version"] = info["version"] or "未知（旧版客户端，无 /api/ping）"
        else:
            detail = ping_error or home_error or "目标既不是更新客户端主页，也没有 /api/ping"
            if detail.endswith("。"):
                detail = detail[:-1]
            hint = self._page_hint(html)
            if home_code == 200 and hint:
                detail += "；目标返回页面《%s》" % hint
            elif home_code == 200:
                detail += "；目标返回 HTTP 200，但不是更新客户端页面"
            low = (html or "").lower()
            if "argo tunnel error" in low or ("cloudflare" in low and "1033" in low):
                detail += "；这是 Cloudflare 隧道错误页，隧道没有指向客户端 9527"
            if code == 0 and (
                "127.0.0.1" in self.base_url or "localhost" in self.base_url
            ):
                detail += "；127.0.0.1 指服务端本机，客户端在另一台机器时请填它的局域网 IP"
            if code == 401:
                detail += "。请把服务端与客户端的推送令牌改成一致"
            else:
                detail += (
                    "。客户端地址应为运行更新接收的机器 IP:9527，"
                    "不是游戏服务端 8443，也不是指向 8443 的 cloudflared 隧道"
                    "（测试地址 %s）" % self.base_url
                )
            raise PushError(detail)

        try:
            code, body = self.request("/api/status", timeout=5)
        except PushError:
            code = 0
        if code == 200:
            try:
                info["status"] = json.loads(body)
            except ValueError:
                info["status"] = {}
        info["ok"] = True
        info["elapsed"] = time.time() - started
        return info

    def push_items(self, items, server_url: str) -> dict:
        batch = {
            "id": "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:4]),
            "created": now_text(),
            "server": {
                "app": SERVER_APP,
                "name": socket.gethostname(),
                "url": server_url,
                "build": SERVER_BUILD,
            },
        }
        payload = {
            "protocol": PROTOCOL,
            "app": SERVER_APP,
            "batch": batch,
            "items": list(items),
        }
        code, body = self.request("/api/push", "POST", payload, timeout=30)
        if code != 200:
            raise PushError("客户端拒收（HTTP %s）：%s" % (code, (body or "")[:300]))
        try:
            return json.loads(body or "{}")
        except ValueError as exc:
            raise PushError("客户端返回内容无法解析：%s" % body[:200]) from exc

    def handshake(self) -> dict:
        payload = {
            "app": SERVER_APP,
            "name": socket.gethostname(),
            "url": self.base_url,
            "version": SERVER_BUILD,
            "protocol": PROTOCOL,
        }
        code, body = self.request("/api/handshake", "POST", payload, timeout=5)
        if code != 200:
            raise PushError("握手失败（HTTP %s）" % code)
        try:
            return json.loads(body or "{}")
        except ValueError:
            return {}


# ---------------------------------------------------------------------------
# 推送窗口
# ---------------------------------------------------------------------------
class PushUpdateDialog(tk.Toplevel):
    """输入客户端地址 → 连通性测试 → 逐接口推送源码。"""

    def __init__(self, app, registry: InterfaceRegistry | None = None):
        super().__init__(app)
        self.app = app
        self.cfg = load_server_config()
        self.registry = registry or InterfaceRegistry(
            getattr(app, "root_dir", QUICK_PATH),
            UDP_SRC_DIR,
        )
        self.title("推送更新 · 接口源码 → 客户端")
        self.geometry("1020x760")
        self.minsize(880, 620)
        self.transient(app)
        self.configure(bg="#f4f6f8")

        self.link: ClientLink | None = None
        self.base_url = ""
        self.connected = False
        self.pending_on_client = 0
        self._busy = set()
        self._buttons = []
        self._size_labels = {}
        self._rendered = False
        self._testing = False
        self._closing = False
        self._probe_gen = 0
        self._hb_job = None
        self._hb_fail = 0
        self._filter_job = None
        self._scan_thread = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 打开窗口即后台扫描源码目录（只统计，连接成功后才展示）
        self._start_scan()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        head = tk.Frame(self, bg="#f4f6f8")
        head.pack(fill="x", padx=14, pady=(12, 2))
        head.columnconfigure(5, weight=1)

        tk.Label(head, text="客户端地址", bg="#f4f6f8",
                 font=("微软雅黑", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value=self.cfg.get("last_url") or DEFAULT_CLIENT_URL)
        entry = tk.Entry(head, textvariable=self.url_var, width=32,
                         font=("Consolas", 10), relief="solid", bd=1)
        entry.grid(row=0, column=1, padx=(6, 8), ipady=3, sticky="w")
        entry.bind("<Return>", lambda _e: self._on_test())

        self.btn_test = tk.Button(
            head, text="连通性测试", width=12, cursor="hand2",
            bg="#1976d2", fg="white", activebackground="#1565c0",
            activeforeground="white", relief="flat",
            font=("微软雅黑", 9, "bold"), command=self._on_test,
        )
        self.btn_test.grid(row=0, column=2, padx=4)

        self.btn_home = tk.Button(
            head, text="打开 HTML 主页", width=14, cursor="hand2",
            command=self._open_home,
        )
        self.btn_home.grid(row=0, column=3, padx=4)

        self.auto_open = tk.BooleanVar(value=bool(self.cfg.get("auto_open", True)))
        tk.Checkbutton(
            head, text="测试成功自动打开主页", variable=self.auto_open,
            bg="#f4f6f8", activebackground="#f4f6f8",
        ).grid(row=0, column=4, padx=(8, 4))

        self.link_state = tk.Label(
            head, text="未连接", bg="#f4f6f8", fg="#78909c",
            font=("微软雅黑", 9, "bold"), anchor="e",
        )
        self.link_state.grid(row=0, column=5, sticky="e")

        tk.Label(head, text="访问令牌(可选)", bg="#f4f6f8",
                 font=("微软雅黑", 9)).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.token_var = tk.StringVar(value=self.cfg.get("token") or "")
        tk.Entry(head, textvariable=self.token_var, width=26,
                 font=("Consolas", 10), relief="solid", bd=1).grid(
            row=1, column=1, padx=(6, 8), pady=(8, 0), ipady=3, sticky="w")
        tk.Label(
            head,
            text="① 输入客户端域名/IP 并测试（会直接访问客户端 HTML 主页）  "
                 "② 连接成功后逐个点『推送』把接口源码发过去  "
                 "③ 客户端右上角变成『有新版本』后确认更新",
            bg="#f4f6f8", fg="#78909c", font=("微软雅黑", 8), anchor="w",
        ).grid(row=1, column=2, columnspan=4, sticky="w", padx=(4, 0), pady=(8, 0))

        tk.Label(
            head,
            text="地址示例：http://<客户端局域网 IP>:9527  ｜  游戏服务端的 8443、"
                 "指向 8443 的 cloudflared 隧道都不能填在这里",
            bg="#fff3e0", fg="#e65100", font=("微软雅黑", 8), anchor="w",
        ).grid(row=2, column=0, columnspan=6, sticky="we", pady=(6, 0))

        self.status_line = tk.Label(
            self, text="尚未连接客户端。", bg="#f4f6f8", fg="#546e7a",
            font=("微软雅黑", 9), anchor="w", justify="left", wraplength=980,
        )
        self.status_line.pack(fill="x", padx=14, pady=(6, 6))

        # 接口清单
        list_wrap = tk.LabelFrame(self, text=" 接口清单 ", bg="#f4f6f8",
                                  font=("微软雅黑", 9, "bold"))
        list_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        bar = tk.Frame(list_wrap, bg="#f4f6f8")
        bar.pack(fill="x", padx=8, pady=(6, 2))
        self.count_label = tk.Label(
            bar, text="正在扫描源码目录…", bg="#f4f6f8", fg="#37474f",
            font=("微软雅黑", 9, "bold"), anchor="w",
        )
        self.count_label.pack(side="left")
        tk.Label(bar, text="筛选", bg="#f4f6f8", fg="#78909c",
                 font=("微软雅黑", 9)).pack(side="left", padx=(16, 4))
        self.filter_var = tk.StringVar()
        filter_entry = tk.Entry(bar, textvariable=self.filter_var, width=24,
                                font=("Consolas", 9), relief="solid", bd=1)
        filter_entry.pack(side="left", ipady=2)
        filter_entry.bind("<KeyRelease>", self._on_filter_change)
        self.btn_refresh = tk.Button(bar, text="刷新清单", width=10,
                                     command=self._start_scan)
        self.btn_refresh.pack(side="right")

        canvas_wrap = tk.Frame(list_wrap, bg="#f4f6f8")
        canvas_wrap.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.canvas = tk.Canvas(canvas_wrap, bg="#fafafa",
                                highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(canvas_wrap, orient="vertical",
                               command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.inner = tk.Frame(self.canvas, bg="#fafafa")
        self._inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

        self.placeholder = tk.Frame(self.inner, bg="#fafafa")
        self.placeholder.pack(fill="both", expand=True, padx=24, pady=40)
        tk.Label(
            self.placeholder,
            text="先连接客户端\n\n① 输入域名/IP → ② 点击『连通性测试』\n连接成功后，这里会列出全部接口，每个接口一行：\n\n"
                 "账号接口 / 服务接口 / 观星接口 / 后台接口 / UDP 源码",
            bg="#fafafa", fg="#90a4ae", font=("微软雅黑", 11),
            justify="center",
        ).pack()

        # 日志
        log_wrap = tk.LabelFrame(self, text=" 推送日志 ", bg="#f4f6f8",
                                 font=("微软雅黑", 9, "bold"))
        log_wrap.pack(fill="x", padx=14, pady=(0, 6))
        self.log_text = tk.Text(log_wrap, height=5, state="disabled",
                                font=("Consolas", 9), bg="#fbfcfd",
                                relief="flat", wrap="word")
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical",
                                   command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        log_scroll.pack(side="right", fill="y", pady=4)

        footer = tk.Frame(self, bg="#f4f6f8")
        footer.pack(fill="x", padx=14, pady=(0, 10))
        tk.Button(footer, text="关闭", width=12, command=self._on_close).pack(side="right")
        self._log(
            "提示：客户端打开 admin_panel_client.py 后会自动监听 9527（关掉了可点『启动监听』）；"
            "地址填客户端“更新接收”页蓝色那行显示的地址，"
            "不是游戏服务端 8443。"
        )

    # -- 滚动 ---------------------------------------------------------------
    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._inner_id, width=event.width)

    def _bind_wheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    # -- 日志 / 状态 --------------------------------------------------------
    def _log(self, message: str):
        if self._closing:
            return
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"), message))
            if int(self.log_text.index("end-1c").split(".")[0]) > 800:
                self.log_text.delete("1.0", "200.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _status(self, text: str, color: str = "#546e7a"):
        if self._closing:
            return
        self.status_line.configure(text=text, fg=color)

    def _set_link_label(self, text: str, color: str):
        self.conn_text = text
        self.link_state.configure(text=text, fg=color)

    def _update_link_label(self):
        if not self.connected:
            return
        detail = "已连接 · %s" % (self.base_url.split("//", 1)[-1])
        if self.pending_on_client:
            detail += " · 待更新 %d 项" % self.pending_on_client
        self._set_link_label(detail, "#2e7d32")

    # ------------------------------------------------------------- 扫描清单
    def _start_scan(self):
        if self._scan_thread and self._scan_thread.is_alive():
            return
        self.count_label.configure(text="正在扫描源码目录…")
        self.btn_refresh.configure(state="disabled")

        def work():
            try:
                entries = self.registry.scan()
                self._ui(self._on_scan_done, entries, None)
            except Exception as exc:
                self._ui(self._on_scan_done, None, str(exc))

        self._scan_thread = threading.Thread(target=work, daemon=True)
        self._scan_thread.start()

    def _on_scan_done(self, entries, error):
        if self._closing:
            return
        self.btn_refresh.configure(state="normal")
        if error:
            self.count_label.configure(text="扫描失败")
            self._log("扫描源码目录失败：%s" % error)
            self._status("✗ 扫描源码目录失败：%s" % error, "#c62828")
            return
        counts = self.registry.counts()
        self.count_label.configure(
            text="共 %d 个接口 · 账号 %d / 服务 %d / 观星 %d / 后台 %d / UDP %d"
                 % (len(entries), counts["account"], counts["service"],
                    counts["starwatch"], counts["admin"], counts["udp"])
        )
        if self.registry.errors:
            for err in self.registry.errors[:6]:
                self._log("⚠ %s" % err)
        else:
            self._log("源码清单扫描完成，用时 %.2f 秒。" % self.registry.elapsed)
        if self.connected:
            self._render_registry()

    # ------------------------------------------------------------- 连通性测试
    def _on_test(self):
        if self._testing:
            return
        self._testing = True
        self.btn_test.configure(state="disabled", text="测试中…")
        self._set_link_label("正在连接…", "#ef6c00")
        self._status("正在访问客户端 HTML 主页 …", "#546e7a")
        raw = self.url_var.get().strip()
        token = self.token_var.get().strip()
        self._probe_gen += 1
        gen = self._probe_gen

        def work():
            try:
                link = ClientLink(raw, token)
                info = link.probe()
                self._ui(self._on_probe_done, link, info, None, gen)
            except PushError as exc:
                self._ui(self._on_probe_done, None, None, str(exc), gen)
            except Exception as exc:  # 兜底，错误显示到界面而不是卡死
                self._ui(self._on_probe_done, None, None, "连接异常：%s" % exc, gen)

        threading.Thread(target=work, daemon=True).start()

    def _on_probe_done(self, link, info, error, gen):
        if self._closing or gen != self._probe_gen:
            return
        self._testing = False
        self.btn_test.configure(state="normal", text="连通性测试")
        if error:
            self.connected = False
            self.link = None
            self._set_link_label("连接失败", "#c62828")
            self._status("✗ %s" % error, "#c62828")
            self._log("连接失败：%s" % error)
            self._set_push_enabled(False)
            return

        self.connected = True
        self.link = link
        self._hb_fail = 0
        self.base_url = link.base_url
        self.url_var.set(link.base_url)
        self.cfg["last_url"] = link.base_url
        self.cfg["token"] = link.token
        self.cfg["auto_open"] = bool(self.auto_open.get())
        save_server_config(self.cfg)

        status = info.get("status") or {}
        self.pending_on_client = int(
            status.get("pending_count") or len(status.get("pending") or [])
        )
        self._update_link_label()
        home_ok = bool(info.get("homepage"))
        home_txt = "HTML 主页访问正常" if home_ok else "HTML 主页未匹配（/api/ping 正常）"
        self._status(
            "✓ 连通性测试通过：%s（%.0f ms），客户端版本 v%s。"
            " 现在可以从下面的清单逐条推送接口源码。"
            % (home_txt, info.get("elapsed", 0) * 1000, info.get("version") or "?"),
            "#2e7d32",
        )
        self._log(
            "连通性测试通过：%s  主页 %s  /api/ping ✓  客户端 v%s"
            % (link.base_url, "✓" if home_ok else "—", info.get("version") or "?")
        )
        if not self._rendered:
            self._render_registry()
        self._start_heartbeat()
        if self.auto_open.get():
            self._open_home()

    def _open_home(self):
        url = (self.base_url or normalize_base_url(self.url_var.get())).rstrip("/") + "/"
        if not self.connected and not self.base_url:
            if not messagebox.askyesno(
                "尚未连接", "还没有成功连接客户端，仍要在浏览器中打开 %s 吗？" % url,
                parent=self,
            ):
                return
        webbrowser.open(url)

    # ------------------------------------------------------------- 心跳
    def _start_heartbeat(self):
        if self._hb_job is not None:
            try:
                self.after_cancel(self._hb_job)
            except tk.TclError:
                pass
        self._hb_job = self.after(8000, self._heartbeat_tick)

    def _heartbeat_tick(self):
        self._hb_job = None
        if self._closing or not self.connected or self.link is None:
            return
        link = self.link

        def work():
            try:
                link.handshake()
                status = {}
                code, body = link.request("/api/status", timeout=4)
                if code == 200:
                    try:
                        status = json.loads(body)
                    except ValueError:
                        status = {}
                self._ui(self._heartbeat_done, status, None)
            except Exception as exc:
                self._ui(self._heartbeat_done, None, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _heartbeat_done(self, status, error):
        if self._closing:
            return
        if error is None:
            self._hb_fail = 0
            self.connected = True
            self.pending_on_client = int(
                status.get("pending_count") or len(status.get("pending") or [])
            )
            self._update_link_label()
            self._set_push_enabled(True)
            self._hb_job = self.after(8000, self._heartbeat_tick)
            return
        self._hb_fail += 1
        self._log("心跳失败（%d/2）：%s" % (self._hb_fail, error))
        if self._hb_fail >= 2:
            self.connected = False
            self.link = None
            self._set_link_label("连接已断开", "#c62828")
            self._status("✗ 与客户端断开连接，请重新『连通性测试』。", "#c62828")
            self._set_push_enabled(False)
            self._hb_job = self.after(10000, self._heartbeat_tick)
        else:
            self._hb_job = self.after(4000, self._heartbeat_tick)

    # ------------------------------------------------------------- 渲染清单
    def _on_filter_change(self, _event=None):
        if self._filter_job is not None:
            try:
                self.after_cancel(self._filter_job)
            except tk.TclError:
                pass
        self._filter_job = self.after(250, self._render_registry)

    def _render_registry(self):
        self._filter_job = None
        if self._closing:
            return
        if not self.registry.entries:
            self._status("源码清单为空，请检查源码目录是否正确。", "#c62828")
            return
        if not self.connected:
            return

        if self.placeholder is not None:
            self.placeholder.destroy()
            self.placeholder = None

        for child in self.inner.winfo_children():
            child.destroy()
        self._buttons = []
        self._size_labels = {}
        self._rendered = True

        query = self.filter_var.get().strip().lower()
        by_group = {}
        for entry in self.registry.entries:
            if query and query not in entry["name"].lower() and query not in entry["relpath"].lower():
                continue
            by_group.setdefault(entry["group"], []).append(entry)

        row = 0
        shown = 0
        for key, title, _pkg in GROUP_ORDER:
            items = by_group.get(key) or []
            if not items:
                continue
            header = tk.Frame(self.inner, bg="#e3f2fd")
            header.grid(row=row, column=0, sticky="ew", pady=(8 if row else 2, 0))
            tk.Label(
                header, text="%s · %d 个" % (title, len(items)),
                bg="#e3f2fd", fg="#1565c0", anchor="w",
                font=("微软雅黑", 9, "bold"),
            ).pack(side="left", padx=10, pady=3)
            row += 1
            for entry in items:
                self._render_row(entry, row)
                row += 1
                shown += 1

        self.inner.columnconfigure(0, weight=1)
        counts = self.registry.counts()
        self.count_label.configure(
            text="显示 %d / 共 %d 个接口 · 账号 %d / 服务 %d / 观星 %d / 后台 %d / UDP %d"
                 % (shown, len(self.registry.entries), counts["account"],
                    counts["service"], counts["starwatch"], counts["admin"], counts["udp"])
        )
        if not shown:
            tk.Label(self.inner, text="没有匹配的接口。", bg="#fafafa",
                     fg="#90a4ae", font=("微软雅黑", 9)).grid(
                row=row, column=0, pady=20)
        self._set_push_enabled(self.connected)
        self.canvas.yview_moveto(0)

    def _render_row(self, entry: dict, row: int):
        frame = tk.Frame(self.inner, bg="#ffffff", highlightthickness=1,
                         highlightbackground="#eceff1")
        frame.grid(row=row, column=0, sticky="ew", padx=4, pady=1)

        btn = tk.Button(frame, text="推送", width=7, cursor="hand2",
                        relief="groove", command=lambda e=entry: self._push_entry(e))
        btn.pack(side="right", padx=(6, 8), pady=3)

        name = tk.Label(frame, text=entry["name"], bg="#ffffff",
                        fg="#1e293b", anchor="w", font=("Consolas", 9))
        name.pack(side="left", fill="x", expand=True, padx=(10, 4), pady=4)

        meta_text = "%s · %s" % (entry["group_title"], human_size(entry["size"]))
        meta = tk.Label(frame, text=meta_text, bg="#ffffff", fg="#90a4ae",
                        font=("微软雅黑", 8))
        meta.pack(side="left", padx=(4, 2))

        self._buttons.append((entry["id"], btn))
        self._size_labels.setdefault(entry["id"], []).append((meta, entry["group_title"]))

    def _set_push_enabled(self, enabled: bool):
        for entry_id, button in self._buttons:
            try:
                state = "normal" if (enabled and entry_id not in self._busy) else "disabled"
                button.configure(state=state)
            except tk.TclError:
                pass

    # ------------------------------------------------------------- 推送单个接口
    def _push_entry(self, entry: dict):
        if not self.connected or self.link is None:
            messagebox.showwarning("尚未连接", "请先做『连通性测试』并连接成功。", parent=self)
            return
        if entry["id"] in self._busy:
            return
        self._busy.add(entry["id"])
        self._set_push_enabled(True)
        self._status("正在读取并推送 %s …" % entry["name"], "#1565c0")
        link = self.link
        registry = self.registry

        def work():
            try:
                path, raw, text, encoding = registry.read_entry_source(entry)
                item = {
                    "id": entry["id"],
                    "name": entry["name"],
                    "kind": entry["kind"],
                    "kind_label": entry["group_title"],
                    "scope": entry["scope"],
                    "relpath": entry["relpath"],
                    "size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "encoding": encoding,
                    "content": text,
                }
                result = link.push_items([item], self.base_url)
                self._ui(self._push_done, entry, item, result, None)
            except Exception as exc:
                self._ui(self._push_done, entry, None, None, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _push_done(self, entry, item, result, error):
        if self._closing:
            return
        self._busy.discard(entry["id"])
        self._set_push_enabled(self.connected)
        if error:
            self._log("推送失败 %s：%s" % (entry["name"], error))
            self._status("✗ 推送 %s 失败：%s" % (entry["name"], error), "#c62828")
            return

        for meta, group_title in self._size_labels.get(entry["id"], []):
            try:
                meta.configure(text="%s · %s" % (group_title, human_size(item["size"])))
            except tk.TclError:
                pass
        self.pending_on_client = int(result.get("pending") or 0)
        self._update_link_label()
        self._log(
            "已推送 %s · %s · 客户端待更新 %d 项"
            % (entry["name"], human_size(item["size"]), self.pending_on_client)
        )
        self._status(
            "✓ 已推送 %s（%s）。客户端右上角状态已变为『有新版本』，等待对方确认更新。"
            % (entry["name"], human_size(item["size"])),
            "#2e7d32",
        )

    # ------------------------------------------------------------- 工具
    def _ui(self, func, *args):
        if self._closing:
            return
        try:
            self.after(0, lambda: func(*args))
        except tk.TclError:
            pass

    def _on_close(self):
        self._closing = True
        for job in (self._hb_job, self._filter_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._unbind_wheel()
        try:
            self.destroy()
        except tk.TclError:
            pass


def _install_push_bar(self):
    """工具栏已经排满，单独加一条细操作条，避免挤压右侧状态灯。"""
    try:
        self.nb.pack_forget()
    except Exception:
        return
    bar = tk.Frame(self, bg="#eef3f8", height=36)
    bar.pack(side="top", fill="x")
    self.nb.pack(fill="both", expand=True, padx=6, pady=4)

    self.btn_push = tk.Button(
        bar, text="推送更新", width=14, cursor="hand2",
        bg="#1976d2", fg="white", activebackground="#1565c0",
        activeforeground="white", relief="flat",
        font=("微软雅黑", 9, "bold"), command=self.open_push_dialog,
    )
    self.btn_push.pack(side="left", padx=(10, 10), pady=5)

    tk.Label(
        bar,
        text="把当前源码目录里的接口源码逐条推送给客户端；客户端确认后替换源码并热更新",
        bg="#eef3f8", fg="#546e7a", font=("微软雅黑", 9),
    ).pack(side="left")

    self.push_hint = tk.Label(bar, text="", bg="#eef3f8", fg="#2e7d32",
                              font=("微软雅黑", 8))
    self.push_hint.pack(side="right", padx=10)

def open_push_dialog(self):
    if self._push_dialog is not None:
        try:
            if self._push_dialog.winfo_exists():
                self._push_dialog.deiconify()
                self._push_dialog.lift()
                self._push_dialog.focus_force()
                return
        except tk.TclError:
            pass
    self._push_dialog = PushUpdateDialog(self)
    try:
        self.push_hint.configure(text="源码目录：%s" % self.root_dir)
    except tk.TclError:
        pass


AdminPanel._install_push_bar = _install_push_bar
AdminPanel.open_push_dialog = open_push_dialog


_APP_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------
PROTOCOL = "sky-push/1"
SERVER_APP = "sky-admin-server"
CLIENT_APP = "sky-admin-client"
CLIENT_VERSION = "1.0.0"
CLIENT_CONFIG = os.path.join(_APP_DIR, "sky_client_config.json")
DEFAULT_PORT = 9527
CLOUDFLARED_PATH = _cloudflared_path()
CONNECT_TTL = 25                 # 服务端超过该秒数没心跳即视为断开
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_PENDING = 400
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".svn", ".idea", ".vscode"}
WRITE_EXTS = {".py", ".js", ".mjs", ".cjs", ".json", ".html", ".css", ".txt", ".md", ".yml", ".yaml"}
KIND_ORDER = {"account": 0, "service": 1, "starwatch": 2, "admin": 3, "udp": 4}

STATUS_STYLES = {
    "disconnected": {"bg": "#eceff1", "fg": "#455a64", "dot": "#9e9e9e", "border": "#cfd8dc"},
    "scanning": {"bg": "#fff3e0", "fg": "#e65100", "dot": "#ff9800", "border": "#ffe0b2"},
    "connecting": {"bg": "#fff3e0", "fg": "#e65100", "dot": "#ff9800", "border": "#ffe0b2"},
    "ready": {"bg": "#e8f5e9", "fg": "#2e7d32", "dot": "#4caf50", "border": "#c8e6c9"},
    "pending": {"bg": "#ffebee", "fg": "#c62828", "dot": "#f44336", "border": "#ffcdd2"},
    "updated": {"bg": "#e3f2fd", "fg": "#1565c0", "dot": "#2196f3", "border": "#bbdefb"},
}


class ClientError(RuntimeError):
    """客户端内部可读错误。"""


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def human_size(num) -> str:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0 or unit == "GB":
            if unit == "B":
                return "%d B" % n
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "-"


def lan_ips():
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            ips.append(probe.getsockname()[0])
            probe.close()
        except OSError:
            pass
    return ips


def safe_join(root: str, relpath: str) -> str:
    """把推送包里的相对路径安全地拼到源码根目录，拒绝穿越。"""
    rel = (relpath or "").replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/") or ":" in rel:
        raise ClientError("非法的源码路径：%r" % relpath)
    root_abs = os.path.abspath(root)
    target = os.path.abspath(os.path.join(root_abs, *rel.split("/")))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise ClientError("源码路径越界：%r" % relpath)
    return target


def unverified_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------------------------------------------------------------------------
# 客户端配置
# ---------------------------------------------------------------------------
def load_client_config() -> dict:
    defaults = {
        "bind": "0.0.0.0",
        "port": DEFAULT_PORT,
        "client_root": "",
        "udp_root": "",
        "admin_url": "https://127.0.0.1:8443",
        "token": "",
        "version": CLIENT_VERSION,
        "applied": "",
        "applied_at": "",
    }
    try:
        with open(CLIENT_CONFIG, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            defaults.update({k: v for k, v in data.items() if v is not None})
    except (OSError, ValueError):
        pass
    return defaults


# ---------------------------------------------------------------------------
# 更新引擎（无界面依赖，方便单独测试）
# ---------------------------------------------------------------------------
class UpdateEngine:
    def __init__(self, cfg: dict, cfg_path: str, default_root: str = ""):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.lock = threading.RLock()
        self.logs = collections.deque(maxlen=600)
        self.log_seq = 0
        self.server = {}
        self.scanning = False
        self.scan_done = False
        self.resource = {"files": 0, "bytes": 0, "udp_files": 0,
                         "broken": [], "broken_count": 0, "seconds": 0.0,
                         "scanned_at": ""}
        self.pending = collections.OrderedDict()
        self.applied = str(cfg.get("applied") or "")
        self.applied_at = str(cfg.get("applied_at") or "")
        self.last_apply = None
        self.httpd = None
        self.http_port = 0
        self.http_error = ""
        self._scan_thread = None

        if default_root and not (cfg.get("client_root") or "").strip():
            cfg["client_root"] = default_root
        if not (cfg.get("udp_root") or "").strip():
            guess = self._guess_udp_root(cfg.get("client_root") or "")
            if guess:
                cfg["udp_root"] = guess
        self.log("更新客户端已启动（版本 v%s）" % CLIENT_VERSION)

    # -- 配置 ---------------------------------------------------------------
    @staticmethod
    def _guess_udp_root(root: str) -> str:
        if not root:
            return ""
        candidates = (
            os.path.join(os.path.dirname(os.path.abspath(root)), "realtime-server"),
            os.path.join(os.path.dirname(os.path.abspath(root)), "udp源码"),
            os.path.join(os.path.abspath(root), "udp源码"),
        )
        for cand in candidates:
            if os.path.isdir(cand):
                return cand
        return ""

    def roots(self):
        root = (self.cfg.get("client_root") or "").strip()
        udp = (self.cfg.get("udp_root") or "").strip()
        if not udp and root:
            udp = self._guess_udp_root(root)
        return root, udp

    def save_config(self):
        with self.lock:
            data = {
                key: self.cfg.get(key)
                for key in ("bind", "port", "client_root", "udp_root",
                            "admin_url", "token", "version", "applied", "applied_at")
            }
        try:
            with open(self.cfg_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            self.log("保存配置失败：%s" % exc)

    # -- 日志 ---------------------------------------------------------------
    def log(self, message: str):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), message)
        with self.lock:
            self.logs.append(line)
            self.log_seq += 1

    # -- 监听地址 -----------------------------------------------------------
    def listen_urls(self):
        port = self.http_port
        if not port:
            try:
                port = int(self.cfg.get("port") or DEFAULT_PORT)
            except (TypeError, ValueError):
                port = DEFAULT_PORT
        host = (self.cfg.get("bind") or "0.0.0.0").strip()
        if host in ("", "0.0.0.0", "::"):
            urls = ["http://127.0.0.1:%d/" % port]
            for ip in lan_ips():
                urls.append("http://%s:%d/" % (ip, port))
            return urls
        return ["http://%s:%d/" % (host, port)]

    # -- HTTP 服务 ----------------------------------------------------------
    def start_http(self, port=None) -> bool:
        if self.httpd is not None:
            return True
        bind = (self.cfg.get("bind") or "0.0.0.0").strip() or "0.0.0.0"
        if not self.cfg.get("bind"):
            self.cfg["bind"] = bind
        try:
            desired = int(port if port is not None else (self.cfg.get("port") or DEFAULT_PORT))
        except (TypeError, ValueError):
            desired = DEFAULT_PORT
        try:
            httpd = ThreadingHTTPServer((bind, desired), ClientRequestHandler)
        except OSError as exc:
            self.http_error = "监听端口 %d 启动失败：%s" % (desired, exc)
            self.log(self.http_error)
            return False
        httpd.daemon_threads = True
        httpd.engine = self
        self.httpd = httpd
        self.http_port = httpd.server_address[1]
        self.http_error = ""
        self.cfg["port"] = self.http_port
        threading.Thread(target=self._serve, args=(httpd,), daemon=True).start()
        self.log("更新服务已启动：%s" % "   ".join(self.listen_urls()))
        return True

    def _serve(self, httpd):
        try:
            httpd.serve_forever(poll_interval=0.3)
        except Exception as exc:
            self.log("更新服务异常退出：%s" % exc)

    def stop_http(self):
        httpd, self.httpd = self.httpd, None
        if httpd is None:
            return
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        self.log("更新服务已停止")

    # -- 状态 ---------------------------------------------------------------
    def _connected(self, now=None) -> bool:
        now = time.time() if now is None else now
        return bool(self.server) and (now - float(self.server.get("last_seen", 0) or 0) < CONNECT_TTL)

    def status(self, now=None):
        connected = self._connected(now)
        if self.pending:
            return "pending", "有新版本", connected
        if not connected:
            return "disconnected", "未连接", False
        if self.scanning:
            return "scanning", "资源检测中…", True
        if self.scan_done:
            if self.applied:
                return "updated", "已是最新", True
            return "ready", "资源检测完成", True
        return "connecting", "资源检测中…", True

    def snapshot(self) -> dict:
        with self.lock:
            key, text, connected = self.status()
            pending = [self._public_item(item) for item in self.pending.values()]
            return {
                "app": CLIENT_APP,
                "version": CLIENT_VERSION,
                "status_key": key,
                "status_text": text,
                "connected": connected,
                "client_root": self.cfg.get("client_root") or "",
                "udp_root": self.cfg.get("udp_root") or "",
                "admin_url": self.cfg.get("admin_url") or "",
                "listen": self.listen_urls(),
                "http_error": self.http_error,
                "resource": dict(self.resource),
                "pending": pending,
                "pending_count": len(pending),
                "applied": self.applied,
                "applied_at": self.applied_at,
                "server": dict(self.server),
                "last_apply": dict(self.last_apply) if self.last_apply else None,
                "logs": list(self.logs)[-40:],
                "log_seq": self.log_seq,
            }

    @staticmethod
    def _public_item(item: dict) -> dict:
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "kind": item.get("kind"),
            "kind_label": item.get("kind_label"),
            "scope": item.get("scope"),
            "relpath": item.get("relpath"),
            "size": item.get("size"),
            "batch": item.get("batch"),
            "batch_time": item.get("batch_time"),
            "server": item.get("server"),
            "received": item.get("received"),
        }

    # -- 资源检测 -----------------------------------------------------------
    def start_scan(self):
        root, udp = self.roots()
        with self.lock:
            if self.scanning:
                return
            if not root:
                self.log("未配置客户端源码目录，跳过资源检测")
                return
            self.scanning = True
            self.scan_done = False
        self.log("开始资源检测：%s" % root)
        self._scan_thread = threading.Thread(
            target=self._scan_worker, args=(root, udp), daemon=True)
        self._scan_thread.start()

    def _scan_worker(self, root: str, udp: str):
        started = time.time()
        files = 0
        total_bytes = 0
        udp_files = 0
        broken = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for filename in filenames:
                    if not filename.endswith(".py"):
                        continue
                    path = os.path.join(dirpath, filename)
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        continue
                    files += 1
                    total_bytes += size
                    if files <= 500:
                        try:
                            with open(path, "rb") as fh:
                                compile(fh.read(), path, "exec")
                        except SyntaxError as exc:
                            broken.append("%s 第 %s 行：%s" % (
                                os.path.relpath(path, root), exc.lineno, exc.msg))
                        except (OSError, ValueError):
                            pass
            if udp and os.path.isdir(udp):
                for _dirpath, dirnames, filenames in os.walk(udp):
                    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                    udp_files += sum(1 for f in filenames
                                     if os.path.splitext(f)[1].lower() in (".js", ".mjs", ".json"))
        except Exception as exc:
            self.log("资源检测异常：%s" % exc)
        elapsed = time.time() - started
        with self.lock:
            self.resource = {
                "files": files,
                "bytes": total_bytes,
                "udp_files": udp_files,
                "broken": broken[:15],
                "broken_count": len(broken),
                "seconds": elapsed,
                "scanned_at": now_text(),
            }
            self.scanning = False
            self.scan_done = True
        self.log("资源检测完成：%d 个 Python 文件 · %s · 语法异常 %d 个（%.2fs）"
                 % (files, human_size(total_bytes), len(broken), elapsed))

    # -- 握手 ---------------------------------------------------------------
    def note_handshake(self, payload: dict):
        now = time.time()
        with self.lock:
            prev_connected = self._connected(now)
            self.server = {
                "name": str(payload.get("name") or (self.server or {}).get("name") or ""),
                "url": str(payload.get("url") or (self.server or {}).get("url") or ""),
                "version": str(payload.get("version") or (self.server or {}).get("version") or ""),
                "last_seen": now,
            }
        if not prev_connected:
            self.log("服务端已连接：%s（%s）" % (
                self.server.get("name") or "?", self.server.get("url") or "?"))
            if not self.scanning:
                self.start_scan()

    # -- 接收推送 -----------------------------------------------------------
    def accept_push(self, payload: dict) -> int:
        if payload.get("protocol") != PROTOCOL:
            raise ClientError("协议不匹配：服务端 %s，本机 %s"
                              % (payload.get("protocol"), PROTOCOL))
        batch = payload.get("batch") or {}
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ClientError("推送内容为空")
        now = time.time()
        accepted = 0
        with self.lock:
            for raw in items:
                item = self._validate_item(raw, batch, now)
                self.pending[item["id"]] = item
                accepted += 1
            while len(self.pending) > MAX_PENDING:
                self.pending.popitem(last=False)
            server_info = batch.get("server") or {}
            old = self.server or {}
            self.server = {
                "name": str(server_info.get("name") or old.get("name") or ""),
                "url": str(server_info.get("url") or old.get("url") or ""),
                "version": str(server_info.get("build") or old.get("version") or ""),
                "last_seen": now,
            }
        self.log("收到服务端推送：%d 个接口（批次 %s）"
                 % (accepted, batch.get("id") or "?"))
        return accepted

    def _validate_item(self, raw, batch: dict, now: float) -> dict:
        if not isinstance(raw, dict):
            raise ClientError("推送项格式不正确")
        for key in ("id", "name", "relpath", "scope", "content"):
            if key not in raw:
                raise ClientError("推送项缺少字段：%s" % key)
        scope = str(raw.get("scope"))
        if scope not in ("server", "udp"):
            raise ClientError("未知的源码范围：%s" % scope)
        relpath = str(raw.get("relpath") or "").replace("\\", "/").strip()
        if not relpath or ".." in relpath.split("/") or ":" in relpath:
            raise ClientError("非法的源码路径：%r" % relpath)
        content = raw.get("content")
        if not isinstance(content, str):
            raise ClientError("推送内容不是文本")
        encoding = str(raw.get("encoding") or "utf-8")
        try:
            content.encode(encoding)
        except (LookupError, UnicodeEncodeError):
            encoding = "utf-8"
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            size = len(content.encode(encoding, "replace"))
        extras = []
        for raw_extra in raw.get("extra_files") or []:
            if not isinstance(raw_extra, dict):
                continue
            erel = str(raw_extra.get("relpath") or "").replace(chr(92), "/").strip()
            if not erel or ".." in erel.split("/") or ":" in erel:
                continue
            econtent = raw_extra.get("content")
            if not isinstance(econtent, str):
                continue
            eenc = str(raw_extra.get("encoding") or "utf-8")
            try:
                econtent.encode(eenc)
            except (LookupError, UnicodeEncodeError):
                eenc = "utf-8"
            extras.append({"relpath": erel, "encoding": eenc,
                           "content": econtent,
                           "size": len(econtent.encode(eenc, "replace"))})
        return {
            "id": str(raw["id"]),
            "name": str(raw.get("name") or relpath),
            "kind": str(raw.get("kind") or scope),
            "kind_label": str(raw.get("kind_label") or ""),
            "scope": scope,
            "relpath": relpath,
            "size": size,
            "sha256": str(raw.get("sha256") or ""),
            "encoding": encoding,
            "content": content,
            "batch": str(batch.get("id") or ""),
            "batch_time": str(batch.get("created") or ""),
            "server": str((batch.get("server") or {}).get("url") or ""),
            "extra_files": extras,
            "received": now,
        }

    # -- 应用更新 -----------------------------------------------------------
    def apply_async(self, ids, callback):
        def work():
            try:
                result = self.apply_pending(ids)
            except Exception as exc:  # 保证界面一定收到结果
                result = {"ok": False, "results": [], "applied": 0, "failed": 0,
                          "message": "更新异常：%s" % exc}
            callback(result)

        threading.Thread(target=work, daemon=True).start()

    def apply_pending(self, ids=None) -> dict:
        with self.lock:
            items = [dict(item) for item in self.pending.values()
                     if ids is None or item["id"] in ids]
        if not items:
            return {"ok": True, "results": [], "applied": 0, "failed": 0,
                    "message": "没有待更新项"}

        root, udp = self.roots()
        roots = {"server": root, "udp": udp}
        sha_by_id = {item["id"]: item.get("sha256") for item in items}
        results = []
        written_paths = []

        for item in items:
            res = {"id": item["id"], "name": item["name"], "ok": False,
                   "size": item["size"], "error": "", "path": ""}
            try:
                scope_root = roots.get(item["scope"]) or ""
                if not scope_root or not os.path.isdir(scope_root):
                    raise ClientError(
                        "未配置%s目录，无法替换 %s"
                        % (" UDP 源码" if item["scope"] == "udp" else "客户端源码",
                           item["relpath"]))
                target = safe_join(scope_root, item["relpath"])
                ext = os.path.splitext(target)[1].lower()
                if ext not in WRITE_EXTS:
                    raise ClientError("不允许替换的文件类型：%s" % (ext or "无扩展名"))
                if ext == ".py":
                    try:
                        compile(item["content"], target, "exec")
                    except SyntaxError as exc:
                        raise ClientError("源码语法错误（第 %s 行）：%s"
                                          % (exc.lineno, exc.msg))
                try:
                    backup = self._backup_file(target)
                except OSError as exc:
                    backup = ""
                    self.log("备份 %s 失败（%s），继续替换源码" % (item["relpath"], exc))
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                tmp = target + ".skytmp-" + uuid.uuid4().hex[:8]
                with open(tmp, "w", encoding=item["encoding"], newline="") as fh:
                    fh.write(item["content"])
                os.replace(tmp, target)
                res["ok"] = True
                res["path"] = target
                res["backup"] = backup
                written_paths.append(target)

                extra_files = item.get("extra_files") or []
                extra_ok = 0
                for extra in extra_files:
                    extra_rel = str(extra.get("relpath") or "").strip()
                    if not extra_rel:
                        continue
                    try:
                        extra_target = safe_join(scope_root, extra_rel)
                        extra_ext = os.path.splitext(extra_target)[1].lower()
                        if extra_ext not in WRITE_EXTS:
                            continue
                        extra_text = extra.get("content") or ""
                        if extra_ext == ".py":
                            compile(extra_text, extra_target, "exec")
                        extra_parent = os.path.dirname(extra_target)
                        if extra_parent:
                            os.makedirs(extra_parent, exist_ok=True)
                        try:
                            self._backup_file(extra_target)
                        except OSError:
                            pass
                        extra_tmp = extra_target + ".skytmp-" + uuid.uuid4().hex[:8]
                        with open(extra_tmp, "w", encoding=extra.get("encoding") or "utf-8", newline="") as fh:
                            fh.write(extra_text)
                        os.replace(extra_tmp, extra_target)
                        written_paths.append(extra_target)
                        extra_ok += 1
                    except Exception as exc:
                        self.log("附带文件 %s 写入失败：%s" % (extra_rel, exc))
                if extra_ok:
                    res["extra"] = extra_ok
            except Exception as exc:
                res["error"] = str(exc)
            results.append(res)

        applied = sum(1 for r in results if r["ok"])
        failed = len(results) - applied
        reload_message = ""
        if applied:
            reload_message = self._hot_reload(written_paths)
            with self.lock:
                for res in results:
                    if not res["ok"]:
                        continue
                    current = self.pending.get(res["id"])
                    if current and current.get("sha256") == sha_by_id.get(res["id"]):
                        self.pending.pop(res["id"], None)
                if applied and not self.pending:
                    self.applied = items[0].get("batch") or now_text()
                    self.applied_at = now_text()
                    self.cfg["applied"] = self.applied
                    self.cfg["applied_at"] = self.applied_at
        summary = "成功 %d 项，失败 %d 项。" % (applied, failed)
        if reload_message:
            summary += " 热更新：" + reload_message
        self.last_apply = {
            "time": now_text(),
            "ok": applied,
            "failed": failed,
            "message": summary,
        }
        self.log("应用更新：%s" % summary)
        if applied:
            self.save_config()
        return {"ok": failed == 0, "results": results, "applied": applied,
                "failed": failed, "message": summary}

    @staticmethod
    def _backup_file(target: str) -> str:
        if not os.path.isfile(target):
            return ""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = "%s.skybak-%s" % (target, stamp)
        index = 1
        while os.path.exists(backup):
            backup = "%s.skybak-%s-%d" % (target, stamp, index)
            index += 1
        shutil.copy2(target, backup)
        return backup

    # -- 热更新 -------------------------------------------------------------
    def _hot_reload(self, paths) -> str:
        messages = []
        admin_url = (self.cfg.get("admin_url") or "").strip()
        if admin_url:
            endpoint = admin_url.rstrip("/") + "/admin/api/hot_reload"
            try:
                req = urllib.request.Request(
                    endpoint,
                    headers={"User-Agent": "SkyAdminClient/%s" % CLIENT_VERSION},
                    method="GET",
                )
                context = unverified_ssl_context() if endpoint.lower().startswith("https://") else None
                with urllib.request.urlopen(req, timeout=20, context=context) as resp:
                    body = resp.read().decode("utf-8", "replace")
                data = json.loads(body or "{}")
                msg = str(data.get("msg") or "已请求热更新")
                extra = ""
                if data.get("reloaded_modules") is not None:
                    extra = "（reloaded=%s, endpoints=%s）" % (
                        data.get("reloaded_modules"), data.get("reregistered_endpoints"))
                errors = data.get("errors") or []
                if errors:
                    extra += " 警告：%s" % "；".join(str(e) for e in errors[:3])
                messages.append("%s%s" % (msg, extra))
            except Exception as exc:
                messages.append("后台热更新接口不可达（%s），源码已替换，重启后生效" % exc)
        else:
            messages.append("未配置后台热更新地址，源码已替换，重启后生效")

        reloaded = self._reload_local_modules(paths)
        if reloaded:
            messages.append("本进程重载 %d 个模块" % reloaded)
        return "；".join(messages)

    def _reload_local_modules(self, paths) -> int:
        wanted = set()
        for path in paths:
            try:
                wanted.add(os.path.normcase(os.path.abspath(path)))
            except OSError:
                continue
        if not wanted:
            return 0
        modules = []
        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if not filename:
                continue
            try:
                if os.path.normcase(os.path.abspath(filename)) in wanted:
                    modules.append(module)
            except OSError:
                continue
        reloaded = 0
        for module in modules:
            try:
                importlib.reload(module)
                reloaded += 1
            except Exception as exc:
                self.log("本地重载 %s 失败：%s" % (getattr(module, "__name__", "?"), exc))
        return reloaded


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------
class ClientRequestHandler(BaseHTTPRequestHandler):
    server_version = "SkyAdminClient/%s" % CLIENT_VERSION
    sys_version = ""
    protocol_version = "HTTP/1.0"

    @property
    def engine(self) -> UpdateEngine:
        return self.server.engine

    def log_message(self, fmt, *args):  # 静默，避免刷屏
        pass

    # -- 输出 ---------------------------------------------------------------
    def _send(self, status: int, payload: bytes, content_type: str):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, status: int, data: dict):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, payload, "application/json; charset=utf-8")

    # -- 请求体 -------------------------------------------------------------
    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            raise ClientError("请求体为空")
        if length > MAX_BODY_BYTES:
            raise ClientError("请求体过大（%s）" % human_size(length))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ClientError("请求体不是有效 JSON") from exc
        if not isinstance(data, dict):
            raise ClientError("请求体必须是 JSON 对象")
        return data

    def _check_token(self) -> bool:
        token = (self.engine.cfg.get("token") or "").strip()
        if not token:
            return True
        given = self.headers.get("X-Sky-Token") or ""
        if hmac.compare_digest(given, token):
            return True
        query_token = urllib.parse.parse_qs(
            urllib.parse.urlsplit(self.path).query).get("token", [""])[0]
        return hmac.compare_digest(query_token, token)

    # -- GET ---------------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                html = HOMEPAGE_HTML.replace("{{VERSION}}", CLIENT_VERSION)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/status":
                self._send_json(200, self.engine.snapshot())
            elif path == "/api/ping":
                snapshot = self.engine.snapshot()
                self._send_json(200, {
                    "ok": True, "app": CLIENT_APP, "version": CLIENT_VERSION,
                    "status": snapshot["status_text"],
                    "pending": snapshot["pending_count"],
                })
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._send_json(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    # -- POST --------------------------------------------------------------
    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/handshake":
                body = self._read_json()
                self.engine.note_handshake(body)
                snapshot = self.engine.snapshot()
                self._send_json(200, {
                    "ok": True,
                    "status": snapshot["status_text"],
                    "status_key": snapshot["status_key"],
                    "pending": snapshot["pending_count"],
                })
                return
            if path == "/api/push":
                if not self._check_token():
                    self._send_json(401, {"ok": False, "error": "访问令牌不正确"})
                    return
                body = self._read_json()
                accepted = self.engine.accept_push(body)
                snapshot = self.engine.snapshot()
                self._send_json(200, {
                    "ok": True,
                    "accepted": accepted,
                    "pending": snapshot["pending_count"],
                    "status": snapshot["status_text"],
                    "status_key": snapshot["status_key"],
                })
                return
            if path == "/api/ping":
                self._send_json(200, {"ok": True, "app": CLIENT_APP,
                                      "version": CLIENT_VERSION})
                return
            self._send_json(404, {"ok": False, "error": "not found"})
        except ClientError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# HTML 主页（服务端连通性测试会直接 GET 这个页面）
# ---------------------------------------------------------------------------
HOMEPAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="application-name" content="sky-admin-client">
<title>Sky 更新客户端</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0f172a; color: #e2e8f0;
         font-family: "Microsoft YaHei", "Segoe UI", system-ui, sans-serif; }
  header { display: flex; align-items: center; justify-content: space-between;
           padding: 20px 28px; border-bottom: 1px solid #1e293b; background: #111c33; }
  header h1 { margin: 0; font-size: 19px; letter-spacing: .5px; }
  header .sub { color: #64748b; font-size: 12px; margin-top: 4px; }
  .badge { padding: 6px 16px; border-radius: 999px; font-weight: 700;
           font-size: 13px; color: #0b1220; background: #94a3b8; }
  .badge.disconnected { background: #94a3b8; }
  .badge.scanning, .badge.connecting { background: #f59e0b; }
  .badge.ready { background: #22c55e; }
  .badge.pending { background: #ef4444; color: #fff; }
  .badge.updated { background: #3b82f6; color: #fff; }
  main { max-width: 1080px; margin: 0 auto; padding: 24px 20px 48px; }
  .card { background: #111c33; border: 1px solid #1e293b; border-radius: 14px;
          padding: 18px 20px; margin-bottom: 16px; }
  .card h2 { margin: 0 0 12px; font-size: 14px; color: #93c5fd;
             font-weight: 600; letter-spacing: .4px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
          gap: 16px; margin-bottom: 16px; }
  .grid .card { margin-bottom: 0; }
  dl { display: grid; grid-template-columns: 92px 1fr; gap: 6px 12px; margin: 0; }
  dt { color: #64748b; font-size: 12px; }
  dd { margin: 0; font-size: 13px; word-break: break-all; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e293b; }
  th { color: #64748b; font-weight: 500; font-size: 12px; }
  td.name { font-family: Consolas, "Cascadia Mono", monospace; color: #e2e8f0; }
  .hint { color: #f59e0b; font-size: 12px; margin: -4px 0 12px; }
  pre { margin: 0; font-size: 12px; color: #94a3b8; white-space: pre-wrap;
        font-family: Consolas, "Cascadia Mono", monospace; line-height: 1.55; }
  footer { text-align: center; color: #475569; font-size: 12px; padding: 10px 0 30px; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px;
          background: #1e293b; color: #93c5fd; font-size: 11px; margin-right: 6px; }
</style>
</head>
<body id="sky-client" data-app="sky-admin-client">
<header>
  <div>
    <h1>Sky 更新客户端</h1>
    <div class="sub">接口源码推送接收端 · 协议 sky-push/1 · <span id="updated-at">--:--:--</span> 刷新</div>
  </div>
  <div class="badge disconnected" id="status">未连接</div>
</header>
<main>
  <section class="card">
    <h2>运行状态</h2>
    <dl>
      <dt>客户端版本</dt><dd>v<span id="client-version">-</span></dd>
      <dt>监听地址</dt><dd id="listen">-</dd>
      <dt>待更新</dt><dd><span id="pending-count">0</span> 个接口（需在客户端窗口确认）</dd>
    </dl>
  </section>

  <div class="grid">
    <section class="card">
      <h2>源码目录</h2>
      <dl>
        <dt>Sky 源码</dt><dd id="client-root">-</dd>
        <dt>UDP 源码</dt><dd id="udp-root">-</dd>
      </dl>
    </section>
    <section class="card">
      <h2>资源检测</h2>
      <p id="resource" style="margin:0;font-size:13px">尚未检测</p>
    </section>
    <section class="card">
      <h2>服务端</h2>
      <p id="server" style="margin:0;font-size:13px">未连接</p>
    </section>
    <section class="card">
      <h2>最近更新</h2>
      <p id="applied" style="margin:0;font-size:13px">尚未应用更新</p>
    </section>
  </div>

  <section class="card" id="pending-card" style="display:none">
    <h2>待更新接口</h2>
    <p class="hint">请在客户端窗口右上角点击状态胶囊，确认后才会替换源码并热更新。</p>
    <table>
      <thead><tr><th>接口名</th><th>类型</th><th>大小</th><th>批次</th></tr></thead>
      <tbody id="pending-body"></tbody>
    </table>
  </section>

  <section class="card">
    <h2>运行日志</h2>
    <pre id="log">—</pre>
  </section>
</main>
<footer>sky-admin-client v{{VERSION}} · 仅用于本地/局域网更新分发</footer>
<script>
const SORTS = {account: 0, service: 1, starwatch: 2, admin: 3, udp: 4};
function fmtSize(n) {
  if (n === null || n === undefined) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(n), i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return (i === 0 ? value + " B" : value.toFixed(1) + " " + units[i]);
}
function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = (value === null || value === undefined || value === "") ? "—" : String(value);
}
async function refresh() {
  const badge = document.getElementById("status");
  try {
    const resp = await fetch("/api/status", {cache: "no-store"});
    const s = await resp.json();
    badge.textContent = s.status_text;
    badge.className = "badge " + (s.status_key || "disconnected");
    setText("client-version", s.version);
    setText("listen", (s.listen || [])[0]);
    setText("client-root", s.client_root);
    setText("udp-root", s.udp_root);
    const r = s.resource || {};
    setText("resource", r.scanned_at
      ? r.files + " 个 Python 文件 · " + fmtSize(r.bytes) +
        (r.udp_files ? " · UDP " + r.udp_files + " 个文件" : "") +
        (r.broken_count ? " · 语法异常 " + r.broken_count + " 个" : " · 语法检查通过")
      : "尚未检测");
    if (s.connected && s.server) {
      setText("server", (s.server.name || s.server.url || "服务端") +
              (s.server.version ? " · " + s.server.version : "") + " · 最近 " +
              new Date().toLocaleTimeString());
    } else {
      setText("server", "未连接");
    }
    setText("applied", s.applied ? s.applied + (s.applied_at ? " · " + s.applied_at : "") : "尚未应用更新");
    setText("pending-count", (s.pending || []).length);
    const tbody = document.getElementById("pending-body");
    tbody.innerHTML = "";
    const items = (s.pending || []).slice().sort((a, b) =>
      ((SORTS[a.kind] || 9) - (SORTS[b.kind] || 9)) || String(a.name).localeCompare(String(b.name)));
    for (const item of items) {
      const tr = document.createElement("tr");
      const tdName = document.createElement("td");
      tdName.className = "name";
      tdName.textContent = item.name;
      const tdKind = document.createElement("td");
      tdKind.textContent = item.kind_label || item.kind;
      const tdSize = document.createElement("td");
      tdSize.textContent = fmtSize(item.size);
      const tdBatch = document.createElement("td");
      tdBatch.textContent = item.batch || "—";
      tr.append(tdName, tdKind, tdSize, tdBatch);
      tbody.appendChild(tr);
    }
    document.getElementById("pending-card").style.display = items.length ? "" : "none";
    const logs = s.logs || [];
    setText("log", logs.slice(-14).reverse().join("\n"));
    document.getElementById("updated-at").textContent = new Date().toLocaleTimeString();
  } catch (err) {
    badge.textContent = "客户端进程无响应";
    badge.className = "badge disconnected";
  }
}
refresh();
setInterval(refresh, 2500);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 更新确认弹窗
# ---------------------------------------------------------------------------
class ClientUpdateDialog(tk.Toplevel):
    def __init__(self, app, engine: UpdateEngine):
        super().__init__(app)
        self.app = app
        self.engine = engine
        self.title("有新版本 · 确认更新")
        self.geometry("820x600")
        self.minsize(720, 500)
        self.transient(app)
        self.grab_set()
        self.configure(bg="#f4f6f8")

        self._queue = queue.Queue()
        self._applying = False
        self._ids = []
        self._row_map = {}

        self._build()
        self._load_items()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._poll_queue)

    def _build(self):
        head = tk.Frame(self, bg="#f4f6f8")
        head.pack(fill="x", padx=16, pady=(14, 6))
        self.title_label = tk.Label(
            head, text="服务端推送了新版本", bg="#f4f6f8", fg="#0d47a1",
            font=("微软雅黑", 14, "bold"), anchor="w",
        )
        self.title_label.pack(anchor="w")
        self.sub_label = tk.Label(
            head, text="", bg="#f4f6f8", fg="#78909c",
            font=("微软雅黑", 9), anchor="w", justify="left",
        )
        self.sub_label.pack(anchor="w", pady=(4, 0))

        table_wrap = tk.Frame(self, bg="#f4f6f8")
        table_wrap.pack(fill="both", expand=True, padx=16, pady=(6, 4))
        columns = ("name", "kind", "size", "status")
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="headings",
                                 selectmode="none")
        headings = {"name": ("接口名", 380), "kind": ("类型", 110),
                    "size": ("大小", 100), "status": ("状态", 180)}
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w" if key != "size" else "e")
        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("ok", foreground="#2e7d32")
        self.tree.tag_configure("fail", foreground="#c62828")

        info = tk.Frame(self, bg="#f4f6f8")
        info.pack(fill="x", padx=16)
        self.total_label = tk.Label(info, text="", bg="#f4f6f8", fg="#455a64",
                                    font=("微软雅黑", 9, "bold"), anchor="w")
        self.total_label.pack(anchor="w")
        self.result_label = tk.Label(
            info, text="确定后将替换客户端源码中对应接口的数据，并立即热更新。",
            bg="#f4f6f8", fg="#546e7a", font=("微软雅黑", 9),
            anchor="w", justify="left", wraplength=770,
        )
        self.result_label.pack(anchor="w", pady=(4, 0))

        footer = tk.Frame(self, bg="#f4f6f8")
        footer.pack(fill="x", padx=16, pady=(8, 14))
        self.btn_ok = tk.Button(
            footer, text="确定更新", width=14, cursor="hand2",
            bg="#1976d2", fg="white", activebackground="#1565c0",
            activeforeground="white", relief="flat",
            font=("微软雅黑", 9, "bold"), command=self._on_apply,
        )
        self.btn_ok.pack(side="right", padx=(8, 0))
        self.btn_cancel = tk.Button(
            footer, text="取消", width=12, cursor="hand2", command=self._on_close)
        self.btn_cancel.pack(side="right")

    def _load_items(self):
        snapshot = self.engine.snapshot()
        items = sorted(
            snapshot["pending"],
            key=lambda it: (KIND_ORDER.get(it.get("kind"), 9),
                            str(it.get("name") or "").lower()),
        )
        self._ids = [it["id"] for it in items]
        total = 0
        batch = ""
        created = ""
        for index, item in enumerate(items):
            total += int(item.get("size") or 0)
            batch = batch or (item.get("batch") or "")
            created = created or (item.get("batch_time") or "")
            iid = "row-%d" % index
            self._row_map[item["id"]] = iid
            self.tree.insert(
                "", "end", iid=iid,
                values=(item.get("name") or "?", item.get("kind_label") or item.get("kind") or "?",
                        human_size(item.get("size")), "等待确认"),
            )
        self.title_label.configure(text="服务端推送了 %d 个接口更新" % len(items))
        self.sub_label.configure(
            text="批次：%s    推送时间：%s    来源：%s"
                 % (batch or "?", created or "?", (items[0].get("server") if items else "") or "服务端")
        )
        self.total_label.configure(text="共 %d 项 · 合计 %s" % (len(items), human_size(total)))
        if not items:
            self.btn_ok.configure(state="disabled")
            self.result_label.configure(text="没有待更新项。", fg="#78909c")

    def _on_apply(self):
        if self._applying:
            return
        if not self._ids:
            return
        self._applying = True
        self.btn_ok.configure(state="disabled", text="更新中…")
        self.btn_cancel.configure(state="disabled")
        self.result_label.configure(
            text="正在替换源码并请求后台热更新，请稍候…", fg="#1565c0")
        self.engine.apply_async(list(self._ids), self._queue.put)

    def _poll_queue(self):
        try:
            result = self._queue.get_nowait()
        except queue.Empty:
            self.after(150, self._poll_queue)
            return
        self._on_apply_done(result)

    def _on_apply_done(self, result):
        self._applying = False
        for res in result.get("results") or []:
            iid = self._row_map.get(res.get("id"))
            if not iid:
                continue
            if res.get("ok"):
                text = "✓ 已替换"
                tag = "ok"
            else:
                text = "✗ 失败：%s" % (res.get("error") or "未知错误")
                tag = "fail"
            try:
                self.tree.set(iid, "status", text)
                self.tree.item(iid, tags=(tag,))
            except tk.TclError:
                pass
        message = result.get("message") or ""
        ok = bool(result.get("ok"))
        self.result_label.configure(
            text=("✓ 更新完成。" if ok else "部分接口更新失败。") + message,
            fg="#2e7d32" if ok else "#c62828",
        )
        self.btn_ok.configure(text="完成", state="normal", command=self._on_close)
        self.btn_cancel.configure(text="关闭", state="normal", command=self._on_close)

        snapshot = self.engine.snapshot()
        if snapshot["pending_count"]:
            self.title_label.configure(
                text="还有 %d 个接口待更新" % snapshot["pending_count"])

    def _on_close(self):
        if self._applying:
            if not messagebox.askokcancel(
                    "正在更新", "源码正在替换中，确定关闭窗口吗？（替换会继续完成）",
                    parent=self):
                return
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# ---------------------------------------------------------------------------
# 主面板：右上角版本状态胶囊 + 「更新接收」页
# ---------------------------------------------------------------------------
class AdminPanelClient(AdminPanel):
    # 客户端面板不显示「推送更新」操作条（服务端 admin_panel.py 才显示）
    enable_push_bar = False

    def __init__(self, root_dir):
        self.client_cfg = load_client_config()
        super().__init__(root_dir)
        self.title("Sky 私服管理面板 · 更新客户端 - 端口 %d" % self.server_port)
        self.engine = UpdateEngine(self.client_cfg, CLIENT_CONFIG, default_root=self.root_dir)
        self._log_seq_seen = -1
        self._pulse = False
        self._sync_job = None
        self._update_dialog = None
        self._tunnel_proc = None
        self._tunnel_url = ""

        self._build_client_tab()
        self._build_status_chip()
        self.engine.start_http()
        self._refresh_lan_hint()
        self.after(900, lambda: self._start_tunnel(quiet=True))
        self.after(300, self._sync_state)
        self.protocol("WM_DELETE_WINDOW", self._on_close_client)

    # ------------------------------------------------------------ 状态胶囊
    def _build_status_chip(self):
        try:
            bar = self.btn_start.master
            self.state_dot.pack_forget()
            self.state_label.pack_forget()
        except Exception:
            return

        self.chip = tk.Frame(bar, bg="#eceff1", highlightthickness=1,
                             highlightbackground="#cfd8dc", cursor="hand2")
        self.chip.pack(side="right", padx=(4, 8), pady=5)

        self.chip_dot = tk.Label(self.chip, text="●", bg="#eceff1", fg="#9e9e9e",
                                 font=("微软雅黑", 9))
        self.chip_dot.pack(side="left", padx=(8, 2))

        inner = tk.Frame(self.chip, bg="#eceff1", cursor="hand2")
        inner.pack(side="left", padx=(0, 10), pady=2)
        self.chip_text = tk.Label(inner, text="未连接", bg="#eceff1", fg="#455a64",
                                  font=("微软雅黑", 9, "bold"), cursor="hand2")
        self.chip_text.pack(anchor="w")
        self.chip_sub = tk.Label(inner, text="v%s" % CLIENT_VERSION, bg="#eceff1",
                                 fg="#90a4ae", font=("微软雅黑", 7), cursor="hand2")
        self.chip_sub.pack(anchor="w")

        for widget in (self.chip, inner, self.chip_dot, self.chip_text, self.chip_sub):
            widget.bind("<Button-1>", lambda _e: self._on_chip_click())

        self.state_dot.pack(side="right", padx=(4, 6))
        self.state_label.pack(side="right")

    # ------------------------------------------------------------ 页签
    def _build_client_tab(self):
        self.tab_client = tk.Frame(self.nb)
        self.nb.add(self.tab_client, text="  更新接收  ")
        self.nb.select(self.tab_client)

        outer = tk.Frame(self.tab_client)
        outer.pack(fill="both", expand=True, padx=10, pady=8)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = tk.LabelFrame(outer, text=" 客户端设置 ", padx=10, pady=8)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))

        row = 0
        tk.Label(left, text="监听地址", font=("微软雅黑", 9)).grid(
            row=row, column=0, sticky="w", pady=3)
        bind_row = tk.Frame(left)
        bind_row.grid(row=row, column=1, columnspan=2, sticky="w", pady=3)
        self.var_bind = tk.StringVar(value=str(self.engine.cfg.get("bind") or "0.0.0.0"))
        tk.Entry(bind_row, textvariable=self.var_bind, width=15,
                 font=("Consolas", 9)).pack(side="left")
        tk.Label(bind_row, text=":", font=("Consolas", 9)).pack(side="left")
        self.var_port = tk.StringVar(value=str(self.engine.cfg.get("port") or DEFAULT_PORT))
        tk.Entry(bind_row, textvariable=self.var_port, width=7,
                 font=("Consolas", 9)).pack(side="left")

        self.var_root = tk.StringVar(value=str(self.engine.cfg.get("client_root") or ""))
        self.var_udp = tk.StringVar(value=str(self.engine.cfg.get("udp_root") or ""))
        self.var_admin = tk.StringVar(value=str(self.engine.cfg.get("admin_url") or ""))
        self.var_token = tk.StringVar(value=str(self.engine.cfg.get("token") or ""))

        self._add_path_row(left, 1, "Sky 源码目录", self.var_root)
        self._add_path_row(left, 2, "UDP 源码目录", self.var_udp)

        tk.Label(left, text="后台热更新", font=("微软雅黑", 9)).grid(
            row=3, column=0, sticky="w", pady=3)
        tk.Entry(left, textvariable=self.var_admin, width=40,
                 font=("Consolas", 9)).grid(row=3, column=1, columnspan=2, sticky="w", pady=3)
        tk.Label(left, text="访问令牌", font=("微软雅黑", 9)).grid(
            row=4, column=0, sticky="w", pady=3)
        tk.Entry(left, textvariable=self.var_token, width=40,
                 font=("Consolas", 9)).grid(row=4, column=1, columnspan=2, sticky="w", pady=3)

        buttons = tk.Frame(left)
        buttons.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 2))
        self.btn_listen = tk.Button(buttons, text="启动监听", width=10,
                                    command=self._start_listening)
        self.btn_listen.pack(side="left", padx=(0, 4))
        self.btn_unlisten = tk.Button(buttons, text="停止监听", width=10,
                                      command=self._stop_listening)
        self.btn_unlisten.pack(side="left", padx=4)
        tk.Button(buttons, text="保存配置", width=10,
                  command=self._save_config_from_ui).pack(side="left", padx=4)

        buttons2 = tk.Frame(left)
        buttons2.grid(row=6, column=0, columnspan=3, sticky="w", pady=2)
        tk.Button(buttons2, text="重新检测资源", width=12,
                  command=self._rescan).pack(side="left", padx=(0, 4))
        tk.Button(buttons2, text="打开 HTML 主页", width=14,
                  command=self._open_homepage).pack(side="left", padx=4)

        self.lan_hint_var = tk.StringVar(value="正在探测本机监听地址…")
        tk.Label(
            left, textvariable=self.lan_hint_var, font=("Consolas", 9, "bold"),
            fg="#1565c0", anchor="w", justify="left",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

        tunnel_row = tk.Frame(left)
        tunnel_row.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._tunnel_var = tk.StringVar(value="公网地址：启动后自动生成…")
        tk.Label(tunnel_row, textvariable=self._tunnel_var, font=("Consolas", 8),
                 fg="#00695c", anchor="w", justify="left", wraplength=430).pack(side="left")
        tk.Button(tunnel_row, text="复制", width=5,
                  command=self._copy_tunnel).pack(side="left", padx=(4, 0))
        tk.Button(tunnel_row, text="重新生成", width=8,
                  command=lambda: (self._stop_tunnel(), self._start_tunnel())).pack(side="left", padx=4)

        tk.Label(
            left,
            text="把本文件和 admin_panel.py 放到需要更新的机器上运行；\n"
                 "服务端地址优先填上面的「公网地址」，同一局域网也可填局域网地址。\n"
                 "后台热更新地址一般是 https://127.0.0.1:8443。",
            font=("微软雅黑", 8), fg="#78909c", justify="left",
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(10, 2))

        right = tk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        self.hero = tk.Frame(right, bg="#eceff1", highlightthickness=1,
                             highlightbackground="#cfd8dc")
        self.hero.grid(row=0, column=0, sticky="ew", padx=2, pady=(0, 8))
        self.var_status = tk.StringVar(value="未连接")
        self.var_status_sub = tk.StringVar(value="等待服务端连接…")
        self.hero_label = tk.Label(self.hero, textvariable=self.var_status,
                                   bg="#eceff1", fg="#455a64",
                                   font=("微软雅黑", 15, "bold"), anchor="w")
        self.hero_label.pack(anchor="w", padx=16, pady=(10, 0))
        self.hero_sub = tk.Label(self.hero, textvariable=self.var_status_sub,
                                 bg="#eceff1", fg="#78909c",
                                 font=("微软雅黑", 9), anchor="w")
        self.hero_sub.pack(anchor="w", padx=16, pady=(2, 10))

        info = tk.LabelFrame(right, text=" 状态详情 ", padx=10, pady=6)
        info.grid(row=1, column=0, sticky="ew", padx=2, pady=(0, 8))
        info.columnconfigure(1, weight=1)
        self.var_listen = tk.StringVar(value="—")
        self.var_resource = tk.StringVar(value="尚未检测")
        self.var_server = tk.StringVar(value="未连接")
        self.var_applied = tk.StringVar(value="尚未应用更新")
        for index, (label, var) in enumerate((
                ("监听地址", self.var_listen),
                ("资源检测", self.var_resource),
                ("服务端", self.var_server),
                ("最近更新", self.var_applied))):
            tk.Label(info, text=label, font=("微软雅黑", 9), fg="#607d8b").grid(
                row=index, column=0, sticky="nw", pady=2)
            tk.Label(info, textvariable=var, font=("微软雅黑", 9), anchor="w",
                     justify="left", wraplength=470).grid(
                row=index, column=1, sticky="w", pady=2)

        actions = tk.Frame(right)
        actions.grid(row=2, column=0, sticky="ew", padx=2, pady=(0, 6))
        self.btn_pending = tk.Button(
            actions, text="查看待更新 (0)", width=16, cursor="hand2",
            bg="#e3f2fd", activebackground="#bbdefb", relief="ridge",
            font=("微软雅黑", 9, "bold"), command=self._open_update_dialog,
        )
        self.btn_pending.pack(side="left")
        tk.Button(actions, text="刷新状态", width=10,
                  command=self._refresh_now).pack(side="left", padx=8)

        log_wrap = tk.LabelFrame(right, text=" 运行日志 ")
        log_wrap.grid(row=3, column=0, sticky="nsew", padx=2)
        self.client_log = tk.Text(log_wrap, height=10, state="disabled",
                                  font=("Consolas", 9), bg="#fbfcfd", relief="flat",
                                  wrap="word")
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical",
                                   command=self.client_log.yview)
        self.client_log.configure(yscrollcommand=log_scroll.set)
        self.client_log.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        log_scroll.pack(side="right", fill="y", pady=4)

    def _add_path_row(self, parent, row, label, var):
        tk.Label(parent, text=label, font=("微软雅黑", 9)).grid(
            row=row, column=0, sticky="w", pady=3)
        tk.Entry(parent, textvariable=var, width=40,
                 font=("Consolas", 9)).grid(row=row, column=1, sticky="w", pady=3)
        tk.Button(parent, text="浏览", width=6,
                  command=lambda v=var: self._browse_into(v)).grid(
            row=row, column=2, sticky="w", padx=(4, 0), pady=3)

    # ------------------------------------------------------------ 配置动作
    def _browse_into(self, var):
        directory = filedialog.askdirectory(parent=self)
        if directory:
            var.set(directory)

    def _read_form(self):
        cfg = self.engine.cfg
        cfg["bind"] = self.var_bind.get().strip() or "0.0.0.0"
        try:
            port = int(self.var_port.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
            cfg["port"] = port
        except ValueError:
            messagebox.showerror("端口错误", "监听端口必须是 1-65535 的整数。", parent=self)
            return False
        cfg["client_root"] = self.var_root.get().strip()
        cfg["udp_root"] = self.var_udp.get().strip()
        cfg["admin_url"] = self.var_admin.get().strip()
        cfg["token"] = self.var_token.get().strip()
        return True

    def _save_config_from_ui(self):
        if not self._read_form():
            return
        self.engine.save_config()
        self.engine.log("配置已保存")
        self._refresh_lan_hint()

    def _refresh_lan_hint(self):
        """显示服务端应该填写的地址：优先第一个非回环 IPv4。"""
        if not hasattr(self, "lan_hint_var"):
            return
        try:
            urls = self.engine.listen_urls()
        except Exception:
            urls = []
        lan = [u for u in urls if "127.0.0.1" not in u and "0.0.0.0" not in u]
        chosen = (lan or urls or ["http://<本机IP>:9527/"])[0]
        target = chosen.rstrip("/")
        state = "监听中" if self.engine.httpd is not None else "未监听"
        if self.engine.http_error:
            state = "监听失败"
        self.lan_hint_var.set("服务端填这个地址（%s）→ %s" % (state, target))

    def _start_listening(self):
        if not self._read_form():
            return
        if self.engine.httpd is not None:
            self.engine.log("更新服务已在监听中")
            return
        self.engine.start_http()
        self._refresh_lan_hint()
        self._start_tunnel(quiet=True)

    def _stop_listening(self):
        self.engine.stop_http()
        self._stop_tunnel()
        self._refresh_lan_hint()

    def _rescan(self):
        if not self._read_form():
            return
        self.engine.scan_done = False
        self.engine.start_scan()

    def _open_homepage(self):
        urls = self.engine.listen_urls()
        if self.engine.httpd is None:
            messagebox.showinfo("未启动", "更新服务尚未启动，请先点『启动监听』。", parent=self)
            return
        webbrowser.open(urls[0])

    # ------------------------------------------------------------ 状态同步
    def _refresh_now(self):
        self._sync_state(force=True)

    def _sync_state(self, force=False):
        snapshot = self.engine.snapshot()
        key = snapshot["status_key"]
        style = STATUS_STYLES.get(key, STATUS_STYLES["disconnected"])

        self.chip.configure(bg=style["bg"], highlightbackground=style["border"])
        self.chip_text.configure(bg=style["bg"], fg=style["fg"], text=snapshot["status_text"])
        self.chip_dot.configure(bg=style["bg"], fg=style["dot"])
        self.chip_sub.configure(bg=style["bg"])
        if key == "pending":
            self._pulse = not self._pulse
            try:
                self.chip_dot.configure(fg="#b71c1c" if self._pulse else "#ef9a9a")
            except tk.TclError:
                pass
        applied = snapshot.get("applied") or ""
        if snapshot["connected"]:
            sub = "v%s · 待更新 %d" % (CLIENT_VERSION, snapshot["pending_count"])
            if not snapshot["pending_count"] and applied:
                shown = applied if len(applied) <= 18 else applied[:17] + "…"
                sub = "v%s · 已更新 %s" % (CLIENT_VERSION, shown)
        else:
            sub = "v%s · 等待服务端" % CLIENT_VERSION
        self.chip_sub.configure(text=sub)

        self.hero.configure(bg=style["bg"], highlightbackground=style["border"])
        self.hero_label.configure(bg=style["bg"], fg=style["fg"],
                                  text=snapshot["status_text"])
        self.hero_sub.configure(bg=style["bg"], text=self._status_subtitle(snapshot))

        urls = snapshot["listen"] or []
        listen_text = urls[0] if urls else "未启动"
        if snapshot.get("http_error"):
            listen_text = "启动失败：%s" % snapshot["http_error"]
        self.var_listen.set(listen_text)
        self.var_resource.set(self._resource_text(snapshot["resource"]))
        self.var_server.set(self._server_text(snapshot))
        self.var_applied.set(
            (applied + (" · " + snapshot["applied_at"] if snapshot.get("applied_at") else ""))
            if applied else "尚未应用更新"
        )
        count = snapshot["pending_count"]
        self.btn_pending.configure(
            text="查看待更新 (%d)" % count,
            state="normal" if count else "disabled",
        )
        self.btn_listen.configure(state="disabled" if self.engine.httpd else "normal")
        self.btn_unlisten.configure(state="normal" if self.engine.httpd else "disabled")

        if force or snapshot.get("log_seq") != self._log_seq_seen:
            self._log_seq_seen = snapshot.get("log_seq")
            self.client_log.configure(state="normal")
            self.client_log.delete("1.0", "end")
            self.client_log.insert("1.0", "\n".join(snapshot.get("logs") or []))
            self.client_log.see("end")
            self.client_log.configure(state="disabled")

        self._schedule_sync()

    def _schedule_sync(self):
        """保证同一时刻只存在一个状态轮询 after，避免手动刷新叠出多条链。"""
        if self._sync_job is None:
            try:
                self._sync_job = self.after(600, self._sync_tick)
            except tk.TclError:
                self._sync_job = None

    def _sync_tick(self):
        self._sync_job = None
        self._sync_state()

    @staticmethod
    def _status_subtitle(snapshot) -> str:
        if snapshot["status_key"] == "pending":
            return "服务端推送了新版本，点击右上角胶囊或『查看待更新』确认。"
        if snapshot["status_key"] == "disconnected":
            return "等待服务端在『推送更新』窗口里连接本机…"
        if snapshot["status_key"] == "scanning":
            return "正在检测客户端源码资源，请稍候…"
        if snapshot["status_key"] == "updated":
            return "源码已更新，可继续接收下一批推送。"
        return "客户端已就绪，等待服务端推送接口源码。"

    @staticmethod
    def _resource_text(resource) -> str:
        if not resource or not resource.get("scanned_at"):
            return "尚未检测"
        text = "%d 个 Python 文件 · %s" % (
            resource.get("files", 0), human_size(resource.get("bytes", 0)))
        if resource.get("udp_files"):
            text += " · UDP %d 个文件" % resource["udp_files"]
        if resource.get("broken_count"):
            text += " · 语法异常 %d 个" % resource["broken_count"]
        else:
            text += " · 语法检查通过"
        text += "（%.1fs）" % resource.get("seconds", 0)
        return text

    @staticmethod
    def _server_text(snapshot) -> str:
        server = snapshot.get("server") or {}
        if not snapshot.get("connected"):
            return "未连接"
        name = server.get("name") or server.get("url") or "服务端"
        age = max(0, int(time.time() - float(server.get("last_seen") or time.time())))
        text = "%s · %d 秒前心跳" % (name, age)
        if server.get("version"):
            text += " · %s" % server["version"]
        return text

    # ------------------------------------------------------------ 交互
    def _on_chip_click(self):
        snapshot = self.engine.snapshot()
        if snapshot["pending_count"]:
            self._open_update_dialog()
            return
        lines = [
            "状态：%s" % snapshot["status_text"],
            "监听：%s" % (", ".join(snapshot["listen"]) or "未启动"),
            "源码目录：%s" % (snapshot["client_root"] or "未配置"),
            "资源检测：%s" % self._resource_text(snapshot["resource"]),
            "服务端：%s" % self._server_text(snapshot),
            "最近更新：%s" % (snapshot["applied"] or "尚未应用更新"),
        ]
        messagebox.showinfo("更新客户端状态", "\n".join(lines), parent=self)

    def _open_update_dialog(self):
        snapshot = self.engine.snapshot()
        if not snapshot["pending_count"]:
            messagebox.showinfo("暂无更新", "当前没有待更新的接口。", parent=self)
            return
        if self._update_dialog is not None:
            try:
                if self._update_dialog.winfo_exists():
                    self._update_dialog.deiconify()
                    self._update_dialog.lift()
                    self._update_dialog.focus_force()
                    return
            except tk.TclError:
                pass
        self._update_dialog = ClientUpdateDialog(self, self.engine)

    # ------------------------------------------------------------ 公网地址
    def _tunnel_port(self):
        try:
            return int(self.var_port.get().strip() or DEFAULT_PORT)
        except Exception:
            return DEFAULT_PORT

    def _start_tunnel(self, quiet=False):
        proc = getattr(self, "_tunnel_proc", None)
        if proc is not None and proc.poll() is None:
            return
        if not os.path.isfile(CLOUDFLARED_PATH):
            self._tunnel_var.set("公网地址：未找到 cloudflared（可继续用上面的局域网地址）")
            return
        if getattr(self.engine, "httpd", None) is None:
            self.engine.start_http()
        if getattr(self.engine, "httpd", None) is None:
            self._tunnel_var.set("公网地址：本机 9527 没能启动，先看下方状态里的监听错误")
            return
        port = self._tunnel_port()
        cmd = [CLOUDFLARED_PATH, "tunnel", "--no-autoupdate",
               "--url", "http://127.0.0.1:%d" % port,
               "--edge-ip-version", "4", "--protocol", "http2"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace",
                                    creationflags=flags)
        except Exception as exc:
            self._tunnel_var.set("公网地址：隧道启动失败 %s" % exc)
            self.engine.log("cloudflared 启动失败: %s" % exc)
            return
        self._tunnel_proc = proc
        self._tunnel_url = ""
        self._tunnel_var.set("公网地址：正在生成…（约 10 秒）")
        threading.Thread(target=self._read_tunnel, args=(proc,), daemon=True).start()
        self.engine.log("正在申请 Cloudflare 临时隧道…")

    def _read_tunnel(self, proc):
        pattern = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")
        try:
            for line in proc.stdout:
                match = pattern.search(line or "")
                if match:
                    url = match.group(0)
                    self._tunnel_url = url
                    self.after(0, lambda u=url: self._tunnel_ready(u))
                    self.engine.log("公网隧道已就绪: %s" % url)
                    break
        except Exception:
            pass

    def _tunnel_ready(self, url):
        self._tunnel_url = url
        self._tunnel_var.set("公网地址：%s（正在验证…）" % url)
        threading.Thread(target=self._verify_tunnel, args=(url,),
                         daemon=True).start()

    def _verify_tunnel(self, url):
        ok = False
        body = ""
        for _ in range(6):
            try:
                with urllib.request.urlopen(url + "/api/ping", timeout=8,
                                            context=self._ssl_ctx()) as resp:
                    body = resp.read().decode("utf-8", "replace")
                    ok = "sky-admin-client" in body and '"app"' in body
            except Exception as exc:
                body = str(exc)
                ok = False
            if ok:
                break
            time.sleep(4)
        self.after(0, lambda: self._tunnel_verified(url, ok, body))

    @staticmethod
    def _ssl_ctx():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _tunnel_verified(self, url, ok, body):
        if not ok:
            self._tunnel_var.set("公网地址：%s（隧道已建，但 530/不可达）" % url)
            try:
                messagebox.showwarning(
                    "隧道暂时不可用",
                    "Cloudflare 隧道已经建好，但它连不到本机的 9527。\n\n"
                    "常见原因：9527 被别的程序占用、监听失败，或云隧道刚建立还差几秒。\n"
                    "先看「更新接收」页的状态是否显示“监听中”，等 10 秒可点“重新生成”。\n\n"
                    "返回：%s" % (body or "")[:300],
                    parent=self)
            except Exception:
                pass
            return
        self._tunnel_var.set("✅ 公网地址（已复制）：%s" % url)
        try:
            self.clipboard_clear()
            self.clipboard_append(url)
        except Exception:
            pass
        try:
            self.bell()
            messagebox.showinfo(
                "公网地址已就绪",
                "已复制到剪贴板：\n\n%s\n\n"
                "把这一串填到服务端「推送更新」窗口的地址栏（不要加 :9527），\n"
                "不要填游戏面板日志里那个隧道地址。" % url,
                parent=self)
        except Exception:
            pass

    def _copy_tunnel(self):
        url = getattr(self, "_tunnel_url", "")
        if not url:
            messagebox.showinfo("还没有地址", "隧道还在生成，等几秒再点。", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self._tunnel_var.set("公网地址：已复制 %s" % url)

    def _stop_tunnel(self):
        proc = getattr(self, "_tunnel_proc", None)
        self._tunnel_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _on_close_client(self):
        if self._sync_job is not None:
            try:
                self.after_cancel(self._sync_job)
            except tk.TclError:
                pass
            self._sync_job = None
        try:
            self.engine.save_config()
            self.engine.stop_http()
        except Exception:
            pass
        try:
            self._on_close()
        except Exception:
            self.destroy()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    cfg = load_client_config()
    panel_cfg = load_panel_config()
    default_dir = (
        cfg.get("client_root")
        or panel_cfg.get("source_dir")
        or QUICK_PATH
    )

    root_dir = ""
    if default_dir and os.path.isfile(os.path.join(default_dir, "index.py")):
        root_dir = default_dir
    if not root_dir:
        bootstrap = tk.Tk()
        bootstrap.withdraw()
        dialog = DirSelectDialog(bootstrap, default=default_dir)
        bootstrap.wait_window(dialog)
        root_dir = dialog.result
        bootstrap.destroy()
        if not root_dir:
            return

    cfg["client_root"] = cfg.get("client_root") or root_dir
    panel_cfg["source_dir"] = root_dir
    save_panel_config(panel_cfg)

    app = AdminPanelClient(root_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
