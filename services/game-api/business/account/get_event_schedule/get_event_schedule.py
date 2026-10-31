from paths import CONFIG_DIR, SERVICE_ROOT
from flask import Response, request
import os
import json
import time
import random
import re
from datetime import date, datetime, time as dtime

# tcandle 活动后缀类型
TCANDLE_TYPES = ["tcandles1", "tcandles2", "tcandle_master"]
NEVER = 9999999999  # 禁用: 偏移量远未来, base_time+NEVER 永不触发

# 烛火分组(合并): "candles" = 烛火1+烛火2 合并为一类; "tcandle_master" = 主控
# 全部启用 -> 该组所有活动启用; 随机 -> 在该组所有活动名中随机 auto_count 个(为期一天)
TCANDLE_GROUPS = {
    "candles": ["tcandles1", "tcandles2"],
    "tcandle_master": ["tcandle_master"],
}


def _tcandle_type_of(name):
    """根据活动名后缀判断 tcandle 类型, 非 tcandle 返回 None。"""
    for t in TCANDLE_TYPES:
        if name.endswith("_" + t):
            return t
    return None


def _tcandle_group_of(name):
    """返回活动所属分组 key (candles / tcandle_master), 非 tcandle 返回 None。"""
    t = _tcandle_type_of(name)
    if not t:
        return None
    for gk, types in TCANDLE_GROUPS.items():
        if t in types:
            return gk
    return None


def _tcandle_map_of(name):
    """event_dusk_tcandles1 -> dusk ; event_sunset_tcandle_master -> sunset"""
    s = name[len("event_"):] if name.startswith("event_") else name
    for t in TCANDLE_TYPES:
        suf = "_" + t
        if s.endswith(suf):
            return s[:-len(suf)]
    return s


def _normalize_tcandle_groups(tc):
    """
    统一返回分组配置 {candles:{...}, tcandle_master:{...}}。
    兼容:
      - 新分组结构 tc["groups"]
      - 三类结构 tc["types"] (tcandles1/tcandles2 合并入 candles, 任一开即开)
      - 旧扁平结构 (all_enable_candles1/2, mode/auto_type)
    每组键: all_enable, auto, auto_count, duration_seconds。
    """
    def _mk(all_en=False, auto=False, cnt=2, dur=86400):
        return {"all_enable": bool(all_en), "auto": bool(auto),
                "auto_count": int(cnt or 2), "duration_seconds": int(dur or 86400)}

    groups = tc.get("groups")
    if isinstance(groups, dict) and groups:
        out = {}
        for gk in TCANDLE_GROUPS:
            c = dict(groups.get(gk, {}) or {})
            out[gk] = _mk(c.get("all_enable"), c.get("auto"),
                          c.get("auto_count", 2), c.get("duration_seconds", 86400))
        return out

    types = tc.get("types")
    if isinstance(types, dict) and types:
        t1 = types.get("tcandles1", {}) or {}
        t2 = types.get("tcandles2", {}) or {}
        tm = types.get("tcandle_master", {}) or {}
        return {
            "candles": _mk(t1.get("all_enable") or t2.get("all_enable"),
                           t1.get("auto") or t2.get("auto"),
                           t1.get("auto_count") or t2.get("auto_count") or 2,
                           t1.get("duration_seconds") or t2.get("duration_seconds") or 86400),
            "tcandle_master": _mk(tm.get("all_enable"), tm.get("auto"),
                                  tm.get("auto_count", 2), tm.get("duration_seconds", 86400)),
        }

    dur = int(tc.get("duration_seconds", 86400) or 86400)
    cnt = int(tc.get("auto_count", 2) or 2)
    mode = tc.get("mode", "manual")
    cand_all = bool(tc.get("all_enable_candles1") or tc.get("all_enable_candles2"))
    cand_auto = (mode == "auto")
    return {
        "candles": _mk(cand_all, cand_auto, cnt, dur),
        "tcandle_master": _mk(False, False, cnt, dur),
    }


# 标准 tcandle 活动名 = 5 张图 x 3 类(烛火1/烛火2/主控)。
_TCANDLE_REALMS = ("prairie", "rain", "sunset", "dusk", "night")


def _tcandle_names_from_library(config_dir):
    """全部 tcandle 活动名(优先从高版本库/主库文件里取, 取不到用标准 5 图 x 3 类)。"""
    names, seen = [], set()

    def _add(n):
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    for fn in ("event_schedule_high.json", "event_schedule.json"):
        try:
            with open(os.path.join(config_dir, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
            for e in (d.get("event_schedule", {}) or {}).get("events", []) or []:
                if _tcandle_group_of(e.get("name", "")):
                    _add(e.get("name"))
        except Exception:
            continue
        if names:
            break
    if not names:
        for realm in _TCANDLE_REALMS:
            for t in TCANDLE_TYPES:
                _add("event_%s_%s" % (realm, t))
    return names


def _apply_tcandles(data, config_dir):
    """
    每次请求按 game_control.json 的 tcandles 配置重写所有 tcandle 活动的 when/duration。
    两组独立:
      - candles      : 烛火1+烛火2 合并。全部启用=10个活动全开; 随机=在10个活动名中随机
                       auto_count(默认2)个启用, 为期一天(0点起算)。
      - tcandle_master: 主控。全部启用=5地图全开; 随机=随机 auto_count 个。
    全部启用 when=当前时刻偏移, 随机 when=当天0点偏移。禁用 when=[NEVER]。
    """
    gc_path = os.path.join(config_dir, "game_control.json")
    try:
        with open(gc_path, "r", encoding="utf-8") as f:
            gc = json.load(f)
    except Exception:
        return data

    tc = gc.get("tcandles", {}) or {}
    master_off = not tc.get("enabled", True)
    groups_cfg = _normalize_tcandle_groups(tc)

    es = data.setdefault("event_schedule", {})
    base_time = es.get("base_time", 1587366000)
    evs = es.setdefault("events", [])
    now_off = int(time.time()) - base_time

    # 【关键】低版本库(初始备份的 event_schedule / event_schedule_low.json, 只有 global_*)
    # 里**根本没有 tcandle 事件** -> 中控"开启所有蜡烛活动"对老客户端(0.7.1/0.12/0.22/
    # 0.23 等 <0.26.0)完全无效, 游戏里一根蜡烛都不出现(用户反馈: 开启所有蜡烛活动后
    # 游戏里没有)。这里先把缺失的 tcandle 事件按标准名补进**内存响应**(不写文件),
    # 再统一按中控配置设置 when/duration, 使所有版本都生效。
    _have = {e.get("name") for e in evs}
    for _nm in _tcandle_names_from_library(config_dir):
        if _nm not in _have:
            evs.append({"name": _nm, "when": [NEVER], "duration": "1440"})
            _have.add(_nm)

    midnight_off = int(datetime.combine(date.today(), dtime.min).timestamp()) - base_time
    today = date.today().isoformat()

    # 按分组收集活动 (以活动名为单位) -> {group_key: [event_dict, ...]}
    by_group = {gk: [] for gk in TCANDLE_GROUPS}
    for e in evs:
        gk = _tcandle_group_of(e.get("name", ""))
        if gk:
            by_group[gk].append(e)

    for gk, members in by_group.items():
        if not members:
            continue
        cfg = groups_cfg.get(gk, {}) or {}
        names = sorted(e.get("name", "") for e in members)
        dur_min = str(max(1, int(cfg.get("duration_seconds", 86400)) // 60))

        if not master_off and cfg.get("all_enable"):
            enabled = set(names)
            # 稍早于此刻(1 分钟前) = "已在进行中"。若正好写 now, 客户端时钟比服务器慢
            # 一点就会判成"尚未开始" -> 游戏里看不到蜡烛。
            enable_off = now_off - 60
            use_dur = dur_min
        elif not master_off and cfg.get("auto"):
            # 在该组所有活动名中随机 auto_count 个, 为期一天
            cnt = min(int(cfg.get("auto_count", 2)), len(names))
            rng = random.Random(f"{today}:{gk}")
            enabled = set(rng.sample(names, cnt)) if cnt > 0 else set()
            enable_off = midnight_off
            use_dur = "1440"  # 为期一天
        else:
            enabled = set()
            enable_off = now_off
            use_dur = dur_min

        for e in members:
            if e.get("name", "") in enabled:
                e["when"] = [enable_off]
                e["duration"] = use_dur
            else:
                e["when"] = [NEVER]
    return data


# ===== "关闭全部活动" (节日活动 + 先祖复刻活动) =====
# 中控开关: config/game_control.json -> {"events": {"enabled": false}}
#   缺省(或 true) = 不改动, 保持库文件原样;
#   false          = 把下列"活动类"事件 when 置 [NEVER](duration="0"), 全部关停。
#
# 【只关活动, 不动世界机制】活动 = 节日/限时活动 + 先祖复刻; 而下面这些是游戏本身
# 的常驻调度(昼夜/风暴/黑暗石/收集物/篝火暖气/任务/商店轮换), 关了会直接破坏正常
# 游玩(例如 social_wax_on / social_*_bonfirecamp 是篝火与山洞的烛火来源, 关了反而
# 收不到烛火), 所以**不在**关闭名单里, 一律保持库文件原样:
#   · season_NN / season_NN_ever_*     季节机制(主事件 + 参与标记)
#   · quest_ap* / apNN_fetch_*         季节向导任务与其事件
#   · global_daily_reset_time          每日重置锚点(关了每日任务不刷新)
#   · 烛火 candles / tcandle_master     由 _apply_tcandles 按 tcandles 配置单独控制
#   · social_* / collectible_*        篝火·餐桌·暖炉·收集物(常驻世界机制)
#   · <地图>_cycle_* / stormy_* / *_darkstone_* / snake_* / dusk_* / rain_* /
#     sunset_* / projector_*          世界状态与地形机关
#   · gen_shop_*_reset / weekly_* / musicshop_*_rotation / enable_*  商店与轮换
#   · quest_*(非 ap) / fetch_* / wing_buff_conversion_period       任务与常规开关
_ACTIVITY_RE = re.compile(r"""^(?:
      spiritshopitem\w*          # 先祖复刻: 复刻先祖的兑换项
    | visiting_?vendor\w*        # 先祖复刻: 旅行先祖/旅行商人到访
    | skyfest\w*                 # 天空节
    | aurora\w*                  # 极光音乐会
    | ap18_ritual\w*             # 极光仪式(音乐会现场)
    | feast\w*                   # 盛宴节
    | te_ep\w*                   # 限时活动章节
    | fireworks\w*               # 烟花活动场次
    | nature_geyser\w*           # 自然日间歇泉
    | days?_of_\w+ | days?_\w+   # 节日(Days of ...)
    | global_(?!daily_reset\b)\w+  # 假日/活动的全局开关(保留 global_daily_reset_time)
)""", re.IGNORECASE | re.VERBOSE)  # VERBOSE: 允许上面这种换行+#注释的写法
_KEEP_EVENT_RE = re.compile(r"^(?:season_0*\d+(?:_|$)|quest_ap|ap\d+_fetch)", re.IGNORECASE)
_KEEP_EVENT_EXACT = ("global_daily_reset_time",)


def _close_all_events(data, config_dir):
    """按 game_control.json -> events.enabled 关闭全部活动(节日 / 先祖复刻)。

    只改内存中的 when/duration(不写文件): when=[NEVER] 表示触发时间远在未来, 永不生效。
    命中 _ACTIVITY_RE 的事件被关; 保留项见 _KEEP_EVENT_RE / _KEEP_EVENT_EXACT /
    烛火分组(与 _apply_tcandles 同一套分组判定), 世界机制类事件不受影响。
    """
    gc_path = os.path.join(config_dir, "game_control.json")
    try:
        with open(gc_path, "r", encoding="utf-8") as f:
            gc = json.load(f)
    except Exception:
        return data
    ev_cfg = gc.get("events", {}) or {}
    if ev_cfg.get("enabled", True):
        return data

    es = data.setdefault("event_schedule", {})
    closed = 0
    for e in es.get("events", []) or []:
        name = e.get("name", "") or ""
        if name in _KEEP_EVENT_EXACT:
            continue
        if _KEEP_EVENT_RE.match(name):
            continue
        if _tcandle_group_of(name):  # 烛火/主控: 与 _apply_tcandles 同一套分组判定
            continue
        if not _ACTIVITY_RE.match(name):  # 非活动(世界机制/任务/商店轮换等) -> 不动
            continue
        e["when"] = [NEVER]
        e["duration"] = "0"
        closed += 1
    es["_closed_activity_count"] = closed  # 仅调试用; 响应体只取 base_time/events, 不会外发
    return data


def _parse_client_version():
    """从 User-Agent 解析客户端版本, 返回 (major, minor, patch) 或 None

    统一走 client_version 纠偏(见 config/client_version_overrides.json):
    魔改老客户端可能谎报版本(实测 0.13.4 报 0.23.6.354648)。
    """
    try:
        from business.helpers.client_version import client_version_from_request
        return client_version_from_request()
    except Exception:
        pass
    ua = request.headers.get("User-Agent", "")
    m = re.search(r'Sky[^/]*/(\d+)\.(\d+)\.(\d+)', ua)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _apply_dynamic_timing(data, config_dir):
    """
    所有版本: 接口库/低版本库里的 season_NN / global_daily_reset_time 用的是写死的
    固定 when 偏移量, 现在已过期 -> 游戏判定"季节已结束"、每日任务倒计时为负、无法
    领取/计入。(低版本 event_schedule_low.json 甚至连 season_* 事件都没有。)

    本函数按"当前真实时间"重新锚定:
      - global_daily_reset_time: when 重设为 [昨日, 今日, 明日] 的每日重置点偏移,
        重置时刻 = 本地 0 点(午夜), 使每日任务在本地 0 点刷新、倒计时为正。
      - 当前赛季 season_<N> (取 game_control.json -> season.current_season):
        when 重设为 now 之前、duration 覆盖到远未来, 使季节"进行中"。
    其余 season_* 历史事件保持原状(已结束)。
    """
    es = data.setdefault("event_schedule", {})
    base_time = es.get("base_time", 1587366000)
    evs = es.setdefault("events", [])
    now = int(time.time())
    now_off = now - base_time
    DAY = 86400

    # 当前赛季生命周期状态 (一键启动/改时间/提前结束都体现在这里)
    cur_season = None
    season_state = None
    try:
        from business.helpers.game_control_helper import get_season_state
        st = get_season_state()
        cur_season = int(st["current_season"])
        season_state = st
    except Exception:
        # 兜底: 直接读 current_season, 按"进行中"处理
        try:
            with open(os.path.join(config_dir, "game_control.json"), "r", encoding="utf-8") as f:
                gc = json.load(f)
            cur_season = int(gc.get("season", {}).get("current_season"))
        except Exception:
            cur_season = None

    # 本地 0 点(午夜)对齐: 每日任务在本地 0 点重置
    midnight_today = int(datetime.combine(date.today(), dtime.min).timestamp())
    midnight_off = midnight_today - base_time

    def _season_when_dur():
        """根据赛季状态返回 (when_offset_list, duration_str)。
          active : 从开始时间起, 覆盖到结束时间(分钟); 未设开始时间则 7 天前起、远未来。
          future : 从未来开始时间起, 客户端显示"即将开始", 未到不进行。
          ended  : when 在过去且已结束 -> 客户端判定"季节已结束" -> 调 season_finalize
                   (服务端做一次货币转换+弹窗, 之后幂等)。
        """
        if not season_state:
            return [now_off - 7 * DAY], "99999999"
        state = season_state["state"]
        start = season_state["start_time"]
        end = season_state["end_time"]
        if state == "future":
            start_off = start - base_time
            dur_min = max(1, (end - start) // 60) if end > start else 99999999
            return [start_off], str(dur_min)
        if state == "ended":
            # 已结束: when 落在过去, duration 使结束点也在过去, 客户端据此触发结算。
            if start > 0 and end > 0:
                start_off = start - base_time
                dur_min = max(1, (end - start) // 60)
                # 若 force_end 提前结束, 用"开始->现在"作为已结束区间
                if season_state["force_end"]:
                    dur_min = max(1, (now - start) // 60) if now > start else 1
                return [start_off], str(dur_min)
            # 未设时间却 force_end: 给一个明确已结束的过去区间
            return [now_off - 8 * DAY], str(7 * 24 * 60)
        # active
        if start > 0:
            start_off = start - base_time
            dur_min = max(1, (end - start) // 60) if end > start else 99999999
            return [start_off], str(dur_min)
        # 未设开始时间: 锚到"今天本地0点"(稳定值, 不随每次请求漂移), 持续远未来。
        # 用固定锚点而非滚动的 now-7d, 避免客户端每次轮询都认为赛季"刚开始"->
        return [midnight_off - 7 * DAY], "99999999"

    season_when, season_dur = _season_when_dur()

    # 匹配"赛季相关事件名": 形如 season_28 / season_06 / season_28_end_survey /
    # season_27_end_shop / season_27_ch4_shop / season_21_deersummon。
    # 即 season_<数字> 开头(可带后缀)。不匹配 season_a_*/season_b_* 等烛火活动。
    import re as _re
    _season_num_re = _re.compile(r"^season_0*(\d+)(?:_.*)?$")

    def _season_num_of(name):
        m = _season_num_re.match(name or "")
        return int(m.group(1)) if m else None

    found_season = False
    found_cur_ever_started = False
    for e in evs:
        name = e.get("name", "")
        snum = _season_num_of(name)
        is_cur_main = cur_season is not None and name in (
            f"season_{cur_season:02d}", f"season_{cur_season}")
        # 赛季"参与标记": season_NN_ever_started / season_NN_ever_completed。
        # 客户端用它判断"玩家参与过哪个赛季", 季节结束总结弹窗据此显示对应赛季。
        # 抓包库是第 6 季时期的, 只含 season_06_ever_started, 没有当前季的标记 ——
        # 旧季标记屏蔽掉, 当前季标记置为"很久以前已开始且持续有效"(when=[0], 远未来),
        # 缺失则在循环后补建, 避免弹窗回退到旧赛季(第 6 季)。
        is_ever_marker = snum is not None and (
            "_ever_started" in name or "_ever_completed" in name)
        if name == "global_daily_reset_time":
            e["when"] = [midnight_off - DAY, midnight_off, midnight_off + DAY]
            # 每日重置必须是"1 天(1440 分钟)"的一次性循环(昨日/今日/明日), 否则
            # duration 太大会让客户端把首次出现当成"永久进行中", 后续不再触发重置
            # -> 每日任务倒计时不动/无法领取。低版本库这里是 99999999, 一并纠正。
            e["duration"] = "1440"
        elif is_ever_marker:
            if cur_season is not None and snum == int(cur_season):
                e["when"] = [0]
                e["duration"] = "99999999"
                if "_ever_started" in name:
                    found_cur_ever_started = True
            else:
                e["when"] = [NEVER]
                e["duration"] = "0"
        elif is_cur_main:
            e["when"] = season_when
            e["duration"] = season_dur
            found_season = True
        elif snum is not None and cur_season is not None and snum == int(cur_season) \
                and ("_end" in name or "_survey" in name):
            e["when"] = [NEVER]
            e["duration"] = "0"
        elif snum is not None and (cur_season is None or snum != int(cur_season)):
            e["when"] = [NEVER]
            e["duration"] = "0"
        # quest_apXX / spiritshopitem 等季节内容事件保持原始时间不动，
        # 客户端根据主 season_XX 状态自然判断它们是否可用。

    # 兜底: 若日程里根本没有"当前赛季"主事件, 按状态补建一个, 保证客户端能正确
    # 识别"进行中/未开始/已结束", 不再出现找不到赛季而每次登录弹窗。
    if cur_season is not None and not found_season:
        evs.append({
            "name": f"season_{cur_season:02d}",
            "when": season_when,
            "duration": season_dur,
        })
    # 兜底: 补建当前赛季的参与标记(season_NN_ever_started)。抓包库只有旧季(第6季)
    # 的标记, 不补建会导致季节结束总结弹窗显示旧赛季内容。when=[0] 表示"很久以前
    # 就已开始", 远未来持续, 即"玩家参与过当前赛季"。
    if cur_season is not None and not found_cur_ever_started:
        evs.append({
            "name": f"season_{cur_season:02d}_ever_started",
            "when": [0],
            "duration": "99999999",
        })
    return data


def get_event_schedule():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = SERVICE_ROOT
    config_dir = CONFIG_DIR

    # 版本分流: < 0.26.0 用低版本库(初始备份), >= 0.26.0 用高版本库(接口库)
    ver = _parse_client_version()
    use_v26 = ver and ver >= (0, 26, 0)

    low_path = os.path.join(config_dir, "event_schedule_low.json")
    high_path = os.path.join(config_dir, "event_schedule_high.json")

    if use_v26:
        sched_path = high_path if os.path.exists(high_path) else os.path.join(config_dir, "event_schedule.json")
    else:
        sched_path = low_path if os.path.exists(low_path) else os.path.join(config_dir, "event_schedule.json")

    # read schedule
    try:
        with open(sched_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"event_schedule": {"valid_until": 2147483648, "base_time": 1587366000, "events": []}}

    # tcandles: **所有版本**都按中控配置在内存中实时覆盖 when/duration(不写文件, 防篡改)。
    # 原来只对 >=0.26.0 生效, 而老版本库(初始备份)里连 tcandle 事件都没有 ->
    # "开启所有蜡烛活动"在老客户端上完全无效(游戏里没有烛火)。_apply_tcandles 内部
    # 会先补齐缺失的 tcandle 事件再按配置启用。
    data = _apply_tcandles(data, config_dir)

    # 赛季/每日重置的"动态时间锚定"对所有版本都做(原来只对 >=0.26.0 生效):
    # 低版本(<0.26.0, 如 0.12.1/0.23.6)走的是 event_schedule_low.json, 该文件只有
    # global_* 事件、没有任何 season_*, 且 when 是写死的过去时间 -> 这些客户端
    #   1) 看不到当前赛季: 中控"一键启动季节"改了 game_control.json 也毫无效果;
    #   2) 固定 when 让客户端判定"季节已结束", 而 season_finalize 又不分版本,
    #      于是每次进游戏都收到"季节结束"弹窗。
    # 这里同样按 game_control.json 的赛季配置重锚/补建 season_NN 与
    # season_NN_ever_started, 使所有版本的客户端都跟随中控的赛季设置。
    data = _apply_dynamic_timing(data, config_dir)

    # 关闭全部活动(节日活动 / 先祖复刻活动): 由中控 game_control.json -> events.enabled
    # 控制(当前为 false = 全关)。放在最后的"动态时间锚定"之后, 保证赛季/每日重置的
    # 正确时间不被覆盖, 而活动类事件一律 when=[NEVER]。
    data = _close_all_events(data, config_dir)

    no_season = os.path.exists(os.path.join(config_dir, "no_season"))

    if no_season:
        events = data.get("event_schedule", {}).get("events", [])
        non_season = [e for e in events if "season" not in str(e.get("name", "")).lower()]
        if non_season:
            schedule = {
                "base_time": data.get("event_schedule", {}).get("base_time", 1587366000),
                "events": non_season
            }
            if use_v26:
                now = int(time.time())
                schedule["server_time"] = now
                schedule["valid_until"] = now + 1800
            else:
                schedule["valid_until"] = data.get("event_schedule", {}).get("valid_until", 2147483648)
            body = {"event_schedule": schedule}
            return Response(json.dumps(body, ensure_ascii=False), 200, mimetype="application/json")
        else:
            return Response("{}", 200, mimetype="application/json")

    # normal mode: 版本分流
    schedule = {
        "base_time": data.get("event_schedule", {}).get("base_time", 1587366000),
        "events": data.get("event_schedule", {}).get("events", [])
    }
    if use_v26:
        now = int(time.time())
        schedule["server_time"] = now
        schedule["valid_until"] = now + 1800
    else:
        schedule["valid_until"] = data.get("event_schedule", {}).get("valid_until", 2147483648)
    body = {"event_schedule": schedule}
    return Response(json.dumps(body, ensure_ascii=False), 200, mimetype="application/json")
