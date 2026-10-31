# -*- coding: utf-8 -*-
"""
接口: /account/auth/create
函数: create_account()
创建账号接口
"""
from flask import request, jsonify, Response
from infrastructure.database.models import db, User
import json
import uuid
import hashlib
import time

def parse_user_agent(user_agent):
    """解析User-Agent字符串，提取设备信息"""
    result = {
        'device_type': '未知',
        'device_model': '未知型号',
        'os': 'Unknown OS',
        'browser': ''
    }
    
    if not user_agent:
        return result
    
    user_agent_lower = user_agent.lower()
    
    if 'iphone' in user_agent_lower:
        result['device_type'] = 'iPhone'
        result['device_model'] = 'iPhone'
    elif 'ipad' in user_agent_lower:
        result['device_type'] = 'iPad'
        result['device_model'] = 'iPad'
    elif 'android' in user_agent_lower:
        result['device_type'] = 'Android'
        result['device_model'] = 'Android设备'
    elif 'windows phone' in user_agent_lower:
        result['device_type'] = 'Windows Phone'
        result['device_model'] = 'Windows Phone'
    elif 'windows' in user_agent_lower:
        result['device_type'] = 'Windows'
    elif 'macintosh' in user_agent_lower or 'mac os' in user_agent_lower or 'macos' in user_agent_lower:
        result['device_type'] = 'macOS'
    elif 'linux' in user_agent_lower:
        result['device_type'] = 'Linux'
    
    device_patterns = [
        ('samsung', 'sm-', '三星'),
        ('xiaomi', 'm210', '小米'),
        ('huawei', 'huawei', '华为'),
        ('honor', 'honor', '荣耀'),
        ('oppo', 'oppo', 'OPPO'),
        ('vivo', 'vivo', 'VIVO'),
        ('oneplus', 'oneplus', '一加'),
        ('pixel', 'pixel', 'Google Pixel')
    ]
    
    for brand, pattern, chinese_name in device_patterns:
        if pattern in user_agent_lower:
            result['device_model'] = f'{chinese_name}手机'
            break
    
    if 'ios' in user_agent_lower or 'iphone os' in user_agent_lower:
        import re
        ios_match = re.search(r'iphone os (\d+_\d+)', user_agent_lower)
        if ios_match:
            result['os'] = f'iOS {ios_match.group(1).replace("_", ".")}'
        else:
            result['os'] = 'iOS'
    
    # 检测Android版本
    if 'android' in user_agent_lower:
        import re
        android_match = re.search(r'android (\d+\.\d+)', user_agent_lower)
        if android_match:
            result['os'] = f'Android {android_match.group(1)}'
        else:
            result['os'] = 'Android'
    
    return result

def create_account():
    try:
        # 直接强制解析 JSON，兼容各种客户端
        data = request.get_json(force=True, silent=True)
        
        if data is None and request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid JSON format"}), 400
        elif data is None:
            return jsonify({"error": "Empty request body"}), 400
        
        required_keys = ["type", "device_name", "device_token", "production", "tos_version", "device_key", "sig_ts", "sig", "hashes"]
        
        missing_keys = [key for key in required_keys if key not in data]
        if missing_keys:
            return jsonify({
                "error": f"Missing required keys: {', '.join(missing_keys)}"
            }), 400
        
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')
        device_info = parse_user_agent(user_agent)
        device_name = data.get('device_name', 'Unknown Device')
        device_identifier = str(uuid.uuid4())
        
        user_id = str(uuid.uuid4())
        session = f"UNAUTH_{uuid.uuid4().hex}"
        rid = str(uuid.uuid4())
        recovery_token = hashlib.sha256(rid.encode('utf-8')).hexdigest()

        created_at = int(time.time())
        # 自动授权: 面板开启"自动授权"后, 开启时间点之后新建的账号自动授权并标记
        auto_authorized = 0
        remark = ''
        try:
            from business.helpers.auto_auth_helper import should_auto_authorize, AUTO_AUTH_MARK
            if should_auto_authorize(created_at):
                auto_authorized = 1
                remark = AUTO_AUTH_MARK
                # 自动授权后会话不应保留 UNAUTH_ 前缀, 否则登录流程仍按未授权处理
                session = uuid.uuid4().hex
        except Exception:
            pass

        new_user = User(
            id=user_id,
            device=device_name[:255],
            device_identifier=device_identifier,
            session=session,
            recovery_token=recovery_token,
            platform='',
            ip_address=ip_address,
            is_authorized=auto_authorized,
            remark=remark,
            created_at=created_at,
            device_name=device_name[:255],
            device_type=device_info.get('device_type', 'Unknown')[:50],
            device_model=device_info.get('device_model', 'Unknown Model')[:100],
            device_os=device_info.get('os', 'Unknown OS')[:50],
            user_agent=user_agent[:500]
        )

        db.session.add(new_user)
        db.session.commit()

        # 注册成功: 货币行按管理面板『账号默认数据』初始化(config/game_control.json ->
        # season.finalize_currency)。[2026-10-06 修正] 面板设的新号初始蜡烛/爱心
        # 以前从来没有落库, 新号进游戏永远是 0 蜡烛。
        try:
            from business.helpers.currency_helper import ensure_user_currency
            ensure_user_currency(user_id)
            db.session.commit()
        except Exception as _ce:
            db.session.rollback()
            print(f"初始化新号货币行失败 {user_id}: {_ce}")

        if auto_authorized:
            print(f"新用户注册成功(已自动授权)，用户ID: {user_id}")
        else:
            print(f"新用户注册成功，用户ID: {user_id}，等待授权")

        response_body = {
            "authinfo": {
                "user": new_user.id,
                "device": new_user.device_identifier,
                "key": "no_bcrypt",
                "recovery_token": new_user.recovery_token
            },
            "session": new_user.session,
            "resource_list": [
                "device_capabilities", "userdata", "set_friends", "set_friend_status", "online_friends",
                "set_random", "currency", "forge_rates", "motd", "outfit_defs", "achievements",
                "achievement_stats", "relationship_defs", "questionnaires", "event_schedule",
                "set_world_quests", "iap_list", "invite_list", "unlocks", "get_shop", "spirit_shops",
                "collectibles", "wing_buffs", "gift_messages", "set_app_badge_number", "get_consumable_defs",
                "get_consumables", "get_buff_defs", "get_buffs", "get_lootboxes", "checkpoints",
                "generic_shop_defs", "external_account_friends", "serendipity_matches", "get_star_tag_defs",
                "status_unlocks", "free_gifts", "get_map_defs", "achievement_defs", "achievement_stats_tracking",
                "event_currency_defs", "infractions", "messages", "external_links"
            ],
            "authorization_required": True,
            "message": "账号已创建，等待管理员授权后才能登录"
        }

        return Response(
            response=json.dumps(response_body, sort_keys=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"注册过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500
