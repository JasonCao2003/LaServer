from flask import request, jsonify, Response
from database.models import db, User, UserOutfit
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def set_outfit():
    try:
        request_data = request.get_json(force=True)
        
        # 验证请求数据是否为 None
        if request_data is None:
            return Response(
                response=json.dumps({"error": "Request body is empty or not valid JSON"}),
                status=400,
                mimetype='application/json'
            )
        
        if not isinstance(request_data, dict):
            return Response(
                response=json.dumps({"error": "Invalid request format"}),
                status=400,
                mimetype='application/json'
            )
        
        # 获取 user_id（优先使用 user_id，如果没有则使用 user）
        user_id = request_data.get('user')
        if user_id is None:
            user_id = request_data.get('user')
        
        session = request_data.get('session')
        outfit_data = request_data.get('outfit')  # 注意：这里是 'outfit' 不是 'outfit_data'
        
        if not user_id or not session or not outfit_data:
            return Response(
                response=json.dumps({"error": "Missing required fields: user_id, session, or outfit"}),
                status=400,
                mimetype='application/json'
            )
        
        # 验证 outfit_data 是否为字典
        if not isinstance(outfit_data, dict):
            return Response(
                response=json.dumps({"error": "Invalid outfit data format"}),
                status=400,
                mimetype='application/json'
            )
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        user_outfit = UserOutfit.query.filter_by(user_id=user_id).first()
        
        if not user_outfit:
            user_outfit = UserOutfit(user_id=user_id)
            db.session.add(user_outfit)
        
        if 'body' in outfit_data and isinstance(outfit_data['body'], dict):
            body = outfit_data['body']
            user_outfit.body_tex = body.get('tex', 0)
            user_outfit.body_pat = body.get('pat', 0)
            user_outfit.body_mask = body.get('mask', 0)
            user_outfit.body_id = body.get('id', 3691343236)
        
        if 'wing' in outfit_data and isinstance(outfit_data['wing'], dict):
            wing = outfit_data['wing']
            user_outfit.wing_tex = wing.get('tex', 0)
            user_outfit.wing_pat = wing.get('pat', 0)
            user_outfit.wing_mask = wing.get('mask', 0)
            user_outfit.wing_id = wing.get('id', 2245279351)
        
        if 'hair' in outfit_data and isinstance(outfit_data['hair'], dict):
            hair = outfit_data['hair']
            user_outfit.hair_tex = hair.get('tex', 0)
            user_outfit.hair_pat = hair.get('pat', 0)
            user_outfit.hair_mask = hair.get('mask', 0)
            user_outfit.hair_id = hair.get('id', 1229053584)
        
        if 'mask' in outfit_data and isinstance(outfit_data['mask'], dict):
            mask = outfit_data['mask']
            user_outfit.mask_tex = mask.get('tex', 0)
            user_outfit.mask_pat = mask.get('pat', 0)
            user_outfit.mask_mask = mask.get('mask', 0)
            user_outfit.mask_id = mask.get('id', 3663390882)
        
        if 'neck' in outfit_data and isinstance(outfit_data['neck'], dict):
            neck = outfit_data['neck']
            user_outfit.neck_tex = neck.get('tex', 0)
            user_outfit.neck_pat = neck.get('pat', 0)
            user_outfit.neck_mask = neck.get('mask', 0)
            user_outfit.neck_id = neck.get('id', 3800884691)
        
        if 'feet' in outfit_data and isinstance(outfit_data['feet'], dict):
            feet = outfit_data['feet']
            user_outfit.feet_tex = feet.get('tex', 0)
            user_outfit.feet_pat = feet.get('pat', 0)
            user_outfit.feet_mask = feet.get('mask', 0)
            user_outfit.feet_id = feet.get('id', 1532093990)
        
        if 'horn' in outfit_data and isinstance(outfit_data['horn'], dict):
            horn = outfit_data['horn']
            user_outfit.horn_tex = horn.get('tex', 0)
            user_outfit.horn_pat = horn.get('pat', 0)
            user_outfit.horn_mask = horn.get('mask', 0)
            user_outfit.horn_id = horn.get('id', 3680499229)
        
        if 'arms' in outfit_data and isinstance(outfit_data['arms'], dict):
            arms = outfit_data['arms']
            user_outfit.arms_id = arms.get('id', 0)
            # 注意：原数据库中只有 arms_id 字段，没有 tex、pat、mask 字段
        
        if 'prop' in outfit_data and isinstance(outfit_data['prop'], dict):
            prop = outfit_data['prop']
            user_outfit.prop_tex = prop.get('tex', 0)
            user_outfit.prop_pat = prop.get('pat', 0)
            user_outfit.prop_mask = prop.get('mask', 0)
            user_outfit.prop_id = prop.get('id', 2035109393)
        
        if 'hat' in outfit_data and isinstance(outfit_data['hat'], dict):
            hat = outfit_data['hat']
            user_outfit.hat_tex = hat.get('tex', 0)
            user_outfit.hat_pat = hat.get('pat', 0)
            user_outfit.hat_mask = hat.get('mask', 0)
            user_outfit.hat_id = hat.get('id', 1470462579)
        
        if 'face' in outfit_data and isinstance(outfit_data['face'], dict):
            face = outfit_data['face']
            user_outfit.face_tex = face.get('tex', 0)
            user_outfit.face_pat = face.get('pat', 0)
            user_outfit.face_mask = face.get('mask', 0)
            user_outfit.face_id = face.get('id', 4267756678)
        
        if 'height' in outfit_data:
            user_outfit.height = outfit_data.get('height', 1.992823)
        
        if 'scale' in outfit_data:
            user_outfit.scale = outfit_data.get('scale', 0.083086)
        
        if 'voice' in outfit_data:
            user_outfit.voice = outfit_data.get('voice', 9)
        
        if 'attitude' in outfit_data:
            # 将 attitude 转换为字符串
            attitude_value = outfit_data.get('attitude', "0")
            user_outfit.attitude = str(attitude_value)
        
        if 'seed' in outfit_data:
            user_outfit.seed = outfit_data.get('seed', 16515)
        
        db.session.commit()
        
        response_body = {
            "result": "ok",
            "message": "Outfit updated successfully"
        }
        
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