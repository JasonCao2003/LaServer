from flask import request, jsonify, Response
import json
import os
# User/model 供下面 validate_session/get_checkpoint_defs 使用(此前漏 import,
# 一旦被调用就会 NameError -> 500)。
from database.models import User

def set_joinable():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data:
            return Response(
                response=json.dumps({"error": "Request body is empty or not valid JSON"}),
                status=400,
                mimetype='application/json'
            )
        
        # 检查必需参数 'joinable'
        if 'joinable' not in request_data:
            return Response(
                response=json.dumps({"error": "Missing required parameter: joinable"}),
                status=400,
                mimetype='application/json'
            )
        
        # 获取 joinable 值
        joinable = request_data.get('joinable')
        
        response_body = {"joinable": joinable}
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        return Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype='application/json'
        )

def check_request_params(request_data, required_params):
    """
    检查请求参数是否包含所有必需的参数
    """
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
    """
    验证用户会话
    """
    user = User.query.filter_by(id=user_id, session=session_token).first()
    if not user:
        return {
            "status": 401,
            "result": {"error": "Invalid session or user not found"}
        }
    return None

def get_checkpoint_defs():
    """
    读取检查点定义文件
    """
    try:
        config_path = os.path.join('config', 'checkpoint_defs.json')
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        print(f"Error parsing checkpoint_defs.json: {e}")
        return []