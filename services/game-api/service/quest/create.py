# -*- coding: utf-8 -*-
"""
Service接口: /service/quest/api/v1/create
每日/每周任务的正确创建流程: 先创建获得 quest_id, 并写入 init/completion/target
三类 stat, 客户端据此显示任务。完成后由 set_achievement_stats 推进, 再由
update_quest 收尾(succeeded/collected)。
"""
from flask import request, jsonify
from database.models import db, User, PlayerQuest, QuestStat, AchievementStat
import json
import uuid
import time
import os

_QUEST_DEFS = None


def _load_defs():
    global _QUEST_DEFS
    if _QUEST_DEFS is None:
        # 本文件位于 service/quest/create.py, 服务器根目录需向上 3 级
        # (quest -> service -> 根)。之前只向上 2 级 -> 指向不存在的 service/config,
        # 导致 def 永远加载为空, 每个任务都退化用 quest_name+"_count" 作 stat,
        # 与客户端真实统计名(如 non_unique_high_five_count)不符 -> 任务永远无法完成。
        # 数据源优先级: 先 config/quest.json, 再用 get_quest_defs 静态库补全/覆盖。
        # 静态库(434 条)是客户端实际加载的定义, 为权威超集; config/quest.json(387 条)
        # 有部分任务缺失或 completion_stats_types 为空。以静态库为准可保证服务端建任务时
        # 写入的 stat_type 与客户端发来的真实统计名一致, 任务才能被推进/完成。
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        merged = {}
        for rel in ("config/quest.json",
                    "resources/static_responses/account__quest__get_quest_defs.json"):
            try:
                with open(os.path.join(root, rel), encoding="utf-8") as f:
                    data = json.load(f)
                defs = data.get("get_quest_defs", data) if isinstance(data, dict) else data
                for q in defs:
                    if isinstance(q, dict) and q.get("id"):
                        # 静态库后加载 -> 覆盖 config 里缺 stats 的同名定义
                        ex = merged.get(q["id"])
                        if ex and not q.get("completion_stats_types") and ex.get("completion_stats_types"):
                            continue  # 不用空 stats 覆盖已有的非空
                        merged[q["id"]] = q
            except Exception:
                continue
        _QUEST_DEFS = merged
    return _QUEST_DEFS


def _pacific_expiry(now):
    """每日任务到期点 = 玩家所在"今天"(太平洋日界)的次日午夜前 1 秒。

    官方抓包 start=1775939729 / expiry=1775977199 (差 37470s) 就是这个日界;
    旧实现用 UTC 整日(now - now % 86400 + 86399), 与非太平洋时区的客户端
    (以及本服 get_quest_schedule 的太平洋 epoch)差 7~8 小时, 任务会在客户端
    以为还没结束时就过期, 或排期与任务对不上。
    """
    from datetime import datetime, timedelta
    from service.quest.get_quest_schedule import _pacific_midnight_epoch, _us_pacific_dst
    utc = datetime.utcfromtimestamp(now)
    off = 7 if _us_pacific_dst(utc.date()) else 8
    d = (utc - timedelta(hours=off)).date()
    return _pacific_midnight_epoch(d + timedelta(days=1)) - 1


def _quest_type_and_expiry(quest_name, now):
    """判定任务类型与到期时间。

    季节任务与每日任务共用 /service/quest/api/v1/create(用户 2026-10-23 确认:
    "季节任务的开始, 也靠这个")。判定依据是中控 daily_quests.season.pool ——
    季节任务池里的 id 按季节任务处理, 到期时间取赛季结束, 否则每日任务按
    太平洋日界到期。
    """
    try:
        from helpers.game_control_helper import get_daily_quests_config, get_season_config
        pool = (get_daily_quests_config().get("season") or {}).get("pool") or []
        season_ids = {p.get("daily_quest_def_id") for p in pool if isinstance(p, dict)}
        if quest_name in season_ids:
            end = int(get_season_config().get("end_time") or 0)
            if end > now:
                return "season", end
    except Exception:
        pass
    return "daily", _pacific_expiry(now)


def create():
    try:
        data = request.get_json(force=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        quest_name = data.get("quest_name")
        now = int(data.get("current_time") or time.time())
        if not user_id or not session or not quest_name:
            return jsonify({"success": False, "error": "missing fields"}), 400
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return jsonify({"success": False, "error": "invalid session"}), 401

        qdef = _load_defs().get(quest_name, {})
        stat_types = qdef.get("completion_stats_types") or [quest_name + "_count"]
        stat_vals = qdef.get("completion_stats_values") or [1]
        quest_giver = qdef.get("npc", "")
        # 每日任务按太平洋日界到期; 季节任务到期时间取赛季结束
        quest_type, expiry = _quest_type_and_expiry(quest_name, now)

        # 去重: 若该用户已有同名"未过期且未领取"的任务, 直接复用, 不再新建。
        # 否则向导每次提示都会建一条新 quest_id, 旧的滞留为 active, 客户端按 start_time
        # 取到的可能是带旧基线的那条, 导致进度错乱、互动卡住。
        existing = (PlayerQuest.query
                    .filter(PlayerQuest.user_id == user_id,
                            PlayerQuest.quest_name == quest_name,
                            PlayerQuest.expiry_time >= now,
                            PlayerQuest.quest_status.in_(["active", "succeeded"]))
                    .order_by(PlayerQuest.start_time.desc()).first())
        if existing:
            stats = QuestStat.query.filter_by(quest_id=existing.quest_id).all()
            init_s, comp_s, tgt_s = [], [], []
            for s in stats:
                if s.stat_category == "init_stats":
                    init_s.append({"stat_type": s.stat_type, "value": s.value})
                elif s.stat_category == "completion_requirements":
                    comp_s.append({"stat_type": s.stat_type, "value": s.value})
                elif s.stat_category == "target_values":
                    tgt_s.append({"stat_type": s.stat_type, "value": s.value})
            return jsonify({
                "expiry_time": existing.expiry_time,
                "is_scheduled_quest": bool(qdef.get("scheduled_event")),
                "previously_completed": False,
                "quest_giver": quest_giver,
                "quest_id": existing.quest_id,
                "quest_name": quest_name,
                "quest_type": quest_type,
                "start_time": existing.start_time,
                "stats": {
                    "completion_requirements": comp_s,
                    "init_stats": init_s,
                    "target_values": tgt_s,
                },
                "status": existing.status or existing.quest_status or "active",
                "success": True,
            }), 200

        quest_id = str(uuid.uuid4())
        pq = PlayerQuest(user_id=user_id, quest_name=quest_name, quest_id=quest_id,
                         quest_status="active", start_time=now, expiry_time=expiry,
                         status="active")
        db.session.add(pq)

        init_stats, completion_reqs, target_values = [], [], []
        for i, st in enumerate(stat_types):
            req_val = int(stat_vals[i]) if i < len(stat_vals) else 1
            # 记录"创建时玩家该成就的累计值"为隐藏基线 (stat_category="baseline")。
            # 成就是全局累计的(如击掌总次数), 任务进度必须按"自接取以来的增量"计算:
            #   progress = 当前累计 - 基线; 完成条件 progress >= req_val。
            # 否则老玩家累计值早已 >= req, 任务要么瞬间完成、要么 init 显示成累计值
            # (如 101) 与 target(1) 对不上 -> 客户端进度渲染错乱、互动卡在第一段动画。
            cur = AchievementStat.query.filter_by(
                user_id=user_id, stat_type=st).first()
            baseline = int(cur.value) if cur and cur.value is not None else 0
            db.session.add(QuestStat(quest_id=quest_id, stat_type=st,
                                     stat_category="baseline", value=baseline))
            db.session.add(QuestStat(quest_id=quest_id, stat_type=st,
                                     stat_category="init_stats", value=baseline))
            db.session.add(QuestStat(quest_id=quest_id, stat_type=st,
                                     stat_category="completion_requirements", value=req_val))
            db.session.add(QuestStat(quest_id=quest_id, stat_type=st,
                                     stat_category="target_values", value=baseline + req_val))
            init_stats.append({"stat_type": st, "value": baseline})
            completion_reqs.append({"stat_type": st, "value": req_val})
            target_values.append({"stat_type": st, "value": baseline + req_val})
        db.session.commit()

        return jsonify({
            "expiry_time": expiry,
            "is_scheduled_quest": bool(qdef.get("scheduled_event")),
            "previously_completed": False,
            "quest_giver": quest_giver,
            "quest_id": quest_id,
            "quest_name": quest_name,
            "quest_type": quest_type,
            "start_time": now,
            "stats": {
                "completion_requirements": completion_reqs,
                "init_stats": init_stats,
                "target_values": target_values,
            },
            "status": "active",
            "success": True,
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
