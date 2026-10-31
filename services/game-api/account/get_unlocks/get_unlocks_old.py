# -*- coding: utf-8 -*-
"""
低版本(< 0.20.0)专用 get_unlocks 实现

来自 H:/beifen/Sky初始备份 的原始版本, 用于版本分流:
  客户端版本 < 0.20.0 时由 get_unlocks 委托到本函数。
"""
from flask import request, jsonify, Response
from database.models import db, User, UnlockItem
import json
import os
import re as _re

# 与 >=0.20.0 主分支(get_unlocks.py)相同的"默认集合"判定:
# 新号只保留"左下角默认动作 + 初始外观", 其余一律不发也不种库。
try:
    from helpers.emote_helper import is_emote_unlock, default_emote_names
    from helpers.clothes_helper import is_clothing, default_clothes_names
except Exception:  # 助手模块缺失时退化为"什么都不保留", 宁可少给也不给全量
    def is_emote_unlock(_n):
        return False

    def default_emote_names():
        return set()

    def is_clothing(_n):
        return False

    def default_clothes_names():
        return set()


def _keep_default_only(item):
    """该解锁项是否属于"新号默认就该有"的集合(默认动作 / 初始外观)。

    返回 False 的项既不发给客户端, 也不写进数据库:
      * ap06unlock_* / teleport_to_quest*  -> 开门/传送, 等于"全地图";
      * CharSkyKid_Wing_*                  -> 全光翼;
      * 其余 Char*_Body_/Hair_ 等           -> 全套衣服/发型;
      * 季节解锁、关系能力等                 -> 由管理层推送或游戏内解锁。
    """
    nm = item.get("name") if isinstance(item, dict) else item
    if not nm:
        return False
    if is_emote_unlock(nm):
        return nm in set(default_emote_names())
    if is_clothing(nm):
        return nm in (default_clothes_names() or set())
    return False


def _client_version():
    """从请求 User-Agent 解析客户端版本 (major, minor, patch) 或 None。"""
    try:
        ua = request.headers.get("User-Agent", "") if request else ""
        m = _re.search(r"/(\d+)\.(\d+)\.(\d+)", ua)
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


# ===== "开全门"联动(老客户端专用通道) =====
# 管理面板的"开全门"是给 /service/status/api/v1/get_unlocks 写响应覆盖体
# (config/kaiquantu.json, 625 条 status_unlocks, type=level)。
# 但 <0.20.0 的老客户端(0.7.1 / 0.12 等)**没有** /service/status 接口, 它的
# 地图/门状态只认本接口(/account/get_unlocks)里 type=level 的条目 —— 结果就是
# 用户反馈的"高版本能开全部地图, 0.7.1 开不了"。
# 这里做联动: 该账号若已设"开全门"覆盖体, 就把覆盖体里的解锁名以 type=level
# 一并下发给老客户端(只改响应, 不写库; 与面板往 unlock_item 写 628 条 level
# 的效果一致, 但无需等玩家重新登录/面板重启)。
_STATUS_UNLOCK_PATH = "/service/status/api/v1/get_unlocks"


def _status_override_level_items(user_id):
    """该账号"开全门"覆盖体里的解锁 -> get_unlocks 条目列表; 没有则返回 []。

    覆盖体形如 {"status_unlocks": [{"name":..,"type":"level",...}, ...]}。
    会剔除客户端已删除(墓碑)的项, 与 status 接口的合并口径保持一致。
    """
    if not user_id:
        return []
    try:
        from helpers.response_override import get_override
        body = get_override(user_id, _STATUS_UNLOCK_PATH)
        if not body:
            return []
        data = json.loads(body)
        if not isinstance(data, dict):
            return []
        deleted = set()
        try:
            from helpers.status_unlock_tombstone import deleted_names
            deleted = set(deleted_names(user_id) or ())
        except Exception:
            deleted = set()
        items, seen = [], set()
        for it in data.get("status_unlocks") or []:
            nm = it.get("name") if isinstance(it, dict) else it
            if not nm or nm in seen or nm in deleted:
                continue
            seen.add(nm)
            items.append({
                "name": nm,
                "type": (it.get("type") if isinstance(it, dict) else None) or "level",
                "ack": bool(it.get("ack", True)) if isinstance(it, dict) else True,
                "created_at": (it.get("unlocked_at") if isinstance(it, dict) else 0) or 0,
            })
        return items
    except Exception:
        return []


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_unlocks_old():
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
                    unlocks_list = (config_data.get("unlocks", [])
                                    if isinstance(config_data, dict) else [])

                # 【关键修复】0.6.5_unlock.json 是"全解锁"清单(含 ap06unlock_*/
                # teleport_to_quest* 开门传送 = 全地图、CharSkyKid_Wing_* = 全光翼、
                # 全套衣服)。原来这里**原样返回**, 于是 <0.20.0 的老客户端
                # (0.7.1 / 0.13.4 等)一登录就是"全服饰 + 全地图", 与"不解锁模式"
                # 完全矛盾。现在与 >=0.20.0 主分支同一口径: 新号默认只发
                # "左下角默认动作 + 初始外观"。
                filtered = [it for it in unlocks_list if _keep_default_only(it)]

                # 并入该账号已推送/已拥有的解锁(管理层"推送解锁"会写进 UnlockItem),
                # 保证老版本客户端同样能立刻看到管理层推送的内容。
                try:
                    if user_id:
                        names_seen = {(it.get("name") if isinstance(it, dict) else it)
                                      for it in filtered}
                        for row in UnlockItem.query.filter_by(user_id=user_id).all():
                            if row.name in names_seen:
                                continue
                            filtered.append({
                                "name": row.name,
                                "type": row.unlock_type,
                                "ack": row.ack,
                                "created_at": row.created_at,
                            })
                            names_seen.add(row.name)
                except Exception:
                    pass

                # 开全门联动: 老客户端没有 /service/status 接口, 地图/门要靠这里下发
                _seen_ov = {(it.get("name") if isinstance(it, dict) else it)
                            for it in filtered}
                for _ov in _status_override_level_items(user_id):
                    if _ov["name"] in _seen_ov:
                        continue
                    filtered.append(_ov)
                    _seen_ov.add(_ov["name"])

                # 排序 + 体积闸门必须放在**并库/并覆盖体之后**(见 finalize_unlocks 注释):
                # 先裁后并会把裁掉的部分原样加回来 -> 响应暴涨 -> 老客户端闪退。
                try:
                    from .get_unlocks import finalize_unlocks
                    filtered = finalize_unlocks(filtered, _client_version())
                except Exception:
                    pass

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

        # 新建账号全解锁(版本分流): 开启后, 开启时间点之后新建的账号拉取数据时
        # 直接返回该客户端版本对应的完整解锁(按 H:\beifen 各版本 OutfitDefs 分段)。
        auto_full = False
        try:
            from helpers.full_unlock_helper import should_auto_full_unlock, full_unlocks
            auto_full = should_auto_full_unlock(user.created_at)
        except Exception:
            auto_full = False
        if auto_full:
            names_seen = set()
            full_list = []
            _cver = _client_version()
            for it in full_unlocks(client_version=_cver):
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
            # 体积闸门(与 get_unlocks 主分支同策): 老客户端(<0.20.0 这里更老)解析不了
            # 过大的响应会闪退。**必须并完库内的行之后再裁**(见 finalize_unlocks):
            # 先裁后并的话库内那两千多行会把裁掉的原样加回来, 响应 82KB -> 290KB ->
            # 客户端一登录就闪退(2026-10-01 实测, 0.22.0 设备)。
            try:
                from .get_unlocks import finalize_unlocks
                full_list = finalize_unlocks(full_list, _cver)
            except Exception:
                pass
            return Response(
                response=json.dumps({
                    "unlocks": full_list,
                    "unlocks_total_count": len(full_list)
                }, sort_keys=False, ensure_ascii=False),
                status=200,
                mimetype='application/json'
            )

        # 检查用户是否已有解锁记录，如果没有则初始化默认解锁
        existing_unlocks = UnlockItem.query.filter_by(user_id=user_id).count()
        if existing_unlocks == 0:
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
                {"name": "season_finish_14", "type": "season", "ack": False, "created_at": 1659243072},
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
                {"name": "season_finish_15", "type": "season", "ack": False, "created_at": 1664932880},
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
                {"name": "season_finish_16", "type": "season", "ack": False, "created_at": 1689208333},
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
                {"name": "season_finish_18", "type": "season", "ack": False, "created_at": 1689209029},
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
                {"name": "season_finish_19", "type": "season", "ack": False, "created_at": 1697450047},
                {"name": "season_finish_20", "type": "season", "ack": False, "created_at": 1720010941},
                {"name": "teleport_to_questap23", "type": "spiritshop", "ack": False, "created_at": 1724892996},
                {"name": "season_finish_23", "type": "season", "ack": False, "created_at": 1734325138},
                {"name": "season_finish_24", "type": "season", "ack": False, "created_at": 1737348938},
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
            
            for unlock_data in default_unlocks:
                # 【关键修复】原来把这张 782 项的默认表**整包写进数据库**
                # (add 解锁项), 其中含 ap06unlock_*/teleport_to_quest*(开门+传送
                # = 全地图)、全部 CharSkyKid_Wing_*(全光翼)、全部衣服/发型。
                # 后果有两个, 都很致命:
                #   1) 新号一进游戏就是"全服饰 + 全地图";
                #   2) 这些行**永久留在账号上**, 之后即使把"新建账号全解锁"关掉、
                #      甚至改代码, 该账号也仍然显示全解锁(行已经种进去了)。
                # 现在只把"默认动作 + 初始外观"种入库, 与 >=0.20.0 主分支一致。
                if not _keep_default_only(unlock_data):
                    continue
                unlock_item = UnlockItem(
                    user_id=user_id,
                    name=unlock_data["name"],
                    unlock_type=unlock_data["type"],
                    ack=unlock_data["ack"],
                    created_at=unlock_data["created_at"]
                )
                db.session.add(unlock_item)
            
            db.session.commit()

        unlocks_query = UnlockItem.query.filter_by(user_id=user_id)
        total_count = unlocks_query.count()

        # 排序: 重要程度优先(动作 -> 斗篷/光翼 -> 服饰 -> 其余), 同组内新的在前。
        # 纯 created_at DESC 时"管理层批量推送"的两千多行 created_at 全相同, 谁进前
        # 1000 名纯看 rowid, 斗篷/光翼经常被挤掉(见 get_unlocks.py 的同一处说明)。
        _rows = unlocks_query.all()
        try:
            from .get_unlocks import _unlock_priority as _prio
        except Exception:
            def _prio(_it):
                return 0
        _rows.sort(key=lambda r: (_prio({"name": r.name}), -(r.created_at or 0)))

        def _row_to_item(r):
            return {
                "name": r.name,
                "type": r.unlock_type,
                "ack": r.ack,
                "created_at": r.created_at,
            }

        _paged = 'l' in request_data
        if _paged:
            try:
                _off = max(0, int(offset or 0))
            except (TypeError, ValueError):
                _off = 0
            try:
                _lim = max(0, int(limit if limit is not None else 1000))
            except (TypeError, ValueError):
                _lim = 1000
            unlocks_list = [_row_to_item(r) for r in (_rows[_off:_off + _lim] if _lim else [])]
        else:
            # 客户端没带分页参数 -> 要的是完整解锁表(老实现却按 limit=1000 硬截断,
            # 会整片丢掉斗篷/光翼)。全量返回, 再由 finalize_unlocks 兜底。
            unlocks_list = [_row_to_item(r) for r in _rows]

        # 开全门联动: 老客户端(<0.20.0, 如 0.7.1)没有 /service/status/api/v1/get_unlocks,
        # 面板写在那里的"全开门"覆盖体读不到 -> 把覆盖体里的解锁名以 type=level 并入
        # 本响应(只影响首屏 offset==0; 这些项不在库里, 分页拉不到, 故只在首页并入并
        # 相应抬高 total_count, 避免客户端以为少拉了)。
        try:
            _first_page = int(offset or 0) == 0
        except (TypeError, ValueError):
            _first_page = True
        if _first_page:
            _seen_ov = {u["name"] for u in unlocks_list}
            _extra = [it for it in _status_override_level_items(user_id)
                      if it["name"] not in _seen_ov]
            if _extra:
                unlocks_list.extend(_extra)
                total_count += len(_extra)

        if not _paged:
            # 排序 + 体积闸门(并完覆盖体之后) —— 老客户端响应过大=闪退。
            try:
                from .get_unlocks import finalize_unlocks
                unlocks_list = finalize_unlocks(unlocks_list, _client_version())
                total_count = len(unlocks_list)
            except Exception:
                pass

        response_body = {
            "unlocks": unlocks_list,
            "unlocks_total_count": total_count
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