from flask import request, Response
from infrastructure.database.models import db, TrustActivityDef
import json

def get_trust_activity_defs():
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
        
        activity_defs = TrustActivityDef.query.filter_by(is_active=True).all()
        
        trust_activity_defs = []
        for activity_def in activity_defs:
            trust_activity_defs.append({
                "activity_id": activity_def.activity_id,
                "activity_name": activity_def.activity_name,
                "description": activity_def.description,
                "activity_type": activity_def.activity_type,
                "required_level": activity_def.required_level,
                "reward_points": activity_def.reward_points
            })
        
        response_body = {
            "get_trust_activity_defs": trust_activity_defs
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