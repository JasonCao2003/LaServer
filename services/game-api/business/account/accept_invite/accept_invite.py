from flask import request, jsonify, Response
from infrastructure.database.models import db, User, Invite, FriendStatus, Friendship
from datetime import datetime
import uuid
import json
import time
import logging

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取用户会话(本模块原来漏了这个函数, 但函数体里第一件事就是调它 ->
    NameError -> 任何"接受好友码"请求都直接 500 Internal server error,
    好友码永远加不上好友)。与其他 account 模块保持一致的实现。"""
    return User.query.filter_by(id=user_id, session=session_token).first()

def accept_invite():
    """
    接受邀请接口 - 唯一可以设置邀请码为已使用的接口
    核心原理：
    1. 验证用户的身份和会话有效性
    2. 通过token_id查找邀请记录
    3. 检查邀请是否存在且未被使用（重要：只有这里检查使用状态）
    4. 建立朋友关系，更新邀请记录为已使用
    5. 返回朋友关系详情和更新信息
    """
    try:
        # ========== 1. 请求验证和解析 ==========
        request_data = request.get_json(force=True, silent=True)
        
        if not request_data:
            return jsonify({
                "error": "Invalid request body",
                "invite_result": "error"
            }), 400
        
        user_identifier = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        token_id = request_data.get('token_id')
        invite_nickname = request_data.get('invite_nickname', '')
        
        if not user_identifier or not session_token or not token_id:
            return jsonify({
                "error": "Missing required fields",
                "invite_result": "error"
            }), 400
        
        # ========== 2. 用户身份验证 ==========
        user = get_cached_user(user_identifier, session_token)
        
        if not user:
            logger.warning(f"无效的用户会话: user={user_identifier}, session={session_token[:8]}...")
            return jsonify({
                "error": "Invalid user session",
                "invite_result": "error"
            }), 401
        
        if hasattr(user, 'is_banned') and user.is_banned == 1:
            current_time = int(time.time())
            ban_end_time = getattr(user, 'ban_end_time', 0)
            
            if ban_end_time == 0 or current_time < ban_end_time:
                logger.warning(f"被封禁用户尝试接受邀请: {user_identifier}")
                return jsonify({
                    "error": "User account is banned",
                    "invite_result": "error"
                }), 403
        
        # ========== 3. 查找邀请记录 ==========
        invite_record = None
        try:
            invite_record = Invite.query.filter_by(token_id=token_id).first()
        except Exception as db_error:
            logger.error(f"查询邀请记录失败: {str(db_error)}")
            return jsonify({
                "error": "Database error while searching for invite",
                "invite_result": "error"
            }), 500
        
        if not invite_record:
            logger.warning(f"邀请令牌不存在: {token_id}")
            return jsonify({
                "error": "Invite token not found",
                "invite_result": "error"
            }), 404
        
        # ========== 4. 检查邀请是否已被使用（重要：只有这里检查）==========
        if invite_record.friend_id:
            logger.warning(f"邀请已被使用: {token_id}，已被用户 {invite_record.friend_id} 使用")
            return jsonify({
                "error": "Invite has already been used",
                "invite_result": "error"
            }), 400
        
        if invite_record.inviter_user_id == user.id:
            logger.warning(f"用户尝试接受自己的邀请: {user.id}")
            return jsonify({
                "error": "You cannot accept your own invite",
                "invite_result": "error"
            }), 400
        
        current_time = int(time.time())
        invite_age = current_time - invite_record.created
        
        # 设置过期时间为7天
        EXPIRATION_TIME = 7 * 24 * 60 * 60
        if invite_age > EXPIRATION_TIME:
            logger.warning(f"邀请已过期: {token_id}，创建于 {invite_age} 秒前")
            return jsonify({
                "error": "Invite has expired",
                "invite_result": "error"
            }), 400
        
        # ========== 5. 获取邀请者信息 ==========
        inviter = User.query.filter_by(id=invite_record.inviter_user_id).first()
        if not inviter:
            logger.error(f"邀请者用户不存在: {invite_record.inviter_user_id}")
            return jsonify({
                "error": "Inviter user not found",
                "invite_result": "error"
            }), 404
        
        # ========== 6. 处理invite_nickname ==========
        friend_nickname = invite_nickname if invite_nickname else invite_record.nickname
        if not friend_nickname or friend_nickname.strip() == '':
            friend_nickname = "Friend"
        
        # ========== 7. 建立朋友关系并标记邀请为已使用 ==========
        try:
            db.session.begin_nested()
            
            # 7.1 更新邀请记录，标记为已使用（重要：只有这里设置friend_id）
            invite_record.friend_id = user.id  # 设置friend_id表示已使用
            invite_record.updated_at = datetime.utcnow()
            
            # 7.2 创建时间戳
            created_timestamp = int(time.time())
            current_datetime_str = datetime.utcnow().isoformat() + "Z"
            
            # 7.3 创建FriendStatus记录（双向）
            default_abilities = [19, 6, 9, 2, 27, 1, 10]
            
            # 创建从当前用户到邀请者的FriendStatus记录
            friend_status_to_inviter = FriendStatus(
                user_id=user.id,
                friend_id=inviter.id,
                given=0,
                recvd=1,
                nickname=friend_nickname,
                when_created=created_timestamp,
                level=1,
                abilities=json.dumps(default_abilities),
                hints=json.dumps([]),
                outfit=json.dumps({}),
                player_badge_type=0,
                local_soft_deleted=False,
                attitude="0"
            )
            db.session.add(friend_status_to_inviter)
            
            # 创建从邀请者到当前用户的FriendStatus记录
            default_friend_nickname = user.id[:8]
            
            friend_status_to_user = FriendStatus(
                user_id=inviter.id,
                friend_id=user.id,
                given=1,
                recvd=0,
                nickname=default_friend_nickname,
                when_created=created_timestamp,
                level=1,
                abilities=json.dumps(default_abilities),
                hints=json.dumps([]),
                outfit=json.dumps({}),
                player_badge_type=0,
                local_soft_deleted=False,
                attitude="0"
            )
            db.session.add(friend_status_to_user)
            
            # 7.4 创建Friendship记录（双向）
            friendship_to_inviter = Friendship(
                user_id=user.id,
                friend_id=inviter.id,
                status='active'
            )
            db.session.add(friendship_to_inviter)
            
            friendship_to_user = Friendship(
                user_id=inviter.id,
                friend_id=user.id,
                status='active'
            )
            db.session.add(friendship_to_user)
            
            # 7.5 提交所有数据库更改
            db.session.commit()
            
            logger.info(f"接受邀请成功并标记为已使用: 用户 {user.id} 接受了 {inviter.id} 的邀请，令牌: {token_id}")
            
        except Exception as db_error:
            logger.error(f"建立朋友关系失败: {str(db_error)}")
            db.session.rollback()
            return jsonify({
                "error": "Failed to establish friendship relationship",
                "invite_result": "error"
            }), 500
        
        # ========== 8. 构建响应体 ==========
        response_data = {
            "invite_token_id": token_id,
            "invite_accepted": True,
            "invite_result": "ok",
            "invite_friend_id": inviter.id
        }
        
        # 8.1 构建update_friends数组
        update_friends = [{
            "unlinked_star_sku": "",
            "given": 0,
            "recvd": 1,
            "level": 1,
            "abilities": default_abilities,
            "when_created": current_datetime_str,
            "friend_id": inviter.id,
            "last_seen_outfit": None,
            "nickname": friend_nickname,
            "soft_deleted": None,
            "they_soft_deleted": None
        }]
        
        response_data["update_friends"] = update_friends
        
        # 8.2 计算并设置朋友数量
        try:
            friend_count = FriendStatus.query.filter_by(user_id=user.id).count()
            response_data["set_friend_count"] = friend_count
        except Exception as e:
            logger.warning(f"获取朋友数量失败: {str(e)}")
            response_data["set_friend_count"] = 1
        
        # 8.3 更新在线朋友列表
        response_data["update_online_friends"] = []
        
        # 8.4 添加额外信息
        response_data["invite_details"] = {
            "inviter_user_id": invite_record.inviter_user_id,
            "formatted_code": invite_record.formatted_code,
            "original_nickname": invite_record.nickname,
            "custom_nickname": friend_nickname,
            "created_at": invite_record.created,
            "friend_created_at": created_timestamp,
            "is_used": True  # 明确标记为已使用
        }
        
        response_data["accepted_at"] = current_time
        response_data["server_time"] = current_time
        
        logger.info(f"接受邀请完成: 用户 {user.id} 与 {inviter.id} 成为朋友，邀请码标记为已使用")
        
        # ========== 9. 返回响应 ==========
        return Response(
            response=json.dumps(response_data, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json',
            headers={
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache'
            }
        )
        
    except Exception as e:
        logger.error(f"接受邀请接口发生未捕获的异常: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "invite_result": "error"
        }), 500