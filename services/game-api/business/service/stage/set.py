# -*- coding: utf-8 -*-
"""
Service接口: /service/stage/api/v1/set   (共享空间: 保存"这里摆着的东西")

官方响应(static_responses/service__stage__api__v1__set.json):
  {"status": "OK"}

请求: 客户端把当前摆放的整份 props 列表回传(键名各版本不一: props / items / placements...)。
实测 2026-08-24 19:53:46 客户端发过这个路径, 当时没有注册 -> 404 HTML ->
"共享空间里摆下去的东西存不住, 退出再进就没了".

这里做**宽容解析**(不猜死键名):
  1) 先找 props/prop/items/placements/stage_props/props_list/objects 里第一个"字典列表";
  2) 找不到就找请求体里**最长的字典列表**字段(客户端各版本键名不一);
  3) 一个都没找到(比如它换了别的协议)时: **不动已有摆放**(避免误清空),
     把请求体摘要存进 last_request 便于排查, 仍然回 {"status":"OK"}。
props 落旁路文件(instance/stage/<stage_id>.json), 不动数据库表结构。
"""
import json
import logging

from flask import Response, request

from . import store

logger = logging.getLogger(__name__)

_PROP_KEYS = ("props", "prop", "items", "placements", "stage_props", "props_list",
              "objects", "props_data")
_SKIP_KEYS = ("user", "session", "user_id", "stage_id", "level_id", "sequence")


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def _brief(data):
    """请求体摘要(存排查用, 不存整份大 body)。"""
    try:
        s = json.dumps({k: v for k, v in data.items() if k != "session"},
                       ensure_ascii=False)
    except Exception:
        s = repr(data)
    return s[:2000]


def _pick_props(data):
    """尽力从请求体里找出 props 列表; 找不到返回 None。"""
    for k in _PROP_KEYS:
        v = data.get(k)
        if isinstance(v, list) and all(isinstance(x, dict) for x in v):
            return v
    # 嵌套一层(如 {"props": {"items": [...]}})
    for k in _PROP_KEYS:
        v = data.get(k)
        if isinstance(v, dict):
            for kk in _PROP_KEYS:
                vv = v.get(kk)
                if isinstance(vv, list) and all(isinstance(x, dict) for x in vv):
                    return vv
    best = None
    for k, v in data.items():
        if k in _SKIP_KEYS:
            continue
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            if best is None or len(v) > len(best):
                best = v
    return best


def set_stage():
    """保存共享空间的摆放。永不回非 2xx。"""
    try:
        data = request.get_json(force=True, silent=True)
        if isinstance(data, list):
            data = {"props": data}
        if not isinstance(data, dict):
            data = {}
        stage_id = str(data.get("stage_id") or "").strip()
        level_id = str(data.get("level_id") or "").strip()
        if not stage_id and level_id:
            stage_id = level_id + "_CNDL"
        # 保存的一定是"请求者本人"的共享空间; user_id 字段在部分版本里是全 0 或别人
        user_id = str(data.get("user") or "")
        if not user_id:
            user_id = str(data.get("user_id") or "")
            if user_id.startswith("00000000-0000-0000-0000-000000000000"):
                user_id = ""

        props_list = _pick_props(data)
        if props_list is None:
            old = store.load(stage_id, user_id)
            if stage_id:
                old["last_request"] = _brief(data)
                store.save(stage_id, old, user_id)
            logger.info("stage set: 未识别出 props, 请求摘要=%s", _brief(data))
            return _resp({"status": "OK"})

        seq = store.set_props(stage_id, store.normalize_props(props_list),
                              level_id=level_id, user_id=user_id,
                              sequence=data.get("sequence"))
        logger.info("stage set: stage=%s level=%s props=%d sequence=%s user=%s",
                    stage_id, level_id, len(props_list), seq, user_id[:8])
        return _resp({"status": "OK"})
    except Exception as e:
        logger.error("stage set 异常: %s", e, exc_info=True)
        return _resp({"status": "OK"})