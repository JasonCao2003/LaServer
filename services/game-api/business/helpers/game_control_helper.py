# -*- coding: utf-8 -*-
"""
游戏控制配置读取辅助

集中管理可由管理面板控制的游戏参数:
- 季节结算奖励货币 / 当前赛季号
- 先祖复刻 (get_spirit_shops) 返回内容

配置文件: config/game_control.json
商店物品(价格/货币/上架)直接存于数据库 shop_item 表, 由面板操作, 不在此文件。
活动日程由 config/event_schedule.json 控制, 面板直接编辑该文件。
"""
from paths import CONFIG_DIR, SERVICE_ROOT
import os
import json
import threading

_lock = threading.Lock()

DEFAULT_FINALIZE_CURRENCY = {
    "candles": 0, "heart": 0, "heart_wax": 0,
    "prestige": 0, "prestige_vip": 0, "prestige_wax": 0,
    "season_candle": 0, "season_wax": 0, "season_wax_14": 0,
    "vip": 0, "wax": 3,
}

# 季节每日任务默认池(管理面板可改 config/game_control.json -> season_quests)
DEFAULT_SEASON_QUEST_POOL = [
    {"daily_quest_def_id": "fetchlight_mind", "stat_type": "fetchlight_mind_count"},
    {"daily_quest_def_id": "pickup_30_wax", "stat_type": "wax_pickup_count"},
    {"daily_quest_def_id": "hug_someone", "stat_type": "non_unique_hug_count"},
    {"daily_quest_def_id": "save_a_spirit_in_night", "stat_type": "night_collectibles_collected_count"},
]
DEFAULT_SEASON_QUEST_REWARD_SEASON_CANDLE = 1
DEFAULT_SEASON_QUEST_REWARD_CANDLE = 1


def _config_path():
    root = SERVICE_ROOT
    return os.path.join(CONFIG_DIR, "game_control.json")


def load_config():
    """读取游戏控制配置, 失败时返回安全默认值。"""
    path = _config_path()
    try:
        with _lock:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return {
            "season": {
                "current_season": 25,
                "season_unlock_name": "season_finish_25",
                "finalize_currency": dict(DEFAULT_FINALIZE_CURRENCY),
            },
            "spirit_shops": {"enabled": False, "shops": []},
        }


def get_season_config():
    cfg = load_config()
    s = cfg.get("season", {}) or {}
    cur = s.get("finalize_currency") or {}
    currency = dict(DEFAULT_FINALIZE_CURRENCY)
    currency.update({k: v for k, v in cur.items() if k in DEFAULT_FINALIZE_CURRENCY})
    csn = int(s.get("current_season", 25))
    return {
        "current_season": csn,
        "season_unlock_name": s.get("season_unlock_name") or f"season_finish_{csn}",
        "finalize_currency": currency,
        "start_time": int(s.get("start_time", 0) or 0),
        "duration_days": int(s.get("duration_days", 70) or 70),
        "force_end": bool(s.get("force_end", False)),
        "convert_ratio": int(s.get("convert_ratio", 1) or 1),
    }


def get_season_state():
    """根据 season 配置计算赛季运行状态, 供 get_event_schedule / season_finalize 使用。

    返回 dict:
      current_season: 赛季号
      start_time    : 开始 epoch (0 表示未设置 -> 视为已开始/永久进行中)
      end_time      : 结束 epoch (start_time + duration_days*86400)
      force_end     : 是否被管理面板"提前结束"
      state         : "future" 未开始 | "active" 进行中 | "ended" 已结束
      convert_ratio : 季蜡->蜡烛 转换比例

    判定:
      - force_end=True            -> ended (无论时间)
      - start_time<=0             -> active (未设开始时间, 当作永久进行中)
      - now < start_time          -> future
      - start_time<=now<end_time  -> active
      - now >= end_time           -> ended
    """
    import time as _t
    sc = get_season_config()
    now = int(_t.time())
    start = sc["start_time"]
    dur = sc["duration_days"] * 86400
    end = (start + dur) if start > 0 else 0
    if sc["force_end"]:
        state = "ended"
    elif start <= 0:
        state = "active"
    elif now < start:
        state = "future"
    elif now < end:
        state = "active"
    else:
        state = "ended"
    return {
        "current_season": sc["current_season"],
        "season_unlock_name": sc["season_unlock_name"],
        "start_time": start,
        "end_time": end,
        "force_end": sc["force_end"],
        "convert_ratio": sc["convert_ratio"],
        "state": state,
    }



def get_season_quests_config():
    """季节每日任务配置: 任务池 + 领取奖励量。管理面板可在中控编辑。"""
    cfg = load_config()
    sq = cfg.get("season_quests", {}) or {}
    pool = sq.get("daily_pool") or []
    # 过滤掉缺字段的项, 保证每项都有 daily_quest_def_id / stat_type
    clean = []
    for it in pool:
        if not isinstance(it, dict):
            continue
        qid = it.get("daily_quest_def_id")
        if not qid:
            continue
        clean.append({
            "daily_quest_def_id": qid,
            "stat_type": it.get("stat_type") or (str(qid) + "_count"),
        })
    if not clean:
        clean = [dict(x) for x in DEFAULT_SEASON_QUEST_POOL]
    def _int(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d
    return {
        "daily_pool": clean,
        "reward_season_candle": _int(sq.get("reward_season_candle"),
                                     DEFAULT_SEASON_QUEST_REWARD_SEASON_CANDLE),
        "reward_candle": _int(sq.get("reward_candle"),
                              DEFAULT_SEASON_QUEST_REWARD_CANDLE),
    }


# ===================== 每日任务池 (季节每日 / 普通每日) =====================
# 需求: 每次服务器重启 或 到达每天0点 自动换新任务; 任务从数据库任务目录随机。
# 中控可分别控制"季节每日任务"和"普通每日任务": 手动增删 + 随机4个 + 自动随机开关。
# game_control.json -> daily_quests:
#     "season": {"auto_random": bool, "count": 4, "pool": [{daily_quest_def_id, stat_type}, ...]},
#     "normal": {"auto_random": bool, "count": 4, "pool": [...]},
#     "_last_token": "YYYY-MM-DD#<server_start_ts>"   # 上次自动刷新的标记
import random as _random

DAILY_DEFAULT_COUNT = 4


def _catalog_path():
    root = SERVICE_ROOT
    return os.path.join(CONFIG_DIR, "daily_quest_catalog.json")


def load_daily_quest_catalog():
    """读取每日任务目录 (build_daily_quest_catalog.py 生成)。返回 list。"""
    try:
        with open(_catalog_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        cat = data.get("daily_quest_catalog") or []
        out = []
        for c in cat:
            qid = c.get("daily_quest_def_id")
            if not qid:
                continue
            out.append({
                "daily_quest_def_id": qid,
                "stat_type": c.get("stat_type") or (str(qid) + "_count"),
                "cn": c.get("cn", qid),
            })
        return out
    except Exception:
        return []


def _server_start_ts():
    """读取 pid 文件里的 started 作为服务器启动时间戳; 失败用进程级常量。"""
    root = SERVICE_ROOT
    try:
        for fn in os.listdir(root):
            if fn.startswith(".sky_server_") and fn.endswith(".pid"):
                with open(os.path.join(root, fn), "r", encoding="utf-8") as f:
                    return int(json.load(f).get("started", 0))
    except Exception:
        pass
    return 0


def _today_str():
    from datetime import date
    return date.today().isoformat()


def _refresh_token():
    """刷新标记: 日期 + 服务器启动时间。日期变(0点)或重启都会变 -> 触发换新。"""
    return f"{_today_str()}#{_server_start_ts()}"


def _random_pool(count):
    """从目录随机 count 个 (去重)。"""
    cat = load_daily_quest_catalog()
    if not cat:
        return [dict(x) for x in DEFAULT_SEASON_QUEST_POOL]
    n = min(max(1, int(count or DAILY_DEFAULT_COUNT)), len(cat))
    picked = _random.sample(cat, n)
    return [{"daily_quest_def_id": p["daily_quest_def_id"],
             "stat_type": p["stat_type"]} for p in picked]


def _clean_pool(pool):
    out = []
    for it in pool or []:
        if not isinstance(it, dict):
            continue
        qid = it.get("daily_quest_def_id")
        if not qid:
            continue
        out.append({
            "daily_quest_def_id": qid,
            "stat_type": it.get("stat_type") or (str(qid) + "_count"),
        })
    return out


def get_daily_quests_config(auto_refresh=True):
    """
    返回 {"season": [pool], "normal": [pool], "reward_season_candle", "reward_candle"}。
    auto_refresh=True 时: 若某类开启 auto_random 且刷新标记变化(重启/0点),
    自动从目录随机 count 个写回 game_control.json。
    兼容旧字段 season_quests.daily_pool -> 作为 season 初始池。
    """
    cfg = load_config()
    dq = cfg.get("daily_quests")
    sq = cfg.get("season_quests", {}) or {}

    def _int(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d

    reward_sc = _int(sq.get("reward_season_candle"), DEFAULT_SEASON_QUEST_REWARD_SEASON_CANDLE)
    reward_c = _int(sq.get("reward_candle"), DEFAULT_SEASON_QUEST_REWARD_CANDLE)

    if not isinstance(dq, dict):
        # 首次迁移: 用旧 season_quests.daily_pool 作季节池, 普通池随机4个
        dq = {
            "season": {"auto_random": False, "count": DAILY_DEFAULT_COUNT,
                       "pool": _clean_pool(sq.get("daily_pool")) or [dict(x) for x in DEFAULT_SEASON_QUEST_POOL]},
            "normal": {"auto_random": True, "count": DAILY_DEFAULT_COUNT,
                       "pool": _random_pool(DAILY_DEFAULT_COUNT)},
            "_last_token": "",
        }

    changed = False
    token = _refresh_token()
    if auto_refresh and dq.get("_last_token") != token:
        for key in ("season", "normal"):
            grp = dq.get(key) or {}
            if grp.get("auto_random"):
                grp["pool"] = _random_pool(grp.get("count", DAILY_DEFAULT_COUNT))
                dq[key] = grp
                changed = True
        dq["_last_token"] = token
        changed = True

    if changed:
        cfg["daily_quests"] = dq
        try:
            with _lock:
                with open(_config_path(), "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    season_pool = _clean_pool((dq.get("season") or {}).get("pool"))
    normal_pool = _clean_pool((dq.get("normal") or {}).get("pool"))
    if not season_pool and not normal_pool:
        season_pool = [dict(x) for x in DEFAULT_SEASON_QUEST_POOL]
    return {
        "season": season_pool,
        "normal": normal_pool,
        "reward_season_candle": reward_sc,
        "reward_candle": reward_c,
    }