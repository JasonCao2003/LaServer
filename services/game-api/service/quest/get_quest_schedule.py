# -*- coding: utf-8 -*-
"""
Service接口: /service/quest/api/v1/get_quest_schedule

v0.30+ 客户端的"每日任务"完全由本接口驱动:
  客户端先调用本接口拿到 [{epoch, quest_id}, ...] 排期, 用"今天的日界 epoch"
  过滤出今天的任务并展示; 玩家接取时调用 /service/quest/api/v1/create 建任务,
  完成由 set_achievement_stats 推进, 最后 update_quest(collect) 领奖。

之前的问题:
  - 旧实现返回 {"get_quest_schedule": []} (键名错且空)。
  - 又因该路径不在 static_responses 排除表里, 高版本客户端实际收到的是接口库里
    写死的 2026-01-13/14 排期 (epoch 1768291200/1768377600)。今天的日界 epoch
    与之不符 -> 客户端筛不出"今天的任务" -> 每日任务整列为空, 既看不到也领不了,
    向导里依赖每日任务的互动(如击掌 high_five_someone)也无法激活/推进。

本实现:
  - 按"太平洋时间(Sky 官方日界)午夜"为每天的 epoch (与抓包 1768291200 对齐),
    生成 [今天-1, ..., 今天+N] 的排期窗口, 保证今天一定在内。
  - 今天的任务取中控配置的每日任务池 (game_control.json -> daily_quests,
    season + normal, 与 get_season_quests 同源, 管理面板/0点/重启换新即时生效)。
  - 其它日期用"按日期确定性随机"从任务目录取 4 个, 仅作排期预览, 不影响领取。
  - 键名修正为 "quest_schedule", 与官方/客户端一致。
"""

from flask import request, jsonify
import calendar
import hashlib
import json
import os
from datetime import date, timedelta


# 排期窗口: 今天往前 1 天, 往后 6 天 (确保今天的日界一定被客户端命中)
_WINDOW_PAST = 1
_WINDOW_FUTURE = 6
_PER_DAY = 4

# 客户端能渲染的任务定义 id 集合 (来自 get_quest_defs, 客户端实际加载的就是它)。
# 排期里只下发客户端"有定义"的任务, 否则客户端无法显示/接取该任务。
_DEFS_IDS = None


def _server_root():
    # service/quest/ -> 上两级是服务器根目录
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _renderable_ids():
    """客户端持有定义的任务 id 集合。优先 get_quest_defs 静态库, 其次 config/quest.json。"""
    global _DEFS_IDS
    if _DEFS_IDS is not None:
        return _DEFS_IDS
    ids = set()
    root = _server_root()
    for rel in ("resources/static_responses/account__quest__get_quest_defs.json",
                "config/quest.json"):
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                data = json.load(f)
            defs = data.get("get_quest_defs", data) if isinstance(data, dict) else data
            for q in defs:
                if isinstance(q, dict) and q.get("id"):
                    ids.add(q["id"])
        except Exception:
            continue
    _DEFS_IDS = ids
    return ids


def _us_pacific_dst(d):
    """美国夏令时(太平洋): 3月第二个周日 ~ 11月第一个周日。"""
    y = d.year
    march = date(y, 3, 1)
    first_sun_mar = 1 + (6 - march.weekday()) % 7
    dst_start = date(y, 3, first_sun_mar + 7)      # 第二个周日
    nov = date(y, 11, 1)
    first_sun_nov = 1 + (6 - nov.weekday()) % 7
    dst_end = date(y, 11, first_sun_nov)           # 第一个周日
    return dst_start <= d < dst_end


def _pacific_midnight_epoch(d):
    """某日(太平洋时区)午夜的 UTC epoch。与抓包 1768291200(2026-01-13)对齐。"""
    off = 7 if _us_pacific_dst(d) else 8           # PDT=UTC-7, PST=UTC-8
    return calendar.timegm((d.year, d.month, d.day, off, 0, 0, 0, 0, 0))


def _pacific_today():
    """太平洋时区(Sky 官方每日日界)的当前日期。

    每日任务的日界是太平洋午夜, 客户端也按太平洋时间判断"今天"。服务器若用本地
    日期(如北京 UTC+8)当"今天", 当北京已是次日、太平洋仍是前一日时, 会把中控的
    当日任务池错配到晚一天的 epoch 上 -> 客户端"今天"那格拿到的是随机预览任务,
    每日任务显示异常、接取/领取走不通。故这里统一用太平洋当前日期。
    """
    from datetime import datetime
    utc_now = datetime.utcnow()
    # 先用 UTC 日期粗判 DST(偏移仅差 1 小时, 不会导致跨多天误差)
    off = 7 if _us_pacific_dst(utc_now.date()) else 8
    return (utc_now - timedelta(hours=off)).date()


def _today_quest_ids():
    """今天的每日任务 id 列表 = 中控每日任务池(season + normal)。失败回退目录随机。
    只保留客户端有定义的 id。"""
    renderable = _renderable_ids()
    try:
        from helpers.game_control_helper import get_daily_quests_config
        cfg = get_daily_quests_config()
        ids = []
        for q in (cfg.get("season", []) + cfg.get("normal", [])):
            qid = q.get("daily_quest_def_id")
            if qid and qid not in ids and (not renderable or qid in renderable):
                ids.append(qid)
        if ids:
            return ids
    except Exception:
        pass
    return _deterministic_ids(_pacific_today())


def _catalog_ids():
    """任务目录里、且客户端有定义的 id 列表。"""
    renderable = _renderable_ids()
    try:
        from helpers.game_control_helper import load_daily_quest_catalog
        out = []
        for c in load_daily_quest_catalog():
            qid = c.get("daily_quest_def_id")
            if qid and (not renderable or qid in renderable):
                out.append(qid)
        return out
    except Exception:
        return []


def _deterministic_ids(d, count=_PER_DAY):
    """按日期确定性地从任务目录取 count 个(同一天同一组, 仅作排期预览)。"""
    cat = _catalog_ids()
    if not cat:
        return []
    seed = int(hashlib.md5(d.isoformat().encode()).hexdigest(), 16)
    n = min(count, len(cat))
    picked, idx = [], seed
    used = set()
    for _ in range(n):
        idx = (idx * 1103515245 + 12345) & 0x7FFFFFFF
        j = idx % len(cat)
        for _k in range(len(cat)):
            if j not in used:
                break
            j = (j + 1) % len(cat)
        used.add(j)
        picked.append(cat[j])
    return picked


def _pacific_date_of_epoch(epoch):
    """把客户端上报的 current_time 换算成太平洋日期(客户端时钟与服务器不一致时以客户端为准)。"""
    from datetime import datetime
    utc = datetime.utcfromtimestamp(int(epoch))
    off = 7 if _us_pacific_dst(utc.date()) else 8
    return (utc - timedelta(hours=off)).date()


def get_quest_schedule():
    try:
        data = request.get_json(silent=True) or {}
        ct = data.get("current_time")
        # 有 current_time 就以客户端时间为准(不同版本/不同时区客户端都会带),
        # 否则用服务器太平洋当前日期。
        today = _pacific_date_of_epoch(ct) if ct else _pacific_today()
        schedule = []
        for delta in range(-_WINDOW_PAST, _WINDOW_FUTURE + 1):
            d = today + timedelta(days=delta)
            epoch = _pacific_midnight_epoch(d)
            qids = _today_quest_ids() if delta == 0 else _deterministic_ids(d)
            for qid in qids:
                schedule.append({"epoch": epoch, "quest_id": qid})
        return jsonify({"quest_schedule": schedule}), 200
    except Exception as e:
        # 出错时退化为空排期(键名正确), 客户端不会因格式错而卡死
        return jsonify({"quest_schedule": [], "error": str(e)}), 200
