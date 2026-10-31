# -*- coding: utf-8 -*-
"""动态(social feed)的 extra_data 旁路存储 —— 不改数据库表结构。

背景: `social_feed_post` 表没有 extra_data 列, 于是"这条留言摆在哪(小船坐标)/它的
朝向/它引用什么资源(留影蜡烛的 recording_id)"这些客户端上传的信息在创建时被丢掉了,
下发时只能回默认值 —— 后果:
  * 纸船/留言在关卡里的位置永远是默认坐标(原来 (0,0,0), 现在 (-10,0,-10)),
    而不是玩家自己摆的位置;
  * extra_data.ori 回了 "(0,0,0)"(3 个分量), 而官方是四元数 "(0,0.998,0,0.063)"
    (4 个分量);
  * extra_data.resource_id 永远是空 —— 留影蜡烛(RecordingCandle)靠它指向
    recording_id, 客户端拿不到资源 id, 点击蜡烛时只能发全 0 的 social_feed_ids
    (实测抓包: ["00000000-0000-0000-0000-000000000000", ...]), 于是"点了没内容"。

方案(附加式、可一键回滚): 每条 feed 存一个小 JSON 旁路文件
    instance/feed_extra/<social_feed_id>.json
内容就是客户端 create 请求里的 extra_data(以及 comments_enabled/tags/private 等可选字段)。
读取时与默认值合并下发; 文件不存在 -> 完全等于旧行为(默认值)。删除 instance/feed_extra/
目录即可回滚全部改动。

为什么不用数据库列: 加列要动生产 users.db 的表结构(见用户约束: 测试期间不写生产库),
旁路文件零风险、可回滚, 且这一层以后想迁到数据库列时也能平滑迁移。
"""
from paths import DATA_DIR, SERVICE_ROOT
import io
import json
import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

_ROOT = SERVICE_ROOT
EXTRA_DIR = os.path.join(DATA_DIR, "instance", "feed_extra")

# 留言小船/纸船在关卡里的默认摆放坐标。改这里即可改默认位置。
# 字符串格式 "(x,y,z)", 与官方抓包一致(如 "(-2.233,-0.102,-0.318)")。
DEFAULT_POS = "(-10,0,-10)"
# 默认朝向: 官方是四元数 "(x,y,z,w)"(4 个分量)。旧实现回 "(0,0,0)" 少了 w 分量。
DEFAULT_ORI = "(0,0,0,1)"
DEFAULT_SCALE = "(1,1,1)"

DEFAULT_EXTRA = {
    "chunk_count": 0,
    "ori": DEFAULT_ORI,
    "pos": DEFAULT_POS,
    "prompt_used": "",
    "resource_id": "",
    "scale": DEFAULT_SCALE,
    "sub_type": 0,
}

_lock = threading.Lock()
_SAFE = re.compile(r"[^0-9A-Za-z_.:-]")


def _key(feed_id):
    """把 feed id 规范成安全的文件名。

    **统一按 ':' 前段(纯 uuid)做键**: 创建时存的是客户端认的形态
    "<uuid>:<8hex>"(见 social_feed_create 的 public_id), 而下发时读的是库里的
    social_feed_id(纯 uuid, 无后缀) —— 不归一化就是"存了读不到"(extra_data 永远
    回默认值/留影蜡烛 resource_id 永远为空)。
    """
    k = _SAFE.sub("", str(feed_id or "").split(":")[0])
    return k[:128]


def _path(feed_id):
    return os.path.join(EXTRA_DIR, _key(feed_id) + ".json")


def save_extra(feed_id, extra, comments_enabled=None, tags=None, is_private=None):
    """存一条 feed 的 extra_data。失败只记日志(绝不影响创建流程)。"""
    if not _key(feed_id) or not isinstance(extra, dict):
        return False
    record = {"extra_data": extra}
    if comments_enabled is not None:
        record["comments_enabled"] = bool(comments_enabled)
    if tags is not None:
        record["tags"] = tags
    if is_private is not None:
        record["private"] = bool(is_private)
    try:
        with _lock:
            os.makedirs(EXTRA_DIR, exist_ok=True)
            tmp = _path(feed_id) + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, _path(feed_id))
        return True
    except Exception as e:
        logger.warning("feed_extra 保存失败(%s): %s", feed_id, e)
        return False


def load_record(feed_id):
    """读原始旁路记录(没存过返回 {})。"""
    p = _path(feed_id)
    if not _key(feed_id) or not os.path.isfile(p):
        return {}
    try:
        with io.open(p, encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else {}
    except Exception as e:
        logger.warning("feed_extra 读取失败(%s): %s", feed_id, e)
        return {}


def feed_extra(feed_id):
    """下发给客户端的 extra_data: 默认值 + 客户端上传的值。"""
    out = dict(DEFAULT_EXTRA)
    rec = load_record(feed_id)
    up = rec.get("extra_data")
    if isinstance(up, dict):
        for k, v in up.items():
            if k in out and v is not None:
                out[k] = v
            elif k in ("posted_to", "read_at", "user_id"):   # 官方 extra_data 的可选字段
                out[k] = v
    return out


_idx_cache = {"key": None, "map": {}}


def resource_index():
    """{extra_data.resource_id -> feed 的纯 uuid} —— 扫一遍 instance/feed_extra。

    用来回答"客户端发来的 id 其实是资源 id"的情况(留影蜡烛的 resource_id =
    recording_id, 客户端点击时可能拿它当 social_feed_id 发过来)。
    按"目录里每个文件的 mtime"做缓存, 目录没变就不重复读。
    """
    try:
        names = [n for n in os.listdir(EXTRA_DIR) if n.endswith(".json")]
        key = tuple(sorted((n, os.path.getmtime(os.path.join(EXTRA_DIR, n)))
                           for n in names))
    except OSError:
        return {}
    if _idx_cache["key"] == key:
        return _idx_cache["map"]
    m = {}
    for n in names:
        rec = load_record(n[:-5])
        up = rec.get("extra_data")
        if isinstance(up, dict):
            rid = up.get("resource_id")
            if isinstance(rid, str) and rid:
                m.setdefault(rid, n[:-5])
    _idx_cache["key"] = key
    _idx_cache["map"] = m
    return m


def feed_id_for_resource(resource_id):
    """按 resource_id 反查 feed 的纯 uuid(没有返回 "")。"""
    return resource_index().get(str(resource_id or ""))


def resource_id_for_feed(feed_id):
    """按 feed id(纯 uuid 或 "<uuid>:<8hex>")取它存过的 resource_id。"""
    rec = load_record(feed_id)
    up = rec.get("extra_data")
    if isinstance(up, dict) and isinstance(up.get("resource_id"), str):
        return up["resource_id"]
    return ""


def comments_enabled(feed_id, default=True):
    rec = load_record(feed_id)
    v = rec.get("comments_enabled")
    return default if v is None else bool(v)


def tags(feed_id, default=None):
    rec = load_record(feed_id)
    return rec.get("tags", default)
