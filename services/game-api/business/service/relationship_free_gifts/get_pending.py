# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/free_gifts/get_pending
"""

from flask import request, jsonify, Response
from infrastructure.database.models import db, User, FreeGift
import json
import uuid
import time

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_pending_free_gifts():
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
        
        current_time = int(time.time())
        
        sent_gifts = FreeGift.query.filter_by(
            sender_id=user_id
        ).filter(
            (FreeGift.expires_at.is_(None)) | (FreeGift.expires_at > current_time)
        ).all()
        
        set_sent_free_gifts = []
        for gift in sent_gifts:
            gift_data = {
                "gift_id": gift.gift_id,
                "receiver_id": gift.receiver_id,
                "gift_type": gift.gift_type,
                "sent_at": gift.sent_at,
                "received": gift.received
            }
            
            if gift.expires_at:
                gift_data["expires_at"] = gift.expires_at
                
            if gift.received_at:
                gift_data["received_at"] = gift.received_at
                
            set_sent_free_gifts.append(gift_data)
        
        received_gifts = FreeGift.query.filter_by(
            receiver_id=user_id,
            received=False
        ).filter(
            (FreeGift.expires_at.is_(None)) | (FreeGift.expires_at > current_time)
        ).all()
        
        set_recvd_free_gifts = []
        for gift in received_gifts:
            gift_data = {
                "gift_id": gift.gift_id,
                "sender_id": gift.sender_id,
                "gift_type": gift.gift_type,
                "sent_at": gift.sent_at,
                "expires_at": gift.expires_at
            }
                
            set_recvd_free_gifts.append(gift_data)
        
        response_body = {
            "set_sent_free_gifts": set_sent_free_gifts,
            "set_recvd_free_gifts": set_recvd_free_gifts
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

