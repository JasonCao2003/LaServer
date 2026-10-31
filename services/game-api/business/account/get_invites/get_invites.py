from flask import request, Response
from infrastructure.database.models import db, User, Invite
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_invites():
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
        
        invites = Invite.query.filter_by(inviter_user_id=user_id).all()
        
        invites_list = []
        for invite in invites:
            invites_list.append({
                "inviter_user_id": invite.inviter_user_id,
                "token_id": invite.token_id,
                "nickname": invite.nickname,
                "friend_id": invite.friend_id,
                "created": invite.created
            })
        
        response_body = {
            "invites": invites_list
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