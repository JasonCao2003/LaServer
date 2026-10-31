from flask import request, Response
from database.models import db, User, VideoDefinition, VideoSchedule
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def get_video_defs():
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
        
        video_defs = VideoDefinition.query.all()
        
        video_defs_list = []
        for video_def in video_defs:
            video_defs_list.append({
                "name": video_def.name,
                "duration": video_def.duration,
                "location": video_def.location,
                "subtitle_base": video_def.subtitle_base,
                "has_translations": video_def.has_translations
            })
        
        video_schedules = VideoSchedule.query.all()
        
        video_schedule_list = []
        for schedule in video_schedules:
            # JSON
            try:
                playlist = json.loads(schedule.playlist)
            except:
                playlist = []
                
            video_schedule_list.append({
                "channel": schedule.channel,
                "event": schedule.event,
                "preshow_seconds": schedule.preshow_seconds,
                "playlist": playlist,
                "loop": schedule.loop,
                "gap_seconds": schedule.gap_seconds,
                "postshow_seconds": schedule.postshow_seconds,
                "silence_please": schedule.silence_please
            })
        
        response_body = {
            "video_defs": video_defs_list,
            "video_schedule": video_schedule_list
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