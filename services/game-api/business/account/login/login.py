# -*- coding: utf-8 -*-
"""
接口: /account/login
函数: account_login()
用户登录接口 - 增强版，包含授权和封号判定

2026-10-06 修正(低版本"无库就建号"不生效):
  老客户端(0.6.5/0.7.1/0.12.2 等)重登走的是 /account/login, 不是
  /account/auth/login。之前"无库就建号"(auto_create_account)只加在 auth_login.py
  里, 所以清空账号库后用老客户端登录, 这里直接回 404 User not found, 客户端反复
  重试也进不去(实测日志: "POST /account/login HTTP/1.1" 404 连刷)。
  现在这里复用 auth_login 里那套同 id 补号逻辑, 两个入口行为一致。
"""
from flask import request, jsonify, Response
from infrastructure.database.models import db, User
import json
import uuid
import time

def parse_user_agent(user_agent):
    """解析User-Agent字符串"""
    device_info = {
        'device_type': 'Unknown',
        'device_model': 'Unknown Model',
        'os': 'Unknown OS'
    }
    
    try:
        ua_lower = user_agent.lower()
        
        if 'mobile' in ua_lower or 'android' in ua_lower or 'iphone' in ua_lower:
            device_info['device_type'] = 'Mobile'
        elif 'tablet' in ua_lower or 'ipad' in ua_lower:
            device_info['device_type'] = 'Tablet'
        elif 'windows' in ua_lower or 'mac' in ua_lower or 'linux' in ua_lower:
            device_info['device_type'] = 'Desktop'
        
        if 'android' in ua_lower:
            device_info['os'] = 'Android'
            import re
            android_version = re.search(r'android (\d+(\.\d+)*)', ua_lower)
            if android_version:
                device_info['os'] = f'Android {android_version.group(1)}'
        elif 'iphone' in ua_lower or 'ipad' in ua_lower or 'mac os' in ua_lower:
            device_info['os'] = 'iOS'
        elif 'windows' in ua_lower:
            device_info['os'] = 'Windows'
        elif 'mac' in ua_lower:
            device_info['os'] = 'macOS'
        elif 'linux' in ua_lower:
            device_info['os'] = 'Linux'
        
        if 'redmi' in ua_lower:
            device_info['device_model'] = 'Redmi Series'
        elif 'samsung' in ua_lower:
            device_info['device_model'] = 'Samsung Series'
        
    except Exception as e:
        print(f"解析User-Agent失败: {str(e)}")
    
    return device_info

def account_login():
    """用户登录接口 - 增强版，包含授权和封号判定"""
    try:
        # ========== 1. 请求验证和解析 ==========
        data = request.get_json(force=True, silent=True)
        
        if data is None and request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid JSON format"}), 400
        elif data is None:
            return jsonify({"error": "Empty request body"}), 400
        
        # 0.5.0 以下老客户端(0.1.8/0.2.x/0.3.x)的 login 请求只有
        # device/key/language, 没有 user —— 用 create 时拿到的 device 反查账号。
        # 反编译依据见 legacy_client_compat.py 顶部注释。
        if 'user' not in data:
            try:
                from business.helpers.legacy_client_compat import is_legacy_request, find_user_by_device, looks_like_legacy_login
                if is_legacy_request() or looks_like_legacy_login(data):
                    legacy_user = find_user_by_device(data.get('device'))
                    if legacy_user is None and data.get('device'):
                        # 设备没有对应账号(客户端被删/换机): 明确 404, 让老客户端走"重新创建账号"
                        return jsonify({"error": "User not found"}), 404
                    if legacy_user is not None:
                        data['user'] = legacy_user.id
                        print(f"老客户端登录: 按 device={data.get('device')} -> user={legacy_user.id}")
            except Exception as _legacy_err:
                print(f"老客户端 login 兼容处理失败(继续走原逻辑): {_legacy_err}")

        if 'user' not in data:
            return jsonify({"error": "Missing required field: user"}), 400
        
        user_id = data.get('user')
        
        if not user_id or not isinstance(user_id, str) or len(user_id.strip()) == 0:
            return jsonify({"error": "Invalid user ID format"}), 400
        
        user_id = user_id.strip()
        
        # ========== 2. 查询用户 ==========
        user = User.query.filter_by(id=user_id).first()

        if not user:
            # 无库就建号: 与 /account/auth/login 同一套逻辑(库里没有这个 id 就用同一个
            # id 补一行)。老客户端重登走的就是这条路径, 所以必须在这里也补上。
            try:
                from ..auth_login.auth_login import _recreate_missing_account
            except Exception:
                try:
                    from business.account.auth_login.auth_login import _recreate_missing_account
                except Exception:
                    _recreate_missing_account = None

            if _recreate_missing_account is not None:
                user = _recreate_missing_account(user_id, data)

            if not user:
                return jsonify({"error": "User not found"}), 404
        
        # ========== 3. 检查封禁状态（新加入的机制） ==========
        if hasattr(user, 'is_banned') and user.is_banned == 1:
            try:
                current_time = int(time.time())
                ban_end_time = getattr(user, 'ban_end_time', 0)
                
                if ban_end_time == 0 or current_time < ban_end_time:
                    ban_info = {
                        "error": "Account banned",
                        "baninfo": {
                            "user_id": user.id,
                            "end_date": ban_end_time,
                            "type": "account"
                        }
                    }
                    
                    if ban_end_time > 0:
                        remaining = ban_end_time - current_time
                        ban_info["baninfo"]["remaining_seconds"] = remaining
                        ban_info["baninfo"]["remaining_days"] = remaining // 86400
                    
                    return jsonify(ban_info), 406
                
                else:
                    print(f"用户 {user_id} 封禁已过期，自动解封")
                    user.is_banned = 0
                    user.ban_duration = 0
                    user.ban_start_time = 0
                    user.ban_end_time = 0
                    db.session.commit()
                    
            except Exception as e:
                print(f"处理封禁状态失败: {str(e)}")
        
        # ========== 4. 授权状态校验（新加入的机制） ==========
        if hasattr(user, 'is_authorized') and user.is_authorized == 0:
            print(f"账号 {user_id} 未授权，阻止登录")
            current_time = int(time.time())
            ban_end_time = 0
            
            return jsonify({
                "error": "Account not authorized",
                "baninfo": {
                    "user_id": user.id,
                    "end_date": ban_end_time,
                    "type": "unauthorized",
                    "message": "账号尚未授权，请联系管理员"
                }
            }), 406
        
        # ========== 5. 更新用户会话和登录信息 ==========
        new_session = uuid.uuid4().hex
        
        if hasattr(user, 'session') and user.session and user.session.startswith('UNAUTH_'):
            print(f"重置未授权会话: {user_id}")
            user.session = new_session
        else:
            user.session = new_session
        
        if data.get('request_recovery_token', False):
            user.recovery_token = uuid.uuid4().hex
        
        if 'device' in data and data['device']:
            user.device_identifier = data['device']
        
        user.ip_address = request.remote_addr
        
        user_agent = request.headers.get('User-Agent', '')
        if user_agent:
            device_info = parse_user_agent(user_agent)
            user.user_agent = user_agent[:500]
            
            if hasattr(user, 'device_type') and not user.device_type:
                user.device_type = device_info.get('device_type', 'Unknown')[:50]
            
            if hasattr(user, 'device_model') and not user.device_model:
                user.device_model = device_info.get('device_model', 'Unknown Model')[:100]
            
            if hasattr(user, 'device_os') and not user.device_os:
                user.device_os = device_info.get('os', 'Unknown OS')[:50]
        
        if hasattr(user, 'platform') and not user.platform:
            user.platform = ''
        
        if hasattr(user, 'last_login'):
            user.last_login = int(time.time())
        
        db.session.commit()
        
        print(f"用户登录成功，用户ID: {user_id}，新session: {new_session[:8]}...")
        
        # ========== 6. 构建成功响应（保持原有格式） ==========
        response_body = {
            "user": user.id,
            "device": None,
            "key": "0000000000000000000000000000000000000000000000000000000000000000",
            "session": new_session,
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

        # 老客户端需要 external_links/player_badge_type/motd_* 与真实 device
        try:
            from business.helpers.legacy_client_compat import is_legacy_request, augment_auth_response
            if is_legacy_request():
                response_body = augment_auth_response(
                    response_body, user, data.get('device') or getattr(user, 'device_identifier', '')
                )
        except Exception as _legacy_err:
            print(f"老客户端 login 响应兼容处理失败(继续原响应): {_legacy_err}")

        return Response(
            response=json.dumps(response_body, sort_keys=False, indent=None),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"登录过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500
