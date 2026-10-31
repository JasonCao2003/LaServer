# -*- coding: utf-8 -*-
"""
基于文件的响应覆盖 (File Response Override)
==========================================
主控面板把"要发给玩家的响应"直接写到本地文件; 私服每次请求时实时读取该文件作为响应体。

特点(满足"直接作用到本地文件 + 私服每次实时读文件 + 防串改"):
  - 覆盖体存为磁盘上的纯文本文件, 一个接口路径一个文件。
  - 服务端在 after_request 阶段, 每次请求都重新 open+read 该文件(不缓存),
    保证发出的永远等于文件当前内容; 改文件即时生效, 无需重启/热更新。
  - 全局生效(对所有用户), 与按用户的数据库覆盖各管各的; 文件覆盖优先级最高。

目录: config/response_overrides/
文件名: 由接口路径生成, 把 '/' 等非法字符换成 '__', 加 .json 后缀。
  例: /account/get_spirit_shops -> account__get_spirit_shops.json
"""
import os
import re

_THIS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_THIS, "config", "response_overrides")


def _ensure_dir():
    try:
        os.makedirs(_DIR, exist_ok=True)
    except Exception:
        pass


def path_to_filename(path):
    """把接口路径转成安全文件名。/account/get_spirit_shops -> account__get_spirit_shops.json"""
    p = (path or "").strip().lstrip("/")
    p = re.sub(r"[^A-Za-z0-9_.-]+", "__", p)
    if not p:
        p = "_root"
    return p + ".json"


def override_file(path):
    return os.path.join(_DIR, path_to_filename(path))


def get_file_override(path):
    """返回该路径的文件覆盖内容(str); 文件不存在/读失败返回 None。每次调用实时读盘。"""
    fp = override_file(path)
    try:
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        return None
    return None


def save_file_override(path, body):
    """把覆盖体写入本地文件(主控面板调用)。"""
    _ensure_dir()
    fp = override_file(path)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(body if isinstance(body, str) else body.decode("utf-8", "replace"))
    return fp


def delete_file_override(path):
    """删除该路径的文件覆盖(恢复实时源码响应)。"""
    fp = override_file(path)
    try:
        if os.path.exists(fp):
            os.remove(fp)
            return True
    except Exception:
        pass
    return False


def has_file_override(path):
    return os.path.exists(override_file(path))
