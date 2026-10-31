# -*- coding: utf-8 -*-
"""
接口: /account/get_surveys
函数: get_surveys()
获取调查问卷
"""
from flask import request, Response
from infrastructure.database.models import db, User, Survey, UserSurvey
from datetime import datetime
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_surveys():
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
        
        current_time = datetime.utcnow()
        active_surveys = Survey.query.filter(
            Survey.is_active == True,
            Survey.start_date <= current_time,
            Survey.end_date >= current_time
        ).all()
        
        # 获取用户已完成的调查问卷ID列表
        completed_survey_ids = [
            us.survey_id for us in UserSurvey.query.filter_by(
                user_id=user_id, 
                completed=True
            ).all()
        ]
        
        surveys = []
        for survey in active_surveys:
            if survey.id not in completed_survey_ids:
                surveys.append({
                    "id": survey.id,
                    "title": survey.title,
                    "description": survey.description,
                    "type": survey.survey_type,
                    "start_date": survey.start_date.isoformat(),
                    "end_date": survey.end_date.isoformat()
                })
        
        response_body = {
            "surveys": surveys
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
