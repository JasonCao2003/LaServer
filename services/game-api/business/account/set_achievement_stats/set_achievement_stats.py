# -*- coding: utf-8 -*-
"""
接口: /account/set_achievement_stats
函数: set_achievement_stats()

客户端上报成就统计(绝对值), 服务端落库后回传该玩家全部成就统计,
并据此推进玩家任务(PlayerQuest)的完成进度。

行为依据真实抓包与服务器 request_dumps:
  - 请求体: {user, user_id, session, achievement_stats:[{type, value}, ...]}
  - 响应体: {achievement_stats:[{type, value, update}, ...]}  (字段名为 update)
  - value 保持原始数值类型: 整数统计回传整数(如 121), 浮点统计回传浮点
    (如 avatar_charge_count=387.52399, highest_flight_altitude=4231.814)。
"""
from flask import request, Response
from infrastructure.database.models import db, User, AchievementStat, PlayerQuest, QuestStat
import json
import time


def get_cached_user(user_id, session_token):
    """按 (id, session) 校验用户会话。"""
    return User.query.filter_by(id=user_id, session=session_token).first()


def _coerce_value(v):
    """把上报值规整为可入库的数值, 非法值返回 None(跳过)。

    - 拒绝布尔(避免 True 被当作 1 写入)。
    - 整数保持整数, 浮点保持浮点, 与官方回传的类型一致。
    - 数字字符串按需解析(容错老客户端), 整数形态的浮点(如 3.0)归一为 int。
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else v
    if isinstance(v, str):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return int(f) if f.is_integer() else f
    return None


def _advance_quests(user_id, updated_types, now):
    """根据更新的成就 stat 推进玩家任务(PlayerQuest)。

    成就值是"全局累计"的(如击掌总次数), 任务进度必须按"自接取以来的增量"算:
      progress = 当前累计 - 创建时基线(baseline);  完成条件: progress >= req。
    并把 init_stats 的显示值同步成该 progress(从 0 向 target 递增), 与客户端
    期望一致(create 时 init=0, target=req)。这样老玩家不会因累计值早已>=req 而
    瞬间完成, 也不会出现 init 显示成累计值(如101)与 target(1)对不上 -> 互动卡死。
    """
    if not updated_types:
        return
    quests = PlayerQuest.query.filter_by(
        user_id=user_id, quest_status="active").all()
    for pq in quests:
        stats = QuestStat.query.filter_by(quest_id=pq.quest_id).all()
        reqs = {s.stat_type: s.value for s in stats
                if s.stat_category == "completion_requirements"}
        if not reqs or not (set(reqs) & updated_types):
            continue
        baselines = {s.stat_type: s.value for s in stats
                     if s.stat_category == "baseline"}
        all_done = True
        for st_type, req_val in reqs.items():
            cur = AchievementStat.query.filter_by(
                user_id=user_id, stat_type=st_type).first()
            cur_val = cur.value if cur else 0
            base = baselines.get(st_type, 0)
            progress = cur_val - base
            if progress < 0:
                progress = 0
            # 同步 init_stats 显示值为"增量进度"(0 -> target)
            init = next((s for s in stats if s.stat_category == "init_stats"
                         and s.stat_type == st_type), None)
            if init is not None:
                init.value = progress
                init.updated_epoch = now
            if progress < req_val:
                all_done = False
        if all_done:
            pq.quest_status = "succeeded"
            pq.status = "succeeded"


def set_achievement_stats():
    try:
        request_data = request.get_json(force=True, silent=True)
        if not isinstance(request_data, dict):
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )

        # 兼容 user / user_id 两种字段(抓包中两者同时出现)
        user_id = request_data.get('user') or request_data.get('user_id')
        session = request_data.get('session')
        achievement_stats = request_data.get('achievement_stats')

        if not user_id or not session or not isinstance(achievement_stats, list):
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )

        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )

        current_time = int(time.time())
        updated_types = set()

        for stat in achievement_stats:
            if not isinstance(stat, dict):
                continue
            stat_type = stat.get('type')
            value = _coerce_value(stat.get('value'))
            if not stat_type or value is None:
                continue

            updated_types.add(stat_type)
            existing = AchievementStat.query.filter_by(
                user_id=user_id, stat_type=stat_type).first()
            if existing:
                existing.value = value
                existing.updated_at = current_time
            else:
                db.session.add(AchievementStat(
                    user_id=user_id,
                    stat_type=stat_type,
                    value=value,
                    updated_at=current_time,
                ))

        # 先落库成就值, 再据此推进任务进度(私服每日任务依赖此步)
        db.session.flush()
        _advance_quests(user_id, updated_types, current_time)
        db.session.commit()

        # 回传该玩家全部成就统计(官方按 type 升序排列, 此处对齐)
        all_stats = AchievementStat.query.filter_by(
            user_id=user_id).order_by(AchievementStat.stat_type.asc()).all()

        response_stats = [
            {
                "type": s.stat_type,
                "value": s.value,
                "update": s.updated_at,
            }
            for s in all_stats
        ]

        return Response(
            response=json.dumps(
                {"achievement_stats": response_stats},
                sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )

    except Exception as e:
        db.session.rollback()
        return Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype='application/json'
        )
