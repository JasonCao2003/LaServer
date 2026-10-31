# -*- coding: utf-8 -*-
"""客户端版本解析(带 UA 谎报纠偏) —— 全服统一入口。

为什么需要"纠偏":
    有些私服/魔改客户端会在 User-Agent 里谎报版本。实测(玩家反馈 + 崩溃日志):
    用户在跑的 0.13.4(crash tag com.tgc.sky.androidold, libBootloader 就是 0.13.4
    那一份)在 UA 里报的却是 0.23.6.354648:
        Sky-Live-com.tgc.sky.android/0.23.6.354648 (READBOY Readboy_C30; android 29.0.0; zh)
    服务端若照单全收, 就会按 0.23.6 给它发 0.23 时代的解锁/魔法/静态库:
        * 老引擎不认识那些名字 -> 轻则"全解锁点了没生效";
        * 重则载入时 GetMetaClassByName 找不到元类 -> 原生 abort / 断言闪退
          (实测: RelationshipAbilityDefs.cpp: RelationshipAbility 'iap' - no
           RelationshipButtonEvent of type SocialIAP)。
    真机 0.23.6 的 build 号在 238xxx~240xxx 区间(见 E:/Sky/测试服 各 APK 文件名),
    而 354648 谁也匹配不上, 所以按 build 精确纠偏是安全的(不会误伤真 0.23.6)。

配置: config/client_version_overrides.json(改完立即生效, 按文件 mtime 热加载)
    {
      "by_full":       {"0.23.6.354648": "0.13.4"},  # 整个 4 段版本号命中(最高优先)
      "by_build":      {"354648": "0.13.4"},         # UA 第 4 段 build 命中
      "by_version":    {"0.23.6": "0.13.4"},         # 仅三段版本号命中
      "by_ua_contains":{"androidold": "0.13.4"}      # UA 子串命中(最低优先)
    }
匹配优先级: by_full > by_build > by_version > by_ua_contains。

对外接口:
    parse_raw(ua)               -> (ver_tuple|None, build_str|None, full_str|None) 不做纠偏
    client_version_from_ua(ua)  -> (major, minor, patch) 已纠偏; 解析不了返回 None
    client_version_from_request()-> 同上, 直接从当前 Flask 请求的 UA 取
    client_build(ua)            -> build 字符串(str) 或 None
"""
from paths import SERVICE_ROOT
import json
import os
import re
import threading

_lock = threading.Lock()
_cache = {"mtime": None, "data": None}

_OVERRIDE_FILE = "client_version_overrides.json"

# Sky-Live-com.tgc.sky.android/0.23.6.354648 (...)  -> 0.23.6 + build 354648
_VER_RE = re.compile(r"Sky[^/]*/(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")
# 兜底: 没有 Sky 前缀时, 取 URL 里形如 /x.y.z 的第一处
_VER_RE_FALLBACK = re.compile(r"/(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")


def _root_dir():
    return SERVICE_ROOT


def _overrides():
    """读取纠偏表(按 mtime 热加载)。失败返回空表。"""
    path = os.path.join(_root_dir(), "config", _OVERRIDE_FILE)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        if _cache["data"] is not None and _cache["mtime"] == mtime:
            return _cache["data"]
    data = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for key in ("by_full", "by_build", "by_version", "by_ua_contains"):
                sub = raw.get(key)
                if isinstance(sub, dict):
                    data[key] = {str(k): str(v) for k, v in sub.items()}
    except Exception:
        data = {}
    with _lock:
        _cache["mtime"] = mtime
        _cache["data"] = data
    return data


def _to_tuple(text):
    """'0.13.4' -> (0, 13, 4)。失败返回 None。"""
    try:
        nums = [int(p) for p in str(text).strip().split(".")]
    except (TypeError, ValueError):
        return None
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def parse_raw(ua):
    """解析原始 UA, 返回 (ver_tuple|None, build_str|None, full_str|None)。不做纠偏。"""
    ua = ua or ""
    m = _VER_RE.search(ua) or _VER_RE_FALLBACK.search(ua)
    if not m:
        return None, None, None
    ver = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    build = m.group(4)
    full = "%d.%d.%d" % ver + ((".%s" % build) if build else "")
    return ver, build, full


def _apply_override(ver, build, full, ua):
    """按配置纠偏, 命中返回 tuple; 否则返回 None。"""
    cfg = _overrides()
    if not cfg or ver is None:
        return None
    if full:
        hit = cfg.get("by_full", {}).get(full)
        if hit:
            return _to_tuple(hit)
    if build:
        hit = cfg.get("by_build", {}).get(str(build))
        if hit:
            return _to_tuple(hit)
    vstr = "%d.%d.%d" % ver
    hit = cfg.get("by_version", {}).get(vstr)
    if hit:
        return _to_tuple(hit)
    for needle, repl in cfg.get("by_ua_contains", {}).items():
        if needle and needle in (ua or ""):
            return _to_tuple(repl)
    return None


def client_version_from_ua(ua):
    """从 UA 解析并纠偏, 返回 (major, minor, patch) 或 None。"""
    ver, build, full = parse_raw(ua)
    if ver is None:
        return None
    return _apply_override(ver, build, full, ua) or ver


def client_build(ua):
    """取 UA 里的 build 段(第 4 个数字), 取不到返回 None。"""
    _, build, _ = parse_raw(ua)
    return build


def client_version_from_request():
    """从当前 Flask 请求的 User-Agent 解析并纠偏; 无请求时返回 None。"""
    try:
        from flask import request
        ua = request.headers.get("User-Agent", "") if request else ""
    except Exception:
        ua = ""
    return client_version_from_ua(ua)