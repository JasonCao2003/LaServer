from flask import request, Response
from database.models import db, User, SeasonQuest, SeasonQuestReward
from datetime import datetime
import json
from helpers.game_control_helper import get_daily_quests_config

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def get_season_quests():
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
        
        current_date = datetime.now().strftime("%Y-%m-%d")

        # 当前每日任务池 = 季节每日 + 普通每日 (中控配置, 重启/0点自动换新)
        dq_cfg = get_daily_quests_config()
        pool = []
        seen_ids = set()
        for q in (dq_cfg.get("season", []) + dq_cfg.get("normal", [])):
            qid = q.get("daily_quest_def_id")
            if not qid or qid in seen_ids:
                continue
            seen_ids.add(qid)
            pool.append({
                "daily_quest_def_id": qid,
                "stat_type": q.get("stat_type") or (str(qid) + "_count"),
            })

        existing = SeasonQuest.query.filter_by(user_id=user_id, date=current_date).all()
        existing_ids = {e.daily_quest_def_id for e in existing}
        pool_ids = set(seen_ids)

        # 池变化(重启/0点换新)时, 重建当天任务以匹配新池:
        # 删除不在新池里的旧任务行, 补建新池里缺失的任务行 (保留仍在池中的进度)。
        if existing_ids != pool_ids:
            for e in existing:
                if e.daily_quest_def_id not in pool_ids:
                    db.session.delete(e)
            for r in SeasonQuestReward.query.filter_by(user_id=user_id, date=current_date).all():
                if r.daily_quest_def_id not in pool_ids:
                    db.session.delete(r)
            for q in pool:
                if q["daily_quest_def_id"] not in existing_ids:
                    db.session.add(SeasonQuest(
                        user_id=user_id, date=current_date,
                        daily_quest_def_id=q["daily_quest_def_id"],
                        stat_type=q["stat_type"],
                        start_value=-1.0, activated=False))
                    db.session.add(SeasonQuestReward(
                        user_id=user_id, date=current_date,
                        daily_quest_def_id=q["daily_quest_def_id"],
                        rewarded=False))
            db.session.commit()
        
        season_quests = SeasonQuest.query.filter_by(user_id=user_id, date=current_date).all()
        
        season_quests_rewards = SeasonQuestReward.query.filter_by(user_id=user_id, date=current_date).all()
        
        season_quests_list = []
        for quest in season_quests:
            season_quests_list.append({
                "date": quest.date,
                "daily_quest_def_id": quest.daily_quest_def_id,
                "stat_type": quest.stat_type,
                "start_value": quest.start_value,
                "activated": quest.activated
            })
        
        season_quests_rewards_list = []
        for reward in season_quests_rewards:
            season_quests_rewards_list.append({
                "date": reward.date,
                "daily_quest_def_id": reward.daily_quest_def_id,
                "rewarded": reward.rewarded
            })
        
        response_body = {
            "season_quests": season_quests_list,
            "has_season_quests_rewards": season_quests_rewards_list
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