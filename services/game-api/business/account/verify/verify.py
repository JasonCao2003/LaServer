from flask import request, jsonify
from infrastructure.database.models import db, User, Invite
import logging

# 修复: 本模块多处调用 logger.info/warning/error, 但原来既没 import logging 也没定义
# logger -> 只要走到成功分支就 NameError, 被末尾 except 捕获后再次 NameError, 直接
# 抛 500 (Werkzeug 调试页 NameError: name 'logger' is not defined)。
# 客户端"输入好友码校验"正是打这个接口, 导致好友码在游戏里根本用不了。
logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def verify_invite_code():
    """
    验证邀请码接口 - 仅验证格式和存在性，不检查使用状态
    核心原理：
    1. 验证用户的身份和会话有效性
    2. 解析和验证邀请码格式
    3. 查找邀请码对应的邀请记录
    4. 仅检查邀请是否存在，不检查是否已使用
    5. 返回验证结果（但不建立朋友关系）
    """
    try:
        # ========== 1. 请求验证和解析 ==========
        request_data = request.get_json(force=True, silent=True)
        
        if not request_data:
            return jsonify({
                "error": "Invalid request body",
                "result": "error"
            }), 400
        
        user_id = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        invite_code = request_data.get('code')
        
        if not user_id or not session_token or not invite_code:
            return jsonify({
                "error": "Missing required fields",
                "result": "error"
            }), 400
        
        # ========== 2. 用户验证（宽松） ==========
        user = None
        
        try:
            user = get_cached_user(user_id, session_token)
        except:
            pass
        
        if not user:
            user = User.query.filter_by(id=user_id, session=session_token).first()
        
        if not user:
            return jsonify({
                "error": "User authentication failed",
                "result": "error"
            }), 401
        
        # ========== 3. 清理和验证邀请码格式 ==========
        invite_code = invite_code.strip().upper()
        
        # 验证格式：XXXX-XXXX-XXXX（X为大写字母或数字）
        import re
        if not re.match(r'^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$', invite_code):
            return jsonify({
                "error": "Invalid invite code format",
                "result": "error"
            }), 400
        
        # ========== 4. 查找邀请记录 ==========
        invite_record = Invite.query.filter_by(formatted_code=invite_code).first()
        
        if not invite_record:
            return jsonify({
                "error": "Invite code not found",
                "result": "error"
            }), 404
        
        # ========== 5. 检查邀请有效性（仅检查是否存在，不检查是否已使用）==========
        if invite_record.inviter_user_id == user.id:
            return jsonify({
                "error": "You cannot use your own invite code",
                "result": "error"
            }), 400
        
        # ========== 6. 构建成功响应 ==========
        response_body = {
            "result": "ok",
            "type": "i",  # 'i' 代表 invite
            "code": invite_record.token_id,  # 返回原始的token_id（UUID格式）
            "invite_info": {
                "inviter_user_id": invite_record.inviter_user_id,
                "formatted_code": invite_record.formatted_code,
                "nickname": invite_record.nickname,
                "created_at": invite_record.created,
                # 不返回friend_id字段，避免客户端知道是否已被使用
                # 只有accept_invite接口才能设置friend_id
            }
        }
        
        logger.info(f"邀请码验证成功: {invite_code}，邀请者: {invite_record.inviter_user_id}")
        
        return jsonify(response_body), 200
        
    except Exception as e:
        logger.error(f"Error in verify_invite_code: {str(e)}")
        return jsonify({
            "error": "Internal server error",
            "result": "error"
        }), 500