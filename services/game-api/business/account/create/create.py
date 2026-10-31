# -*- coding: utf-8 -*-
"""
接口: /account/create
函数: account_create()
创建账号接口（备用版本）
"""
from flask import request, jsonify, Response
from infrastructure.database.models import db, User
import json
import uuid
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
    
    if 'android' in user_agent_lower:
        import re
        android_match = re.search(r'android (\d+\.\d+)', user_agent_lower)
        if android_match:
            result['os'] = f'Android {android_match.group(1)}'
        else:
            result['os'] = 'Android'
    
    return result

def account_create():
    try:
        data = request.get_json(force=True, silent=True)
        
        if data is None and request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid JSON format"}), 400
        elif data is None:
            return jsonify({"error": "Empty request body"}), 400

        # 0.5.0 以下老客户端(0.1.8/0.2.x/0.3.x) create 只发 device_name(+link_external),
        # 这里按协议补齐缺失字段; 新客户端原样通过。反编译依据见 legacy_client_compat.py。
        try:
            from business.helpers.legacy_client_compat import is_legacy_request, fill_create_request, looks_like_legacy_create
            if is_legacy_request() or looks_like_legacy_create(data):
                data = fill_create_request(data)
        except Exception as _legacy_err:
            print(f"老客户端 create 兼容处理失败(继续走原逻辑): {_legacy_err}")

        required_keys = ["device_name", "language", "tos_version", "link_external", 
                        "device_key", "sig_ts", "sig", "hashes"]
        
        missing_keys = [key for key in required_keys if key not in data]
        if missing_keys:
            return jsonify({
                "error": f"Missing required keys: {', '.join(missing_keys)}"
            }), 400
        
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')
        device_info = parse_user_agent(user_agent)
        
        device_name = data.get('device_name', 'NO_DEVICE_NAME')
        if device_name == 'NO_DEVICE_NAME':
            device_name = device_info.get('device_type', 'Unknown Device')
        
        device_identifier = uuid.uuid4().hex
        user_id = str(uuid.uuid4())
        session = uuid.uuid4().hex
        recovery_token = uuid.uuid4().hex + uuid.uuid4().hex
        
        print(f"开始创建用户，用户ID: {user_id}，设备标识符: {device_identifier}")
        
        existing_user = User.query.filter_by(id=user_id).first()
        if existing_user:
            print(f"警告：用户ID {user_id} 已存在，生成新的用户ID")
            user_id = str(uuid.uuid4())

        created_at = int(time.time())
        # 自动授权: 面板开启"自动授权"后, 开启时间点之后新建的账号自动授权并标记
        auto_authorized = 0
        remark = ''
        try:
            from business.helpers.auto_auth_helper import should_auto_authorize, AUTO_AUTH_MARK
            if should_auto_authorize(created_at):
                auto_authorized = 1
                remark = AUTO_AUTH_MARK
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
            device_name=device_name[:255],
            device_type=device_info.get('device_type', 'Unknown')[:50],
            device_model=device_info.get('device_model', 'Unknown Model')[:100],
            device_os=device_info.get('os', 'Unknown OS')[:50],
            user_agent=user_agent[:500],
            is_authorized=auto_authorized,
            remark=remark,
            created_at=created_at
        )

        try:
            db.session.add(new_user)
            db.session.commit()
            # 发新号: 货币行按管理面板『账号默认数据』初始化(config/game_control.json ->
            # season.finalize_currency)。[2026-10-06 修正] 以前没有这一步, 面板设的
            # 新号初始蜡烛/爱心不生效。
            try:
                from business.helpers.currency_helper import ensure_user_currency
                ensure_user_currency(user_id)
                db.session.commit()
            except Exception as _ce:
                db.session.rollback()
                print(f"初始化新号货币行失败 {user_id}: {_ce}")
            _auth_txt = "已自动授权" if auto_authorized else "未授权"
            print(f"用户 {user_id} 已成功添加到数据库，授权状态：{_auth_txt}")

            saved_user = User.query.filter_by(id=user_id).first()
            if saved_user:
                print(f"数据库验证成功：用户 {user_id} 已保存")
            else:
                print(f"数据库验证失败：用户 {user_id} 未找到")
                return jsonify({"error": "用户创建失败，数据库保存异常"}), 500
                
        except Exception as db_error:
            db.session.rollback()
            print(f"数据库操作失败: {str(db_error)}")
            import traceback
            traceback.print_exc()
            return jsonify({"error": f"数据库保存失败: {str(db_error)}"}), 500
        
        response_body = {
            "user": new_user.id,
            "device": device_identifier,
            "key": "0000000000000000000000000000000000000000000000000000000000000000",
            "session": session,
            "resource_list": [
                "device_capabilities",
                "userdata",
                "set_friends",
                "set_friend_statues",
                "online_friends",
                "set_rank",
                "currency",
                "forge_rates",
                "motd",
                "outfit_defs",
                "achievements",
                "achievement_stats",
                "relationship_defs",
                "questionnaires",
                "event_schedule",
                "set_world_quests",
                "iap_list",
                "invite_list",
                "unlocks",
                "get_shop",
                "spirit_shops",
                "collectibles",
                "wing_buffs",
                "gift_messages",
                "set_app_badge_number",
                "get_consumable_defs",
                "get_consumables",
                "get_buff_defs",
                "get_buffs",
                "get_lootboxes",
                "checkpoints",
                "generic_shop_defs",
                "external_account_friends",
                "serendipity_matches",
                "get_star_tag_defs",
                "status_unlocks",
                "free_gifts",
                "get_map_defs",
                "achievement_defs",
                "achievement_stats_tracking",
                "event_currency_defs",
                "infractions",
                "messages"
            ]
        }

        # 老客户端校验登录响应字段(见 legacy_client_compat.py 顶部反编译说明)
        try:
            from business.helpers.legacy_client_compat import is_legacy_request, augment_auth_response
            if is_legacy_request():
                response_body = augment_auth_response(response_body, new_user, device_identifier)
        except Exception as _legacy_err:
            print(f"老客户端 create 响应兼容处理失败(继续原响应): {_legacy_err}")

        return Response(
            response=json.dumps(response_body, sort_keys=False, indent=None),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"注册过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500
