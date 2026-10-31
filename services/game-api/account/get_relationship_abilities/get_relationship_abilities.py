# -*- coding: utf-8 -*-
"""
/account/get_relationship_abilities —— 好友树(关系树)的节点定义接口。

客户端拿到 relationship_defs 之后才会渲染"点击好友 -> 弹出的好友树"里的可解锁项。
之前这里只返回 1 个节点(id 26 hug, cost 0, 无前置), 所以好友树是空的、一个能解锁的
项都没有。

数据来源: static_responses/account__get_relationship_abilities.json (仓库自带的完整
47 节点定义, 字段与旧的手写单节点响应完全一致)。运行时读取(按 mtime 缓存), 读不到时
退回旧单节点响应, 保证这个接口永远不会 500。

2026-10-04 重要修正(此前那版过滤是**错的**, 已默认关闭):
  之前这里会砍掉 18 个 id(35/36/37/43/45/47/50/57/59/60/61..69), 再对 pre_reqs 做闭包,
  最终只回 27 个节点。实测后果与"好友树"报错完全一致:
  「一方已经解锁另一方没有解锁 / 两边都该有节点, 但树却往左边越排越远, 栏外点不到」。

  反编译佐证(D:\\工具\\SO\\libBootloader0200.so.c):
    * 客户端载入 relationship_defs 后会**自己按 tier 升序排序**再分组建树
      (sub_85811C 是对 def 数组的 std::sort, 比较键 = def+224 即 tier), 所以
      服务端回数组的顺序不重要, **tier 才决定树的形状**。
    * 布局函数 sub_868F40: 一个父节点的子节点若只有一个, 偏移量常量 xmmword_120A8A0
      = (0, 1.0) —— 单子节点是**正下方**, 不会横移; 只有"多子节点"时第 i 个子节点
      才按 (i-1) 横向铺开(i=1 走正下方的主干, i=0 往左, i=2 往右)。
    * 也就是说: 主干是不是居中, 取决于每个分叉点上"稍微深一点的那个子节点"必须是
      tier 第二小(排到 index 1)。把某个分叉点的兄弟节点删掉, 主干就会顶替到 index 0
      被顶到左边, 之后每一层都往左偏一格 —— 越排越远, 到最后跑到面板外点不到。
    * 对完整 47 节点表按 tier 排序跑一遍 sub_868F40 的布局(x/y 见在电脑上跑过的模拟):
      所有节点 x ∈ [-1, +1], 左右两边都有节点, 完全居中;
      而砍成 27 个之后最小 x = **-6**(id41 跑到左边第 6 格) —— 就是用户看到的那棵树。

  另外核对过: 被砍掉的那 18 个节点的 event 只有 SocialOffer / SocialTierUnlock(这两个
  事件本客户端都有, 不会触发 sub_8686C0 的 "No event named %s" abort); 它们的贴图名不在
  0.22 里时会走 sub_856528 的 UiMiscStar 兜底 + 一行警告(非致命)。所以完整回 47 个
  (≤ kMaxRelationshipAbilities = 0x40 = 64)是安全的。

  现在默认**整表回**(不做任何过滤)。想临时回到旧的 27 节点行为:
      SKY_RELATIONSHIP_DEFS_FILTER=1
  (仅供对比排查用, 正常不要开 —— 开了好友树就会往左偏。)

2026-10-06 老客户端闪退修正(**按版本下发事件**) —— 本轮新增:
  0.7.1 / 0.13.4 客户端一进"好友树/关系能力"就原生 abort:
      ASSERT! RelationshipAbilityDefs.cpp (364/532):
      GetMetaClassByName( abilityDef->eventName.c_str() ),
      RelationshipAbility 'iap' - no RelationshipButtonEvent of type SocialIAP
      - remove from kSupportedAbilityIds?
  原因是服务端把 iap(id 55, event=SocialIAP) 也发给了没有 SocialIAP 这个
  RelationshipButtonEvent 元类的老客户端: 客户端载入 relationship_defs 时会逐条
  GetMetaClassByName(eventName), 查不到就 abort(整个 App 闪退)。

  判据(D:\\工具\\SO 里全部 libBootloader*.so 反编译 .c 逐个扫事件字符串):
      SocialIAP          0.19.5/0.20.0/0.33.7 有, 0.6.5~0.13.4 无
      SocialWarpBlock    0.11.0+ 有, 0.6.5/0.10.1 无
      SocialLink/Unlink  0.12.2+ 有, 0.6.5~0.11.0 无
  所以这里按 UA 版本只保留该客户端认识的事件(表见 _REL_EVENT_MIN_VERSION),
  并顺手去掉 pre_reqs 指向被删节点的节点。认不出 UA 时按**最老客户端**处理
  (宁可少发, 绝不触发 abort)。新版客户端(0.22+/0.32+)照旧拿全 47 条, 好友树
  布局不受影响。

  关掉这道闸(仅排查用): SKY_RELATIONSHIP_EVENT_GATE=0
"""

import json
import logging
import os
import re

from flask import request, jsonify, Response

try:
    from helpers.version_helper import parse_client_version
except Exception:  # pragma: no cover - 兜底, 不能因为这个接口 import 失败
    def parse_client_version(user_agent=None):
        return None

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEF_FILE = os.path.join(
    os.path.dirname(os.path.dirname(_HERE)),   # -> Sky/
    "resources", "static_responses",
    "account__get_relationship_abilities.json",
)

# 关系能力事件 → 客户端"首次"具备该 RelationshipButtonEvent 的版本。
# 由 D:\工具\SO 的全版本 libBootloader*.so(.c) 事件字符串扫描得出(见文件头 2026-10-06 说明)。
# 表里没有的事件 = 所有版本都有, 直接放行。
_REL_EVENT_MIN_VERSION = {
    "SocialWarpBlock": (0, 11, 0),
    "SocialLink": (0, 12, 0),
    "SocialUnlink": (0, 12, 0),
    "SocialIAP": (0, 19, 0),
}

# 配置文件(改完按 mtime 就地生效, 不用重启): Sky/config/relationship_events.json
_EVENTS_CFG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(_HERE)), "config", "relationship_events.json"
)

# 默认**对所有客户端**都不下发的事件。
#
# 2026-10-06 第二轮修正: 光按 UA 判版本不够 —— 有的客户端**谎报版本**。
# 实测(instance/users.db + panel_8443.log): 用户跑的 "0.13.4"(crash tag
# com.tgc.sky.androidold, libBootloader 就是 0.13.4 那份, 原生里**根本没有**
# SocialIAP 元类 —— D:\工具\SO\libBootloader0134..so.c 里搜 SocialIAP = 0 处),
# 它在 UA 里报的是 **Sky-Live-com.tgc.sky.android/0.23.6.354648**。
# 服务端按 UA 当它是新客户端, 发了含 iap(id55, event=SocialIAP)的整表(日志:
# "版本=(0, 23, 6) -> 47/47"), 6 秒后原生 abort:
#     RelationshipAbilityDefs.cpp (532): GetMetaClassByName( abilityDef->eventName )
#     RelationshipAbility 'iap' - no RelationshipButtonEvent of type SocialIAP
# 既然 UA 里的版本号可以随便报, 唯一稳妥的做法就是: 这条节点**谁都不发**。
#
# 影响面: id55 只是好友树里一个叶子节点 —— 它的父节点 id5(fist_bump) 还有另外
# 两个孩子(id37 tier16 / id42 tier17), 砍掉它父节点仍是"多子节点", 主干(tier 第二小
# = id42)不动, 所以**不改变树形/布局**(见文件头 2026-10-04 的布局分析)。
# 真要给某个"确认真实"的新版客户端恢复: 把它的完整构建号填进 always_keep_builds。
_ALWAYS_DROP_DEFAULT = ["SocialIAP"]

_ev_cfg_cache = {"mtime": None, "cfg": None}


def _parse_ver(v):
    """'0.19.0' / [0,19,0] / (0,19,0) -> (0,19,0); 解析不了返回 None。"""
    if isinstance(v, (list, tuple)):
        try:
            parts = [int(x) for x in v]
        except (TypeError, ValueError):
            return None
        return tuple((parts + [0, 0, 0])[:3])
    if isinstance(v, str):
        m = re.match(r'\s*(\d+)\.(\d+)\.(\d+)', v)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _events_config():
    """读 config/relationship_events.json (按 mtime 缓存); 读不到就用内置默认。"""
    cfg = {
        "event_min_version": {k: tuple(v) for k, v in _REL_EVENT_MIN_VERSION.items()},
        "always_drop_events": list(_ALWAYS_DROP_DEFAULT),
        # {"SocialIAP": ["0.23.6.354648", ...]} —— UA 里那个完整版本号
        "always_keep_builds": {},
    }
    try:
        mtime = os.path.getmtime(_EVENTS_CFG_FILE)
    except OSError:
        return cfg
    if _ev_cfg_cache["cfg"] is not None and _ev_cfg_cache["mtime"] == mtime:
        return _ev_cfg_cache["cfg"]
    try:
        with open(_EVENTS_CFG_FILE, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            for k, v in (raw.get("event_min_version") or {}).items():
                pv = _parse_ver(v)
                if pv:
                    cfg["event_min_version"][k] = pv
            drop = raw.get("always_drop_events")
            if isinstance(drop, list):
                cfg["always_drop_events"] = [str(x) for x in drop]
            keep = raw.get("always_keep_builds")
            if isinstance(keep, dict):
                cfg["always_keep_builds"] = {
                    str(k): [str(x) for x in (v or [])] for k, v in keep.items()
                }
    except Exception as exc:  # noqa: BLE001 - 配置坏了不能拖垮接口
        logger.warning("读取好友树事件配置 %s 失败(%s), 用内置默认", _EVENTS_CFG_FILE, exc)
    _ev_cfg_cache["mtime"] = mtime
    _ev_cfg_cache["cfg"] = cfg
    return cfg

# 0.22 客户端 binary 里不存在的 ability(见文件头说明)
_CLIENT_UNKNOWN_ABILITY_IDS = {
    35, 36, 37, 43, 45, 47, 50, 57, 59, 60,
    61, 62, 63, 64, 65, 66, 68, 69,
}

_FALLBACK_DEFS = [
    {
        "id": 26,
        "ability": "hug",
        "pre_reqs": [],
        "tier": 0,
        "cost": 0,
        "event": "SocialOffer",
        "icon": "UiSocialHug",
        "unwrap_hint": "accept_hug",
        "gifting_hint": "relationship_hug",
        "anim": "OfferHug",
        "permanent": True,
        "give_also": [],
        "icon_locked": "UiSocialHug",
        "listed": True,
        "icon_hint": "UiSocialHug",
        "enabled": True,
        "currency_type": "candles",
        "controller": "king",
        "x": 0,
        "y": 0,
        "reward_type": "heart",
        "reward_amount": 0,
        "anims": [
            "whiteEmbraceHold",
            "avatarEmbraceHold",
            "hugTakeF",
            "hugGiveF",
            "charTwoHugHold",
            "hugTakeF2",
            "hugGiveF2"
        ],
        "trust_tier": None,
        "trust_order": None,
        "trust_dependency": None,
        "trust_activity": None,
        "scheduled_event": None
    }
]

# mtime 缓存的节点列表
_cache = {"mtime": None, "defs": None}


def _filter_enabled():
    """是否启用旧的 27 节点过滤 —— 默认关闭(见文件头 2026-10-04 说明)。

    开着它会让好友树主干每层往左偏一格(单子节点/兄弟被删), 实测跑出 x=-6 的
    栏外节点。只建议在排查旧行为时临时置 1。
    """
    return os.environ.get("SKY_RELATIONSHIP_DEFS_FILTER", "0") == "1"


def _event_gate_enabled():
    """是否按客户端版本过滤 event —— 默认开启(见文件头 2026-10-06 说明)。"""
    return os.environ.get("SKY_RELATIONSHIP_EVENT_GATE", "1") != "0"


def _client_version():
    """尽力取出客户端版本 (major, minor, patch); 取不到返回 None。

    version_helper.parse_client_version 只认 "Sky.../0.x.y" 这种官方 UA。有些渠道包
    UA 是 "com.tgc.sky.androidold/0.13.4.169938" 之类的写法, 严格正则匹配不到 ->
    会被当成"最老客户端"(安全但保守)。这里补一个只认 "/0.x.y" 的宽松兜底: 老版本
    一定要被识别出来(否则漏发 SocialIAP 就是闪退), 新版本被识别出来才不会白丢节点。
    """
    ver = parse_client_version()
    if ver:
        return ver
    try:
        ua = request.headers.get("User-Agent", "") or ""
    except Exception:
        return None
    m = re.search(r'/0\.(\d+)\.(\d+)', ua)
    if m:
        return (0, int(m.group(1)), int(m.group(2)))
    return None


def _client_build():
    """UA 里的完整版本号, 形如 "0.23.6.354648"; 取不到返回 ""。

    只用于 always_keep_builds 白名单(确认真实的新客户端), 不用来判版本。
    """
    try:
        ua = request.headers.get("User-Agent", "") or ""
    except Exception:
        return ""
    m = re.search(r'/(\d+\.\d+\.\d+\.\d+)', ua)
    return m.group(1) if m else ""


def _event_supported(event, version, cfg=None):
    """该客户端版本认不认识这个 relationship event(不含 always_drop 判定)。"""
    cfg = cfg or _events_config()
    if event in (cfg.get("always_drop_events") or []):
        return False
    need = (cfg.get("event_min_version") or {}).get(event)
    if need is None:
        return True
    if not version:
        # 认不出客户端版本 -> 按最老客户端处理, 只发所有版本都认的事件(绝不 abort)。
        return False
    return tuple(version) >= tuple(need)


def _filter_for_version(defs, version, build=""):
    """剔掉该客户端不认识的 event, 再去掉 pre_reqs 指向被剔节点的节点。

    只做减法: 老客户端收到自己不认识的事件时 RelationshipAbilityDefs 会直接 abort
    (就是 'iap'/SocialIAP 那个闪退)。多留一个它认识的无所谓, 少发一个不认识的才是
    关键, 所以这里宁严勿宽。

    build = UA 里的完整版本号(如 "0.23.6.354648"); 只有当它出现在配置的
    always_keep_builds 白名单里时, 才会对 always_drop_events 里的节点破例放行。
    """
    cfg = _events_config()
    drop = set(cfg.get("always_drop_events") or [])
    keep_builds = cfg.get("always_keep_builds") or {}

    kept = []
    removed = 0
    for d in defs:
        ev = d.get("event")
        if ev in drop:
            forced = bool(build) and build in (keep_builds.get(ev) or [])
            if not forced:
                removed += 1
                continue
        elif not _event_supported(ev, version, cfg):
            removed += 1
            continue
        kept.append(d)

    if not removed:
        return defs

    # 前置闭包: 被删节点的下游也要删, 免得 pre_reqs 指向不存在的 id。
    changed = True
    while changed:
        changed = False
        ids = set()
        for d in kept:
            try:
                ids.add(int(d.get("id")))
            except (TypeError, ValueError):
                pass
        survivors = []
        for d in kept:
            reqs = d.get("pre_reqs") or []
            if not isinstance(reqs, (list, tuple)):
                reqs = [reqs]
            if any(r not in ids for r in reqs if r is not None):
                changed = True
                continue
            survivors.append(d)
        kept = survivors
    return kept


def _filter_for_client_022(defs):
    """[已默认停用] 只做减法: 去掉客户端不认识的节点, 再去掉前置指向已删节点的节点。"""
    kept = []
    for d in defs:
        try:
            if int(d.get("id", -1)) in _CLIENT_UNKNOWN_ABILITY_IDS:
                continue
        except (TypeError, ValueError):
            continue
        kept.append(d)

    changed = True
    while changed:
        changed = False
        ids = set()
        for d in kept:
            try:
                ids.add(int(d.get("id")))
            except (TypeError, ValueError):
                pass
        survivors = []
        for d in kept:
            reqs = d.get("pre_reqs") or []
            if not isinstance(reqs, (list, tuple)):
                reqs = [reqs]
            if any(r not in ids for r in reqs if r is not None):
                changed = True
                continue
            survivors.append(d)
        kept = survivors
    return kept


def _load_relationship_defs():
    """读取 static_responses 里的完整定义(按 mtime 缓存, 存原始表); 失败则返回 None。

    注意: 缓存的是**未过滤**的整表 —— 版本过滤是每个请求按 UA 现算的, 不同版本
    客户端拿到的不一样, 不能缓存成某一次的过滤结果。
    """
    try:
        mtime = os.path.getmtime(_DEF_FILE)
    except OSError:
        return None

    if _cache["defs"] is not None and _cache["mtime"] == mtime:
        return _cache["defs"]

    try:
        with open(_DEF_FILE, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
        defs = payload.get("relationship_defs") if isinstance(payload, dict) else payload
        if not isinstance(defs, list) or not defs:
            logger.warning("relationship_defs 文件里没有节点数据: %s", _DEF_FILE)
            return None
        _cache["mtime"] = mtime
        _cache["defs"] = defs
        logger.info("已从 %s 载入 %d 个好友树节点", _DEF_FILE, len(defs))
        return defs
    except Exception as exc:  # noqa: BLE001 - 兜底, 不能因为这个接口 500
        logger.warning("载入完整好友树节点失败(%s), 退回单节点响应", exc)
        return None


# 0.5.0 以下的客户端比 0.6.5 还老: 它注册的 RelationshipButtonEvent 元类很少,
# 载入 definition 时 GetMetaClassByName(event) 查不到就 abort —— 整 App 闪退。
# 下面两个集合来自二进制 cstring(0.3.15: Sky.bin 索引 24814-24824 一带;
# 0.1.8/0.2.x 更早, 只认 SocialOffer), 只保留确定存在的 event。
_LEGACY_EVENTS_03 = frozenset({
    "SocialOffer", "SocialHandHold", "SocialChat", "SocialFreeChat",
    "SocialKingHug", "SocialTeleportUnlock", "RelationshipGift", "SocialShop",
})
_LEGACY_EVENTS_01 = frozenset({"SocialOffer"})


def _filter_for_legacy(defs, version):
    """0.5.0 以下: 只发该客户端认识的 event, 再做一次前置闭包。

    和 _filter_for_version 的区别是白名单更窄 —— 0.3.15 不认识
    SocialTierUnlock/SocialName/RelationshipBlockPlayer 这些后来才有的元类,
    发过去就会在 RelationshipAbilityDefs.cpp 里 abort。
    """
    ver = tuple((list(version) + [0, 0, 0])[:3]) if version else (0, 0, 0)
    allowed = _LEGACY_EVENTS_03 if ver >= (0, 3, 0) else _LEGACY_EVENTS_01
    kept = [d for d in defs if d.get("event") in allowed]

    changed = True
    while changed:
        changed = False
        ids = set()
        for d in kept:
            try:
                ids.add(int(d.get("id")))
            except (TypeError, ValueError):
                pass
        survivors = []
        for d in kept:
            reqs = d.get("pre_reqs") or []
            if not isinstance(reqs, (list, tuple)):
                reqs = [reqs]
            if any(r not in ids for r in reqs if r is not None):
                changed = True
                continue
            survivors.append(d)
        kept = survivors
    return kept


def get_relationship_abilities():
    try:
        data = request.get_json(force=True, silent=True)

        if not data or 'user' not in data:
            return jsonify({'error': 'Missing required fields'}), 400

        defs = _load_relationship_defs()
        if not defs:
            defs = _FALLBACK_DEFS
        else:
            # 版本过滤(默认开): 只发该客户端认识的事件, 免得老客户端载入时 abort。
            if _event_gate_enabled():
                client_version = _client_version()
                client_build = _client_build()
                before = len(defs)
                if client_version and tuple(client_version) < (0, 5, 0):
                    defs = _filter_for_legacy(defs, client_version)
                else:
                    defs = _filter_for_version(defs, client_version, client_build)
                if not defs:
                    defs = _FALLBACK_DEFS
                # 每次都记一行(带 UA): 老客户端再崩时, 从日志就能确认它到底是谁、
                # 服务端给了它几个节点(含 SocialIAP 的整表已对所有客户端禁用)。
                try:
                    _ua = (request.headers.get("User-Agent", "") or "")[:120]
                except Exception:
                    _ua = ""
                logger.info(
                    "好友树节点: 版本=%s(构建=%s) -> %d/%d (剔掉本版本没有/全局禁用的事件) UA=%s",
                    client_version, client_build or "-", len(defs), before, _ua,
                )
            # 旧的 27 节点过滤(默认关, 仅供对比排查)。
            if _filter_enabled():
                defs = _filter_for_client_022(defs)

        return Response(
            json.dumps({"relationship_defs": defs}, ensure_ascii=False),
            status=200,
            mimetype='application/json',
        )

    except Exception as e:
        logger.exception("get_relationship_abilities 失败")
        return jsonify({'error': str(e)}), 500