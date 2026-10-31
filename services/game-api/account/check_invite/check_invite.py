from flask import request, jsonify, Response
from database.models import db, User, Invite
import json
import time
import logging

# 修复: 同 verify.py —— 原来用了 logger 却没定义, 成功分支必 NameError -> 500。
logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def check_invite():
    """
    检查邀请是否存在的接口 - 仅检查存在性，不检查使用状态
    核心原理：
    1. 验证用户的身份和会话有效性
    2. 通过token_id查找邀请记录
    3. 仅检查邀请是否存在，不检查是否已使用
    4. 返回邀请的基本信息，不包含使用状态
    """
    try:
        # ========== 1. 请求验证和解析 ==========
        request_data = request.get_json(force=True, silent=True)
        
        if not request_data:
            return jsonify({
                "error": "Invalid request body",
                "invite_result": "error",
                "invite_exists": False
            }), 400
        
        user_identifier = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        token_id = request_data.get('token_id')
        
        if not user_identifier or not session_token or not token_id:
            return jsonify({
                "error": "Missing required fields",
                "invite_result": "error",
                "invite_exists": False
            }), 400
        
        # ========== 2. 用户身份验证（宽松） ==========
        user = None
        
        try:
            user = get_cached_user(user_identifier, session_token)
        except Exception as auth_error:
            logger.warning(f"用户验证过程出现异常: {str(auth_error)}")
        
        if not user:
            try:
                user = User.query.filter_by(id=user_identifier, session=session_token).first()
            except Exception as db_error:
                logger.error(f"数据库查询用户失败: {str(db_error)}")
        
        if not user:
            logger.warning(f"用户验证失败或用户不存在: user={user_identifier}")
        
        # ========== 3. 查找邀请记录 ==========
        # 通过token_id查找邀请记录
        invite_record = None
        try:
            invite_record = Invite.query.filter_by(token_id=token_id).first()
        except Exception as db_error:
            logger.error(f"查询邀请记录失败: {str(db_error)}")
            return jsonify({
                "error": "Database error while searching for invite",
                "invite_result": "error",
                "invite_exists": False
            }), 500
        
        # ========== 4. 检查邀请是否存在 ==========
        if not invite_record:
            logger.warning(f"邀请令牌不存在: {token_id}")
            return jsonify({
                "error": "Invite token not found",
                "invite_result": "error",
                "invite_exists": False
            }), 404
        
        # ========== 5. 构建响应体（不包含使用状态） ==========
        response_data = {
            "invite_token_id": invite_record.token_id,
            "invite_exists": True,
            "invite_result": "ok"
        }
        
        response_data["invite_details"] = {
            "inviter_user_id": invite_record.inviter_user_id,
            "formatted_code": invite_record.formatted_code,
            "nickname": invite_record.nickname,
            "created_at": invite_record.created,
            # 特别注意：不返回friend_id字段
            "is_available": True,  # 总是返回可用，实际可用性由accept_invite接口判断
            "invite_age_seconds": int(time.time()) - invite_record.created
        }
        
        if user and invite_record.inviter_user_id == user.id:
            response_data["invite_details"]["is_own_invite"] = True
        
        response_data["checked_at"] = int(time.time())
        
        logger.info(f"邀请检查成功: token_id={token_id}，邀请者: {invite_record.inviter_user_id}")
        
        # ========== 6. 返回响应 ==========
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
        logger.error(f"检查邀请接口发生未捕获的异常: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "invite_result": "error",
            "invite_exists": False
        }), 500