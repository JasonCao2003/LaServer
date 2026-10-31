# -*- coding: utf-8 -*-
"""
接口: /account/auth/login
函数: login()
用户登录接口
"""
from flask import request, jsonify, Response
from database.models import db, User
import json
import uuid
import time
import os
import binascii
import base64
import logging

logger = logging.getLogger(__name__)

def parse_user_agent(user_agent):
    """解析User-Agent字符串的简单函数"""
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
        logger.warning(f"解析User-Agent失败: {str(e)}")
    
    return device_info

def _auto_create_account_enabled():
    """无库就建号 开关: config/friend_config.json 的 auto_create_account(默认 true)。"""
    try:
        import helpers.friend_helper as friend_helper
        return friend_helper.auto_create_account_enabled()
    except Exception:
        return os.environ.get("SKY_AUTO_CREATE_ACCOUNT", "1") != "0"


def _recreate_missing_account(user_id, data):
    """账号库里没有这个 id 时, 用**同一个 id** 补一行, 让登录继续走下去。

    与 /account/create 的区别: 那个是"发新号"(随机 uuid, 客户端拿新的 id);
    这里只补客户端手里已经揣着的 id —— 清空/换了账号库之后客户端不用重装、
    不用换号, 直接按原 id 重新落库(世界/物品/好友这些是新的, 因为库是空的)。

    字段照 create.py 填: 设备信息从请求头解析, session / recovery_token 新生成,
    是否授权仍看面板的"自动授权"开关(auto_auth_helper) —— 授权关着时
    is_authorized=0, 下面第 4 步会照常回 406 让客户端等管理员授权, 与注册新号的
    待遇一致(不会因为自动建号就绕过授权)。
    """
    if not _auto_create_account_enabled():
        return None
    if not isinstance(user_id, str) or not user_id.strip():
        return None

    user_agent = request.headers.get('User-Agent', '')
    try:
        device_info = parse_user_agent(user_agent)
    except Exception:
        device_info = {}

    device_name = ''
    if isinstance(data, dict):
        device_name = str(data.get('device_name') or '').strip()
    if not device_name:
        device_name = device_info.get('device_type', 'Unknown Device')

    created_at = int(time.time())
    auto_authorized = 0
    remark = ''
    try:
        from helpers.auto_auth_helper import should_auto_authorize, AUTO_AUTH_MARK
        if should_auto_authorize(created_at):
            auto_authorized = 1
            remark = AUTO_AUTH_MARK
    except Exception:
        pass

    user = User(
        id=user_id,
        device=device_name[:255],
        device_identifier=uuid.uuid4().hex,
        session=uuid.uuid4().hex,
        recovery_token=uuid.uuid4().hex + uuid.uuid4().hex,
        platform='',
        ip_address=request.remote_addr,
        device_name=device_name[:255],
        device_type=device_info.get('device_type', 'Unknown')[:50],
        device_model=device_info.get('device_model', 'Unknown Model')[:100],
        device_os=device_info.get('os', 'Unknown OS')[:50],
        user_agent=user_agent[:500],
        is_authorized=auto_authorized,
        is_banned=0,
        remark=remark,
        created_at=created_at,
    )
    try:
        db.session.add(user)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # 并发: 同一秒里另一个请求刚好把这一行建好了 -> 再查一次, 别把登录打回去
        try:
            again = User.query.filter_by(id=user_id).first()
            if again is not None:
                return again
        except Exception:
            pass
        logger.error(f"无库自动建号失败 {user_id}: {e}", exc_info=True)
        return None
    logger.info(f"账号库里没有 {user_id}, 已用同一 id 自动建号 (自动授权={auto_authorized})")
    # 建号同时按管理面板『账号默认数据』把货币行也建好(config/game_control.json ->
    # season.finalize_currency)。[2026-10-06 修正] 面板把新号蜡烛设成 300, 但之前
    # 没有任何建号路径写这份默认值 -> 自动建出来的新号永远是 0 蜡烛。
    try:
        from helpers.currency_helper import ensure_user_currency
        ensure_user_currency(user_id)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"自动建号后初始化货币行失败 {user_id}: {e}")
    return user


def logini():
    """用户登录接口"""
    # ========== 1. 请求验证和解析 ==========
    try:
        logger.debug(f"请求头: {dict(request.headers)}")
        logger.debug(f"Content-Type: {request.content_type}")
        logger.debug(f"请求方法: {request.method}")
        
        if not request.data:
            logger.warning("请求体为空")
            return jsonify({
                "error": "请求体不能为空",
                "code": "EMPTY_BODY"
            }), 400
        
        data = None
        raw_data = request.get_data(as_text=True)
        
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {str(e)}")
            try:
                if raw_data.startswith('\ufeff'):
                    raw_data = raw_data[1:]
                data = json.loads(raw_data)
            except json.JSONDecodeError as e2:
                logger.error(f"宽松JSON解析也失败: {str(e2)}")
                if request.form:
                    logger.info("尝试从表单数据中提取")
                    data = request.form.to_dict()
                else:
                    raise e2
        
        logger.debug(f"解析后的数据: {data}")
        
        if not data:
            logger.warning("无法解析请求数据")
            return jsonify({
                "error": "无法解析请求数据",
                "code": "INVALID_FORMAT"
            }), 400
        
        if 'user' not in data:
            logger.warning(f"缺少必要字段 'user'，收到字段: {list(data.keys())}")
            return jsonify({
                "error": "缺少必要字段: user",
                "code": "MISSING_FIELD",
                "received_fields": list(data.keys())
            }), 400
        
        user_id = data.get('user')
        
        if not user_id or not isinstance(user_id, str) or len(user_id.strip()) == 0:
            logger.warning(f"无效的用户ID: {user_id}")
            return jsonify({
                "error": "无效的用户ID格式",
                "code": "INVALID_USER_ID"
            }), 400
        
        user_id = user_id.strip()
        logger.info(f"处理用户登录: {user_id}")
        
    except Exception as e:
        logger.error(f"请求解析失败: {str(e)}", exc_info=True)
        return jsonify({
            "error": "请求数据格式错误",
            "code": "PARSE_ERROR",
            "details": str(e)
        }), 400
    
    # ========== 2. 查询用户 ==========
    try:
        user = User.query.filter_by(id=user_id).first()

        if not user:
            # 无库就建号: 库里没这个 id 就用同一个 id 补一行, 继续走下面的登录流程。
            # 建不出来(开关关了/并发失败)才按老样子回 not_found。
            user = _recreate_missing_account(user_id, data)
            if user is None:
                logger.info(f"用户不存在: {user_id}")
                return jsonify({
                    "result": "not_found",
                    "message": "用户不存在",
                    "user_id": user_id
                }), 200

        logger.info(f"找到用户: {user_id}, 封禁状态: {user.is_banned}, 授权状态: {user.is_authorized}")
        
    except Exception as e:
        logger.error(f"查询用户失败: {str(e)}", exc_info=True)
        return jsonify({
            "error": "服务器内部错误",
            "code": "DB_ERROR"
        }), 500
    
    # ========== 3. 检查封禁状态（优先级最高） ==========
    if hasattr(user, 'is_banned') and user.is_banned == 1:
        try:
            current_time = int(time.time())
            ban_end_time = getattr(user, 'ban_end_time', 0)
            
            logger.info(f"用户 {user_id} 被封禁，封禁结束时间: {ban_end_time}，当前时间: {current_time}")
            
            if ban_end_time == 0 or current_time < ban_end_time:
                logger.warning(f"用户 {user_id} 仍在封禁期内")
                
                ban_info = {
                    "baninfo": {
                        "user_id": user.id,
                        "end_date": ban_end_time,
                        "type": "account"
                    },
                    "user": user.id,
                    "end_date": ban_end_time,
                    "message": "账号已被封禁"
                }
                
                if ban_end_time > 0:
                    remaining = ban_end_time - current_time
                    ban_info["baninfo"]["remaining_seconds"] = remaining
                    ban_info["baninfo"]["remaining_days"] = remaining // 86400
                
                return jsonify(ban_info), 406
            
            else:
                logger.info(f"用户 {user_id} 封禁已过期，自动解封")
                user.is_banned = 0
                user.ban_duration = 0
                user.ban_start_time = 0
                user.ban_end_time = 0
                db.session.commit()
                
        except Exception as e:
            logger.error(f"处理封禁状态失败: {str(e)}")
    
    # ========== 4. 授权状态校验 ==========
    if hasattr(user, 'is_authorized') and user.is_authorized == 0:
        logger.warning(f"账号 {user_id} 未授权，阻止登录")
        current_time = int(time.time())
        ban_end_time = 0
        
        return jsonify({
            "baninfo": {
                "user_id": user.id,
                "end_date": ban_end_time,
                "type": "unauthorized",
                "message": "账号尚未授权，请联系管理员"
            },
            "user": user.id,
            "end_date": ban_end_time,
            "message": "账号未授权"
        }), 406
    
    # ========== 5. 更新用户会话和登录信息 ==========
    try:
        new_session = uuid.uuid4().hex
        
        if hasattr(user, 'session') and user.session and user.session.startswith('UNAUTH_'):
            logger.info(f"重置未授权会话: {user_id}")
            user.session = new_session
        
        user.ip_address = request.remote_addr
        user.session = new_session
        
        if hasattr(user, 'recovery_token'):
            user.recovery_token = uuid.uuid4().hex + uuid.uuid4().hex
        
        user_agent = request.headers.get('User-Agent', '')
        if user_agent:
            device_info = parse_user_agent(user_agent) if 'parse_user_agent' in globals() else {}
            
            if hasattr(user, 'user_agent'):
                user.user_agent = user_agent[:500]
            
            if hasattr(user, 'device_type'):
                device_type = device_info.get('device_type', 'Unknown')[:50]
                if not user.device_type or user.device_type == '未知':
                    user.device_type = device_type
            
            if hasattr(user, 'device_model'):
                device_model = device_info.get('device_model', 'Unknown Model')[:100]
                if not user.device_model or user.device_model == '未知型号':
                    user.device_model = device_model
            
            if hasattr(user, 'device_os'):
                device_os = device_info.get('os', 'Unknown OS')[:50]
                if not user.device_os or user.device_os == 'Unknown OS':
                    user.device_os = device_os
        
        if hasattr(user, 'platform') and not user.platform:
            user.platform = ''
        
        if hasattr(user, 'last_login'):
            user.last_login = int(time.time())
        
        db.session.commit()
        
        logger.info(f"用户 {user_id} 登录成功，新会话: {new_session[:8]}...")
        
    except Exception as e:
        logger.error(f"更新用户信息失败: {str(e)}", exc_info=True)
        db.session.rollback()
        return jsonify({
            "error": "更新用户信息失败",
            "code": "UPDATE_ERROR"
        }), 500
    
    # ========== 6. 构建成功响应 ==========
    # 说明: 这个字段 Android 客户端(libBootloader 0.65~0.337 全版本)其实**不读** ——
    # 它自己拼 "https://<host>/account/ws", 且 <host> 是把 API 主机名第一个 '.' 前
    # 插入 ".ws"(反编译 sub_42FE58: 常量 7567150 = ".ws\0"), 见 .so.c:686811 / 706162。
    # 保留端口而不是 split(':')[0]: LAN 访问时 Host 是 "192.168.x.x:8443", 原来会被
    # 截成 192.168.x.x -> wss://192.168.x.x/account/ws 打到 443 上必然失败。
    host = request.host
    scheme = 'wss' if request.is_secure else 'ws'
    ws_url = f"{scheme}://{host}/account/ws"
    
    def generate_client_key():
        """生成客户端期望的key格式"""
        random_bytes = os.urandom(32)
        
        while random_bytes[:16] == b'\x00' * 16:
            random_bytes = os.urandom(32)
        
        key_hex = binascii.hexlify(random_bytes).decode('ascii')
        
        if len(key_hex) != 64:
            key_hex = uuid.uuid4().hex + uuid.uuid4().hex + uuid.uuid4().hex + uuid.uuid4().hex
        
        logger.debug(f"生成的key: {key_hex[:8]}... (前16字节: {random_bytes[:16].hex()})")
        return key_hex
    
    device_id = data.get('device', '')
    if not device_id or len(device_id) < 32:
        device_id = str(uuid.uuid4())
    
    recovery_token = getattr(user, 'recovery_token', '') if hasattr(user, 'recovery_token') else ""
    
    client_key = generate_client_key()
    
    response_body = {
        "authinfo": {
            "user": user.id,
            "device": device_id,
            "key": client_key,
            "recovery_token": recovery_token
        },
        "session": user.session,
        "websocket_url": ws_url,
        "websocket_auth": f"Basic {base64.b64encode(f'{user.id}:{user.session}'.encode()).decode()}",
        "resource_list": [
            "device_capabilities", "userdata", "set_friends", "set_friend_statues",
            "online_friends", "set_rank", "currency", "forge_rates", "motd",
            "outfit_defs", "achievements", "achievement_stats", "relationship_defs",
            "questionnaires", "event_schedule", "set_world_quests", "iap_list",
            "invite_list", "unlocks", "get_shop", "spirit_shops", "collectibles",
            "wing_buffs", "gift_messages", "set_app_badge_number", "get_consumable_defs",
            "get_consumables", "get_buff_defs", "get_buffs", "get_lootboxes",
            "checkpoints", "generic_shop_defs", "external_account_friends",
            "serendipity_matches", "get_star_tag_defs", "status_unlocks", "free_gifts",
            "get_map_defs", "achievement_defs", "achievement_stats_tracking",
            "event_currency_defs", "infractions", "get_user_profile", "emote_sequences",
            "messages"
        ],
        "external_links": [user.platform] if hasattr(user, 'platform') and user.platform else []
    }
    
    if hasattr(user, 'display_name'):
        response_body["authinfo"]["display_name"] = user.display_name
    
    logger.debug(f"返回的authinfo结构: {json.dumps(response_body['authinfo'], indent=2)}")
    
    return Response(
        response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
        status=200,
        mimetype='application/json',
        headers={
            'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
            'Pragma': 'no-cache'
        }
    )
