# -*- coding: utf-8 -*-
"""
接口: /account/get_questionnaires
函数: get_questionnaires()
获取问卷调查
"""
from flask import request, jsonify

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    from database.models import User
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_questionnaires():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return jsonify({"result": "missing_parameters"}), 400
        
        user_id = request_data.get('user')
        session_token = request_data.get('session')
        
        user = get_cached_user(user_id, session_token)
        if not user:
            return jsonify({"result": "invalid_session"}), 401
        
        return jsonify({"result": "ok"}), 200
        
    except Exception as error:
        print(f"An error has occurred - {error}")
        import traceback
        traceback.print_exc()
        return jsonify({"result": "internal_error"}), 500
