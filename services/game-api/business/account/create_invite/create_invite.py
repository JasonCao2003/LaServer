from flask import request, jsonify, Response
from infrastructure.database.models import db, User, Invite
from datetime import datetime
import uuid
import time
import json
import logging

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def create_invite():
    """
    创建邀请码接口
    核心原理：
    1. 验证用户的身份和会话有效性
    2. 为当前用户生成一个唯一的邀请令牌和格式化邀请码
    3. 在数据库中记录邀请信息
    4. 返回邀请令牌和格式化邀请码给客户端
    """
    try:
        # ========== 1. 请求验证和解析 ==========
        # 强制解析JSON请求体，不依赖Content-Type头
        request_data = request.get_json(force=True, silent=True)
        
        if request_data is None and request.data:
            try:
                request_data = json.loads(request.data.decode('utf-8'))
            except json.JSONDecodeError:
                logger.error("无法解析请求体为JSON格式")
                return jsonify({
                    "error": "Invalid JSON format",
                    "code": "INVALID_JSON"
                }), 400
        elif request_data is None:
            logger.error("请求体为空")
            return jsonify({
                "error": "Empty request body",
                "code": "EMPTY_BODY"
            }), 400
        
        logger.info(f"创建邀请请求数据: {request_data}")
        
        # 优先使用'user'字段，其次是'user_id'
        user_identifier = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        invite_nickname = request_data.get('invite_nickname')
        
        if not user_identifier:
            logger.warning(f"缺少用户标识字段，收到字段: {list(request_data.keys())}")
            return jsonify({
                "error": "Missing required field: user or user_id",
                "code": "MISSING_USER",
                "received_fields": list(request_data.keys())
            }), 400
        
        if not session_token:
            logger.warning("缺少会话令牌字段")
            return jsonify({
                "error": "Missing required field: session",
                "code": "MISSING_SESSION"
            }), 400
        
        if not invite_nickname:
            logger.warning("缺少邀请昵称字段")
            return jsonify({
                "error": "Missing required field: invite_nickname",
                "code": "MISSING_NICKNAME"
            }), 400
        
        # ========== 2. 用户身份验证 ==========
        user = get_cached_user(user_identifier, session_token)
        
        if not user:
            logger.warning(f"无效的用户会话: user={user_identifier}, session={session_token[:8]}...")
            return jsonify({
                "error": "Invalid user session",
                "code": "INVALID_SESSION"
            }), 401
        
        if hasattr(user, 'is_banned') and user.is_banned == 1:
            current_time = int(time.time())
            ban_end_time = getattr(user, 'ban_end_time', 0)
            
            if ban_end_time == 0 or current_time < ban_end_time:
                logger.warning(f"被封禁用户尝试创建邀请: {user_identifier}")
                return jsonify({
                    "error": "User account is banned",
                    "code": "USER_BANNED"
                }), 403
        
        # ========== 3. 生成邀请码 ==========
        # 生成唯一的邀请令牌ID（UUID格式）
        invite_token_id = str(uuid.uuid4())
        
        # Crocksford Base32字母表（排除I、L、O、U等易混淆字符）
        CHARS = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
        
        def uuid_to_invite_code(uuid_str):
            """
            将UUID转换为邀请码（按照JavaScript逻辑）
            逻辑：
            1. 移除UUID中的连字符
            2. 将UUID转换为字节数组
            3. 提取第8-15个字节（0索引），按JS逻辑处理
            4. 构建64位整数v8
            5. 按每5位提取并映射到Base32字符
            6. 格式化为XXXX-XXXX-XXXX
            """
            try:
                # 1. 移除连字符
                clean = uuid_str.replace('-', '')
                
                # 2. 将UUID字符串转换为字节数组
                # 16个十六进制字符 = 8个字节
                # 但JS代码中处理的是16个字节，所以我们需要16个字节
                bytes_arr = []
                for i in range(0, len(clean), 2):
                    hex_byte = clean[i:i+2]
                    bytes_arr.append(int(hex_byte, 16))
                
                # 3. 按JS逻辑提取字节
                # JS代码：const b8  = bytes[8] & 0x7;
                b8 = bytes_arr[8] & 0x7
                b9 = bytes_arr[9]
                b10 = bytes_arr[10]
                b11 = bytes_arr[11]
                
                # JS代码：const movbe = ((bytes[12] << 24) | (bytes[13] << 16) | (bytes[14] << 8) | bytes[15]) >>> 0;
                movbe = ((bytes_arr[12] << 24) | (bytes_arr[13] << 16) | (bytes_arr[14] << 8) | bytes_arr[15])
                
                # 4. 构建64位整数v8（使用Python的大整数）
                # JS代码：const v8 = (BigInt(b8) << 56n) | (BigInt(b9) << 48n) | (BigInt(b10) << 40n) | (BigInt(b11) << 32n) | BigInt(movbe);
                v8 = (b8 << 56) | (b9 << 48) | (b10 << 40) | (b11 << 32) | movbe
                
                # 5. 按每5位提取并映射到Base32字符
                # JS代码中的位操作转换为Python
                chars = [
                    CHARS[(v8 >> 55) & 0x1F],
                    CHARS[(v8 >> 50) & 0x1F],
                    CHARS[(v8 >> 45) & 0x1F],
                    CHARS[(v8 >> 40) & 0x1F],
                    CHARS[(v8 >> 35) & 0x1F],
                    CHARS[(v8 >> 30) & 0x1F],
                    CHARS[(v8 >> 25) & 0x1F],
                    CHARS[(v8 >> 20) & 0x1F],
                    CHARS[(v8 >> 15) & 0x1F],
                    CHARS[(v8 >> 10) & 0x1F],
                    CHARS[(v8 >> 5) & 0x1F],
                    CHARS[v8 & 0x1F]
                ]
                
                # 6. 格式化为XXXX-XXXX-XXXX
                formatted_code = f"{''.join(chars[0:4])}-{''.join(chars[4:8])}-{''.join(chars[8:12])}"
                
                return formatted_code
                
            except Exception as e:
                logger.error(f"生成邀请码失败: {str(e)}")
                raise
        
        formatted_code = uuid_to_invite_code(invite_token_id)
        
        # 检查格式化邀请码是否已存在（极低概率，但需要处理）
        max_retries = 5
        retry_count = 0
        
        while Invite.query.filter_by(formatted_code=formatted_code).first() and retry_count < max_retries:
            # 重新生成token_id
            invite_token_id = str(uuid.uuid4())
            formatted_code = uuid_to_invite_code(invite_token_id)
            retry_count += 1
        
        if retry_count >= max_retries:
            logger.error(f"无法生成唯一的格式化邀请码，已达到最大重试次数: {max_retries}")
            return jsonify({
                "error": "Failed to generate unique invite code",
                "code": "CODE_GENERATION_FAILED"
            }), 500
        
        created_time = int(time.time())
        
        # ========== 4. 保存邀请记录到数据库 ==========
        try:
            new_invite = Invite(
                inviter_user_id=user.id,
                token_id=invite_token_id,
                formatted_code=formatted_code,
                nickname=invite_nickname,
                friend_id=None,  # 初始时没有朋友接受
                created=created_time
            )
            
            db.session.add(new_invite)
            db.session.commit()
            
            logger.info(f"用户 {user.id} 创建邀请成功，令牌: {invite_token_id}，格式化邀请码: {formatted_code}，昵称: {invite_nickname}")
            
        except Exception as db_error:
            logger.error(f"保存邀请记录到数据库失败: {str(db_error)}")
            db.session.rollback()
            
            if "unique_token_id" in str(db_error) or "Duplicate entry" in str(db_error):
                # 重新生成令牌并重试（最多重试3次）
                for retry_count in range(3):
                    invite_token_id = str(uuid.uuid4())
                    formatted_code = uuid_to_invite_code(invite_token_id)
                    
                    try:
                        new_invite = Invite(
                            inviter_user_id=user.id,
                            token_id=invite_token_id,
                            formatted_code=formatted_code,
                            nickname=invite_nickname,
                            friend_id=None,
                            created=int(time.time())
                        )
                        db.session.add(new_invite)
                        db.session.commit()
                        logger.info(f"重试 {retry_count+1} 次后创建邀请成功")
                        break
                    except Exception as retry_error:
                        logger.error(f"重试 {retry_count+1} 次失败: {str(retry_error)}")
                        db.session.rollback()
                
                if not new_invite:
                    return jsonify({
                        "error": "Failed to generate unique invite token after multiple attempts",
                        "code": "TOKEN_GENERATION_FAILED"
                    }), 500
            else:
                return jsonify({
                    "error": "Database error while saving invite",
                    "code": "DATABASE_ERROR"
                }), 500
        
        # ========== 5. 构建成功响应 ==========
        response_body = {
            "invite_nickname": invite_nickname,
            "invite_token_id": invite_token_id,
            "invite_formatted_code": formatted_code
        }
        
        response_body["invite_created_at"] = created_time
        response_body["inviter_user_id"] = user.id
        response_body["result"] = "success"
        
        logger.debug(f"创建邀请响应: {response_body}")
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json',
            headers={
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache'
            }
        )
        
    except Exception as e:
        logger.error(f"创建邀请接口发生未捕获的异常: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "code": "INTERNAL_ERROR"
        }), 500