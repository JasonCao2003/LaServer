from flask import request, jsonify
from infrastructure.database.models import User
import time
import logging
import traceback

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def upload_remote_config():
    """
    处理远程配置文件上传
    验证会话后返回成功响应
    """
    try:
        required_params = ['user', 'session']
        
        data = request.get_json(force=True, silent=True)
        
        if not data:
            return jsonify({
                "result": "error",
                "message": "请求体不能为空"
            }), 400
        
        missing_params = [param for param in required_params if param not in data]
        if missing_params:
            return jsonify({
                "result": "error",
                "message": f"缺少必需参数: {', '.join(missing_params)}"
            }), 400
        
        user_id = data.get('user')
        session_token = data.get('session')
        
        user = get_cached_user(user_id, session_token)
        if not user:
            return jsonify({
                "result": "error",
                "message": "无效的会话或用户不存在"
            }), 401
        
        if hasattr(user, 'is_banned') and user.is_banned == 1:
            current_time = int(time.time())
            ban_end_time = getattr(user, 'ban_end_time', 0)
            
            if ban_end_time == 0 or current_time < ban_end_time:
                return jsonify({
                    "result": "banned",
                    "message": "账号已被封禁"
                }), 403
        
        logger.info(f"用户 {user_id} 上传了远程配置文件")
        
        return jsonify({
            "result": "ok"
        }), 200
        
    except Exception as error:
        logger.error(f"处理远程配置文件上传时发生错误: {str(error)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "result": "internal_error",
            "message": "服务器内部错误"
        }), 500