# -*- coding: utf-8 -*-
"""
接口: /account/season_start
季节开始。默认返回当前赛季号; 可由管理面板"季节开始"标签编辑/覆盖响应,
或勾选"禁用该功能"后返回 204 空响应 (不向游戏发送该接口及响应)。
真实响应体可被 response_override 中间件按管理面板的覆盖体替换。
"""
from flask import request, Response
from database.models import db, User, SeasonUnlock
import json
import os
from helpers.game_control_helper import get_season_config


def _disabled_flag_path():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "config", "season_start_disabled")


def get_cached_user(user_id, session_token):
    return User.query.filter_by(id=user_id, session=session_token).first()


def season_start():
    # 管理面板禁用该接口: 返回合法的空 200 响应 (游戏正常接收但无季节开始数据),
    # 注意不能用 204/空体, 否则游戏客户端判定为失败并不断重试导致连接错误。
    if os.path.exists(_disabled_flag_path()):
        return Response(
            response=json.dumps({}, ensure_ascii=False),
            status=200,
            mimetype="application/json",
        )
    try:
        request_data = request.get_json(force=True, silent=True) or {}
        user_id = request_data.get("user")
        session = request_data.get("session")

        if not user_id or not session:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype="application/json",
            )

        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype="application/json",
            )

        sc = get_season_config()
        # result 语义: "本次新开启、需要播放开场动画的季节"。客户端据此放一次开场动画。
        # 必须按玩家做幂等: 该玩家第一次开本季 -> 返回季节名(播一次); 之后返回空列表,
        # 否则每次进游戏都被当成"季节刚开" -> 循环重播开场动画。
        # 幂等标记复用 SeasonUnlock 表, 用 name="season_start_<N>" 一行表示"已看过本季开场"。
        # (与 season_finalize 用的 season_finish_<N> 是不同的 name, 互不影响。)
        season_no = str(sc["current_season"])
        start_mark = "season_start_" + season_no
        first_open = False
        try:
            mark = SeasonUnlock.query.filter_by(user_id=user_id, name=start_mark).first()
            if mark is None:
                first_open = True
                db.session.add(SeasonUnlock(
                    user_id=user_id,
                    unlock_type="season_start",
                    name=start_mark,
                    created_at=0,
                    ack=True,
                ))
                db.session.commit()
        except Exception:
            db.session.rollback()
            first_open = False

        response_body = {
            "result": (["season_" + season_no] if first_open else []),
        }
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        return Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )
