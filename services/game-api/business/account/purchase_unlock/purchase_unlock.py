from flask import request, jsonify
from infrastructure.database.models import db, User, UserCurrency, UnlockItem
import os
import json
import time
import traceback
from business.helpers.emote_helper import is_emote_unlock, family_levels_for

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def purchase_unlock():
    try:
        data = request.get_json(force=True)
        
        required_params = ['user', 'session', 'cost']
        missing_params = [param for param in required_params if param not in data]
        if missing_params:
            return jsonify({'result': 'missing_parameters', 'missing': missing_params}), 400
        
        user_id = data.get('user')
        session_token = data.get('session')
        
        user = get_cached_user(user_id, session_token)
        if not user:
            return jsonify({'result': 'unauthorized'}), 401
        
        user_currency = UserCurrency.query.filter_by(user_id=user_id).first()
        if not user_currency:
            user_currency = UserCurrency(
                user_id=user_id,
                candles=0,
                heart=0,
                heart_wax=0,
                prestige=0,
                prestige_vip=0,
                prestige_wax=0,
                season_candle=0,
                season_wax=0,
                season_wax_14=0,
                vip=0,
                wax=3
            )
            db.session.add(user_currency)
            db.session.commit()
        
        user_unlocks = UnlockItem.query.filter_by(user_id=user_id).all()
        unlocks_dict = {unlock.name: unlock for unlock in user_unlocks}
        
        if 'name' in data and 'type' in data and 'ack' in data and 'cost' in data:
            item_name = data.get('name')
            item_type = data.get('type')
            item_ack = data.get('ack')
            item_cost = data.get('cost')
            
            if item_name in unlocks_dict:
                return jsonify({'result': 'already'}), 200
            
            if user_currency.candles < item_cost:
                return jsonify({'result': 'insufficient_currency'}), 400
            
            user_currency.candles -= item_cost
            
            current_time = int(time.time())
            new_unlock = UnlockItem(
                user_id=user_id,
                name=item_name,
                unlock_type=item_type,
                ack=item_ack,
                created_at=current_time
            )
            db.session.add(new_unlock)
            
            # 特殊处理：cape 解锁
            update_unlocks = [{
                "name": item_name,
                "created_at": current_time,
                "ack": item_ack,
                "type": item_type
            }]
            
            # 需求：解锁动作时直接开到最高级，自动补齐该动作族其余等级
            if is_emote_unlock(item_name):
                _owned = set(unlocks_dict.keys())
                _owned.add(item_name)
                for _lvl in family_levels_for(item_name):
                    if _lvl in _owned:
                        continue
                    db.session.add(UnlockItem(
                        user_id=user_id,
                        name=_lvl,
                        unlock_type=item_type,
                        ack=item_ack,
                        created_at=current_time
                    ))
                    _owned.add(_lvl)
                    update_unlocks.append({
                        "name": _lvl,
                        "created_at": current_time,
                        "ack": item_ack,
                        "type": item_type
                    })
            
            if item_name == "cape" and "GainedFlame" not in unlocks_dict:
                # 添加 GainedFlame 解锁
                flame_unlock = UnlockItem(
                    user_id=user_id,
                    name="GainedFlame",
                    unlock_type="level",
                    ack=False,
                    created_at=current_time
                )
                db.session.add(flame_unlock)
                update_unlocks.append({
                    "name": "GainedFlame",
                    "created_at": current_time,
                    "ack": False,
                    "type": "level"
                })
            
            # 特殊处理：SkyHubFirstArrival 解锁
            if item_name == "SkyHubFirstArrival" and "HubStatueForm" not in unlocks_dict:
                # 添加 HubStatueForm 解锁
                hub_unlock = UnlockItem(
                    user_id=user_id,
                    name="HubStatueForm",
                    unlock_type="level",
                    ack=False,
                    created_at=current_time
                )
                db.session.add(hub_unlock)
                update_unlocks.append({
                    "name": "HubStatueForm",
                    "created_at": current_time,
                    "ack": False,
                    "type": "level"
                })
                
                try:
                    project_root = os.path.dirname(os.path.dirname(current_dir))
                    live_config_path = os.path.join(project_root, 'config', 'live.json')
                    beta_config_path = os.path.join(project_root, 'config', 'beta.json')
                    
                    pass
                except Exception as config_error:
                    print(f"更新配置文件时出错: {config_error}")
            
            db.session.commit()
            
            response_data = {
                'result': 'ok',
                'currency': {
                    'candles': user_currency.candles,
                    'heart': user_currency.heart,
                    'heart_wax': user_currency.heart_wax,
                    'prestige': user_currency.prestige,
                    'prestige_vip': user_currency.prestige_vip,
                    'prestige_wax': user_currency.prestige_wax,
                    'season_candle': user_currency.season_candle,
                    'season_wax': user_currency.season_wax,
                    'season_wax_14': user_currency.season_wax_14,
                    'vip': user_currency.vip,
                    'wax': user_currency.wax
                },
                'update_unlocks': update_unlocks
            }
            
            return jsonify(response_data), 200
            
        elif 'unlock_id' in data:
            unlock_id = data.get('unlock_id')
            
            project_root = os.path.dirname(os.path.dirname(current_dir))
            shops_path = os.path.join(project_root, 'config', 'shops.json')
            
            try:
                with open(shops_path, 'r', encoding='utf-8') as f:
                    shops_data = json.load(f)
            except Exception as e:
                print(f"读取商店定义文件失败: {e}")
                return jsonify({'result': 'shop_def_not_found'}), 500
            
            unlock_item = None
            for item in shops_data:
                if item.get('unlockid') == unlock_id:
                    unlock_item = item
                    break
            
            if not unlock_item:
                return jsonify({'result': 'item_not_found'}), 404
            
            item_name = unlock_item.get('name')
            currency_type = unlock_item.get('currency_type')
            unlock_cost = unlock_item.get('unlock_cost', 0)
            
            if item_name in unlocks_dict:
                return jsonify({'result': 'already'}), 200
            
            currency_value = getattr(user_currency, currency_type, 0)
            if currency_value < unlock_cost:
                return jsonify({'result': 'insufficient_currency'}), 400
            
            setattr(user_currency, currency_type, currency_value - unlock_cost)
            
            current_time = int(time.time())
            new_unlock = UnlockItem(
                user_id=user_id,
                name=item_name,
                unlock_type="shop",
                ack=False,
                created_at=current_time
            )
            db.session.add(new_unlock)
            
            update_unlocks = [{
                "name": item_name,
                "created_at": current_time,
                "ack": False,
                "type": "shop"
            }]
            
            # 需求：解锁动作时直接开到最高级，自动补齐该动作族其余等级
            if is_emote_unlock(item_name):
                _owned = set(unlocks_dict.keys())
                _owned.add(item_name)
                for _lvl in family_levels_for(item_name):
                    if _lvl in _owned:
                        continue
                    db.session.add(UnlockItem(
                        user_id=user_id,
                        name=_lvl,
                        unlock_type="shop",
                        ack=False,
                        created_at=current_time
                    ))
                    _owned.add(_lvl)
                    update_unlocks.append({
                        "name": _lvl,
                        "created_at": current_time,
                        "ack": False,
                        "type": "shop"
                    })
            
            db.session.commit()
            
            response_data = {
                'result': 'ok',
                'currency': {
                    'candles': user_currency.candles,
                    'heart': user_currency.heart,
                    'heart_wax': user_currency.heart_wax,
                    'prestige': user_currency.prestige,
                    'prestige_vip': user_currency.prestige_vip,
                    'prestige_wax': user_currency.prestige_wax,
                    'season_candle': user_currency.season_candle,
                    'season_wax': user_currency.season_wax,
                    'season_wax_14': user_currency.season_wax_14,
                    'vip': user_currency.vip,
                    'wax': user_currency.wax
                },
                'update_unlocks': update_unlocks
            }
            
            return jsonify(response_data), 200
            
        else:
            return jsonify({'result': 'invalid_request'}), 400
            
    except Exception as e:
        print(f"购买解锁时发生错误: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'result': 'internal_error'}), 500