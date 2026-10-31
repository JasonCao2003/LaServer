from flask import request, Response
from infrastructure.database.models import db, User, FriendStatus, ServerAssignment, UserOnlineStatus
from datetime import datetime
import json
import time
import logging

logger = logging.getLogger(__name__)


def get_cached_user(user_id, session_token):
    """验证用户会话, 返回 User 或 None (与其它接口一致)。"""
    return User.query.filter_by(id=user_id, session=session_token).first()

def join_friend_game():
    """
    加入好友游戏接口
    原理：客户端请求加入特定好友的游戏实例，服务器验证用户会话后，
    查询好友的服务器分配记录，返回服务器连接信息供客户端连接
    """
    try:
        # ========== 1. 解析和验证请求数据 ==========
        
        # 强制解析JSON，确保即使Content-Type不正确也能处理
        request_data = request.get_json(force=True)
        
        if not request_data:
            logger.error("join_friend_game: 空请求体")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "请求体为空",
                    "code": "EMPTY_REQUEST_BODY"
                }),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        friend_id = request_data.get('friend_id')
        
        if not user_id:
            logger.error("join_friend_game: 缺少用户ID字段")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "缺少用户ID字段",
                    "code": "MISSING_USER_ID"
                }),
                status=400,
                mimetype='application/json'
            )
        
        if not session_token:
            logger.error(f"join_friend_game: 用户 {user_id} 缺少会话令牌")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "缺少会话令牌",
                    "code": "MISSING_SESSION_TOKEN"
                }),
                status=400,
                mimetype='application/json'
            )
        
        if not friend_id:
            logger.error(f"join_friend_game: 用户 {user_id} 缺少好友ID")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "缺少好友ID",
                    "code": "MISSING_FRIEND_ID"
                }),
                status=400,
                mimetype='application/json'
            )
        
        logger.info(f"join_friend_game: 用户 {user_id} 请求加入好友 {friend_id} 的游戏")
        
        # ========== 2. 验证用户会话 ==========
        
        user = get_cached_user(user_id, session_token)
        if not user:
            logger.warning(f"join_friend_game: 用户 {user_id} 会话无效或用户不存在")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "无效的会话或用户不存在",
                    "code": "INVALID_SESSION"
                }),
                status=401,
                mimetype='application/json'
            )
        
        # ========== 3. 验证好友存在和关系 ==========
        
        friend_user = User.query.filter_by(id=friend_id).first()
        if not friend_user:
            logger.warning(f"join_friend_game: 好友 {friend_id} 不存在")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "好友不存在",
                    "code": "FRIEND_NOT_FOUND"
                }),
                status=404,
                mimetype='application/json'
            )
        
        friendship = FriendStatus.query.filter_by(
            user_id=user_id,
            friend_id=friend_id
        ).first()
        
        # if not friendship:
        #     logger.warning(f"join_friend_game: 用户 {user_id} 和 {friend_id} 不是好友关系")
        #     return Response(
        #         response=json.dumps({
        #             "result": "error",
        #             "message": "不是好友关系，无法加入游戏",
        #             "code": "NOT_FRIENDS"
        #         status=403,
        #         mimetype='application/json'
        
        # ========== 4. 查询好友的服务器分配信息 ==========
        
        # 按 move_ts 降序排序，获取最新的服务器分配
        server_assignment = ServerAssignment.query.filter_by(
            user_id=friend_id
        ).order_by(
            ServerAssignment.move_ts.desc()
        ).first()
        
        if not server_assignment:
            logger.warning(f"join_friend_game: 好友 {friend_id} 没有服务器分配记录")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "好友当前不在游戏中",
                    "code": "FRIEND_NOT_IN_GAME"
                }),
                status=404,
                mimetype='application/json'
            )
        
        # ========== 5. 检查好友是否在线（可选） ==========
        
        friend_online_status = UserOnlineStatus.query.filter_by(
            user_id=friend_id,
            is_online=True
        ).first()
        
        # if not friend_online_status:
        #     logger.warning(f"join_friend_game: 好友 {friend_id} 不在线")
        #     return Response(
        #         response=json.dumps({
        #             "result": "error",
        #             "message": "好友当前不在线",
        #             "code": "FRIEND_OFFLINE"
        #         status=404,
        #         mimetype='application/json'
        
        # ========== 6. 创建新的服务器分配记录（为当前用户） ==========
        
        # 为了跟踪用户加入好友游戏的记录，可以创建一个新的服务器分配记录
        
        current_time_ms = int(time.time() * 1000)
        
        new_assignment = ServerAssignment(
            user_id=user_id,
            server_uri=server_assignment.server_uri,
            move_ts=current_time_ms,
            signature=server_assignment.signature,
            sig=server_assignment.sig,
            ps_only=server_assignment.ps_only,
            platform_filter=server_assignment.platform_filter
        )
        
        db.session.add(new_assignment)
        
        # ========== 7. 构建响应数据 ==========
        
        move_to_game_data = {
            "signature": server_assignment.signature,
            "sig": server_assignment.sig,
            "target_id": friend_id,  # 目标好友ID
            "client_changelist": request_data.get("client_changelist", "282913"),  # 客户端版本号
            "ps_only": server_assignment.ps_only,  # PlayStation独占标志
            "platform_filter": server_assignment.platform_filter,  # 平台过滤器
            "public_ip": request_data.get("public_ip", "0.0.0.0"),  # 公网IP
            "private_ip": request_data.get("private_ip", "127.0.0.1"),  # 内网IP
            "port": request_data.get("port", 42336),  # 端口号
            # 注意：level, level_hash 等字段通常由游戏服务器提供
            "level": request_data.get("level", 3526133726),  # 关卡ID
            "level_hash": request_data.get("level_hash", 30890),  # 关卡哈希
            "client_version": request_data.get("client_version", 5023),  # 客户端版本
            "players": [friend_id],  # 玩家列表（包含好友）
            "move_ts": current_time_ms,  # 移动时间戳
            "event_id": request_data.get("event_id", 0)  # 事件ID
        }
        
        response_data = {
            "move_to_game": move_to_game_data,
            "is_joinable": True,  # 表示可以加入
            "is_dnd": False  # 表示好友未开启免打扰
        }
        
        # ========== 8. 更新用户在线状态 ==========
        
        online_status = UserOnlineStatus.query.filter_by(
            user_id=user_id,
            friend_id=friend_id
        ).first()
        
        if online_status:
            online_status.is_online = True
            online_status.last_seen = datetime.utcnow()
        else:
            online_status = UserOnlineStatus(
                user_id=user_id,
                friend_id=friend_id,
                is_online=True,
                last_seen=datetime.utcnow()
            )
            db.session.add(online_status)
        
        # ========== 9. 发送WebSocket通知（可选） ==========
        
        # 如果好友在线且有WebSocket连接，可以发送通知
        try:
            for session_id, client_info in chat_clients.items():
                if client_info.get('user') == friend_id:
                    notification = {
                        "type": "friend_joining",
                        "from_user_id": user_id,
                        "from_user_name": user.device_name or "未知玩家",
                        "timestamp": current_time_ms,
                        "server_uri": server_assignment.server_uri
                    }
                    
                    # 发送通知到好友的WebSocket连接
                    socketio.emit('friend_notification', 
                                notification,
                                room=session_id,
                                namespace='/account/ws')
                    logger.info(f"join_friend_game: 已通知好友 {friend_id} 用户 {user_id} 正在加入")
                    break
        except Exception as ws_error:
            # WebSocket通知失败不影响主要功能
            logger.warning(f"join_friend_game: WebSocket通知失败: {ws_error}")
        
        # ========== 10. 提交数据库事务并返回响应 ==========
        
        db.session.commit()
        
        logger.info(f"join_friend_game: 用户 {user_id} 成功获取好友 {friend_id} 的服务器信息")
        
        return Response(
            response=json.dumps(response_data, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except json.JSONDecodeError as e:
        # JSON解析错误
        logger.error(f"join_friend_game: JSON解析错误: {e}")
        return Response(
            response=json.dumps({
                "result": "error",
                "message": "无效的JSON格式",
                "code": "INVALID_JSON",
                "details": str(e)
            }),
            status=400,
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"join_friend_game: 服务器内部错误: {e}", exc_info=True)
        
        db.session.rollback()
        
        return Response(
            response=json.dumps({
                "result": "error",
                "message": "服务器内部错误",
                "code": "INTERNAL_SERVER_ERROR"
            }),
            status=500,
            mimetype='application/json'
        )