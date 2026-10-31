from flask import request, Response
from database.models import db, User
import helpers.geonote_store as geonote_store
import json
import time

def request_geonotes_for_n_friends():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"result": "invalid_params"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session_token = request_data.get('session')
        
        user = User.query.filter_by(id=user_id, session=session_token).first()
        if not user:
            return Response(
                response=json.dumps({"result": "invalid_session"}),
                status=401,
                mimetype='application/json'
            )
        
        current_time = int(time.time())
        if user.is_banned:
            if user.ban_duration > 0:
                if current_time < user.ban_end_time:
                    return Response(
                        response=json.dumps({"result": "user_banned"}),
                        status=403,
                        mimetype='application/json'
                    )
                else:
                    user.is_banned = 0
                    user.ban_start_time = 0
                    user.ban_end_time = 0
                    db.session.commit()
            else:
                return Response(
                    response=json.dumps({"result": "user_permanently_banned"}),
                    status=403,
                    mimetype='application/json'
                )
        
        response_body = geonote_store.payload(
            user_id,
            level_id=request_data.get('level_id') or request_data.get('level') or 0,
            limit=request_data.get('n') or request_data.get('limit') or 50,
        )
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        print(f"An error has occurred in request_geonotes_for_n_friends - {e}")
        return Response(
            response=json.dumps({"result": "internal_error"}),
            status=500,
            mimetype='application/json'
        )