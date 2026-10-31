# -*- coding: utf-8 -*-
"""
接口: /account/get_shop
函数: get_shop()
获取商店物品列表
"""
from flask import request, Response
from infrastructure.database.models import db, User, ShopItem
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_shop():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session = request_data.get('session')
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        shop_items_count = ShopItem.query.count()
        if shop_items_count == 0:
            shop_items_data = [
                {"name": "dayhub_body", "unlockid": 3691343236, "unlock_cost": 0, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_body", "unlockid": 4070169080, "unlock_cost": 1, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_body", "unlockid": 556395364, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_hair", "unlockid": 1229053584, "unlock_cost": 0, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_hair", "unlockid": 2802448856, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_hair", "unlockid": 651777506, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_hair", "unlockid": 310226317, "unlock_cost": 1, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_mask", "unlockid": 3663390882, "unlock_cost": 0, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_mask", "unlockid": 1600525874, "unlock_cost": 1, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_mask", "unlockid": 3207378178, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_wing", "unlockid": 2245279351, "unlock_cost": 0, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dayhub_wing", "unlockid": 4290017146, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dusk_gyard_hair_1", "unlockid": 2666640712, "unlock_cost": 6, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dusk_gyard_hair_1", "unlockid": 351601302, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dusk_gyard_hair_2", "unlockid": 1960678833, "unlock_cost": 10, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dusk_gyard_wing", "unlockid": 3466800656, "unlock_cost": 20, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dusk_hair", "unlockid": 1472657881, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dusk_hair", "unlockid": 3668577427, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dusk_mask", "unlockid": 3659135953, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "dusk_wing", "unlockid": 1676224199, "unlock_cost": 7, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "duskend_hair", "unlockid": 4055513070, "unlock_cost": 8, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "home_prop", "unlockid": 1064146256, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "home_prop", "unlockid": 2003040587, "unlock_cost": 6, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "home_prop", "unlockid": 3280753494, "unlock_cost": 18, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "home_prop", "unlockid": 2035109393, "unlock_cost": 0, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "home_prop", "unlockid": 1889256860, "unlock_cost": 11, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "home_prop", "unlockid": 992885953, "unlock_cost": 27, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "home_prop", "unlockid": 1021011859, "unlock_cost": 1, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "night_body", "unlockid": 4086661700, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "night_hair", "unlockid": 158418233, "unlock_cost": 5, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "night_hair", "unlockid": 392918653, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "night_mask", "unlockid": 1929978494, "unlock_cost": 5, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "night_wing", "unlockid": 1611389272, "unlock_cost": 8, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "nightend_hair", "unlockid": 1740771042, "unlock_cost": 8, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "nightend_mask", "unlockid": 4267756678, "unlock_cost": 8, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "nightend_wing", "unlockid": 4009091214, "unlock_cost": 10, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "orbitdawn_hair", "unlockid": 416680174, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "orbitday_hair", "unlockid": 2432602294, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "orbitdusk_hair", "unlockid": 2681369749, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "orbitnight_hair", "unlockid": 3721236258, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "orbitrain_hair", "unlockid": 1078517906, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "orbitsunset_hair", "unlockid": 777821397, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "orbitsunset_hair", "unlockid": 3381405437, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rain_body", "unlockid": 739857211, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rain_hair", "unlockid": 2784461785, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rain_hair", "unlockid": 21382025, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rain_hair", "unlockid": 189805609, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rain_mask", "unlockid": 252652358, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rainend_body", "unlockid": 2432891829, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rainend_hair", "unlockid": 766797080, "unlock_cost": 8, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rainend_mask", "unlockid": 2954671753, "unlock_cost": 8, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "rainend_wing", "unlockid": 3167707894, "unlock_cost": 6, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunset_body", "unlockid": 4145832489, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunset_hair", "unlockid": 3075031305, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunset_hair", "unlockid": 1091766677, "unlock_cost": 2, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunset_hair", "unlockid": 3437606455, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunset_mask", "unlockid": 3117037165, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunset_mask", "unlockid": 3066704308, "unlock_cost": 3, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunset_wing", "unlockid": 2038455273, "unlock_cost": 5, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunsetend_body", "unlockid": 971855908, "unlock_cost": 4, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunsetend_hair", "unlockid": 1966947567, "unlock_cost": 8, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0},
                {"name": "sunsetend_mask", "unlockid": 685288461, "unlock_cost": 6, "currency_type": "heart", "listed": True, "unlock_dep": 0, "visual_dep": 0, "x": 0, "y": 0}
            ]
            
            for item_data in shop_items_data:
                shop_item = ShopItem(**item_data)
                db.session.add(shop_item)
            
            db.session.commit()
        
        shop_items = ShopItem.query.filter_by(listed=True).all()
        
        shop_items_list = []
        for item in shop_items:
            shop_items_list.append({
                "name": item.name,
                "unlockid": item.unlockid,
                "unlock_cost": item.unlock_cost,
                "currency_type": item.currency_type,
                "listed": item.listed,
                "unlock_dep": item.unlock_dep,
                "visual_dep": item.visual_dep,
                "x": item.x,
                "y": item.y
            })
        
        response_body = {
            "get_shop": [shop_items_list]
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
