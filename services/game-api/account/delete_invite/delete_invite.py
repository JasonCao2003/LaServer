from flask import request, jsonify, Response
from database.models import db, User, Invite
import json
import logging
import time

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def delete_invite():
    """
    删除邀请接口
    根据提供的请求响应示例重写，确保响应格式完全匹配
    """
    try:
        # ========== 1. 解析请求数据 ==========
        request_data = request.get_json(force=True, silent=True)
        
        if not request_data:
            return jsonify({
                "error": "Invalid request body",
                "message": "请求体为空"
            }), 400
        
        # ========== 2. 获取必需字段 ==========
        user_identifier = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        token_id = request_data.get('token_id')
        
        # ========== 3. 验证必需字段 ==========
        if not user_identifier:
            return jsonify({
                "error": "Missing required field: user or user_id",
                "message": "缺少用户标识字段"
            }), 400
        
        if not session_token:
            return jsonify({
                "error": "Missing required field: session",
                "message": "缺少会话令牌字段"
            }), 400
        
        if not token_id:
            return jsonify({
                "error": "Missing required field: token_id",
                "message": "缺少邀请令牌ID字段"
            }), 400
        
        # ========== 4. 验证用户身份 ==========
        user = get_cached_user(user_identifier, session_token)
        
        if not user:
            logger.warning(f"删除邀请时用户验证失败: user={user_identifier}, session={session_token[:8]}...")
            return jsonify({
                "error": "Invalid user session",
                "message": "无效的用户会话"
            }), 401
        
        # ========== 5. 查找要删除的邀请记录 ==========
        invite_to_delete = None
        try:
            invite_to_delete = Invite.query.filter_by(token_id=token_id).first()
        except Exception as db_error:
            logger.error(f"查询邀请记录失败: {str(db_error)}")
            return jsonify({
                "error": "Database error while searching for invite",
                "message": "查询邀请记录时数据库错误"
            }), 500
        
        if not invite_to_delete:
            return jsonify({
                "error": "Invite record not found",
                "message": "邀请记录不存在"
            }), 404
        
        # ========== 6. 验证用户权限 ==========
        if invite_to_delete.inviter_user_id != user.id:
            logger.warning(f"用户 {user.id} 尝试删除不属于自己的邀请: {token_id}，邀请者: {invite_to_delete.inviter_user_id}")
            return jsonify({
                "error": "Unauthorized to delete this invite",
                "message": "无权删除此邀请"
            }), 403
        
        if invite_to_delete.friend_id:
            logger.info(f"用户 {user.id} 删除已被使用的邀请: {token_id}，使用者: {invite_to_delete.friend_id}")
        
        # ========== 7. 执行删除操作 ==========
        try:
            deleted_invite_info = {
                "inviter_user_id": invite_to_delete.inviter_user_id,
                "token_id": invite_to_delete.token_id,
                "nickname": invite_to_delete.nickname,
                "friend_id": invite_to_delete.friend_id,
                "created": invite_to_delete.created
            }
            
            db.session.delete(invite_to_delete)
            db.session.commit()
            
            logger.info(f"用户 {user.id} 成功删除邀请: {token_id}")
            
        except Exception as db_error:
            logger.error(f"删除邀请记录失败: {str(db_error)}")
            db.session.rollback()
            return jsonify({
                "error": "Database error while deleting invite",
                "message": "删除邀请记录时数据库错误"
            }), 500
        
        # ========== 8. 获取用户剩余的所有邀请 ==========
        remaining_invites = []
        try:
            remaining_invites = Invite.query.filter_by(
                inviter_user_id=user.id
            ).order_by(Invite.created.desc()).all()
            
        except Exception as db_error:
            logger.error(f"查询剩余邀请失败: {str(db_error)}")
        
        # ========== 9. 构建响应数据 ==========
        invites_list = []
        for invite in remaining_invites:
            # 确保created是整数时间戳
            created_timestamp = invite.created
            if hasattr(created_timestamp, 'timestamp'):
                created_timestamp = int(created_timestamp.timestamp())
            
            invites_list.append({
                "inviter_user_id": invite.inviter_user_id,
                "token_id": invite.token_id,
                "nickname": invite.nickname,
                "friend_id": invite.friend_id,  # 注意：这是数据库中的实际值，可能是None或字符串
                "created": created_timestamp
            })
        
        response_data = {
            "invite_token_id": token_id,
            "invite_deleted": True,
            "invites": invites_list
        }
        
        logger.debug(f"删除邀请响应: {response_data}")
        
        # ========== 10. 返回响应 ==========
        return Response(
            response=json.dumps(response_data, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json',
            headers={
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache'
            }
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析错误: {str(e)}")
        return jsonify({
            "error": "Invalid JSON format",
            "message": "无效的JSON格式"
        }), 400
        
    except Exception as e:
        logger.error(f"删除邀请接口发生未捕获的异常: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "message": "服务器内部错误"
        }), 500