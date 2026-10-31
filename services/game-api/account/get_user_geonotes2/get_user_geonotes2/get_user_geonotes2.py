from flask import request, Response
from database.models import db, User
import json
import time

def get_user_geonotes2():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"result": "invalid_params"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session_token = request_data.get('session')
        
        # 验证会话 - 使用你提供的User模型结构
        user = User.query.filter_by(id=user_id, session=session_token).first()
        if not user:
            return Response(
                response=json.dumps({"result": "invalid_session"}),
                status=401,
                mimetype='application/json'
            )
        
        current_time = int(time.time())
        if user.is_banned:
            # 如果ban_duration为0，表示永久封禁
            if user.ban_duration > 0:
                if current_time < user.ban_end_time:
                    return Response(
                        response=json.dumps({"result": "user_banned"}),
                        status=403,
                        mimetype='application/json'
                    )
                else:
                    user.is_banned = 0
                    user.ban_start_time = 0
                    user.ban_end_time = 0
                    db.session.commit()
            else:
                return Response(
                    response=json.dumps({"result": "user_permanently_banned"}),
                    status=403,
                    mimetype='application/json'
                )
        
        # 返回空数组数据（假设这是占位符，稍后会填充实际数据）
        response_body = {
            "geonotes": [],
            "emojis": [],
            "likes": [],
            "social": []
        }
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"An error has occurred in get_user_geonotes - {e}")
        return Response(
            response=json.dumps({"result": "internal_error"}),
            status=500,
            mimetype='application/json'
        )