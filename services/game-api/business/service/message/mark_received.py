# -*- coding: utf-8 -*-
"""
Service接口: /service/message/api/v1/mark_received
"""

from flask import request, jsonify, Response, send_from_directory
# Message 必带: 下面 message/existing_message 都是 Message.query, 漏 import
# 会 NameError -> 接口 500。
from infrastructure.database.models import db, User, Message
import json
import uuid
import time
import os

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def mark_received():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data or 'messages' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session = request_data.get('session')
        message_ids = request_data.get('messages', [])
        
        print(f"收到消息接收确认请求: user={user_id}, session={session}, messages={len(message_ids)}")
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        current_timestamp = int(time.time())
        
        # 处理消息ID列表
        response_messages = []
        for msg_data in message_ids:
            message_id = msg_data.get('id')
            if not message_id:
                print(f"跳过无效的消息ID: {msg_data}")
                continue
                
            print(f"处理消息ID: {message_id}")
            
            message = Message.query.filter_by(id=message_id, user_id=user_id).first()
            
            if message:
                if not message.received_at:
                    message.received_at = current_timestamp
                    message.updated_at = current_timestamp
                    db.session.commit()
                    print(f"更新消息 {message_id} 的接收时间")
            else:
                try:
                    new_message = Message(
                        id=message_id,
                        user_id=user_id,
                        received_at=current_timestamp,
                        created_at=current_timestamp,
                        updated_at=current_timestamp
                    )
                    db.session.add(new_message)
                    db.session.commit()
                    print(f"创建新消息记录: {message_id}")
                except Exception as e:
                    print(f"创建消息记录失败: {e}")
                    db.session.rollback()
                    if "Duplicate entry" in str(e):
                        existing_message = Message.query.filter_by(id=message_id, user_id=user_id).first()
                        if existing_message:
                            existing_message.received_at = current_timestamp
                            existing_message.updated_at = current_timestamp
                            db.session.commit()
            
            response_messages.append({
                "payload": {
                    "headers": {
                        "id": message_id,
                        "received_at": current_timestamp
                    }
                }
            })
        
        response_body = {
            "status": "OK",
            "messages": response_messages
        }
        
        print(f"消息接收确认完成，处理了 {len(response_messages)} 条消息")
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"mark_received 接口错误: {str(e)}")
        import traceback
        traceback.print_exc()
        
        db.session.rollback()
        
        return Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype='application/json'
        )
