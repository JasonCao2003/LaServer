from flask import request, Response
from database.models import db, UserCurrency, ForgeRate, UserPickup
import json
import os

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

def collect_pickup_batch():
    try:
        request_data = request.get_json(force=True)
        
        required_params = ['user', 'session', 'level_id', 'pickup_ids']
        params_check = check_request_params(request_data, required_params)
        
        if params_check:
            return Response(
                response=json.dumps({"result": params_check["result"]}),
                status=params_check["status"],
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session_token = request_data.get('session')
        session_validation = validate_session(user_id, session_token)
        
        if session_validation:
            return Response(
                response=json.dumps({"result": session_validation["result"]}),
                status=session_validation["status"],
                mimetype='application/json'
            )
        
        user_currency = UserCurrency.query.filter_by(user_id=user_id).first()
        
        user_forge_rates = ForgeRate.query.filter_by(user_id=user_id).all()
        
        user_pickup = UserPickup.query.filter_by(user_id=user_id).first()
        
        # 如果用户没有货币记录，返回404
        if not user_currency:
            return Response(
                response=json.dumps({"result": "not_found"}),
                status=404,
                mimetype='application/json'
            )
        
        forge_rates_list = []
        for forge_rate in user_forge_rates:
            forge_rates_list.append({
                "source_type": forge_rate.source_type,
                "current_tier": forge_rate.current_tier,
                "current_level": forge_rate.current_level,
                "tier_completion": forge_rate.tier_completion,
                "level_completion": forge_rate.level_completion,
                "level_tier_index": forge_rate.level_tier_index,
                "level_tier_count": forge_rate.level_tier_count,
                "reset_acknowledged": forge_rate.reset_acknowledged,
                "enabled": forge_rate.enabled
            })
        
        # 将第一个锻造率（wax）的reset_acknowledged设置为True
        if forge_rates_list and len(forge_rates_list) > 0:
            forge_rates_list[0]["reset_acknowledged"] = True
        
        pickups = []
        if user_pickup and user_pickup.pickups:
            try:
                pickups = json.loads(user_pickup.pickups)
            except json.JSONDecodeError:
                pickups = []
        
        # 获取level_id
        level_id = request_data.get('level_id')
        
        # 查找对应的level_pickups
        level_pickups = None
        for pickup in pickups:
            if pickup.get("level") == level_id:
                level_pickups = pickup
                break
        
        # 如果找不到，从emitters.json文件中读取
        if not level_pickups:
            try:
                # 读取emitters.json文件
                config_path = os.path.join('config', 'emitters.json')
                if os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        emitters_data = json.load(f)
                    
                    # 查找对应level的emitters
                    emitters = None
                    for element in emitters_data:
                        if element.get("level") == level_id:
                            emitters = element
                            break
                    
                    # 创建新的level_pickups
                    level_pickups = {
                        "global_pickups": [],
                        "pickups": [],
                        "level": level_id,
                        "pickup_emitters": emitters.get("pickup_emitters", []) if emitters else []
                    }
                    
                    # 添加到pickups列表
                    pickups.append(level_pickups)
            except Exception as e:
                print(f"Error reading emitters.json: {e}")
                # 创建默认的level_pickups
                level_pickups = {
                    "global_pickups": [],
                    "pickups": [],
                    "level": level_id,
                    "pickup_emitters": []
                }
                pickups.append(level_pickups)
        
        # 处理pickup_ids
        pickup_ids = request_data.get('pickup_ids', [])
        for pickup_id in pickup_ids:
            # 增加wax
            user_currency.wax += 4
            
            # 检查是否已存在该pickup（注释掉的逻辑）
            # if not level_pickups.get("pickups") or not any(p.get("id") == pickup_id for p in level_pickups.get("pickups", [])):
            #     level_pickups["pickups"].append({
            #         "id": pickup_id,
            #         "wait_sec": 0
        
        # 处理global_pickup_ids（如果存在）
        global_pickup_ids = request_data.get('global_pickup_ids', [])
        for global_pickup_id in global_pickup_ids:
            if global_pickup_id == 4180017154:
                user_currency.season_candle += 1
            else:
                user_currency.wax += 4
            
            # 检查是否已存在该global_pickup（注释掉的逻辑）
            # if not level_pickups.get("global_pickups") or not any(p.get("id") == global_pickup_id for p in level_pickups.get("global_pickups", [])):
            #     level_pickups["global_pickups"].append({
            #         "id": global_pickup_id,
            #         "wait_sec": 0
        
        # 更新UserCurrency
        db.session.add(user_currency)
        
        # 更新ForgeRate（只更新第一个）
        if user_forge_rates and len(user_forge_rates) > 0:
            user_forge_rates[0].reset_acknowledged = True
            db.session.add(user_forge_rates[0])
        
        # 更新UserPickup
        if not user_pickup:
            user_pickup = UserPickup(user_id=user_id)
            db.session.add(user_pickup)
        
        user_pickup.pickups = json.dumps(pickups)
        
        db.session.commit()
        
        # 构建currency响应
        currency_response = {
            "candles": user_currency.candles,
            "heart": user_currency.heart,
            "heart_wax": user_currency.heart_wax,
            "prestige": user_currency.prestige,
            "prestige_vip": user_currency.prestige_vip,
            "prestige_wax": user_currency.prestige_wax,
            "season_candle": user_currency.season_candle,
            "season_wax": user_currency.season_wax,
            "season_wax_14": user_currency.season_wax_14,
            "vip": user_currency.vip,
            "wax": user_currency.wax
        }
        
        response_body = {
            "currency": currency_response,
            "level_id": level_id,
            "update_forge_rates": forge_rates_list
        }
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"An error has occurred: {e}")
        return Response(
            response=json.dumps({"result": "error"}),
            status=500,
            mimetype='application/json'
        )