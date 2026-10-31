# -*- coding: utf-8 -*-
"""
自动授权辅助

由管理面板的"自动授权"按钮控制: 开启后, 在开启时间点之后新建的账号会被
自动设为已授权(is_authorized=1), 并打上 AUTO_AUTH 标记(写入 remark), 供面板
把这些账号在列表里标黄区分。

配置文件: config/auto_auth.json
    {"enabled": true/false, "since": <开启时的unix秒时间戳>}

面板与服务端共用同一份配置文件, 即时生效(不缓存)。
"""
from paths import CONFIG_DIR, SERVICE_ROOT
import os
import json
import threading

_lock = threading.Lock()

# 写入 remark 的标记, 面板据此把账号标黄
AUTO_AUTH_MARK = "AUTO_AUTH"


def _config_path():
    root = SERVICE_ROOT
    return os.path.join(CONFIG_DIR, "auto_auth.json")


def load_auto_auth():
    """读取自动授权配置。返回 {"enabled": bool, "since": int}。失败返回关闭状态。"""
    path = _config_path()
    try:
        with _lock:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        return {
            "enabled": bool(data.get("enabled", False)),
            "since": int(data.get("since", 0) or 0),
        }
    except Exception:
        return {"enabled": False, "since": 0}


def should_auto_authorize(created_at):
    """给定账号创建时间(unix秒), 判断是否应自动授权。"""
    cfg = load_auto_auth()
    if not cfg["enabled"]:
        return False
    return int(created_at or 0) >= cfg["since"]
