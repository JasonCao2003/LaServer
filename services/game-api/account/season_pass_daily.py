# -*- coding: utf-8 -*-
"""季卡(vip)每日额外 1 根季蜡。

官方季卡权益之一: 每天(按太平洋日界)登录额外送 1 根季节蜡烛。私服之前只发了
`vip` 标记和季卡券, 没有任何每日发放 —— 季卡在游戏里看起来"除了图标没效果"。

做法: 在 `/account/get_currency` 里"检查并补发一次", 发放在数据库里是
`season_candle += 1`(与客户端季蜡条同一个字段), 最后发放日记在旁路文件
`instance/season_pass_daily.json`, 不新增数据库列, 删文件即可回滚。
"""
import io
import json
import logging
import os
import threading
import time

from helpers.currency_helper import ensure_user_currency
from database.models import db

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(_ROOT, "data", "instance", "season_pass_daily.json")
_lock = threading.Lock()


def _load():
    try:
        with io.open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        logger.warning("season_pass_daily 保存失败: %s", e)


def _today():
    """太平洋日界下的今天是哪天(与官方每日任务同一个日界)。"""
    from service.quest.get_quest_schedule import _pacific_date_of_epoch
    return _pacific_date_of_epoch(int(time.time())).isoformat()


def _prune(state, today):
    """只保留最近 45 天的发放记录, 防文件无限增长。"""
    try:
        from datetime import date, timedelta
        cutoff = (date.fromisoformat(today) - timedelta(days=45)).isoformat()
        return {k: v for k, v in state.items() if str(v) >= cutoff}
    except Exception:
        return state


def grant_daily_bonus(user_id):
    """vip>0 且今天没领过 -> season_candle += 1。返回 True 表示本次真的发放了。"""
    try:
        uc = ensure_user_currency(user_id)
        if int(getattr(uc, "vip", 0) or 0) <= 0:
            return False
        day = _today()
        with _lock:
            state = _load()
            if state.get(user_id) == day:
                return False
            uc.season_candle = int(uc.season_candle or 0) + 1
            db.session.commit()
            state[user_id] = day
            _save(_prune(state, day))
        logger.info("season_pass_daily: %s 季卡每日季蜡 +1 (day=%s)", user_id, day)
        return True
    except Exception as e:
        logger.warning("season_pass_daily 发放失败(%s): %s", user_id, e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False
