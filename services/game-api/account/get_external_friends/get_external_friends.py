# -*- coding: utf-8 -*-
"""
接口: /account/get_external_friends
函数: get_external_friends()
获取外部平台好友列表
"""
from flask import request, Response
from database.models import db, User, ExternalAccountFriend
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_external_friends():
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
        max_count = request_data.get('max_count', 64)  # 默认最多64个
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        external_friends = ExternalAccountFriend.query.filter_by(
            user_id=user_id
        ).limit(max_count).all()
        
        external_friends_list = []
        for friend in external_friends:
            friend_data = {
                "external_account_id": friend.external_account_id,
                "platform": friend.external_platform,
                "nickname": friend.friend_nickname,
                "is_connected": friend.is_connected
            }
            
            # 添加头像URL（如果有）
            if friend.friend_avatar:
                friend_data["avatar"] = friend.friend_avatar
                
            if friend.is_connected and friend.connected_at:
                friend_data["connected_at"] = friend.connected_at
                
            # 添加游戏内用户ID（如果有）
            if friend.friend_user_id:
                friend_data["game_user_id"] = friend.friend_user_id
                
            external_friends_list.append(friend_data)
        
        response_body = {
            "external_account_friends": external_friends_list
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
