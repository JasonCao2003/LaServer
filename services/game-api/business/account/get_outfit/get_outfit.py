from flask import request, jsonify, Response
from infrastructure.database.models import db, User, UserOutfit
import json

def get_outfit():
    try:
        request_data = request.get_json(force=True)
        
        # 去掉400验证
        user_id = request_data.get('user') if request_data else None
        session = request_data.get('session') if request_data else None
        
        user = User.query.get(user_id)
        if not user:
            return Response(
            response=json.dumps({"error": "User not found"}),
                status=401,
                mimetype='application/json'
            )
        
        # 这里添加获取outfit的代码
        
        user_outfit = UserOutfit.query.filter_by(user_id=user_id).first()
        
        if not user_outfit:
            user_outfit = UserOutfit(
                user_id=user_id,
                wing_tex=0,
                wing_pat=0,
                wing_mask=0,
                wing_id=2245279351,
                voice=9,
                seed=16515,
                scale=0.22549559,
                refreshversion=0,
                prop_tex=0,
                prop_pat=0,
                prop_mask=0,
                prop_id=2035109393,
                neck_tex=0,
                neck_pat=0,
                neck_mask=0,
                neck_id=3800884691,
                mask_tex=0,
                mask_pat=0,
                mask_mask=0,
                mask_id=3663390882,
                horn_tex=0,
                horn_pat=0,
                horn_mask=0,
                horn_id=3680499229,
                height=1.93536222,
                hat_tex=0,
                hat_pat=0,
                hat_mask=0,
                hat_id=1470462579,
                hair_tex=0,
                hair_pat=0,
                hair_mask=0,
                # 初始头发: CharSkyKid_Hair_BraidSideSmall (FNV-1a=1229053584)。
                # 这是正版新账号的初始发型。注意: 极老客户端(如 iOS 0.5.0)的
                # persistent.lua 可能缺该网格, 下发会触发 "Missing outfit mesh"
                # 断言闪退 —— 仅影响 <0.5 级别的远古客户端, 现行版本(0.26/0.30+)正常。
                hair_id=1229053584,
                feet_tex=0,
                feet_pat=0,
                feet_mask=0,
                feet_id=1532093990,
                face_tex=0,
                face_pat=0,
                face_mask=0,
                # 初始脸: CharSkyKid_Mask_None (无面具)。原值 4247878009 反查为
                # CharSkyKid_Mask_ElderRain_02 (长者面具), 会让新号脸上默认戴着长者面具。
                face_id=4267756678,
                body_tex=0,
                body_pat=0,
                body_mask=0,
                body_id=3691343236,
                attitude="0",
                arms_id=0
            )
            db.session.add(user_outfit)
            db.session.commit()
        
        response_body = {
            "result": "ok",
            "set_outfit": {
                "wing": {
                    "tex": user_outfit.wing_tex,
                    "pat": user_outfit.wing_pat,
                    "mask": user_outfit.wing_mask,
                    "id": user_outfit.wing_id
                },
                "voice": user_outfit.voice,
                "seed": user_outfit.seed,
                "scale": user_outfit.scale,
                "refreshversion": user_outfit.refreshversion,
                "prop": {
                    "tex": user_outfit.prop_tex,
                    "pat": user_outfit.prop_pat,
                    "mask": user_outfit.prop_mask,
                    "id": user_outfit.prop_id
                },
                "neck": {
                    "tex": user_outfit.neck_tex,
                    "pat": user_outfit.neck_pat,
                    "mask": user_outfit.neck_mask,
                    "id": user_outfit.neck_id
                },
                "mask": {
                    "tex": user_outfit.mask_tex,
                    "pat": user_outfit.mask_pat,
                    "mask": user_outfit.mask_mask,
                    "id": user_outfit.mask_id
                },
                "horn": {
                    "tex": user_outfit.horn_tex,
                    "pat": user_outfit.horn_pat,
                    "mask": user_outfit.horn_mask,
                    "id": user_outfit.horn_id
                },
                "height": user_outfit.height,
                "hat": {
                    "tex": user_outfit.hat_tex,
                    "pat": user_outfit.hat_pat,
                    "mask": user_outfit.hat_mask,
                    "id": user_outfit.hat_id
                },
                "hair": {
                    "tex": user_outfit.hair_tex,
                    "pat": user_outfit.hair_pat,
                    "mask": user_outfit.hair_mask,
                    "id": user_outfit.hair_id
                },
                "feet": {
                    "tex": user_outfit.feet_tex,
                    "pat": user_outfit.feet_pat,
                    "mask": user_outfit.feet_mask,
                    "id": user_outfit.feet_id
                },
                "face": {
                    "tex": user_outfit.face_tex,
                    "pat": user_outfit.face_pat,
                    "mask": user_outfit.face_mask,
                    "id": user_outfit.face_id
                },
                "body": {
                    "tex": user_outfit.body_tex,
                    "pat": user_outfit.body_pat,
                    "mask": user_outfit.body_mask,
                    "id": user_outfit.body_id
                },
                "attitude": user_outfit.attitude,
                "arms": {
                    "id": user_outfit.arms_id
                }
            }
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