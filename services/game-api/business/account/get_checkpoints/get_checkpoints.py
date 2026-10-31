# get_checkpoints.py
from flask import request, Response
from infrastructure.database.models import db, UserCheckpoint
import json

def check_request_params(request_data, required_params):
    if not request_data:
        return {
            "status": 400,
            "result": {"error": "Request body is empty or not valid JSON"}
        }
    missing_params = [param for param in required_params if param not in request_data]
    if missing_params:
        return {
            "status": 400,
            "result": {"error": f"Missing required parameters: {', '.join(missing_params)}"}
        }
    return None

def validate_session(user_id, session_token):
    from infrastructure.database.models import User  # 注意：要在函数内部导入，避免循环依赖
    user = User.query.filter_by(id=user_id, session=session_token).first()
    if not user:
        return {
            "status": 401,
            "result": {"error": "Invalid session or user not found"}
        }
    return None

# 然后继续原有的 get_checkpoints 函数...

def get_checkpoints():
    try:
        request_data = request.get_json(force=True)
        
        required_params = ['user', 'session']
        params_check = check_request_params(request_data, required_params)
        
        if params_check:
            return Response(
                response=json.dumps(params_check["result"]),
                status=params_check["status"],
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session_token = request_data.get('session')
        session_validation = validate_session(user_id, session_token)
        
        if session_validation:
            return Response(
                response=json.dumps(session_validation["result"]),
                status=session_validation["status"],
                mimetype='application/json'
            )
        
        user_checkpoint = UserCheckpoint.query.filter_by(user_id=user_id).first()
        
        if not user_checkpoint:
            user_checkpoint = UserCheckpoint(user_id=user_id, checkpoints='[]')
            db.session.add(user_checkpoint)
            db.session.commit()
            checkpoints_data = []
        else:
            checkpoints_data = json.loads(user_checkpoint.checkpoints)
        
        response_body = {'checkpoints': checkpoints_data}
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except json.JSONDecodeError as e:
        print(f"Error decoding checkpoints JSON: {e}")
        # 如果JSON解析失败，返回空数组
        response_body = {'checkpoints': []}
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"Error getting checkpoints: {e}")
        return Response(
            response=json.dumps({"error": "internal_error"}),
            status=500,
            mimetype='application/json'
        )