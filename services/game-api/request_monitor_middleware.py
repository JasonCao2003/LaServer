# -*- coding: utf-8 -*-
"""
Sky 私服 - 请求捕获中间件
在 Flask 中拦截所有 HTTP 请求和响应，通过共享队列发送给监控面板。
"""

import time
import json
import os
import re
import threading
import queue
import collections
import ipaddress
from datetime import datetime
from flask import request, g
try:
    import helpers.response_override as _rovr
    _rovr.ensure_table()
    _rovr.ensure_log_table()
except Exception:
    _rovr = None
try:
    import helpers.file_override as _fovr
except Exception:
    _fovr = None

# 总队列放在 request_monitor_bus 里, **不要**在这个模块里建!
# 这个模块在 hot_reload 的重载名单里, 每次热更新都会被 importlib.reload —— 模块字典里
# 的名字会被换成新对象, 而监控面板还捧着服务器启动时那个旧队列, 于是热更新之后
# "接口请求响应查看"窗口再也收不到记录(看起来就是"有时候不监控")。详见 bus 模块头注释。
try:
    from request_monitor_bus import request_log_queue
except Exception:  # noqa: BLE001 - 兜底: 万一 bus 缺失, 至少还能跑
    request_log_queue = queue.Queue(maxsize=500)

# 接口路径 -> "禁用该功能"标志文件名 (管理面板勾选"禁用该功能"时创建)。
# 命中且标志存在时, 该接口的响应覆盖会被跳过(禁用优先于覆盖)。
_DISABLE_FLAG_MAP = {
    "/account/season_finalize": "season_finalize_disabled",
    "/account/season_start": "season_start_disabled",
}


def _config_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")


def _path_function_disabled(path):
    """该接口是否被管理面板'禁用该功能'(对应 config 下标志文件存在)。"""
    flag = _DISABLE_FLAG_MAP.get(path)
    if not flag:
        return False
    try:
        return os.path.exists(os.path.join(_config_dir(), flag))
    except Exception:
        return False


# 经 Cloudflare 隧道转发的请求会带这些头; 本机主控直连不带。
_TUNNEL_HEADERS = ("Cf-Connecting-Ip", "Cf-Ray", "Cf-Worker", "Cf-Ipcountry",
                   "Cf-Visitor", "Cdn-Loop", "X-Forwarded-For", "X-Forwarded-Proto")

# 仅主控本机可访问的路径(管理/写全局数据)。前缀匹配 + 精确匹配两种。
# 这些一旦从公网隧道访问一律 403, 杜绝外部改公告/货币/封号/删号/加好友/推解锁等。
_ADMIN_ONLY_PREFIXES = ("/admin/", "/admin")
_ADMIN_ONLY_EXACT = frozenset({
    "/direct_add_friend",        # 直连加好友管理页面
})


def _is_admin_only_path(path):
    if path in _ADMIN_ONLY_EXACT:
        return True
    return path.startswith("/admin/") or path == "/admin"


def _is_via_tunnel():
    """请求是否来自公网隧道(带 Cloudflare/转发头)。本机主控直连返回 False。"""
    try:
        from flask import request as _req
        for h in _TUNNEL_HEADERS:
            if _req.headers.get(h):
                return True
    except Exception:
        pass
    return False


_request_history = []
_history_lock = threading.Lock()
# 2026-10-23 内存治理: 内存里只留最近 100 条, 其余全部在捕获时同步落盘
# (monitor_exports/cache/<接口>.jsonc), 被挤出内存的记录仍然查得到。
MAX_HISTORY = 100
# 单条请求/响应体在内存里的上限; 请求头/响应头本身很小, 不另设上限。
MAX_BODY_BYTES = 65536

# ---------------- 按接口持续转储请求/响应 (一个接口一个文件) ----------------
# 参考 That_Sky_Requests 的 jsonc 格式: 每条记录含请求头/请求体 + 响应头/响应体,
# 追加写入到 monitor_exports/cache/<接口路径>.jsonc, 持续累积。
#   - 默认(未点击保存): 不落盘, 接口记录只保留在内存(队列 + 历史), 退出即随进程消失;
#     若 cache 目录残留旧文件, 进程退出时一并删除("退出即删除")。
#   - 点击"保存接口缓存"后: 写入 _KEEP_MARKER 标记, 此后每条记录持续落盘,
_EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "monitor_exports")
_DUMP_DIR = os.path.join(_EXPORT_DIR, "cache")
# 标记文件放在 monitor_exports 根下(cache 目录之外), 这样删除 cache 目录不会误删标记。
_KEEP_MARKER = os.path.join(_EXPORT_DIR, ".keep_cache")
_dump_lock = threading.Lock()


def _dump_enabled():
    """是否开启磁盘缓存(用户已点击'保存接口缓存')。"""
    try:
        return os.path.exists(_KEEP_MARKER)
    except Exception:
        return False


def _safe_name(path):
    """把请求路径转成安全文件名, 如 /account/get_currency -> account__get_currency"""
    name = path.strip("/").replace("/", "__")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "root"
    return name[:120]


def _fmt_body(body):
    """请求/响应体: bytes->文本, 是 JSON 则美化, 否则原样(截断)。"""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if not body:
        return ""
    s = body.strip()
    if s[:1] in "{[":
        try:
            return json.dumps(json.loads(s), ensure_ascii=False, indent=4)
        except Exception:
            pass
    return body[:65536]


def _dump_record(record, force=False):
    """把一条记录以 jsonc 风格追加到对应接口文件。
    仅当已开启磁盘缓存(_dump_enabled)或 force=True 时才落盘; 否则只留内存。"""
    if not force and not _dump_enabled():
        return
    try:
        with _dump_lock:
            os.makedirs(_DUMP_DIR, exist_ok=True)
            fp = os.path.join(_DUMP_DIR, _safe_name(record["path"]) + ".jsonc")
            ts = datetime.fromtimestamp(record["timestamp"]).strftime(
                "%Y-%m-%d %H:%M:%S")
            lines = []
            lines.append("/*")
            lines.append(f"{record['method']} {record['path']}  "
                         f"[{record['status_code']}]  {ts}  "
                         f"{record['elapsed_ms']}ms  from {record['remote_addr']}")
            lines.append("*/")
            lines.append("//Request")
            lines.append(_fmt_body(record.get("body_req")) or "(empty)")
            lines.append("")
            lines.append("//Response")
            lines.append(_fmt_body(record.get("body_resp")) or "(empty)")
            lines.append("\n" + "=" * 60 + "\n")
            with open(fp, "a", encoding="utf-8") as f:
                f.write("\n".join(lines))
    except Exception:
        pass


def flush_history_to_disk():
    """把内存中已捕获的历史记录一次性落盘。
    用于用户点击'保存接口缓存'的瞬间: 之前只在内存里的记录也一并写出,
    之后的新记录由 _dump_record 持续追加。"""
    try:
        for rec in get_request_history():
            _dump_record(rec, force=True)
    except Exception:
        pass


def cleanup_cache_dir():
    """删除磁盘缓存目录(cache/)。仅在未开启保存时退出调用 -> '退出即删除'。"""
    try:
        import shutil
        if os.path.isdir(_DUMP_DIR):
            shutil.rmtree(_DUMP_DIR, ignore_errors=True)
    except Exception:
        pass


def _atexit_cache_cleanup():
    """进程退出时: 若用户未点击'保存接口缓存', 删除磁盘缓存; 否则保留。"""
    if not _dump_enabled():
        cleanup_cache_dir()


try:
    import atexit
    atexit.register(_atexit_cache_cleanup)
except Exception:
    pass


def get_request_history():
    """获取历史请求记录的副本"""
    with _history_lock:
        return list(_request_history)


# ============================================================
# IP 限频封禁 (任务4)
# 规则: 同一 IP 在 60 秒滑动窗口内, 对(同一或多个)接口的请求总数 > 150,
#       立即封禁该 IP 名下的所有账号(is_banned=1, 永久), 并在后续直接拦截该 IP。
# ============================================================
RATE_LIMIT_WINDOW = 60        # 滑动窗口(秒)
RATE_LIMIT_MAX = 150          # 窗口内允许的最大请求数(超过即封)
# 不参与限频的本地/内网回环地址(避免面板自身或本机调试被误封)
_RATE_WHITELIST = {"127.0.0.1", "::1", "localhost", ""}

# ip -> deque[请求时间戳]
_ip_hits = collections.defaultdict(collections.deque)
# 已封禁的 IP 集合(命中后直接拦截, 不再查库)
_banned_ips = set()
_rate_lock = threading.Lock()

# 数据库路径: 与 index.py 的 sqlite:///users.db 一致(优先 instance/users.db)
def _users_db_path():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = [os.path.join(here, "data", "instance", "users.db"),
            os.path.join(here, "data", "legacy", "users.db")]
    for p in cand:
        if os.path.exists(p):
            return p
    return cand[0]


def _ban_all_accounts_of_ip(ip):
    """封禁某 IP 名下的全部账号(永久封)。返回受影响账号数。"""
    import sqlite3
    path = _users_db_path()
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            now = int(time.time())
            cur = conn.execute(
                """UPDATE user SET is_banned=1, ban_duration=0,
                   ban_start_time=?, ban_end_time=0 WHERE ip_address=?""",
                (now, ip))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
    except Exception as e:
        try:
            print(f"[限频封禁] 写库失败 ip={ip}: {e}")
        except Exception:
            pass
        return 0


def is_ip_banned(ip):
    """该 IP 是否已被限频封禁(用于请求拦截)。"""
    if not ip:
        return False
    with _rate_lock:
        return ip in _banned_ips


def _check_rate_limit(ip):
    """登记一次请求并判断是否触发限频封禁。触发则封该 IP 全部账号。
    返回 True 表示本次请求应被拦截(该 IP 已封)。"""
    if not ip or ip in _RATE_WHITELIST:
        return False

    now = time.time()
    trigger = False
    with _rate_lock:
        if ip in _banned_ips:
            return True
        dq = _ip_hits[ip]
        dq.append(now)
        cutoff = now - RATE_LIMIT_WINDOW
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) > RATE_LIMIT_MAX:
            _banned_ips.add(ip)
            dq.clear()
            trigger = True

    if trigger:
        affected = _ban_all_accounts_of_ip(ip)
        try:
            print(f"[限频封禁] IP {ip} 在 {RATE_LIMIT_WINDOW}s 内请求超过 "
                  f"{RATE_LIMIT_MAX} 次, 已封禁其名下 {affected} 个账号并拦截该 IP。")
        except Exception:
            pass
        return True
    return False


# ============================================================
# 手动 IP 封禁 (主控面板"IP封禁"标签页)
# 列表存于 config/banned_ips.json (面板添加/删除), 请求到达时检测真实客户端 IP,
# 命中则直接返回 405(不给任何数据)。
# ============================================================
_banned_ips_cache = {"mtime": None, "ips": None}
_banned_cache_lock = threading.Lock()


def _banned_ips_path():
    return os.path.join(_config_dir(), "banned_ips.json")


def _normalize_ban_entry(s):
    """把一条封禁条目规范化为可匹配对象:
    含 '/' 按 CIDR 网段解析, 否则按 IP 地址(IPv4/IPv6)解析。
    成功返回 (kind, obj), kind ∈ {'net','addr'}; 失败返回 None。
    IPv6 大小写/前导零/:: 压缩由 ipaddress 统一规范化, 避免不同写法匹配不上。
    """
    s = (s or "").strip()
    if not s:
        return None
    try:
        if "/" in s:
            return ("net", ipaddress.ip_network(s, strict=False))
        return ("addr", ipaddress.ip_address(s))
    except ValueError:
        return None


def _load_manual_banned_ips():
    """读取手动封禁 IP 列表并按 ipaddress 规范化。
    返回 {"addrs": set[IPv4/IPv6Address], "nets": list[IPvNetwork], "raw": set[str]}
    - addrs: 精确 IP(IPv4/IPv6 均可)
    - nets : CIDR 网段(如 2409:8a28::/32, 192.168.0.0/24)
    - raw  : 既不是合法 IP 也不是网段的原字符串(兼容旧数据/特殊条目)
    按文件 mtime 缓存, 主控修改后即时生效。
    """
    path = _banned_ips_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"addrs": set(), "nets": [], "raw": set()}
    with _banned_cache_lock:
        if _banned_ips_cache["mtime"] == mtime and _banned_ips_cache["ips"] is not None:
            return _banned_ips_cache["ips"]
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                entries = [str(x).strip() for x in data if str(x).strip()]
            elif isinstance(data, dict):
                entries = [str(x).strip() for x in data.get("banned_ips", [])
                           if str(x).strip()]
            else:
                entries = []
        except Exception:
            entries = []
        addrs = set()
        nets = []
        raw = set()
        for e in entries:
            r = _normalize_ban_entry(e)
            if r is None:
                raw.add(e)
            elif r[0] == "addr":
                addrs.add(r[1])
            else:
                nets.append(r[1])
        result = {"addrs": addrs, "nets": nets, "raw": raw}
        _banned_ips_cache["mtime"] = mtime
        _banned_ips_cache["ips"] = result
        return result


def _real_client_ip():
    """获取真实客户端 IP: 优先隧道转发头(Cf-Connecting-Ip / X-Forwarded-For),
    否则退回 remote_addr。"""
    try:
        for h in ("Cf-Connecting-Ip", "X-Forwarded-For"):
            v = request.headers.get(h)
            if v:
                return v.split(",")[0].strip()
    except Exception:
        pass
    try:
        return request.remote_addr or ""
    except Exception:
        return ""


def is_manually_banned(ip):
    """该 IP 是否在手动封禁列表(config/banned_ips.json)。
    支持 IPv4/IPv6 精确地址与 CIDR 网段; IPv6 自动规范化比较。"""
    if not ip:
        return False
    banned = _load_manual_banned_ips()
    # 非 IP 字符串(旧数据/特殊条目)按原串精确比较
    if ip in banned["raw"]:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # 客户端 IP 本身解析失败(极端情况), 退化到原串精确比较
        return ip in banned["raw"]
    if addr in banned["addrs"]:
        return True
    for net in banned["nets"]:
        if addr in net:
            return True
    return False


# ============================================================
# 内存兜底 (2026-10-23): 目标峰值 <= 1GB
# ============================================================
_MEM_SOFT_LIMIT_MB = 900     # 超过就回收(留约 100MB 余量, 硬顶约 1GB)
_MEM_HARD_LIMIT_MB = 950     # 超过再清掉可重建的响应缓存
_MEM_CHECK_SECONDS = 30
_mem_guard_started = False


def _rss_mb():
    """当前进程工作集(MB); 非 Windows 或调用失败返回 0。"""
    try:
        import ctypes
        import ctypes.wintypes as _wt

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", _wt.DWORD), ("PageFaultCount", _wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        _k32 = ctypes.WinDLL("kernel32")
        _fn = _k32.K32GetProcessMemoryInfo
        _fn.argtypes = [_wt.HANDLE, ctypes.POINTER(_PMC), _wt.DWORD]
        _fn.restype = _wt.BOOL
        _pmc = _PMC()
        _pmc.cb = ctypes.sizeof(_PMC)
        if _fn(_k32.GetCurrentProcess(), ctypes.byref(_pmc), _pmc.cb):
            return _pmc.WorkingSetSize / 1048576.0
    except Exception:
        pass
    return 0.0


def trim_memory(reason=""):
    """把监控占用的内存压到最低: 历史只留最近 MAX_HISTORY 条、丢掉已落盘的队列积压、
    回收垃圾; RSS 到硬顶时再清掉可重建的静态响应缓存。返回当前 RSS(MB)。"""
    before = _rss_mb()
    with _history_lock:
        if len(_request_history) > MAX_HISTORY:
            del _request_history[:len(_request_history) - MAX_HISTORY]
    dropped = 0
    if _dump_enabled():   # 确认已落盘才允许丢队列, 避免丢数据
        try:
            while True:
                request_log_queue.get_nowait()
                dropped += 1
        except queue.Empty:
            pass
        except Exception:
            pass
    if before >= _MEM_HARD_LIMIT_MB:
        try:
            import resources.static_responses as _sr
            _sr._BODY_CACHE.clear()   # 按需从磁盘重读, 不影响功能
        except Exception:
            pass
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    after = _rss_mb()
    try:
        if reason:
            print("[内存保护] %s: RSS %.0fMB -> %.0fMB, 内存历史 %d 条, 丢弃队列 %d 条"
                  % (reason, before, after, len(_request_history), dropped))
    except Exception:
        pass
    return after


def _start_memory_guard():
    """每 30 秒量一次 RSS; 超过软顶就回收。daemon 线程, 随进程结束。"""
    global _mem_guard_started
    if _mem_guard_started:
        return
    _mem_guard_started = True

    def _loop():
        last_log = 0.0
        while True:
            time.sleep(_MEM_CHECK_SECONDS)
            try:
                rss = _rss_mb()
                if rss >= _MEM_SOFT_LIMIT_MB:
                    trim_memory("RSS %.0fMB 超过软顶 %dMB" % (rss, _MEM_SOFT_LIMIT_MB))
                elif time.time() - last_log >= 1800:
                    # 每 30 分钟留一行 RSS, 方便在面板日志里确认内存曲线。
                    last_log = time.time()
                    print("[内存保护] 当前 RSS %.0fMB" % rss)
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True, name="mem-guard").start()


def init_request_monitor(app):
    """注册 before_request / after_request 钩子"""

    # 2026-10-23: 内存只留最近 100 条 —— 默认开启磁盘归档(不再依赖面板按钮),
    # 这样被挤出内存的记录在 monitor_exports/cache/<接口>.jsonc 里仍然查得到。
    try:
        os.makedirs(_EXPORT_DIR, exist_ok=True)
        if not os.path.exists(_KEEP_MARKER):
            with open(_KEEP_MARKER, "w", encoding="utf-8") as _f:
                _f.write(str(time.time()))
    except Exception:
        pass
    _start_memory_guard()


    @app.before_request
    def _capture_before():
        g._monitor_start_time = time.time()
        g._monitor_request_body = request.get_data(as_text=False)
        # ===== 管理接口防护: 仅主控本机可访问, 公网隧道一律拒绝 =====
        # 公告(/admin/api/motd)及一切管理/写全局接口此前无任何鉴权, 经 Cloudflare 隧道
        # 暴露在公网 -> 任何拿到隧道地址的人都能改公告/货币/封号/删号/加好友/推解锁。
        # 经隧道来的请求一定带 Cloudflare 头; 本机主控直连不带。据此拦截: 隧道来的
        # 管理路径请求直接 403。游戏接口(玩家正常游玩/读公告)不受影响。
        try:
            if _is_admin_only_path(request.path) and _is_via_tunnel():
                from flask import jsonify
                resp = jsonify({"error": "forbidden",
                                "message": "管理接口仅限主控本机访问"})
                resp.status_code = 403
                return resp
        except Exception:
            pass
        # 在线状态: 只要请求进来就更新该账号最后活跃时间, 不管接口是否存在(404)、
        # 不管返回成功失败 —— 请求到达 = 在线。放在 before_request 保证 404 也算。
        try:
            if _rovr is not None:
                _uid = _rovr.extract_user_id_from_request(g._monitor_request_body)
                if _uid:
                    _rovr.touch_user(_uid)
        except Exception:
            pass
        # IP 限频封禁(任务4): 超过阈值的 IP 直接拦截, 阻止持续刷接口
        try:
            ip = request.remote_addr or ''
            if _check_rate_limit(ip):
                from flask import jsonify
                resp = jsonify({"error": "rate_limited",
                                "message": "请求过于频繁, 该IP已被封禁"})
                resp.status_code = 429
                return resp
        except Exception:
            pass
        # 手动 IP 封禁(主控"IP封禁"标签页): 在列表内的真实客户端 IP 直接 405, 不给数据
        try:
            if is_manually_banned(_real_client_ip()):
                from flask import jsonify
                resp = jsonify({"error": "forbidden",
                                "message": "该IP已被封禁"})
                resp.status_code = 405
                return resp
        except Exception:
            pass

    @app.after_request
    def _capture_after(response):
        # 优先级: 文件覆盖(本地文件, 每次实时读, 防串改) > 数据库覆盖(按用户/全局)。
        # 若该接口被"禁用该功能"(disable 标志), 则两种覆盖都跳过。
        try:
            if not _path_function_disabled(request.path):
                applied = False
                # 1) 文件覆盖: 主控直接写本地文件, 这里每次请求重新读盘, 立即生效不缓存。
                if _fovr is not None:
                    fov = _fovr.get_file_override(request.path)
                    if fov is not None:
                        response.set_data(fov.encode('utf-8') if isinstance(fov, str) else fov)
                        try:
                            response.headers['Content-Length'] = str(len(response.get_data()))
                        except Exception:
                            pass
                        applied = True
                # 2) 数据库覆盖(兼容旧的按用户覆盖)
                if not applied and _rovr is not None:
                    req_body_for_uid = getattr(g, '_monitor_request_body', b'')
                    uid = _rovr.extract_user_id_from_request(req_body_for_uid)
                    ov = _rovr.get_override(uid, request.path)
                    if ov is not None:
                        # get_unlocks 覆盖体是历史静态快照, 缺少新赛季 add_unlock 写入的
                        # 开场标记(如 AP28Intro_01) -> 会导致季节开场动画循环重播。
                        # 这里把该用户 status_unlock 表实时记录并入快照(DB优先), 只播一次。
                        if request.path.endswith('/service/status/api/v1/get_unlocks') and uid:
                            try:
                                ov = _rovr.merge_status_unlocks_override(uid, ov)
                            except Exception:
                                pass
                        response.set_data(ov.encode('utf-8') if isinstance(ov, str) else ov)
                        try:
                            response.headers['Content-Length'] = str(len(response.get_data()))
                        except Exception:
                            pass
        except Exception:
            pass

        try:
            elapsed = time.time() - getattr(g, '_monitor_start_time', time.time())
            req_body = getattr(g, '_monitor_request_body', b'')

            # 响应体（截取前 64KB 避免内存爆炸）
            resp_body = b''
            try:
                resp_body = response.get_data(as_text=False)[:MAX_BODY_BYTES]
            except Exception:
                pass

            record = {
                'timestamp': time.time(),
                'method': request.method,
                'path': request.path,
                'full_url': request.url,
                'query_string': request.query_string.decode('utf-8', errors='replace'),
                'headers_req': dict(request.headers),
                'body_req': req_body[:MAX_BODY_BYTES],
                'status_code': response.status_code,
                'headers_resp': dict(response.headers),
                'body_resp': resp_body,
                'elapsed_ms': round(elapsed * 1000, 2),
                'remote_addr': request.remote_addr or '',
            }

            # 2026-10-23: 先落盘再进队列 —— 队列里的任何记录都保证能在磁盘上
            # 找到, 内存兜底丢弃队列积压时不会丢数据。
            _dump_record(record)

            try:
                request_log_queue.put_nowait(record)
            except queue.Full:
                try:
                    request_log_queue.get_nowait()
                    request_log_queue.put_nowait(record)
                except Exception:
                    pass

            with _history_lock:
                _request_history.append(record)
                if len(_request_history) > MAX_HISTORY:
                    del _request_history[:len(_request_history) - MAX_HISTORY]

            try:
                if _rovr is not None:
                    uid_log = _rovr.extract_user_id_from_request(req_body)
                    _rovr.record_request(uid_log, request.path, request.method,
                                         response.status_code, resp_body)
            except Exception:
                pass

        except Exception:
            pass

        return response
