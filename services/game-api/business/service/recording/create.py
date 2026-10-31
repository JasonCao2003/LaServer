# -*- coding: utf-8 -*-
"""
Service接口: /service/recording/api/v1/create  (留影蜡烛"上传留影")

官方抓包(static_responses/service__recording__api__v1__create.json):
  {"status": "OK", "recording_id": "6696ab96d432468e59398ad9"}

请求(实测抓包 monitor_exports/cache/service__recording__api__v1__create.jsonc):
  {"user","session","recording_id":"","level_id":"CandleSpace",
   "user_id":"00000000-0000-0000-0000-000000000000",
   "recording_data":{"ver":1,"bcet":...,"stfs":[...],"strs":[...],"plyr":[{...}]}}
  * recording_id 为空 = 新建; 非空 = 覆盖重录(沿用同一个 id)。
  * user_id 是占位的全 0(uuid nil), 真实用户看 "user"。
  * 这一版请求里**没有**名字字段 —— 玩家输入的名字随后走
    /service/social-feed/api/v1/create(pool_type=RecordingCandle)那条路,
    所以这里也把 name/title/message 一起存下(万一某个版本传了)。

旧行为: 这个路径没有路由 -> 落到 JSON 兜底返回 {} -> 客户端拿不到 recording_id,
留影就"录制完输入名字后没有创建出现留影"。现在真的存下来并回官方形状的 id。
"""
from flask import request, Response
import hashlib
import itertools
import json
import logging
import os
import time

from . import store

logger = logging.getLogger(__name__)

_counter = itertools.count()


def _new_id(seed):
    """24 位小写十六进制(官方 recording_id 形态, Mongo ObjectId 风格)。"""
    raw = "%s|%s|%s" % (seed, time.time(), next(_counter))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def recording_create():
    """存一条留影。永不回非 2xx(客户端把非 2xx 显示成"非法请求")。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        safe = {k: v for k, v in data.items()
                if k not in ("session", "recording_data")}
        safe["recording_data"] = "(已省略 %d 字节)" % len(
            json.dumps(data.get("recording_data") or {}, ensure_ascii=False))
        logger.info("recording create: %s", json.dumps(safe, ensure_ascii=False))

        uid = data.get("user") or ""
        claimed = str(data.get("user_id") or "")
        # 全 0 的 user_id 是占位值, 不用它当作者
        if uid and set(claimed) <= {"0", "-", ""}:
            claimed = uid
        author = uid or claimed

        rid = str(data.get("recording_id") or "").strip()
        if not rid or not store.safe_id(rid):
            rid = _new_id(author)
        level_id = str(data.get("level_id") or "")
        rec_data = data.get("recording_data")
        if not isinstance(rec_data, dict):
            rec_data = None

        record = {
            "recording_id": rid,
            "user_id": author or "00000000-0000-0000-0000-000000000000",
            "level_id": level_id,
            "name": str(data.get("name") or data.get("title") or data.get("message") or ""),
            "created_at": int(time.time()),
            "recording_data": rec_data or {},
        }
        store.save(rid, record)
        return _resp({"status": "OK", "recording_id": rid})
    except Exception as e:
        logger.error("recording create 异常: %s", e, exc_info=True)
        # 仍然回一个形状正确的响应, 客户端不会弹"非法请求"
        return _resp({"status": "OK", "recording_id": _new_id("fallback")})
