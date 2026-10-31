# -*- coding: utf-8 -*-
"""
静态接口库 (按客户端版本号分流)

为 That_Sky_Requests 抓包库里的接口提供静态响应, 但仅对高版本启用:
- 客户端版本 >= 0.30.0  -> 使用接口库的静态响应 (库数据正是 0.30.0 抓取)。
- 客户端版本 <  0.30.0  -> 完全走服务端原来的动态源码, 旧版不受任何影响。
- 无法解析版本时, 默认按"原动态源码"处理 (保守, 不影响旧版)。

注意: "接口库里有"不代表高版本就一定能在私服里正常请求 —— 登录/鉴权/会话相关、
开全图开全动作、季节&公告等接口必须保持动态, 否则多用户/控制台会失效, 这些放在
_dynamic_exclude.json 排除表里, 无论版本高低都走原动态逻辑。

文件:
- _manifest.json        : 路径 -> 响应文件 索引
- _dynamic_exclude.json : 始终保持动态的路径 (登录/鉴权/开全图开全动作/季节&公告等)。

实现: 仅用一个 before_request 钩子按版本分流。
- 高版本(>=0.30.0) + 库内路径 + 不在排除表 -> 直接返回静态响应 (Flask 短路, 无需注册路由)。
- 其余情况(含全部旧版) -> 返回 None, 交给原路由 (动态接口; 无路由则 404 = 原始行为)。
静态响应仍会经过 request_monitor_middleware 的 response_override 钩子,
管理面板设置的"覆盖体"对这些路径同样生效。json 改动即时生效(不缓存)。
"""
import os
import re
import json
from flask import Response, request, g

_DIR = os.path.dirname(os.path.abspath(__file__))
_MANIFEST = os.path.join(_DIR, "_manifest.json")
_EXCLUDE = os.path.join(_DIR, "_dynamic_exclude.json")

# 启用接口库的最低版本(库数据正是 0.30.0 抓取); 低于此版本一律走原动态源码
# 注意: 不能调太低 —— 部分客户端 UA 会虚报版本(如实际 0.13.4 报 0.23.6),
# 门槛过低会把这类客户端引到 0.30.0 抓包的静态响应导致闪退。
LIBRARY_MIN_VERSION = (0, 26, 0)

# 响应体缓存: {文件路径: (mtime, 压缩后的body)}
_BODY_CACHE = {}

# 当前生效的"可分流路径"集合 {path: file_name}。在注册时与每次热更新时, 由
# _compute_static_paths() 从 _manifest.json/_dynamic_exclude.json 重新计算。
# before_request 钩子从这里读, 所以改排除表后热更新即可生效, 无需重启服务。
_STATIC_PATHS = {}

# UA 形如 Sky-Live-com.tgc.sky.android/0.13.4.169938 或
#         Sky-Test-com.tgc.sky.android.test.gold/0.19.5.203675
_VER_RE = re.compile(r"Sky-[^/\s]+/(\d+(?:\.\d+)*)")


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _load_manifest():
    return _load_json(_MANIFEST, {})


def _compute_static_paths():
    """从 _manifest.json + _dynamic_exclude.json 计算当前生效的可分流路径集合,
    写入模块级 _STATIC_PATHS。注册时与每次热更新都会调用, 使排除表改动即时生效。"""
    manifest = _load_manifest()
    exclude = _load_exclude()
    paths = {p: info["file"] for p, info in manifest.items() if p not in exclude}
    _STATIC_PATHS.clear()
    _STATIC_PATHS.update(paths)
    return len(paths)


def reload_library():
    """热更新用: 清空响应体缓存 + 按最新 _dynamic_exclude.json 重算可分流路径集合,
    使下次请求重新从磁盘读取最新静态响应, 且排除表(动态/静态切换)改动即时生效。
    (静态库的路由/钩子已在启动时注册, 无需重注册。)"""
    try:
        _BODY_CACHE.clear()
    except Exception:
        pass
    try:
        _compute_static_paths()
    except Exception:
        pass


def _load_exclude():
    """返回需保持动态的路径集合。"""
    data = _load_json(_EXCLUDE, {})
    return {k for k, v in data.items() if k != "_comment" and v}


def _parse_version(ua):
    """从 User-Agent 解析版本三元组 (major, minor, patch)。无法解析返回 None。

    统一走 client_version 纠偏(见 config/client_version_overrides.json):
    有的魔改老客户端谎报版本(实测 0.13.4 报 0.23.6.354648); 不纠偏的话
    _use_library 会误判成高版本, 把接口库(新版静态数据)塞给老引擎 -> 闪退。
    """
    try:
        from helpers.client_version import client_version_from_ua
        return client_version_from_ua(ua)
    except Exception:
        pass
    if not ua:
        return None
    m = _VER_RE.search(ua)
    if not m:
        return None
    parts = m.group(1).split(".")
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _use_library(ua):
    """是否对该请求使用接口库 (高版本)。无法解析版本时默认 False(走原动态源码)。"""
    v = _parse_version(ua)
    if v is None:
        return False
    return v >= LIBRARY_MIN_VERSION


def _read_body(file_name):
    """读取库响应并压缩(去除缩进空白)后返回。

    游戏客户端的输出缓冲区有固定上限(约 358KB), 带缩进的 JSON 会偏大,
    个别接口(如 get_generic_shops 带缩进 392KB)会超限导致客户端崩溃。
    压缩后(去空白)同样内容仅 ~273KB, 远低于上限。
    按 文件路径+修改时间 缓存, 磁盘改动后自动刷新, 不重复解析。
    """
    fp = os.path.join(_DIR, file_name)
    try:
        mtime = os.path.getmtime(fp)
    except OSError:
        return "{}"
    cached = _BODY_CACHE.get(fp)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        with open(fp, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return "{}"
    try:
        body = json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        body = raw  # 不是合法 JSON 时原样返回
    _BODY_CACHE[fp] = (mtime, body)
    return body


# spirit_shops_total_count 版本分流:
#   原始抓包文件声明 2279, 但库内实际只有 ~1000 个节点。高版本(>0.16.0)客户端会
#   因 "声明数 > 实际数" 而重复拉取/合并, 导致兑换树出现重复根节点而崩溃
#   (assert: More than one root node detected in spirit shop)。
#   故: >0.16.0 -> 用实际节点数(修复); <=0.16.0 -> 保留原始 2279(旧版行为不变)。
_SPIRIT_SHOPS_PATH = "/account/get_spirit_shops"
_SPIRIT_COUNT_GATE = (0, 16, 0)
_ORIGINAL_SPIRIT_TOTAL = 2279


def _read_spirit_shops_body(file_name, ua):
    """按客户端版本返回 get_spirit_shops 响应体, 修正 spirit_shops_total_count。"""
    base = _read_body(file_name)
    try:
        data = json.loads(base)
    except Exception:
        return base
    nodes = data.get("spirit_shops")
    if not isinstance(nodes, list):
        return base
    v = _parse_version(ua)
    if v is not None and v > _SPIRIT_COUNT_GATE:
        data["spirit_shops_total_count"] = len(nodes)   # 修复: 与实际一致
    else:
        data["spirit_shops_total_count"] = _ORIGINAL_SPIRIT_TOTAL  # 旧版保留原值
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


_MAGIC_DEFS_HOOK = {
    "/account/consumable/get_consumable_defs": "get_consumable_defs",
    "/account/buff/get_buff_defs": "get_buff_defs",
}


def _read_magic_defs_body(file_name, ua):
    """按客户端版本剔掉"这个版本没有的魔法/buff 定义"。

    判据与 magic_helper.MAGIC_EVENT_MIN_VERSION 同源(D:\\工具\\SO 各版本
    libBootloader*.so 里 event 类名串的首次出现版本): 客户端把 def.event 当类名去查表,
    查不到就是 "No event named %s" / "Event named %s is not a BuffEvent" 那一类断言崩溃
    (0.12.1 收到留影蜡烛/空间蜡烛/留言石等就是这种情况, 用户 2026-10-06 反馈)。
    库里的静态定义表是按 0.30.0 抓的, 直接发给 0.26~0.33.6 的客户端同样不合规。
    过滤只针对 def 列表本身(逐条判 event), 结构性字段一律不动。
    """
    base = _read_body(file_name)
    version = _parse_version(ua)
    if version is None:
        return base
    try:
        import helpers.magic_helper as magic_helper
        key = _MAGIC_DEFS_HOOK.get(request.path)
        kind = "buff" if key == "get_buff_defs" else "consumable"
        data = json.loads(base)
        keys = [key] if key else [k for k in ("get_consumable_defs", "get_buff_defs")
                                  if isinstance(data.get(k), list)]
        changed = False
        for k in keys:
            defs = data.get(k)
            if not isinstance(defs, list):
                continue
            kept = magic_helper.filter_magic_defs(defs, "buff" if k == "get_buff_defs" else kind)
            if len(kept) != len(defs):
                data[k] = kept
                changed = True
        if not changed:
            return base
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return base


def _real_currency(user_id):
    """读取该用户真实货币(dict)。失败返回 None。"""
    try:
        from database.models import UserCurrency
        cur = UserCurrency.query.filter_by(user_id=user_id).first()
        if not cur:
            return None
        return {
            "candles": cur.candles, "heart": cur.heart, "heart_wax": cur.heart_wax,
            "prestige": cur.prestige, "prestige_vip": cur.prestige_vip,
            "prestige_wax": cur.prestige_wax, "season_candle": cur.season_candle,
            "season_wax": cur.season_wax, "season_wax_14": cur.season_wax_14,
            "vip": cur.vip, "wax": cur.wax,
        }
    except Exception:
        return None


def _patch_currency_body(body, user_id):
    """若静态响应里含 currency/update_currency 块, 用该用户真实货币覆盖,
    避免静态库里的假币污染客户端货币显示。返回(可能改写后的)body 字符串。"""
    if not user_id or ("currency" not in body):
        return body
    real = _real_currency(user_id)
    if not real:
        return body
    try:
        data = json.loads(body)
    except Exception:
        return body

    def _merge(dst):
        for k, v in real.items():
            dst[k] = v

    changed = False
    if isinstance(data, dict):
        for key in ("currency", "update_currency"):
            blk = data.get(key)
            if isinstance(blk, dict):
                _merge(blk)
                changed = True
    if not changed:
        return body
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def register_static_responses(app):
    """安装按版本分流的静态接口拦截。返回库内可分流路径数。"""
    exclude = _load_exclude()

    # 库里有、且不在排除表的路径 -> 高版本时由 before_request 返回静态响应。
    # 用模块级 _STATIC_PATHS(每次热更新重算), 使排除表改动热更新即生效。
    _compute_static_paths()

    @app.before_request
    def _serve_static_library():
        file_name = _STATIC_PATHS.get(request.path)
        if file_name is None:
            return None  # 不在库内 / 在排除表 -> 走原路由
        # 确保请求体已缓存, 供监控/响应覆盖中间件使用(无论是否被本钩子短路)
        try:
            if not hasattr(g, "_monitor_request_body"):
                g._monitor_request_body = request.get_data(as_text=False)
        except Exception:
            pass
        # 版本分流: 低版本(<0.16.0)走原动态源码, 避免旧版接口闪退
        if not _use_library(request.headers.get("User-Agent", "")):
            return None
        ua = request.headers.get("User-Agent", "")
        # get_spirit_shops 需按版本修正 spirit_shops_total_count
        if request.path == _SPIRIT_SHOPS_PATH:
            return Response(response=_read_spirit_shops_body(file_name, ua),
                            status=200, mimetype="application/json")
        # 魔法/buff 定义表: 按版本剔掉"该版本没有的魔法"(同 magic_helper 的门控)
        if request.path in _MAGIC_DEFS_HOOK:
            return Response(response=_read_magic_defs_body(file_name, ua),
                            status=200, mimetype="application/json")
        body = _read_body(file_name)
        # 静态库里的 currency 是固定假币, 用真实货币覆盖, 防止污染客户端货币显示
        if "currency" in body:
            uid = None
            try:
                rb = getattr(g, "_monitor_request_body", b"") or b""
                if rb:
                    import json as _j
                    d = _j.loads(rb.decode("utf-8", errors="replace"))
                    if isinstance(d, dict):
                        uid = d.get("user") or d.get("user_id")
            except Exception:
                uid = None
            if not uid:
                uid = request.headers.get("User-Id") or request.headers.get("user-id")
            body = _patch_currency_body(body, uid)
        return Response(response=body, status=200, mimetype="application/json")

    print(f"[static_responses] 库内可分流路径: {len(_STATIC_PATHS)} "
          f"(动态保留: {len(exclude)}); 启用版本 >= "
          f"{'.'.join(map(str, LIBRARY_MIN_VERSION))}")
    return len(_STATIC_PATHS)
