# -*- coding: utf-8 -*-
"""老客户端 TCP/HTTP 接口兼容层 (0.1.8 ~ 0.3.17, 未加密 IPA 反编译得到)。

反编译依据 (Sky.bin 里的 JSON 键, 2026-10-25 用 _disasm 定位):
- /account/create 请求:
    0.1.8  : device_name, language
    0.3.15 : device_name, language, tos_version, link_external{...}
    服务端 create.py 却强制要求 8 个字段(device_key/sig/sig_ts/hashes 等),
    所以老客户端一律 400 "Missing required keys"。
- /account/login 请求:
    0.1.8 / 0.3.15 都是 device, key, language —— 没有 user;
    服务端 login.py 要求 user, 否则 400, 老客户端登不进去。
- 老客户端登录成功后按 resource_list 拉资源, 且期望鉴权响应里带
  external_links / player_badge_type / motd_* (参考 0.4.13 legacy 响应)。

本模块只做"协议形状"适配, 不改变业务数据:
  * create: 补齐老客户端没发的字段, 其余走原 create 逻辑;
  * login : 用 device 反查账号, 补成 user 再走原 login 逻辑;
  * 两个响应都补老客户端认识的鉴权字段。

0.5.0 及以上客户端不受影响(UA 版本 >= 0.5.0 时所有函数直接返回原值)。
"""
from __future__ import annotations

import re
import time

from flask import request

# 0.5.0 是协议分水岭(用户实测: 0.5.0 与旧版差异很大)
LEGACY_MAX_VERSION = (0, 5, 0)

_UA_PATTERNS = (
    re.compile(r"Sky[^/]*/(\d+)\.(\d+)\.(\d+)"),
    re.compile(r"/(\d+)\.(\d+)\.(\d+)\s*\("),
)

MOTD_TITLE = "你知道吗？"
MOTD_BODY = "在遇境的天上，有许多星星。这些星星来自先灵或你的友人，点亮他们吧！"
# 0.3.15 登录响应解析里能读到 tos_url / pp_url(见 cstring 索引 13945-13947 附近)
TOS_URL = "https://www.thatgamecompany.com/terms-of-service/"
PP_URL = "https://www.thatgamecompany.com/privacy-policy/"


def ua_version(user_agent: str | None = None):
    """从 User-Agent 解析 (major, minor, patch), 失败返回 None。

    老客户端 UA 形如 Sky-Live-ios/0.3.15.117331 (iPhone; iOS 14.3; Scale/3.00)。
    """
    if user_agent is None:
        try:
            user_agent = request.headers.get("User-Agent", "")
        except RuntimeError:  # 无请求上下文
            user_agent = ""
    ua = user_agent or ""
    for pattern in _UA_PATTERNS:
        match = pattern.search(ua)
        if match:
            try:
                return tuple(int(part) for part in match.groups())
            except (TypeError, ValueError):
                pass
    return None


def is_legacy_version(version) -> bool:
    return bool(version) and tuple(version) < LEGACY_MAX_VERSION


def is_legacy_request() -> bool:
    """当前 Flask 请求是否来自 0.5.0 以下客户端。"""
    try:
        return is_legacy_version(ua_version())
    except Exception:
        return False


def looks_like_legacy_create(data) -> bool:
    """UA 解析不出来时的兜底: 老 create 只有 device_name, 没有 sig/device_key/hashes。"""
    if not isinstance(data, dict) or not data.get("device_name"):
        return False
    return not data.get("sig") and not data.get("device_key") and not data.get("hashes")


def looks_like_legacy_login(data) -> bool:
    """UA 解析不出来时的兜底: 老 login 没有 user, 只有 device/key。"""
    if not isinstance(data, dict):
        return False
    return "user" not in data and bool(data.get("device")) and "type" not in data


def fill_create_request(data):
    """把老客户端的 create 请求补齐成服务端 create.py 期望的形状。

    只补协议字段, 不伪造业务数据; 老客户端真发过来的字段一律保留。
    """
    data = dict(data or {})

    data.setdefault("language", "zh-Hans")
    data.setdefault("tos_version", "0")
    data.setdefault("device_key", "")
    data.setdefault("sig_ts", 0)
    data.setdefault("sig", "")
    data.setdefault("hashes", [])

    # 0.3.15 的 link_external 是对象 {external_account_type, player_id, ...};
    # 0.1.8 根本不发。统一成对象, 避免 create.py/下游按 dict 取值时炸。
    if not isinstance(data.get("link_external"), dict):
        data["link_external"] = {}

    if not data.get("device_name"):
        data["device_name"] = "iOS"

    return data


def find_user_by_device(device):
    """老客户端 login 只带 device(create 返回的设备标识), 用它反查账号。"""
    if not device:
        return None
    try:
        from database.models import User
    except Exception:
        return None
    device = str(device).strip()
    if not device:
        return None
    return User.query.filter_by(device_identifier=device).first()


def augment_auth_response(body: dict, user=None, device=None) -> dict:
    """给 create/login 响应补老客户端读取的鉴权字段。

    0.3.x 的登录响应解析里能看到 resource_list 与 tos_url/pp_url 等键;
    0.4.13 legacy 响应还带 external_links/player_badge_type/motd_*。
    这些字段新客户端会忽略, 但老客户端缺了可能直接判登录失败。
    """
    if not isinstance(body, dict):
        return body

    now = int(time.time())
    body.setdefault("tos_url", TOS_URL)
    body.setdefault("pp_url", PP_URL)
    body.setdefault("external_links", [])
    body.setdefault("player_badge_type", -1)
    body.setdefault("motd_title", MOTD_TITLE)
    body.setdefault("motd", MOTD_BODY)
    body.setdefault("motd_timestamp", now)
    body.setdefault("motd_version", 0)
    body.setdefault("motd_buttons", [])
    body.setdefault("motd_season_end", 0)

    # login 的 device 原本是 None; 老客户端拿它继续请求, 回真实 device 更稳。
    if device:
        body["device"] = device
    elif user is not None and not body.get("device"):
        body["device"] = getattr(user, "device_identifier", "") or ""

    return body
