# -*- coding: utf-8 -*-
"""留影(recording)数据的落盘存储 —— 一条留影一个 JSON 文件。

放 instance/recordings/<recording_id>.json, 不动数据库表结构(见用户约束: 测试期间
不写生产 users.db)。留影数据就是客户端 create 请求里的 recording_data(约 14 KB:
玩家动作/外观/姿态采样), 服务端只是搬运工, 不需要解析内容。

recording_id 用官方抓包的形态: 24 位小写十六进制(如 "6696ab96d432468e59398ad9")。
删除 instance/recordings/ 目录 = 清空全部留影(可一键回滚)。
"""
import io
import json
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REC_DIR = os.path.join(_ROOT, "data", "instance", "recordings")

_lock = threading.Lock()
_SAFE = re.compile(r"[^0-9A-Za-z_.:-]")


def safe_id(recording_id):
    return _SAFE.sub("", str(recording_id or ""))[:128]


def _path(recording_id):
    return os.path.join(REC_DIR, safe_id(recording_id) + ".json")


def save(recording_id, record):
    rid = safe_id(recording_id)
    if not rid or not isinstance(record, dict):
        return False
    try:
        with _lock:
            os.makedirs(REC_DIR, exist_ok=True)
            tmp = _path(rid) + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, _path(rid))
        return True
    except Exception as e:
        logger.warning("recording 保存失败(%s): %s", rid, e)
        return False


def load(recording_id):
    rid = safe_id(recording_id)
    p = _path(rid)
    if not rid or not os.path.isfile(p):
        return None
    try:
        with io.open(p, encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else None
    except Exception as e:
        logger.warning("recording 读取失败(%s): %s", rid, e)
        return None


def latest(user_id=None, level_id=None):
    """最近一条留影(客户端只给了 user/level、没给 recording_id 时的兜底)。"""
    try:
        if not os.path.isdir(REC_DIR):
            return None
        best = None
        for name in os.listdir(REC_DIR):
            if not name.endswith(".json"):
                continue
            rec = load(name[:-5])
            if not rec:
                continue
            if user_id and rec.get("user_id") not in (user_id, None, ""):
                continue
            if level_id and rec.get("level_id") and rec.get("level_id") != level_id:
                continue
            if best is None or int(rec.get("created_at") or 0) > int(best.get("created_at") or 0):
                best = rec
        return best
    except Exception:
        return None
