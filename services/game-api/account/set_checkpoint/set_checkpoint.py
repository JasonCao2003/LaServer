from flask import request, Response
from database.models import db, User, UserCheckpoint
import json
import os
import time

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
    from database.models import User  # 注意：要在函数内部导入，避免循环依赖
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
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        config_path = os.path.join(project_root, 'config', 'checkpoint_defs.json')
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        print(f"Error parsing checkpoint_defs.json: {e}")
        return []

def set_checkpoint():
    try:
        request_data = request.get_json(force=True)
        
        required_params = ['user', 'session', 'level_id', 'level_hash', 'bst_id', 'states']
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
        
        # 神坛存档: 直接保存客户端上报的真实位置, 不再强制改写 level_id/bst_id。
        # (旧版本这里有一大堆 level_id 映射, 会把玩家实际所在关卡改成别的关卡,
        #  导致"在 A 退出, 神坛存档却回到 B" 的串档问题。已移除。)
        level_id = request_data.get('level_id')
        level_hash = request_data.get('level_hash')
        bst_id = request_data.get('bst_id')
        states = request_data.get('states')

        checkpoint_defs = get_checkpoint_defs()
        
        user_checkpoint = UserCheckpoint.query.filter_by(user_id=user_id).first()
        
        if not user_checkpoint:
            user_checkpoint = UserCheckpoint(user_id=user_id, checkpoints='[]')
            db.session.add(user_checkpoint)
            db.session.commit()
        
        checkpoints = json.loads(user_checkpoint.checkpoints)
        
        # 查找要更新的检查点索引（使用映射后的level_id）
        updated_index = -1
        
        for i, checkpoint in enumerate(checkpoints):
            if checkpoint.get('level_id') == level_id:
                updated_index = i
                break
        
        # 计算最远的arc_order
        furthest_arc_order = -1
        for checkpoint in checkpoints:
            arc_order = checkpoint.get('arc_order', -1)
            if arc_order > furthest_arc_order:
                furthest_arc_order = arc_order
        
        if updated_index != -1:
            updated_checkpoint = checkpoints.pop(updated_index)
            updated_checkpoint.update({
                'user': user_id,
                'level_hash': level_hash,
                'bst_id': bst_id,
                'states': states
            })
            checkpoints.insert(0, updated_checkpoint)
        else:
            # 从定义中查找arc_order
            arc_order = -1
            for checkpoint_def in checkpoint_defs:
                if checkpoint_def.get('level_id') == level_id:
                    arc_order = checkpoint_def.get('arc_order', -1)
                    break
            
            is_furthest = False
            if arc_order > furthest_arc_order:
                for checkpoint in checkpoints:
                    checkpoint['is_furthest'] = False
                is_furthest = True
            
            new_checkpoint = {
                "user": user_id,
                "level_id": level_id,
                "level_hash": level_hash,
                "bst_id": bst_id,
                "states": states,
                "is_furthest": is_furthest,
                "arc_order": arc_order
            }
            
            checkpoints.insert(0, new_checkpoint)
        
        user_checkpoint.checkpoints = json.dumps(checkpoints)
        db.session.commit()
        
        response_body = {'checkpoints': checkpoints}
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"An error has occurred - {e}")
        return Response(
            response=json.dumps({"error": "internal_error"}),
            status=500,
            mimetype='application/json'
        )