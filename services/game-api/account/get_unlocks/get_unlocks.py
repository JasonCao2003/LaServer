from flask import request, jsonify, Response
from database.models import db, User, UnlockItem
import json
import logging
import os
import re
from helpers.emote_helper import is_emote_unlock, default_emote_names
from helpers.clothes_helper import is_clothing, default_clothes_names

# 只记 debug 级别: 正常跑日志里看不到, 出问题时把日志级别调低就能看到闸门裁剪细节。
logger = logging.getLogger(__name__)

# 版本分流阈值: 低于 0.20.0 的老版本客户端(如 0.13.4)用备份实现(get_unlocks_old)
_OLD_VERSION = (0, 20, 0)

# 老客户端(<0.26.0)解锁响应的明文体积闸门(字节)。
# 实测(同一台机器: READBOY C30 + 0.22.0 测试服): 82,568 / 96,870 / 101,869 / 142,952 字节
# 都被客户端正常吃下(142,952 那次同一秒里客户端继续发了 social-feed、recording 等请求,
# 会话没断); 而 165KB 兑换树、224,799 字节 buff 表、290,410 字节 get_unlocks 都崩。
# 取 142,952 = 本机实测被客户端吃下的最大字节数(再往上就是没验证过的区间, 而 165KB 崩过)。
# 配合 full_unlock_helper 把 440 条动作排在**最前面**、裁剪从尾部往回丢 —— 玩家最在意的
# 动作 / 斗篷光翼 / 全部服饰(玩家 + NPC)因此 100% 发得出去, 被裁的只会是排在最后的
# "其余"(开门/传送/季节/关系能力, 其中 79 项本来就由 /service/status 端点下发)。
_UNLOCK_MAX_BYTES_OLD = 142952


def _client_version():
    """从 User-Agent 解析客户端版本, 返回 (major, minor, patch) 或 None。

    交给统一的 client_version(带 config/client_version_overrides.json 纠偏):
    有的魔改老客户端会谎报版本(实测 0.13.4 报 0.23.6.354648), 不纠偏就会把
    它当新客户端 -> 版本分流走错分支 / 发它不认识的解锁名 -> 不生效或闪退。
    """
    try:
        from helpers.client_version import client_version_from_request
        return client_version_from_request()
    except Exception:
        ua = request.headers.get("User-Agent", "") if request else ""
        m = re.search(r"Sky-[^/\s]+/(\d+)\.(\d+)\.(\d+)", ua)
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user


def _unlocks_plain_size(items):
    """与下面 Response 里 json.dumps(response_body, sort_keys=False, ensure_ascii=False)
    完全同一套序列化(默认分隔符带空格), 这样闸门量出来的就是客户端实际收到的字节数。"""
    return len(json.dumps({"unlocks": items, "unlocks_total_count": len(items)},
                          ensure_ascii=False).encode("utf-8"))


def _cap_unlocks(items, limit):
    """把超限的解锁表从**尾部**丢到 <= limit 字节, 且**一件都不多丢**。

    列表顺序(从前到后, 越后面越先被丢): 440 条动作 -> 斗篷/光翼 -> 玩家服饰
    (CharSkyKid_*) -> 开门/传送等其余 -> NPC 服饰 -> type=="level" 重复项。
    前两类丢了会在游戏里立刻看出来, 所以垫在最前; NPC 服饰(先祖/鸟巢等非玩家形象)
    是玩家指定的舍去项("超出的部分就舍去 NPC 服饰"); level 那批状态端点已经发过一遍,
    丢它等于不占预算, 所以排最后先丢。

    全 86 个版本档位逐个核对过: 440 条动作 + 斗篷/光翼 + 玩家服饰(面具/裤子/鞋子/
    披风/背饰道具) + 开门/传送等其余项 = **0 件被裁**; 库内最大档 0.23.6(38 个账号)
    这四类合计 1,337 项 / 135,733 字节 < 闸门 142,952, 超出部分由 79 条重复项(7,146
    字节)和约 108 件 NPC 服饰承担。只有 0.24.x/0.25.x(库内没有账号, 总量 175~244KB)
    舍完全部 NPC 服饰后还要再让出几十件开门标记。

    旧实现每步丢 6%(1,600 项时一步就是 96 件), 经常"该丢 10 件却丢了 192 件"、甚至
    丢光整个"其余"还不够、接着啃服装; 现在二分求"装得下的最长前缀", 只丢必须丢的。
    _unlocks_plain_size 对前缀长度单调不减, 二分安全。
    """
    if not items or _unlocks_plain_size(items) <= limit:
        return items
    total = _unlocks_plain_size(items)
    lo, hi = 0, len(items)          # lo 一定装得下(空表); hi 已确认装不下
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _unlocks_plain_size(items[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    logger.debug("解锁体积闸门: %d 项 %d 字节 > %d, 只丢尾部 %d 项 -> %d 项 %d 字节",
                 len(items), total, limit, len(items) - lo, lo,
                 _unlocks_plain_size(items[:lo]))
    return list(items[:lo])


# 体积闸门可覆盖: config/unlock_limits.json
#   {"legacy_max_bytes": 142952, "modern_max_bytes": 262144}
# 老客户端(<0.26.0, 如 0.15.5/0.22.0/0.23.6)解析不了过大的响应会**闪退**
# (实测 0.22.0: 82,568 / 96,870 / 101,869 / 142,952 字节都正常; 165KB 兑换树 /
# 290,410 字节解锁表才崩)。默认 142952 高于"全部动作+全部服饰"所需的量
# (库内最大档 0.23.6 = 130,694 字节)、且正好是实测被吃下过的最大值; 想再多要
# 几件"其余"可以调大一点点, 崩了就调回来, 不用改代码。
_UNLOCK_LIMITS_FILE = "unlock_limits.json"
# >=0.26.0 的新客户端用 unlocks_total_count 分页, 能吞下大得多的响应; 这里给一个
# 上限只是"别把两千多行原样砸过去"的保险, 正常情况下不会碰到。
_UNLOCK_MAX_BYTES_MODERN = 262144


def _unlock_limits():
    """读 config/unlock_limits.json, 返回 (legacy_max_bytes, modern_max_bytes)。"""
    legacy, modern = _UNLOCK_MAX_BYTES_OLD, _UNLOCK_MAX_BYTES_MODERN
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(current_dir))
        # utf-8-sig: 记事本改存会把 BOM 写进去, 用 utf-8 读会直接抛异常 ->
        # 静默退回默认值, 玩家会以为"改了没用"。这里容忍 BOM。
        with open(os.path.join(project_root, "config", _UNLOCK_LIMITS_FILE),
                  encoding="utf-8-sig") as f:
            cfg = json.load(f)
        v = int(cfg.get("legacy_max_bytes") or 0)
        if v >= 4096:
            legacy = v
        v = int(cfg.get("modern_max_bytes") or 0)
        if v >= 4096:
            modern = v
    except Exception:
        pass
    return legacy, modern


def _legacy_max_bytes():
    """老客户端的明文体积闸门(字节)。可被 config/unlock_limits.json 覆盖。"""
    return _unlock_limits()[0]


def _unlock_priority(item):
    """解锁项的"重要程度"分组(数字越小越重要, 体积闸门从尾部裁, 所以先保前面的)。

    0 动作      emote_upgrade_*  —— 不排最前面的话动作面板会整片变灰
    1 斗篷/披风  CharSkyKid_Wing_*(含 *_Cape* 那几件) —— 玩家最容易发现"没解锁"的就是它
    2 玩家服饰  其余 CharSkyKid_*(面具/发型/身体/项链/角/背饰道具 CharSkyKid_Prop_*...)
    3 其余      开门/传送(ap06unlock_*/teleport_*)、季节、关系能力、timeline:* 等
    4 NPC 服饰  CharSkyNPC_* / NPC_* / Nest_*(先祖、鸟巢等非玩家形象)
               —— 玩家点名:"超出的部分就舍去 NPC 服饰"。体积装不下时先丢这一类,
               把字节让给玩家自己的服饰和开门/传送等玩法标记。
               安装包 OutfitDefs.json 里的服饰名 100% 以 Char / NPC_ / Nest_ 开头,
               所以这三个前缀就是"服饰"的完整判据(玩家服饰=CharSkyKid_*, 其余即 NPC 类)。
    5 重复项    type == "level" —— 这批名字 100% 也在 config/kaiquantu.json 里, 由
               /service/status/api/v1/get_unlocks 端点原样下发(service/status/get_unlocks.py),
               账号响应里这份是重复的(实测 0.23.6: 79 项全部命中, 互不重叠),
               所以体积不够时最先丢它们(丢了也一件不少收, 等于不占预算)。
    """
    nm = item.get("name") if isinstance(item, dict) else item
    nm = nm or ""
    if nm.startswith("emote_upgrade_"):
        return 0
    if "_Wing_" in nm or "_Cape" in nm or nm.endswith("Wing"):
        return 1
    if nm.startswith("CharSkyKid_"):
        return 2
    if nm.startswith("Char") or nm.startswith("NPC_") or nm.startswith("Nest_"):
        return 4
    if isinstance(item, dict) and item.get("type") == "level":
        return 5
    return 3


def _order_unlocks(items):
    """按重要程度稳定重排(不改变同组内原有顺序)。"""
    buckets = {0: [], 1: [], 2: [], 3: [], 4: [], 5: []}
    for it in items:
        buckets[_unlock_priority(it)].append(it)
    out = []
    for k in (0, 1, 2, 3, 4, 5):
        out.extend(buckets[k])
    return out


def finalize_unlocks(items, ver):
    """解锁响应的最后一道工序: 排序 -> 体积闸门。

    **必须在"并入库内解锁"之后再调用** —— 早期版本先裁后并, 库里那 2000 多行
    (管理层"推送全解锁"写进去的)会把裁掉的部分原样加回来, 于是响应从 82KB 涨到
    290KB, 老客户端一登录就闪退(2026-10-01 01:36 实测 290,410 字节即崩)。

    闸门永远在最后: 返回值已经 <= 限制, 长度就是客户端要的 unlocks_total_count。
    """
    out = _order_unlocks(list(items or []))
    legacy, modern = _unlock_limits()
    if ver is None or ver < (0, 26, 0):
        out = _cap_unlocks(out, legacy)
    else:
        out = _cap_unlocks(out, modern)
    return out

def get_unlocks():
    # 版本分流: 低于 0.20.0 的老版本客户端使用备份实现(与抓包 0.13.4 一致)
    _ver = _client_version()
    if _ver is not None and _ver < _OLD_VERSION:
        from .get_unlocks_old import get_unlocks_old
        return get_unlocks_old()

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
        limit = request_data.get('l', 1000)
        offset = request_data.get('o', 0)
        version = request_data.get('v', 0)

        # 新建账号全解锁: 开启后, 开启时间点之后新建的账号拉取数据时直接返回完整解锁
        auto_full = False
        try:
            from helpers.full_unlock_helper import should_auto_full_unlock, full_unlocks
            if user_id:
                _u = User.query.filter_by(id=user_id).first()
                auto_full = bool(_u) and should_auto_full_unlock(_u.created_at)
        except Exception:
            auto_full = False

        # 检查是否包含 max_count 参数
        if 'max_count' in request_data:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            config_path = os.path.join(project_root, 'config', '0.6.5_unlock.json')
            
            if not os.path.exists(config_path):
                return Response(
                    response=json.dumps({"error": f"Config file not found: {config_path}"}),
                    status=404,
                    mimetype='application/json'
                )
            
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                
                if isinstance(config_data, list):
                    unlocks_list = config_data
                else:
                    unlocks_list = config_data.get("unlocks", []) if isinstance(config_data, dict) else []
                
                # 需求：新号默认只保留"左下角"的一个动作，其余动作需先祖解锁；
                # 外观(衣服)也只保留"初始"几件，其余由管理层推送。
                # 该 max_count 分支直接返回配置文件，必须同样过滤掉非默认动作和
                # 非初始外观，否则新号首次拉取会拿到配置里的全部动作和全部衣服。
                keep_emotes = set(default_emote_names())
                keep_clothes = default_clothes_names()
                def _name_of(item):
                    return item.get("name") if isinstance(item, dict) else item
                def _keep(item):
                    nm = _name_of(item)
                    if is_emote_unlock(nm):
                        return nm in keep_emotes
                    if is_clothing(nm):
                        return nm in keep_clothes
                    # 其余(季节解锁/AP先祖能力即开门/teleport 传送解锁等)一律不给新号。
                    # 之前这里 return True 会把全部 teleport_to_quest*/ap06unlock_* 返回,
                    # 导致新号一进游戏就解锁了所有传送点/门 -> 等于"获得全地图"。
                    # 与下方主分支保持一致: 非默认动作、非初始外观一律不发, 由管理层推送。
                    return False
                if auto_full:
                    # 新建账号全解锁(版本分流): 按客户端版本发送对应 OutfitDefs 数据
                    filtered = full_unlocks(client_version=_client_version())
                else:
                    filtered = [it for it in unlocks_list if _keep(it)]

                # 适配全版本: 不论客户端是否带 max_count, 账号在数据库里的解锁状态
                # 都要一致。此分支若发现该账号还没有任何解锁记录, 就把"过滤后的初始项"
                # 种入 UnlockItem(与主分支同样的初始集合), 保证两条路径收敛到同一状态。
                try:
                    if user_id and not auto_full and UnlockItem.query.filter_by(user_id=user_id).count() == 0:
                        for it in filtered:
                            nm = _name_of(it)
                            if not nm:
                                continue
                            db.session.add(UnlockItem(
                                user_id=user_id, name=nm,
                                unlock_type=(it.get("type", "spiritshop")
                                             if isinstance(it, dict) else "spiritshop"),
                                ack=bool(it.get("ack", True)) if isinstance(it, dict) else True,
                                created_at=(it.get("created_at", 0)
                                            if isinstance(it, dict) else 0)))
                        db.session.commit()
                except Exception:
                    db.session.rollback()

                # 关键: 合并该账号已推送/已拥有的解锁(UnlockItem 表)。
                # 管理层"推送全解锁/开全门"会把 teleport_to_quest*/ap06unlock_* 等
                # 写入 UnlockItem。此分支(带 max_count 的请求)之前只返回过滤后的初始
                # 项, 完全忽略数据库里已推送的解锁, 导致"推送开全门后地图/门仍未解锁"。
                # 这里按账号并入数据库解锁, 保证推送后在任何版本客户端都立即生效。
                try:
                    if user_id:
                        names_seen = {(_name_of(it)) for it in filtered}
                        for row in UnlockItem.query.filter_by(user_id=user_id).all():
                            if row.name in names_seen:
                                continue
                            filtered.append({
                                "name": row.name,
                                "type": row.unlock_type,
                                "ack": bool(row.ack),
                                "created_at": row.created_at,
                            })
                            names_seen.add(row.name)
                except Exception:
                    pass

                # 与主分支/auto_full 分支同一道工序: 排序 + 老客户端体积闸门。
                # 这里同样必须**放在并库之后**(上面的 for row in UnlockItem... 之后),
                # 否则库里的两千多行会把裁掉的部分原样加回来 -> 响应暴涨 -> 客户端闪退。
                filtered = finalize_unlocks(filtered, _ver)

                response_body = {
                    "unlocks": filtered,
                    "unlocks_total_count": len(filtered)
                }

                return Response(
                    response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
                    status=200,
                    mimetype='application/json'
                )
                
            except json.JSONDecodeError:
                return Response(
                    response=json.dumps({"error": "Invalid JSON in config file"}),
                    status=500,
                    mimetype='application/json'
                )
            except Exception as e:
                return Response(
                    response=json.dumps({"error": f"Failed to read config file: {str(e)}"}),
                    status=500,
                    mimetype='application/json'
                )
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        # 检查用户是否已有解锁记录，如果没有则初始化默认解锁
        existing_unlocks = UnlockItem.query.filter_by(user_id=user_id).count()
        if existing_unlocks == 0 and not auto_full:
            # 默认解锁列表（从上传的JSON文件生成）
            default_unlocks = [
                {"name": "emote_upgrade_point_01", "type": "spiritshop", "ack": False, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_TopSharp", "type": "spiritshop", "ack": True, "created_at": 1654570100},
                {"name": "emote_upgrade_thumbsup_01", "type": "spiritshop", "ack": False, "created_at": 1654571815},
                {"name": "emote_upgrade_yawn_01", "type": "spiritshop", "ack": False, "created_at": 1654571820},
                {"name": "emote_upgrade_wave_01", "type": "spiritshop", "ack": False, "created_at": 1654571824},
                {"name": "emote_upgrade_butterfly_01", "type": "spiritshop", "ack": False, "created_at": 1654571828},
                {"name": "emote_upgrade_laugh_01", "type": "spiritshop", "ack": False, "created_at": 1654571831},
                {"name": "emote_upgrade_pout_01", "type": "spiritshop", "ack": False, "created_at": 1654573624},
                {"name": "emote_upgrade_cold_01", "type": "spiritshop", "ack": False, "created_at": 1654573628},
                {"name": "emote_upgrade_shy_01", "type": "spiritshop", "ack": False, "created_at": 1654573631},
                {"name": "emote_upgrade_ohno_01", "type": "spiritshop", "ack": False, "created_at": 1654573634},
                {"name": "emote_upgrade_proud_01", "type": "spiritshop", "ack": False, "created_at": 1654574982},
                {"name": "emote_upgrade_strong_01", "type": "spiritshop", "ack": False, "created_at": 1654574986},
                {"name": "emote_upgrade_come_01", "type": "spiritshop", "ack": False, "created_at": 1654586537},
                {"name": "CharSkyKid_Hair_BraidSide", "type": "spiritshop", "ack": True, "created_at": 1654586538},
                {"name": "emote_upgrade_nothanks_01", "type": "spiritshop", "ack": False, "created_at": 1654586541},
                {"name": "ap06unlock_ap08_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1654674341},
                {"name": "ap06unlock_ap07_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1654679262},
                {"name": "ap06unlock_ap07_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1654679687},
                {"name": "emote_upgrade_seek_01", "type": "spiritshop", "ack": False, "created_at": 1654729568},
                {"name": "emote_upgrade_cry_01", "type": "spiritshop", "ack": False, "created_at": 1654729571},
                {"name": "emote_upgrade_sorry_01", "type": "spiritshop", "ack": False, "created_at": 1654729573},
                {"name": "emote_upgrade_whale_01", "type": "spiritshop", "ack": False, "created_at": 1654729578},
                {"name": "emote_upgrade_manta_01", "type": "spiritshop", "ack": False, "created_at": 1654731286},
                {"name": "emote_upgrade_handstand_01", "type": "spiritshop", "ack": False, "created_at": 1654731290},
                {"name": "emote_upgrade_cheer_01", "type": "spiritshop", "ack": False, "created_at": 1654731293},
                {"name": "emote_upgrade_backflip_01", "type": "spiritshop", "ack": False, "created_at": 1654732475},
                {"name": "emote_upgrade_bow_01", "type": "spiritshop", "ack": False, "created_at": 1654732479},
                {"name": "emote_upgrade_brave_01", "type": "spiritshop", "ack": False, "created_at": 1654734194},
                {"name": "emote_upgrade_scared_01", "type": "spiritshop", "ack": False, "created_at": 1654734198},
                {"name": "emote_upgrade_sneaky_01", "type": "spiritshop", "ack": False, "created_at": 1654734202},
                {"name": "emote_upgrade_lookaround_01", "type": "spiritshop", "ack": False, "created_at": 1654734205},
                {"name": "emote_upgrade_die_01", "type": "spiritshop", "ack": False, "created_at": 1654735399},
                {"name": "emote_upgrade_salute_01", "type": "spiritshop", "ack": False, "created_at": 1654735402},
                {"name": "emote_upgrade_float_01", "type": "spiritshop", "ack": False, "created_at": 1654740031},
                {"name": "emote_upgrade_ghost_01", "type": "spiritshop", "ack": False, "created_at": 1654740034},
                {"name": "emote_upgrade_force_01", "type": "spiritshop", "ack": False, "created_at": 1654740038},
                {"name": "emote_upgrade_pray_01", "type": "spiritshop", "ack": False, "created_at": 1654740042},
                {"name": "emote_upgrade_love_01", "type": "spiritshop", "ack": False, "created_at": 1654740046},
                {"name": "teleport_to_questap14", "type": "spiritshop", "ack": False, "created_at": 1654812881},
                {"name": "ap06unlock_ap14_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1654813155},
                {"name": "lootbox_memory_01", "type": "spiritshop", "ack": False, "created_at": 1654813213},
                {"name": "relationship_ability_ap06unlock_ap14_high_five", "type": "spiritshop", "ack": False, "created_at": 1654813216},
                {"name": "heart_ap06unlock_ap14_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1654813218},
                {"name": "ap06unlock_ap14_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1654813349},
                {"name": "ap06unlock_ap14_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1654813606},
                {"name": "lootbox_memory_02", "type": "spiritshop", "ack": False, "created_at": 1654813674},
                {"name": "relationship_ability_ap06unlock_ap14_double_five", "type": "spiritshop", "ack": False, "created_at": 1654813675},
                {"name": "heart_ap06unlock_ap14_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1654813676},
                {"name": "ap06unlock_ap14_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1654814928},
                {"name": "lootbox_memory_03", "type": "spiritshop", "ack": False, "created_at": 1654814966},
                {"name": "relationship_ability_ap06unlock_ap14_hug", "type": "spiritshop", "ack": False, "created_at": 1654814966},
                {"name": "heart_ap06unlock_ap14_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1654814969},
                {"name": "ap06unlock_ap14_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1654815751},
                {"name": "relationship_ability_ap06unlock_ap14_duetdance", "type": "spiritshop", "ack": False, "created_at": 1654815752},
                {"name": "heart_ap06unlock_ap14_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1654815752},
                {"name": "lootbox_memory_04", "type": "spiritshop", "ack": False, "created_at": 1654815823},
                {"name": "emote_upgrade_bird_01", "type": "spiritshop", "ack": False, "created_at": 1654817078},
                {"name": "emote_upgrade_beacon_01", "type": "spiritshop", "ack": False, "created_at": 1654817084},
                {"name": "emote_upgrade_wipe_01", "type": "spiritshop", "ack": False, "created_at": 1654817089},
                {"name": "emote_upgrade_hairtousle_01", "type": "spiritshop", "ack": False, "created_at": 1654822187},
                {"name": "lootbox_rare_hairtousle_01", "type": "spiritshop", "ack": False, "created_at": 1654822195},
                {"name": "tier_unlock_hairtousle_01", "type": "spiritshop", "ack": False, "created_at": 1654824579},
                {"name": "lootbox_rare_hairtousle_02", "type": "spiritshop", "ack": False, "created_at": 1654824582},
                {"name": "emote_upgrade_handshake_01", "type": "spiritshop", "ack": False, "created_at": 1654825380},
                {"name": "emote_upgrade_headbob_01", "type": "spiritshop", "ack": False, "created_at": 1654825383},
                {"name": "emote_upgrade_awww_01", "type": "spiritshop", "ack": False, "created_at": 1654825386},
                {"name": "CharSkyKid_Horn_AP04EarMuffs", "type": "spiritshop", "ack": True, "created_at": 1654996349},
                {"name": "teleport_to_questap15", "type": "spiritshop", "ack": False, "created_at": 1659243351},
                {"name": "ap06unlock_ap15_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1659483623},
                {"name": "heart_ap06unlock_ap15_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1659483624},
                {"name": "ap06unlock_ap15_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1659483625},
                {"name": "ap06unlock_ap15_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1659483626},
                {"name": "ap06unlock_ap15_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1659483627},
                {"name": "ap06unlock_ap15_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1659483627},
                {"name": "ap06unlock_ap15_fetch_06", "type": "spiritshop", "ack": False, "created_at": 1659483629},
                {"name": "heart_ap06unlock_ap15_fetch_06", "type": "spiritshop", "ack": False, "created_at": 1659483630},
                {"name": "heart_ap06unlock_ap15_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1659483630},
                {"name": "heart_ap06unlock_ap15_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1659483631},
                {"name": "heart_ap06unlock_ap15_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1659483632},
                {"name": "heart_ap06unlock_ap15_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1659483633},
                {"name": "ap06unlock_ap08_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1659483795},
                {"name": "ap06unlock_ap08_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1659483796},
                {"name": "ap06unlock_ap08_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1659483820},
                {"name": "relationship_ability_ap06unlock_ap08_hug", "type": "spiritshop", "ack": False, "created_at": 1659483838},
                {"name": "ap06unlock_ap07_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1659483926},
                {"name": "ap06unlock_ap07_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1659483949},
                {"name": "ap06unlock_ap07_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1659483974},
                {"name": "ap06unlock_ap07_fetch_06", "type": "spiritshop", "ack": False, "created_at": 1659483996},
                {"name": "relationship_ability_ap06unlock_ap07_hug", "type": "spiritshop", "ack": False, "created_at": 1659484035},
                {"name": "ap06unlock_ap10_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1659484186},
                {"name": "relationship_ability_ap06unlock_ap10_high_five", "type": "spiritshop", "ack": False, "created_at": 1659484187},
                {"name": "ap06unlock_ap10_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1659484208},
                {"name": "ap06unlock_ap10_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1659484209},
                {"name": "relationship_ability_ap06unlock_ap10_hug", "type": "spiritshop", "ack": False, "created_at": 1659484242},
                {"name": "ap06unlock_ap10_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1659484243},
                {"name": "ap06unlock_ap10_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1659484275},
                {"name": "relationship_ability_ap06unlock_ap10_double_five", "type": "spiritshop", "ack": False, "created_at": 1659484275},
                {"name": "ap06unlock_ap10_fetch_06", "type": "spiritshop", "ack": False, "created_at": 1659484295},
                {"name": "emote_upgrade_think_01", "type": "spiritshop", "ack": False, "created_at": 1659693780},
                {"name": "lootbox_rare_think_01", "type": "spiritshop", "ack": False, "created_at": 1659693782},
                {"name": "tier_unlock_think_01", "type": "spiritshop", "ack": False, "created_at": 1659693789},
                {"name": "emote_upgrade_think_03", "type": "spiritshop", "ack": False, "created_at": 1659693794},
                {"name": "lootbox_rare_think_02", "type": "spiritshop", "ack": False, "created_at": 1659693799},
                {"name": "heart_think", "type": "spiritshop", "ack": False, "created_at": 1659693806},
                {"name": "emote_upgrade_think_04", "type": "spiritshop", "ack": False, "created_at": 1659693809},
                {"name": "emote_upgrade_think_02", "type": "spiritshop", "ack": False, "created_at": 1659693812},
                {"name": "CharSkyKid_Horn_SummerHat_01", "type": "spiritshop", "ack": True, "created_at": 1664190627},
                {"name": "teleport_to_questap16", "type": "spiritshop", "ack": False, "created_at": 1667366884},
                {"name": "ap06unlock_fetch_dusk", "type": "spiritshop", "ack": False, "created_at": 1667379349},
                {"name": "ap06unlock_fetch_sunset", "type": "spiritshop", "ack": False, "created_at": 1667379349},
                {"name": "ap06unlock_fetch_dawn", "type": "spiritshop", "ack": False, "created_at": 1667379349},
                {"name": "ap06unlock_fetch_day", "type": "spiritshop", "ack": False, "created_at": 1667379350},
                {"name": "ap06unlock_fetch_night", "type": "spiritshop", "ack": False, "created_at": 1667379350},
                {"name": "ap06unlock_fetch_rain", "type": "spiritshop", "ack": False, "created_at": 1667379350},
                {"name": "ap06unlock_ap09_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1667379353},
                {"name": "ap06unlock_ap09_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1667379353},
                {"name": "ap06unlock_ap09_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1667379353},
                {"name": "ap06unlock_ap09_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1667379354},
                {"name": "ap06unlock_ap09_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1667379354},
                {"name": "ap06unlock_ap11_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1667379356},
                {"name": "ap06unlock_ap11_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1667379356},
                {"name": "ap06unlock_ap11_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1667379357},
                {"name": "ap06unlock_ap11_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1667379357},
                {"name": "ap06unlock_ap11_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1667379357},
                {"name": "ap06unlock_ap11_fetch_06", "type": "spiritshop", "ack": False, "created_at": 1667379357},
                {"name": "ap06unlock_ap11_fetch_07", "type": "spiritshop", "ack": False, "created_at": 1667379358},
                {"name": "ap06unlock_ap12_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1667379358},
                {"name": "ap06unlock_ap12_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1667379358},
                {"name": "relationship_ability_ap06unlock_ap12_high_five", "type": "spiritshop", "ack": False, "created_at": 1667379358},
                {"name": "ap06unlock_ap12_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1667379359},
                {"name": "ap06unlock_ap12_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1667379359},
                {"name": "ap06unlock_ap12_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1667379359},
                {"name": "ap06unlock_ap13_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1667379359},
                {"name": "ap06unlock_ap13_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1667379360},
                {"name": "ap06unlock_ap13_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1667379360},
                {"name": "ap06unlock_ap13_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1667379360},
                {"name": "ap06unlock_ap13_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1667379360},
                {"name": "ap06unlock_ap16_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1667379365},
                {"name": "emote_upgrade_silentclap_01", "type": "spiritshop", "ack": False, "created_at": 1667379365},
                {"name": "ap06unlock_ap16_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1667379366},
                {"name": "emote_upgrade_conduct_01", "type": "spiritshop", "ack": False, "created_at": 1667379366},
                {"name": "ap06unlock_ap16_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1667379366},
                {"name": "emote_upgrade_skip_01", "type": "spiritshop", "ack": False, "created_at": 1667379366},
                {"name": "ap06unlock_ap16_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1667379367},
                {"name": "ap06unlock_ap16_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1667379367},
                {"name": "heart_ap06unlock_fetch_dusk", "type": "spiritshop", "ack": False, "created_at": 1667379371},
                {"name": "heart_ap06unlock_fetch_sunset", "type": "spiritshop", "ack": False, "created_at": 1667379372},
                {"name": "heart_ap06unlock_fetch_dawn", "type": "spiritshop", "ack": False, "created_at": 1667379372},
                {"name": "heart_ap06unlock_fetch_day", "type": "spiritshop", "ack": False, "created_at": 1667379372},
                {"name": "heart_ap06unlock_fetch_night", "type": "spiritshop", "ack": False, "created_at": 1667379373},
                {"name": "heart_ap06unlock_fetch_rain", "type": "spiritshop", "ack": False, "created_at": 1667379373},
                {"name": "heart_ap06unlock_ap07_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1667379373},
                {"name": "heart_ap06unlock_ap07_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1667379373},
                {"name": "heart_ap06unlock_ap16_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1667379384},
                {"name": "CharSkyKid_Hair_ElderDawn", "type": "spiritshop", "ack": True, "created_at": 1667391245},
                {"name": "CharSkyKid_Hair_ElderDay", "type": "spiritshop", "ack": True, "created_at": 1667391246},
                {"name": "CharSkyKid_Hair_ElderRain", "type": "spiritshop", "ack": True, "created_at": 1667391246},
                {"name": "CharSkyKid_Hair_ElderSis", "type": "spiritshop", "ack": True, "created_at": 1667391246},
                {"name": "CharSkyKid_Hair_ElderBro", "type": "spiritshop", "ack": True, "created_at": 1667391247},
                {"name": "CharSkyKid_Hair_ElderDusk", "type": "spiritshop", "ack": True, "created_at": 1667391247},
                {"name": "CharSkyKid_Hair_ElderNight", "type": "spiritshop", "ack": True, "created_at": 1667391247},
                {"name": "CharSkyKid_Mask_ElderDawn_02", "type": "spiritshop", "ack": True, "created_at": 1667391253},
                {"name": "CharSkyKid_Mask_ElderDay_02", "type": "spiritshop", "ack": True, "created_at": 1667391253},
                {"name": "CharSkyKid_Mask_ElderRain_02", "type": "spiritshop", "ack": True, "created_at": 1667391254},
                {"name": "emote_upgrade_silentclap_02", "type": "spiritshop", "ack": False, "created_at": 1667391722},
                {"name": "emote_upgrade_conduct_02", "type": "spiritshop", "ack": False, "created_at": 1667391726},
                {"name": "emote_upgrade_skip_02", "type": "spiritshop", "ack": False, "created_at": 1667391728},
                {"name": "emote_upgrade_twirl_01", "type": "spiritshop", "ack": False, "created_at": 1667391888},
                {"name": "emote_upgrade_rhythmicclap_01", "type": "spiritshop", "ack": False, "created_at": 1667391895},
                {"name": "emote_upgrade_raisetheroof_01", "type": "spiritshop", "ack": False, "created_at": 1667391944},
                {"name": "emote_upgrade_armwave_01", "type": "spiritshop", "ack": False, "created_at": 1667391951},
                {"name": "heart_ap06unlock_ap07_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1689208587},
                {"name": "heart_ap06unlock_ap07_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1689208587},
                {"name": "heart_ap06unlock_ap07_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1689208588},
                {"name": "heart_ap06unlock_ap07_fetch_06", "type": "spiritshop", "ack": False, "created_at": 1689208588},
                {"name": "heart_ap06unlock_ap08_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1689208588},
                {"name": "heart_ap06unlock_ap08_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1689208588},
                {"name": "heart_ap06unlock_ap08_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1689208588},
                {"name": "heart_ap06unlock_ap08_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1689208588},
                {"name": "heart_ap06unlock_ap09_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1689208588},
                {"name": "heart_ap06unlock_ap09_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1689208589},
                {"name": "lootbox_common_19", "type": "spiritshop", "ack": False, "created_at": 1689208845},
                {"name": "emote_upgrade_point_02", "type": "spiritshop", "ack": False, "created_at": 1689208845},
                {"name": "tier_unlock_point_01", "type": "spiritshop", "ack": False, "created_at": 1689208845},
                {"name": "heart_point", "type": "spiritshop", "ack": False, "created_at": 1689208845},
                {"name": "emote_upgrade_point_03", "type": "spiritshop", "ack": False, "created_at": 1689208845},
                {"name": "emote_upgrade_point_04", "type": "spiritshop", "ack": False, "created_at": 1689208845},
                {"name": "teleport_to_questap18", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "ap06unlock_ap18_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "heart_ap06unlock_ap18_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "ap06unlock_ap18_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "ap06unlock_ap18_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "heart_ap06unlock_ap18_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "ap06unlock_ap18_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "heart_ap06unlock_ap18_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "ap06unlock_ap18_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1689208860},
                {"name": "heart_ap06unlock_ap18_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1689208861},
                {"name": "teleport_to_questbloom", "type": "spiritshop", "ack": False, "created_at": 1689208873},
                {"name": "CharSkyKid_Body_LongSkinnyPants", "type": "spiritshop", "ack": True, "created_at": 1689208887},
                {"name": "sheet03", "type": "spiritshop", "ack": False, "created_at": 1689208891},
                {"name": "CharSkyKid_Hair_ShortBobRound", "type": "spiritshop", "ack": True, "created_at": 1689208893},
                {"name": "CharSkyKid_Hair_ShortBob", "type": "spiritshop", "ack": True, "created_at": 1689208895},
                {"name": "CharSkyKid_Hair_PunkTall", "type": "spiritshop", "ack": True, "created_at": 1689208898},
                {"name": "CharSkyKid_Hair_Dreads", "type": "spiritshop", "ack": True, "created_at": 1689208899},
                {"name": "emote_upgrade_respect_01", "type": "spiritshop", "ack": False, "created_at": 1689211395},
                {"name": "emote_upgrade_kungfu_01", "type": "spiritshop", "ack": False, "created_at": 1689321688},
                {"name": "lootbox_rare_kungfu_01", "type": "spiritshop", "ack": False, "created_at": 1689321691},
                {"name": "tier_unlock_kungfu_01", "type": "spiritshop", "ack": False, "created_at": 1689321694},
                {"name": "emote_upgrade_joy_01", "type": "spiritshop", "ack": False, "created_at": 1689321703},
                {"name": "lootbox_rare_joy_01", "type": "spiritshop", "ack": False, "created_at": 1689321705},
                {"name": "tier_unlock_joy_01", "type": "spiritshop", "ack": False, "created_at": 1689321708},
                {"name": "lootbox_rare_respect_01", "type": "spiritshop", "ack": False, "created_at": 1689321715},
                {"name": "tier_unlock_respect_01", "type": "spiritshop", "ack": False, "created_at": 1689321716},
                {"name": "emote_upgrade_chestpound_01", "type": "spiritshop", "ack": False, "created_at": 1691059842},
                {"name": "CharSkyKid_Prop_AP19Camera", "type": "spiritshop", "ack": True, "created_at": 1691060002},
                {"name": "ap06unlock_ap19_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1691060017},
                {"name": "ap06unlock_ap19_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1691060018},
                {"name": "relationship_ability_ap06unlock_ap19_high_five", "type": "spiritshop", "ack": False, "created_at": 1691060018},
                {"name": "ap06unlock_ap19_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1691060019},
                {"name": "ap06unlock_ap19_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1691060034},
                {"name": "ap06unlock_ap19_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1691060034},
                {"name": "heart_ap06unlock_ap19_fetch_05", "type": "spiritshop", "ack": False, "created_at": 1691060035},
                {"name": "heart_ap06unlock_ap19_fetch_03", "type": "spiritshop", "ack": False, "created_at": 1691060035},
                {"name": "heart_ap06unlock_ap19_fetch_02", "type": "spiritshop", "ack": False, "created_at": 1691060036},
                {"name": "heart_ap06unlock_ap19_fetch_04", "type": "spiritshop", "ack": False, "created_at": 1691060037},
                {"name": "heart_ap06unlock_ap19_fetch_01", "type": "spiritshop", "ack": False, "created_at": 1691060039},
                {"name": "teleport_to_questap19", "type": "spiritshop", "ack": False, "created_at": 1691060039},
                {"name": "emote_upgrade_pointup_01", "type": "spiritshop", "ack": False, "created_at": 1692343110},
                {"name": "emote_upgrade_pointup_02", "type": "spiritshop", "ack": False, "created_at": 1692343112},
                {"name": "lootbox_rare_pointup_01", "type": "spiritshop", "ack": False, "created_at": 1692344333},
                {"name": "lootbox_common_49", "type": "spiritshop", "ack": False, "created_at": 1693055516},
                {"name": "emote_upgrade_cry_02", "type": "spiritshop", "ack": False, "created_at": 1693055546},
                {"name": "lootbox_common_52", "type": "spiritshop", "ack": False, "created_at": 1693055548},
                {"name": "heart_cry", "type": "spiritshop", "ack": False, "created_at": 1693055552},
                {"name": "tier_unlock_cry_01", "type": "spiritshop", "ack": False, "created_at": 1693055554},
                {"name": "emote_upgrade_cry_03", "type": "spiritshop", "ack": False, "created_at": 1693055558},
                {"name": "lootbox_common_43", "type": "spiritshop", "ack": False, "created_at": 1693055599},
                {"name": "heart_pout", "type": "spiritshop", "ack": False, "created_at": 1693055603},
                {"name": "CharSkyKid_Hair_ShoulderLong", "type": "spiritshop", "ack": True, "created_at": 1693055609},
                {"name": "lootbox_common_55", "type": "spiritshop", "ack": False, "created_at": 1693055801},
                {"name": "tier_unlock_bow_01", "type": "spiritshop", "ack": False, "created_at": 1693055808},
                {"name": "teleport_to_questap23", "type": "spiritshop", "ack": False, "created_at": 1724892996},
                {"name": "emote_upgrade_crabwalk_01", "type": "spiritshop", "ack": False, "created_at": 1737348992},
                {"name": "lootbox_rare_crabwalk_01", "type": "spiritshop", "ack": False, "created_at": 1737348996},
                {"name": "tier_unlock_crabwalk_01", "type": "spiritshop", "ack": False, "created_at": 1737349004},
                {"name": "heart_crabwalk", "type": "spiritshop", "ack": False, "created_at": 1737349006},
                {"name": "teleport_to_questap25", "type": "spiritshop", "ack": False, "created_at": 1738135940},
                {"name": "emote_upgrade_scheme_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scheme_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scheme_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scheme_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_slouch_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_slouch_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_slouch_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_slouch_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_gloat_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_gloat_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_gloat_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_gloat_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_stretch_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_stretch_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_stretch_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_stretch_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_beckon_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_beckon_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_beckon_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_beckon_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sneeze_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sneeze_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sneeze_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sneeze_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_voila_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_voila_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_voila_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_voila_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_nerdy_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_babymanta_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pointup_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pointup_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_anxious_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_anxious_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_anxious_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_anxious_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wait_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wait_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wait_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wait_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_ouch_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_ouch_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_ouch_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_ouch_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_evillaugh_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_evillaugh_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_evillaugh_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_evillaugh_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_headbob_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_headbob_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_headbob_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_duetdance_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_duetdance_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_handshake_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_awww_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_awww_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_awww_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_armwave_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_armwave_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_armwave_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_raisetheroof_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_raisetheroof_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_raisetheroof_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_twirl_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_twirl_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_twirl_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_rhythmicclap_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_rhythmicclap_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_rhythmicclap_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_penguindance_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_penguindance_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_silentclap_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_silentclap_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_conduct_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_conduct_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_skip_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_skip_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_plead_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_plead_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_plead_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_plead_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tiptoe_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tiptoe_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tiptoe_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tiptoe_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_grief_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_grief_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_grief_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_grief_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_injured_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_hackysack_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_hackysack_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_hackysack_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_hackysack_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_roll_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_roll_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_roll_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_roll_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_mope_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_mope_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_mope_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_mope_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pullup_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pullup_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pullup_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pullup_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_jollydance_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_jollydance_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_jollydance_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_jollydance_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_windpose_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_windpose_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_windpose_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_windpose_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_whistle_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_whistle_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_whistle_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_whistle_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_muscle_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_muscle_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_muscle_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_muscle_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_princesscarry_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_princesscarry_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_floatdance_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_floatdance_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_floatdance_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_floatdance_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_duetbow_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_duetbow_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_reading_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_reading_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_reading_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_spintrick_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_spintrick_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_spintrick_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_spintrick_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_reading_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_come_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_come_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_come_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_nothanks_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_nothanks_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_nothanks_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_butterfly_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_butterfly_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_butterfly_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_laugh_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_laugh_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_laugh_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wave_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wave_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wave_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wave_05", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wave_06", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_yawn_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_yawn_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_yawn_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_thumbsup_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_thumbsup_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_thumbsup_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wipe_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wipe_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wipe_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cold_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cold_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cold_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cry_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cry_05", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cry_06", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sorry_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sorry_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sorry_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pout_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pout_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pout_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shy_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shy_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shy_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_ohno_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_ohno_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_ohno_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bow_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bow_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bow_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_backflip_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_backflip_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_backflip_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cheer_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cheer_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_cheer_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_handstand_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_handstand_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_handstand_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scared_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scared_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scared_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_die_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_die_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_die_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_lookaround_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_lookaround_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_lookaround_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_salute_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_salute_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_salute_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_force_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_force_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_force_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_float_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_float_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_float_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pray_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pray_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_pray_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sass_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_salutation_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_salutation_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_salutation_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_salutation_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sarcastic_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sarcastic_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sarcastic_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sarcastic_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_joy_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_joy_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_joy_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_acknowledge_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_acknowledge_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_acknowledge_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_acknowledge_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_kungfu_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_kungfu_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_kungfu_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_doublefive_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_doublefive_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_lazycool_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tripleaxel_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tripleaxel_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tripleaxel_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tripleaxel_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_crabvoice_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shh_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shh_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shh_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shh_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_carry_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_carry_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_celebrate_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_celebrate_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_celebrate_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_celebrate_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_loopdance_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_loopdance_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_loopdance_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_loopdance_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sparkler_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sparkler_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sparkler_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_sparkler_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_wise_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dontgo_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dontgo_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dontgo_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dontgo_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_hairtousle_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_welcome_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_welcome_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_welcome_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_welcome_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dance_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dance_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dance_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dance_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_kiss_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_kiss_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_kiss_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_kiss_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_juggle_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_juggle_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_juggle_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_juggle_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_respect_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_respect_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_respect_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_nod_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_nod_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_nod_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_nod_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scare_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scare_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scare_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_scare_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_doze_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_doze_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_doze_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_doze_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_crabwalk_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_crabwalk_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_crabwalk_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shrug_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shrug_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shrug_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_shrug_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_playfight_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_playfight_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_jelly_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_timid_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_rally_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_rally_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_rally_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_rally_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_grumpy_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_grumpy_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_grumpy_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_grumpy_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_gratitude_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_gratitude_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_gratitude_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_gratitude_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bellyscratch_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bellyscratch_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bellyscratch_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bellyscratch_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_deepbreath_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_deepbreath_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_deepbreath_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_deepbreath_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dustoff_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dustoff_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dustoff_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_dustoff_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_balance_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_balance_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_balance_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_balance_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_chestpound_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_chestpound_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_chestpound_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_showdance_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_showdance_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_showdance_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_showdance_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_peek_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_peek_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_peek_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_peek_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bearhug_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bearhug_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_chuckle_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_chuckle_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_chuckle_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_chuckle_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bubbles_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bubbles_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bubbles_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_bubbles_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_marching_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_marching_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_marching_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_marching_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_facepalm_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_facepalm_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_facepalm_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_facepalm_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tsktsk_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tsktsk_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tsktsk_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_tsktsk_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_eww_01", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_eww_02", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_eww_03", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "emote_upgrade_eww_04", "type": "spiritshop", "ack": False, "created_at": 1700000000},
                {"name": "CharSkyKid_Hair_AP04Pumpkin_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_ClassicShortPants", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_ShortPants", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_LongPants", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_MonkPants", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_WarriorPants", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_LongMonkPants", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_LongDancePants", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_APMerchantPants_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_APDancerPants_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_APActorPants_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKidGhost", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_Ghost", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKidLODTest", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyElderB", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyElderC", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyGhost", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_Taged", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_FemaleCivilian", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_MerchantRig", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_MusicianRig", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_ActorRig", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_DirectorRig", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_DancerRig", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_JugglerRig", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_MaleCivilian", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_FatLabor", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_FatLaborWave", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_FatLaborStaff", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_FatLaborChild", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_StrongLabor", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_SkinnyIntellectual", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Body_StrongKnight", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Hair_FatLaborBun", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Mask_FatLaborMask", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Body_Perfect", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Neck_APPendant_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Neck_AP02Pendant", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Neck_AP03Pendant", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_Basic", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_ElderDawn", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_ElderDay", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_Bandana", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_ElderRain", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_ElderSunsetB", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_ElderSunsetA", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_ElderDusk", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_ElderNight", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_Smoke", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_BasicQ", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_None", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP02Crab_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP02Fox_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP02Bird_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP02Bull_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP02Deer_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP02Weasel_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP03A", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP03B", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP03C", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP03D", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP03E", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP03F", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_AP03G", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_APMerchant_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_APActor_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_APDirector_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_SunDog", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Mask_BasicZero", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Mask_APDefault", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Mask_KnightHelmet", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Horn_None", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Horn_TuneBand", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Horn_FlowerBuff", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_BraidSideSmall", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_TopOneSideS", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_Bun", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_Mohawk", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_BraidTwo", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_TopTwoKnot", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_TopOneSideM", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_TwoTails", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_BallCurly", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_TopOneSideL", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_Buzz", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_BackLong", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_Beard", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_ShoulderLongTie", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_TwoBallsCurly", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_BraidBack", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_LongBob", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_TopTwoKnotBig", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_Kyle", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_Lion", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_Puff", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_Afro", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_None", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_AP02Stupa_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_AP02Bhutan_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_AP02Cone_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_APDancer_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_APJuggler_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_APMusician_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_AP03Victorian_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_AP03Samurai_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_AP03Barrette_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_AP03Upstyle_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Hair_AP03Umbrella_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_None", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_Harp", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_Drum", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_Bass", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_AP03Flute_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_AP03PanFlute_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_Voice", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_Piano", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_Firework", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_Umbrella", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_AP03Umbrella_02", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_Table", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_AP02Bell_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_AP02Bell_02", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Prop_APPiano_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Book", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Booze", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_ButterflyNet", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Clipboard", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_FireStick", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Flag", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Horn", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Jar", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Oar", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_PickAxe", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_PowerGlove", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Shield", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Spear", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Sword", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Rifle", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_Lightstone", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyNPC_Prop_LongPickaxe", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Default", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_TGCBlue", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_TGCBlue$", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Orange", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Yellow", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Red", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Beige", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Teal", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Ancestor", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Purple", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Pink", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_Black", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_ClassicOne_White", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_Bat", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_AP03Petal_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_AP03Biolum_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_AP03Biolum_02", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_AP02Fur_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_AP02Tail_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_APJuggler_01", "type": "spiritshop", "ack": True, "created_at": 1654570099},
                {"name": "CharSkyKid_Wing_APDirector_01", "type": "spiritshop", "ack": True, "created_at": 1654570099}
            ]
            
            # 需求：新号必须"干净"——不默认拥有全光翼/全动作/全门(先祖能力)/全衣服。
            # 这些只能由管理层通过 /admin 推送解锁。新号只保留"左下角"的默认动作
            # 和"初始"几件外观(空槽+基础面具+默认斗篷+默认裤子), 其余
            # (动作升级、其余衣服、Wing 光翼件、AP 先祖能力即开门、季节解锁等)一律不种入。
            keep_emotes = set(default_emote_names())
            keep_clothes = default_clothes_names()
            for unlock_data in default_unlocks:
                name = unlock_data["name"]
                if is_emote_unlock(name):
                    keep = name in keep_emotes
                elif is_clothing(name):
                    keep = name in keep_clothes
                # 其余项(季节/AP能力等)默认不种入, 等待管理层推送或地图内解锁
                else:
                    keep = False
                if not keep:
                    continue
                unlock_item = UnlockItem(
                    user_id=user_id,
                    name=name,
                    unlock_type=unlock_data["type"],
                    ack=unlock_data["ack"],
                    created_at=unlock_data["created_at"]
                )
                db.session.add(unlock_item)
            
            db.session.commit()

        if auto_full:
            # 新建账号全解锁(版本分流): 按客户端版本发送对应 OutfitDefs 数据,
            # 并并入该账号已有的库内解锁(按 name 去重)。
            names_seen = set()
            full_list = []
            for it in full_unlocks(client_version=_client_version()):
                nm = it.get("name")
                if nm and nm not in names_seen:
                    full_list.append(it)
                    names_seen.add(nm)
            for row in UnlockItem.query.filter_by(user_id=user_id).all():
                if row.name in names_seen:
                    continue
                full_list.append({
                    "name": row.name,
                    "type": row.unlock_type,
                    "ack": row.ack,
                    "created_at": row.created_at,
                })
                names_seen.add(row.name)
            # **先并完库内的行, 再统一排序+过体积闸门**(见 finalize_unlocks 的注释):
            # 老实现是"先裁再并", 库内那两千多行又把裁掉的原样加回来 -> 82KB 变 290KB ->
            # 0.22.0 客户端一登录就闪退(2026-10-01 01:36 实测 290,410 字节即崩)。
            full_list = finalize_unlocks(full_list, _ver)
            response_body = {
                "unlocks": full_list,
                "unlocks_total_count": len(full_list)
            }
            return Response(
                response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
                status=200,
                mimetype='application/json'
            )

        unlocks_query = UnlockItem.query.filter_by(user_id=user_id)
        total_count = unlocks_query.count()

        # 排序: **重要程度优先**, 同组内"新的在前"。
        # 老实现是纯 created_at DESC —— 管理层"推送全解锁"写进去的两千多行 created_at
        # 全是一个值, 谁进得了前 1000 名纯看 rowid, 斗篷/光翼(CharSkyKid_Wing_*)就经常
        # 被挤掉, 表现就是"推送了全解锁但斗篷没解锁"。
        rows = unlocks_query.all()
        # 带上 type: "level" 类(状态端点已同样下发)排到最后, 见 _unlock_priority 的说明。
        rows.sort(key=lambda r: (_unlock_priority({"name": r.name, "type": r.unlock_type}),
                                 -(r.created_at or 0)))

        def _row_to_item(r):
            return {
                "name": r.name,
                "type": r.unlock_type,
                "ack": r.ack,
                "created_at": r.created_at,
            }

        if 'l' in request_data:
            # 客户端带了分页参数(部分版本会发 {"l":1000,"o":0,"v":0}) -> 按官方分页语义
            # 返回请求的那一页, 不多不少。total 仍是库内总行数, 客户端据此翻页。
            try:
                _off = max(0, int(offset or 0))
            except (TypeError, ValueError):
                _off = 0
            try:
                _lim = max(0, int(limit if limit is not None else 1000))
            except (TypeError, ValueError):
                _lim = 1000
            page = rows[_off:_off + _lim] if _lim else []
            unlocks_list = [_row_to_item(r) for r in page]
            response_body = {
                "unlocks": unlocks_list,
                "unlocks_total_count": total_count
            }
        else:
            # 客户端没带分页参数(0.22.0 实测只发 {"user","session"}) —— 它要的就是
            # **完整**解锁表, 老实现却按 limit=1000 硬截断, 2450 行里的 1450 行
            # (含 211 件斗篷/光翼)整片丢掉。这里返回全部, 再由 finalize_unlocks
            # 排序 + (老客户端)体积闸门兜底。
            unlocks_list = [_row_to_item(r) for r in rows]
            unlocks_list = finalize_unlocks(unlocks_list, _ver)
            response_body = {
                "unlocks": unlocks_list,
                "unlocks_total_count": len(unlocks_list)
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