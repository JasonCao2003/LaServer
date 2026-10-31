# -*- coding: utf-8 -*-
"""
接口: /account/device_capabilities/lookup
函数: lookup()
设备能力查询接口
"""
from flask import request, jsonify, Response
from infrastructure.database.models import db, User
import json
import uuid
import time

def lookup():
    try:
        data = None
        try:
            # 使用force=True强制解析JSON
            data = request.get_json(force=True, silent=True)
        except Exception as e:
            print(f"JSON解析失败: {str(e)}")
        
        if data is None and request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except json.JSONDecodeError:
                print("无法解析请求数据")
        
        user_id = None
        if data and 'user' in data:
            user_id = data.get('user')
        
        # ========== 解析设备信息 ==========
        if data and 'device_desc' in data:
            try:
                device_desc_str = data.get('device_desc', '')
                print(f"原始device_desc: {device_desc_str}")
                
                # 解析device_desc JSON字符串
                device_desc = json.loads(device_desc_str)
                brand = device_desc.get('brand', '')
                model = device_desc.get('model', '')
                
                if brand and model:
                    device_name = f"{brand} {model}"
                    print(f"从device_desc提取设备信息: {device_name}")
                    
                    if user_id:
                        user = User.query.filter_by(id=user_id).first()
                        if user:
                            # 更新用户设备名称（使用现有的device_name字段）
                            user.device_name = device_name[:255]
                            user.device = device_name[:255]  # 同时更新device字段
                            
                            # 如果有device_model字段，也更新它（只存储model部分）
                            if hasattr(user, 'device_model'):
                                user.device_model = model[:100]
                            
                            db.session.commit()
                            print(f"已更新用户 {user_id} 的设备信息: {device_name}")
                else:
                    print(f"无法从device_desc提取完整信息: brand={brand}, model={model}")
                    
            except json.JSONDecodeError as e:
                print(f"解析device_desc失败: {str(e)}")
            except Exception as e:
                print(f"处理设备信息失败: {str(e)}")
        
        # 如果请求中有user字段，检查用户授权状态
        if user_id:
            print(f"设备能力查询请求，用户: {user_id}")
            
            user = User.query.filter_by(id=user_id).first()
            
            if not user:
                print(f"用户 {user_id} 不存在")
                return jsonify({
                    "error": "User not found",
                    "message": "用户不存在"
                }), 404
            
            # ========== 检查封禁状态 ==========
            if user.is_banned == 1:
                try:
                    current_time = int(time.time())
                    ban_end_time = user.ban_end_time
                    
                    # 永久封禁 或 未到期 → 返回封禁信息 + 406 响应码
                    if ban_end_time == 0 or current_time < ban_end_time:
                        print(f"用户 {user_id} 被封禁，禁止查询设备能力")
                        
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
                        
                        return jsonify(ban_info), 503
                    
                    else:
                        print(f"用户 {user_id} 封禁已过期，自动解封")
                        user.is_banned = 0
                        user.ban_duration = 0
                        user.ban_start_time = 0
                        user.ban_end_time = 0
                        db.session.commit()
                        
                except Exception as e:
                    print(f"处理封禁状态失败: {str(e)}")
            
            # ========== 授权状态校验 ==========
            if user.is_authorized == 0:
                print(f"账号 {user_id} 未授权，禁止查询设备能力")
                current_time = int(time.time())
                ban_end_time = 0  # 永久封禁
                
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
                }), 503
            
            print(f"用户 {user_id} 已授权，允许查询设备能力")
        else:
            print("设备能力查询请求，无用户信息")
        
        # ========== 已授权或无需授权，返回设备能力信息 ==========
        hashid = uuid.uuid4().int & 0xFFFFFFFF
        response_body = {
            "device_capabilities": {
                "gpu_rating": 10003,
                "hash_id": hashid
            }
        }
        
        return Response(
            response=json.dumps(response_body, sort_keys=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"设备能力查询过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Internal server error"}), 500
