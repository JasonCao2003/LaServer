# -*- coding: utf-8 -*-
"""
Service接口: 共享空间/公寓(stage/apartment)家族的几个小接口 —— 官方就是这么回的,
之前这些路径都没注册(客户端发过来落到 404/JSON 兜底, 界面卡住或弹"非法请求")。

  /service/stage/api/v1/friends_in_apartments     -> {"friends": []}
     (static_responses/service__stage__api__v1__friends_in_apartments.json 是
      {"friends":[{"friend_id":...,"nickname":...}]}; 我们没有人一起进公寓时回空表)
  /service/stage/api/v1/leave_apartment           -> {"result": "success"}
  /service/stage/api/v1/update_apartment_status   -> {"result": "success"}

公寓=共享空间的"一起玩"入口: 客户端进/出公寓、改状态都要这几条, 拿不到就退不出去/进不来。
"""
import json
import logging

from flask import Response, request

logger = logging.getLogger(__name__)


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def friends_in_apartments():
    """谁在公寓里(我们不做多人公寓房间, 一律空表)。"""
    try:
        request.get_json(force=True, silent=True)
    except Exception:
        pass
    return _resp({"friends": []})


def leave_apartment():
    """离开公寓。"""
    try:
        request.get_json(force=True, silent=True)
    except Exception:
        pass
    return _resp({"result": "success"})


def update_apartment_status():
    """改公寓状态(公开/私密/是否可加入)。"""
    try:
        request.get_json(force=True, silent=True)
    except Exception:
        pass
    return _resp({"result": "success"})