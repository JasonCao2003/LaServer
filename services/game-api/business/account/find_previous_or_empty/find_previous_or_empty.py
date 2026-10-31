from flask import request, jsonify, Response
from infrastructure.database.models import db, User, ServerAssignment, GameServer
import json
import re
import uuid
import time
import os
import binascii
import base64
import logging
from business.helpers.room_helper import (assign_room_for_user, ensure_idle_rooms,
                         downgrade_response_for_old_client, NEW_CLIENT_MIN,
                         single_shared_room_response)

logger = logging.getLogger(__name__)


def _client_version():
    """从 User-Agent 解析客户端版本元组, 如 0.13.4 -> (0,13,4)。解析失败返回 None。"""
    ua = request.headers.get("User-Agent", "") if request else ""
    m = re.search(r"Sky-[^/\s]+/(\d+)\.(\d+)\.(\d+)", ua)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def find_previous_or_empty():
    try:
        # 客户端发的是 Content-Type: text/plain(body 其实是 JSON), 必须用 force=True
        # 解析, 否则 Flask 会抛 415 Unsupported Media Type。silent 防止空体直接抛异常。
        request_data = request.get_json(force=True, silent=True)

        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )

        user_id = request_data.get('user')
        session = request_data.get('session')
        server_version = request_data.get('server_version')
        client_version = request_data.get('client_version')
        force_empty = request_data.get('force_empty', False)
        level_hash = request_data.get('level_hash')

        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )

        # ========== 新增：检查用户是否被封禁 ==========
        if user.is_banned == 1:
            current_time = int(time.time())
            ban_end_time = user.ban_end_time

            if ban_end_time == 0 or current_time < ban_end_time:
                ban_info = {
                    "baninfo": {
                        "user_id": user.id,
                        "end_date": ban_end_time,
                        "type": "account"
                    },
                    "user": user.id,
                    "end_date": ban_end_time,
                    "message": "账号已被封禁"
                }
                if ban_end_time > 0:
                    remaining = ban_end_time - current_time
                    ban_info["baninfo"]["remaining_seconds"] = remaining
                    ban_info["baninfo"]["remaining_days"] = remaining // 86400

                return Response(
                    response=json.dumps(ban_info, ensure_ascii=False),
                    status=406,
                    mimetype='application/json'
                )
            else:
                user.is_banned = 0
                user.ban_duration = 0
                user.ban_start_time = 0
                user.ban_end_time = 0
                db.session.commit()
        # ========== 封禁检查结束 ==========

        # ========== 房间分配 ==========
        # 「人人同房」模式(默认): 直接把所有玩家指向同一个联机 ENet/UDP 服务器地址
        # (config/room_config.json 的 ip_host:realtime_port, 须与 udp源码 ROOM_PORT 一致)。
        # 所有玩家连进同一个 ENet 房间、彼此可见。不依赖数据库房间池。
        single = single_shared_room_response()
        if single is not None:
            # uri 已是短 IPv4:端口, 直接返回(客户端实时层只认 IPv4 字面量)。
            return Response(
                response=json.dumps(single, sort_keys=False, ensure_ascii=False),
                status=200,
                mimetype='application/json'
            )

        # ========== 旧房间池分配(single_shared_room=false 时回退) ==========
        # 规则: 启动时建 3 个空闲房间; 玩家加入某房间就再补一个, 始终保持 3 个空闲。
        # force_empty=True -> 强制开新空闲房间, 不复用上次房间。
        # 公网对外使用动态隧道地址: 房间 host 取当前隧道公网, 停服换隧道后自动同步。
        # 这里先保证池里有 3 个空闲房间(冷启动/被清空时自愈), 再分配。
        ensure_idle_rooms(db, GameServer, ServerAssignment)
        response_body = assign_room_for_user(
            db, GameServer, ServerAssignment, user_id,
            force_empty=bool(force_empty))

        if not response_body:
            # 没有可用房间(补池也失败), 按官方语义返回空对象。
            return Response(
                response=json.dumps({}),
                status=200,
                mimetype='application/json'
            )

        # 房间 host 一律改写成短 IPv4(ip_host)。
        # 客户端实时层断言 strlen(host) < INET_ADDRSTRLEN(=16), 即房间 host 只能是
        # IPv4 字面量, 这与客户端版本无关(0.33.7 也崩)。隧道域名既超长会触发断言,
        # trycloudflare 隧道也只代理 HTTPS、带不动裸 UDP/TCP 实时流量, 故房间 host
        # 绝不能用隧道域名。无条件降级为 ip_host:端口。
        response_body = downgrade_response_for_old_client(response_body)

        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )

    except Exception as e:
        logger.exception("find_previous_or_empty 异常")
        return Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype='application/json'
        )