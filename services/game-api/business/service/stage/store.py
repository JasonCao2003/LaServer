# -*- coding: utf-8 -*-
"""共享空间(Shared Space)摆放物品的落盘存储 —— 不改数据库表结构。

客户端流程(官方抓包 static_responses/service__stage__api__v1__get.json, 实测请求见
monitor_exports/cache/service__stage__api__v1__get.jsonc):

    POST /service/stage/api/v1/get   {"user","session","stage_id":"CandleSpace_CNDL",
                                      "level_id":"CandleSpace","user_id":"0000...-000000000000"}
    ->   {"level_id":"Prairie_ButterflyFields",
          "props":[{"name":"CharSkyKid_Prop_RugMediumArgyle",
                    "pos":"(-2.233,-0.102,-0.318)","ori":"(0.034,0.196,0.045,0.979)",
                    "scale":"(1,1,1)","type":"unlock","userDataBool":false,"version":0}, ...],
          "sequence":1,"stage_id":"Prairie_ButterflyFields_CNDL","status":"OK","user_id":"..."}

玩家在共享空间里把道具摆好/挪动/收起后, 客户端用 /service/stage/api/v1/set 把整份 props
回传上来(摆放栏里能摆的东西 = 玩家账号里拥有的道具).

老实现的两个问题(用户反馈"共享空间无法放置物品, 物品放置栏打不开"):
  1) stage/get 只按库里的 stage 表回 {"status":"no result"} —— 客户端拿不到 props 列表,
     物品放置栏就不出来(它要拿已摆放的道具来决定显示什么);
  2) stage/set 这条路径**根本没注册**(实测 2026-08-24 19:53:46 客户端发 stage/set -> 404),
     所以摆下去的东西也存不住, 下次进共享空间还是空的.

数据库 stage 表(models.Stage)只有 stage_id/level_id/name/description/status, **没有 props
列**, 所以 props 走旁路文件(和 social_feed 的 extra_store 同一思路, 不动表结构):
    instance/stage/<stage_id>.json
内容: {"props":[...], "sequence":N, "level_id":..., "user_id":..., "updated_at":...}
删除 instance/stage/ 目录 = 清空全部摆放(一键回滚)。
"""
from paths import DATA_DIR, SERVICE_ROOT
import io
import json
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

_ROOT = SERVICE_ROOT
STAGE_DIR = os.path.join(DATA_DIR, "instance", "stage")

_lock = threading.Lock()
_SAFE = re.compile(r"[^0-9A-Za-z_.:-]")


def safe_key(stage_id):
    return _SAFE.sub("", str(stage_id or ""))[:128]


def _path(stage_id, owner=""):
    key = safe_key(stage_id)
    if owner:
        key = key + "@" + safe_key(owner)
    return os.path.join(STAGE_DIR, key + ".json")


def load(stage_id, owner=""):
    """读某个 stage 的记录(没存过返回 {})。

    owner 非空时按「玩家自己摆的共享空间」存: <stage_id>@<owner>.json;
    旧数据只有 <stage_id>.json, 读不到 owner 专属文件时回退读旧文件(平滑迁移)。
    """
    if not safe_key(stage_id):
        return {}
    cands = [_path(stage_id, owner)] if owner else []
    cands.append(_path(stage_id))
    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            with io.open(p, encoding="utf-8") as f:
                rec = json.load(f)
            if isinstance(rec, dict):
                return rec
        except Exception as e:
            logger.warning("stage 读取失败(%s): %s", p, e)
    return {}


def save(stage_id, record, owner=""):
    """整份写一个 stage 记录。失败只记日志(绝不影响接口返回)。"""
    if not safe_key(stage_id) or not isinstance(record, dict):
        return False
    try:
        with _lock:
            os.makedirs(STAGE_DIR, exist_ok=True)
            tmp = _path(stage_id, owner) + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, _path(stage_id, owner))
        return True
    except Exception as e:
        logger.warning("stage 保存失败(%s): %s", stage_id, e)
        return False


def props(stage_id, owner=""):
    v = load(stage_id, owner).get("props")
    return v if isinstance(v, list) else []


def set_props(stage_id, props_list, level_id="", user_id="", sequence=None):
    """整份覆盖 props(客户端 set 传的就是"现在摆着的东西"的完整列表)。
    返回新的 sequence(写失败返回 0)。按 owner(user_id) 各存一份。"""
    owner = user_id or ""
    old = load(stage_id, owner)
    try:
        seq = int(sequence)
    except (TypeError, ValueError):
        seq = 0
    if seq <= 0:
        seq = int(old.get("sequence") or 0) + 1
    rec = dict(old)
    rec.update({
        "props": props_list if isinstance(props_list, list) else [],
        "sequence": seq,
        "level_id": level_id or old.get("level_id") or "",
        "user_id": owner or old.get("user_id") or "",
        "updated_at": int(time.time()),
    })
    return seq if save(stage_id, rec, owner) else 0


def normalize_prop(p):
    """把一条 prop 规整成官方形状; 客户端多发的字段原样保留(不丢信息)。"""
    if not isinstance(p, dict):
        return None
    out = {
        "name": str(p.get("name") or p.get("prop_name") or p.get("prop") or ""),
        "pos": str(p.get("pos") or p.get("position") or "(0,0,0)"),
        "ori": str(p.get("ori") or p.get("rotation") or p.get("rot") or "(0,0,0,1)"),
        "scale": str(p.get("scale") or "(1,1,1)"),
        "type": str(p.get("type") or "unlock"),
        "userDataBool": bool(p.get("userDataBool", False)),
    }
    try:
        out["version"] = int(p.get("version") or 0)
    except (TypeError, ValueError):
        out["version"] = 0
    for k, v in p.items():
        if k not in out:
            out[k] = v
    return out


def normalize_props(props_list):
    out = []
    for p in (props_list if isinstance(props_list, list) else []):
        n = normalize_prop(p)
        if n is not None:
            out.append(n)
    return out