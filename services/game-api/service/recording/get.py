# -*- coding: utf-8 -*-
"""
Service接口: /service/recording/api/v1/get  (取回留影数据 -> 播放)

官方抓包(static_responses/service__recording__api__v1__get.json):
  {"level_id":"MainStreet","recording_data":{...},"recording_id":"66963344550e1e2d657b2a1a",
   "status":"OK","user_id":"746969a9-0cde-4369-9198-2085a889bbc0"}

客户端在世界里点击留影蜡烛(或走到别人留影旁边)时调它, 按 recording_id 取数据;
拿不到就播不了("点击后没有内容")。

recording_id 可能放在 recording_id / resource_id / id 里(各版本不一), 都认;
真的一个 id 都没有时, 退回"该用户在该关卡最近的一条留影"。
未收录的 id 回 {"status":"NOT_FOUND","recording_id":...} —— 形状合法, 客户端只会
显示"内容不存在", 不会崩/不会弹非法请求。
"""
from flask import request, Response
import json
import logging

from . import store

logger = logging.getLogger(__name__)


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def _pick(data, *keys):
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):                    # 有些版本嵌一层 {"recording":{"id":...}}
            inner = v.get("recording_id") or v.get("id")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def recording_get():
    """按 id 返回留影。永不回非 2xx。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        rid = _pick(data, "recording_id", "resource_id", "id", "recording")
        logger.info("recording get: recording_id=%r level_id=%r user=%r",
                    rid, data.get("level_id"), data.get("user"))
        rec = store.load(rid) if rid else None
        if rec is None and rid:
            # 客户端有时发的是 feed 的 id(uuid 或 "<uuid>:<8hex>"), 而不是 recording_id ——
            # 反查这条 feed 存过的 resource_id(它才是真正的 recording_id) 再取一次。
            try:
                from service.social_feed import extra_store
                for cand in (extra_store.resource_id_for_feed(rid.split(":")[0]),
                             extra_store.resource_id_for_feed(rid)):
                    if cand and cand != rid:
                        got = store.load(cand)
                        if got is not None:
                            rec, rid = got, cand
                            break
            except Exception:
                pass
        if rec is None and not rid:
            # 只有"客户端一个 id 都没给"时才兜底最近一条。给了 id 但没收录 -> NOT_FOUND:
            # 否则点别人的留影(id 不认识)会播放成"自己最近录的那条", 内容张冠李戴。
            uid = data.get("user") or data.get("user_id")
            rec = store.latest(uid, data.get("level_id"))
            if rec is not None:
                rid = rec.get("recording_id") or rid

        if rec is None:
            return _resp({"status": "NOT_FOUND", "recording_id": rid})

        return _resp({
            "level_id": rec.get("level_id") or data.get("level_id") or "",
            "recording_data": rec.get("recording_data") or {},
            "recording_id": rec.get("recording_id") or rid,
            "status": "OK",
            "user_id": rec.get("user_id")
                       or data.get("user") or data.get("user_id") or "",
        })
    except Exception as e:
        logger.error("recording get 异常: %s", e, exc_info=True)
        return _resp({"status": "NOT_FOUND"})
