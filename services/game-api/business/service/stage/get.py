# -*- coding: utf-8 -*-
"""
Service接口: /service/stage/api/v1/get   (共享空间: 取"这里摆着的东西")

官方响应(static_responses/service__stage__api__v1__get.json):
  {"level_id":"Prairie_ButterflyFields",
   "props":[{"name":"CharSkyKid_Prop_RugMediumArgyle","pos":"(-2.233,-0.102,-0.318)",
             "ori":"(0.034,0.196,0.045,0.979)","scale":"(1,1,1)","type":"unlock",
             "userDataBool":false,"version":0}, ...],
   "sequence":1,"stage_id":"Prairie_ButterflyFields_CNDL","status":"OK","user_id":"..."}

请求(实测抓包 monitor_exports/cache/service__stage__api__v1__get.jsonc):
  {"user","session","stage_id":"CandleSpace_CNDL","level_id":"CandleSpace",
   "user_id":"00000000-0000-0000-0000-000000000000"}

老行为: 按库里的 stage 表查, 没有行就回 {"status":"no result"} —— 每次都是这一句。
客户端拿不到 props 列表就**打不开共享空间的物品放置栏**(用户反馈: "共享空间无法放置
物品 物品放置栏打不开"), 摆下去的道具也无从恢复。现在改成:
  * 从旁路文件(service/stage/store.py)读玩家的摆放;
  * 没有摆放时回 props: [] + status: "OK"(而不是 "no result"), 让放置栏能开;
  * 永远回 200 + 官方形状, 不回 400/401(客户端把非 2xx 显示成"非法请求。\n请重试。")。
"""
import json
import logging

from flask import Response, request

from . import store

logger = logging.getLogger(__name__)

NIL_ID = "00000000-0000-0000-0000-000000000000"


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def get_stage():
    """取某个 stage(共享空间)的摆放。永不回非 2xx。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        stage_id = str(data.get("stage_id") or "").strip()
        level_id = str(data.get("level_id") or "").strip()
        if not stage_id and level_id:
            # 客户端只给 level_id 时就按官方的 "<关卡>_CNDL" 补全(CNDL = 共享空间蜡烛)
            stage_id = level_id + "_CNDL"
        # user = 请求者本人; user_id = 要查看的共享空间主人(自己看自己时是全 0 占位)。
        # 旧实现把两者混成一个、且按 stage_id 全局只存一份 -> 所有人共用一个共享空间。
        caller = str(data.get("user") or "")
        target = str(data.get("user_id") or "")
        if target == NIL_ID:
            target = ""
        owner = target or caller

        rec = store.load(stage_id, owner)
        props = rec.get("props") if isinstance(rec.get("props"), list) else []
        try:
            seq = int(rec.get("sequence") or 1)
        except (TypeError, ValueError):
            seq = 1
        logger.info("stage get: stage=%s level=%s props=%d owner=%s caller=%s",
                    stage_id, level_id, len(props), owner[:8], caller[:8])
        return _resp({
            "level_id": level_id or rec.get("level_id") or "",
            "props": props,
            "sequence": seq,
            "stage_id": stage_id,
            "status": "OK",
            "user_id": rec.get("user_id") or owner or NIL_ID,
            "updated_at": rec.get("updated_at") or 0,
        })
    except Exception as e:
        logger.error("stage get 异常: %s", e, exc_info=True)
        # 形状正确的空响应: 客户端至少能开出空的放置栏, 不会弹"非法请求"
        return _resp({
            "level_id": "", "props": [], "sequence": 1, "stage_id": "",
            "status": "OK", "user_id": NIL_ID,
        })