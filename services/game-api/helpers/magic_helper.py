# -*- coding: utf-8 -*-
"""
魔法(消耗品) / 增益(buff) 公共逻辑。

游戏里"魔法"在协议上是拿 lootbox 解锁 -> 开箱变成一件 consumable(消耗品), 用的
时候 consume_item1 给玩家挂一个 buff, 到期/手动取消走 buff/remove_buff1。整条链:
    purchase_spirit_shop_item(买 lootbox_xxx)
      -> /account/lootboxes/get + /account/lootboxes/redeem_box(开箱 -> consumable)
      -> /account/consumable/consume_item1(使用 -> buff)
      -> /account/buff/get_buffs1(查) / /account/buff/remove_buff1(移除)

服务端必须给出与官方一致的"定义表"和"字段形状", 否则客户端拿到不认识的 id
(或者干脆是 404 的 HTML) 就会弹"非法请求。请重试。"、甚至直接闪退/掉登录
(2026-09-13 实测: 先祖树买魔法后闪退、锻造弹非法请求, 均与本文件相关)。

定义表直接取抓包库里官方原样的两份(客户端就是靠它把 id 解析成图标/名字):
  static_responses/account__consumable__get_consumable_defs.json  507 条魔法
  static_responses/account__buff__get_buff_defs.json              532 条 buff

实测要点(507/532 条全部符合, 见下): 物品 id = FNV-1a 32bit(物品名字)。所以
"名字 <-> id"可以互相换算, 我们发出去的 id 必须是定义表里真实存在的值。

**本文件同时是 6 个魔法接口的协议层**(2026-09-19 重构): 请求/响应形状、字段单位、
使用语义全部集中在这里的"协议层"一节, account 下的 6 个 handler
(consumable/consume_item1、consumable/get_consumables{,1}、buff/get_buffs{,1}、
buff/remove_buff1、buff/get_buff_defs、purchase_event_buff)只做取参数 + 鉴权 +
调 *_response() 组装。形状的权威来源:
  * 0.20.0 反编译客户端 E:/工具/SO/libBootloader0200.so.c 的各解析器(sub_3FACD8 /
    sub_52372C / sub_3FC974 / sub_524FC4 / sub_3FDB24 ...), 见协议层注释;
  * 官方抓包 static_responses/account__consumable__consume_item1.json;
  * 参考实现 that-sky-server-master.zip(that-sky-server-master/)、十二的光遇服务器_v3.39
    (serverPlayerBuffs/serverPlayerConsumables/accountConsumeItemRequest)。
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone


def _utc_epoch(dt):
    """naive datetime 按 **UTC** 解释成 epoch 秒(0 表示无效)。

    踩过的坑: models 里的 created_at / expires_at 都是 datetime.utcnow() 写进去的
    **naive UTC**(SQLite 里存 "2026-09-13 08:37:18")。直接 dt.timestamp() 会按
    本地时区(东八区)解释, 结果比真实 epoch **少 8 小时** —— 客户端收到的 buff 是
    "已经过期 7.5 小时", 于是背饰放置权(30 分钟)之类的魔法效果完全不生效。
    """
    if dt is None:
        return 0
    try:
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFS_DIR = os.path.join(_ROOT, "resources", "static_responses")

CONSUMABLE_DEFS_FILE = os.path.join(_DEFS_DIR,
                                    "account__consumable__get_consumable_defs.json")
BUFF_DEFS_FILE = os.path.join(_DEFS_DIR, "account__buff__get_buff_defs.json")

# 图标(UI 资源名)门控表: 资源名 -> 第一次出现的客户端版本。
# 由 E:\Sky 下 78 个 APK 的 Data/Resources/{Persistent,Boot,ImageDefs,UIPackedAtlas,
# UIMapAtlas}.lua 里**真实的** resource/resourcedef "Image|ImageRegion|ImageAnim" 名单汇总
# (0.6.5 ~ 0.34.5, 4261 个名字)。见下面的"图标也要对版本"一节。
ICON_MAP_FILE = os.path.join(_ROOT, "config", "icon_version_map.json")

# 下发"完整定义表"的最低客户端版本。
#
# 默认 (0, 26, 0): 也就是**只有 >= 0.26.0 才拿完整表**, 而它们的请求本来就被静态
# 接口库短路(库里就是官方完整定义表), 所以实际上动态路由几乎总是发**子集** ——
# 这是故意的: 老客户端(<0.26.0)解析大响应会闪退(实测 165KB 的兑换树就把老客户端
# 打崩过), 而完整魔法表是 86KB + buff 表 244KB, 风险太大。
# 子集里已经包含"玩家拥有的魔法 + 宝箱可能开出的魔法 + 这些魔法引用的 buff",
# 客户端渲染背包里的魔法完全够用。
# 想给某个版本放开完整表, 把这里改小即可(例如 (0, 22, 0)), 但请先实测该版本。
FULL_DEFS_MIN_VERSION = (0, 26, 0)

# 老客户端 buff 定义子集的**字节预算**(见 defs_subset)。
#
# 实测锚点(都来自同一台设备上的 0.22.0 测试包, server_runtime_8443.log):
#   * 224,799 字节的 buff 表            -> 客户端闪退(16:19:45 之后再无任何请求);
#   * 86,219 字节的完整魔法定义表(507 条) -> 正常解析(同一登录里紧接着还有请求);
#   * 32,879 字节的 buff 子集            -> 正常(16:47 那次登录, 玩家能进游戏)。
# 所以默认把 buff 子集压在 84KB —— **就是同一个客户端已经证明能吃下的体积量级**,
# 而 244KB(全部 532 条)在它上面是必崩的。
#
# 想放开(例如为了测试老客户端到底能吃多大): 改 instance/magic_defs_config.json 里的
# buff_defs_budget, 改完**立即生效**(按 mtime 热读, 不用重启也不用热重载)。
# 值 0 / 负数 = 不限制(下发全部 489 条, ~224KB), 同时会自动开启 gzip(见下)。
BUFF_DEFS_SUBSET_BUDGET = 86016

# 超过这个字节数就 gzip 压缩下发(仅在客户端 Accept-Encoding 里有 gzip 时)。
# **默认 0 = 不压缩**。依据(2026-09-13 查实, monitor_exports/2026-07-04_21-24-15.json
# 是官方服务器的抓包): 官方对同一个客户端(0.33.2)就是这么发的 ——
#   /account/buff/get_buff_defs            Content-Length=244289  Content-Encoding=None
#   /account/consumable/get_consumable_defs Content-Length=86084  Content-Encoding=None
# 也就是**明文大响应**。我们曾经把 buff 表 gzip 成 ~17KB 下发(客户端确实声明了
# Accept-Encoding: gzip), 但它显然没有解压这份表 -> 手里还是旧定义表 ->
# 服饰类魔法(靠 buff 定义里的 effect/params 换装)全部"点了没反应", 其余只有旧的能用。
# 所以这里与官方逐字节对齐: 默认不压缩。
# 若以后确实要压(例如某客户端限制网络字节数): 把 instance/magic_defs_config.json 的
# gzip_over_bytes 改成 86016 之类即可, 改完立即生效(mtime 热读, 不用重启)。
GZIP_OVER_BYTES = 0

# 发给客户端的"魔法背包"件数上限(见 consumables_inventory 的实测说明)。
# 0 = 不限制。这里只是**配置文件缺失时的兜底值**; 实际值取 instance/magic_defs_config.json
# 的 magic_list_max(热读)。UGC(留言/纸船/留影)11 件不受这个上限影响, 永远下发。
MAGIC_LIST_MAX_DEFAULT = 160

# 社交三件: 玩家一直在测的留影/纸船/空间魔法, 必须排在最前面发。
SOCIAL_MAGIC_NAMES = ("memory_candle", "message_boat", "sharedspace_candle")

# ---- UGC 这一族(留言纸船 / 留影蜡烛 / 空间蜡烛)----------------------------------
#
# 客户端"留言/留影"面板列出的就是这一族(官方定义表里 subcategory == "UGC", 共 11 件:
# memory_candle, message_boat/crane/gondola/lantern/lotus/rose/sunflower/vgflower,
# personality_message_boat, sharedspace_candle)。
#
# 2026-09-13 定位"创建任何一个留言/纸船/留影都闪退"的**直接证据**(只读查库 +
# server_runtime_8443.log, 同一个账号同一台设备):
#   * 21:54:49 用 outfit_horn_flowercrown(非 UGC) -> 客户端照常继续发请求;
#   * 21:55:08 用 message_lantern(UGC 纸船, 库 quantity=0) -> 客户端**立刻没了**
#     (consume_item1 之后再无任何请求 = 闪退);
#   * 22:38:35 用 message_boat(UGC 纸船, 库 quantity=0) -> 同样立刻消失(1 秒内);
#   * 19:18 用 memory_candle 创建留影**成功**(recording create -> consume_item1 ->
#     social-feed create -> get_user_feeds 全通) —— 而它库 quantity=9999, 一直在背包里。
# 差别就是"这件 UGC 魔法在 get_consumables1 里有没有条目":
#   * message_boat / message_lantern 库 quantity=0 -> 老实现直接过滤掉 -> 客户端拿到
#     consume 成功但**被消耗的那件不在 get_consumables 里** -> 留言/纸船流程取不到
#     自己的条目 -> 闪退(feed create 都没发出去);
#   * message_lotus / gondola / crane / rose 库 quantity=99, 但被 magic_list_max=160
#     件数上限裁掉了(实测下发背包里 0 件 message_*) -> 一样是"查不到这件";
#   * message_sunflower / message_vgflower / personality_message_boat 连**定义**都没下发。
# 所以 UGC 这一族必须满足三条: (1) 定义必发; (2) 背包必发且数量 > 0(库里被用光也补);
# (3) 排最前, 不被件数上限裁掉。只改**下发**, 不写数据库。
UGC_SUBCATEGORY = "UGC"
# 但 subcategory 是**私服 599 条表才有的可选字段**: 官方(0.22.0 抓包)那份 507 条表
# 每条只有 9 个字段(buff_id/cooldown/event/icon/id/is_toy/name/param/type), **没有**
# subcategory/category/order_in_subcategory。把定义表回退成官方那份之后, 只认
# subcategory 就会一件 UGC 都识别不出来 -> message_boat / message_lantern 又掉出背包
# = 又变回"创建留言闪退"。所以 UGC 的判据以**官方表也有**的字段为准:
#   * event(客户端编译期字符串表里就有这四个):
#       SocialMessageBoats(纸船/留言) / SocialMessageStones(留言石)
#       RecordingCandleEvent(留影蜡烛) / SharedSpaceCandleEvent(空间蜡烛)
#   * name: message_* / personality_message_* / memory_candle / sharedspace_candle
# 三者任中其一即算 UGC(subcategory 命中只是私服表的加分项)。
UGC_EVENTS = (
    "SocialMessageBoats",
    "SocialMessageStones",
    "RecordingCandleEvent",
    "SharedSpaceCandleEvent",
)
UGC_NAME_EXACT = ("memory_candle", "sharedspace_candle")
UGC_NAME_PREFIXES = ("message_", "personality_message_")
# UGC 魔法下发时的最小数量(库里 quantity=0 的按这个发, 与库里已存在的 99 一致)。
UGC_MIN_QUANTITY = 99


def is_ugc_def(c):
    """这一条魔法定义是不是 UGC 一族(留言/纸船/留影/空间)。官方表/私服表都能认出。"""
    if not isinstance(c, dict):
        return False
    if str(c.get("subcategory") or "") == UGC_SUBCATEGORY:
        return True
    if str(c.get("event") or "") in UGC_EVENTS:
        return True
    nm = str(c.get("name") or "")
    return nm in UGC_NAME_EXACT or nm.startswith(UGC_NAME_PREFIXES)

# 运行时可调参数文件(见 magic_defs_config())。
_DEFS_CONFIG_FILE = os.path.join(_ROOT, "data", "instance", "magic_defs_config.json")
_DEFS_CONFIG_CACHE = {}
_DEFS_CONFIG_DEFAULT = {
    "_说明": ("魔法定义/背包下发的可调参数。改完立即生效, 不用重启也不用热重载。"
              "buff_defs_budget=0 表示不限制(下发完整 532 条 buff 定义); "
              "gzip_over_bytes=0 表示不压缩(客户端不解压 gzip, 压缩了魔法会失效); "
              "magic_list_max=发给客户端的背包件数上限(0=不限制), 太大客户端会闪退。"),
    "buff_defs_budget": BUFF_DEFS_SUBSET_BUDGET,
    "gzip_over_bytes": GZIP_OVER_BYTES,
    "magic_list_max": MAGIC_LIST_MAX_DEFAULT,
    # version_gate: 1/缺省 = 按客户端 UA 版本过滤"该版本没有的魔法"(见 MAGIC_EVENT_MIN_VERSION);
    #               0 = 关闭(旧行为, 低版本收到不认识的魔法事件类会闪退)。
    # event_min_versions: 覆盖/扩充门控表, 例如 {"ShoutBuffEvent": "0.11.0"} 或 {"xxx": null} 删除。
    "version_gate": 1,
    "event_min_versions": {},
    # icon_gate: 1/缺省 = 按 config/icon_version_map.json 挡"这个版本的资源表里没有的图标"
    #            (0.12.1 查看 UiOutfitPropDOL23JellyFireworks 会踩 Hud.cpp (460) 断言);
    #            0 = 关闭。
    "icon_gate": 1,
    # effect_min_versions: 覆盖 buff effect 门控表(只对 <0.17.0 的客户端生效, 见
    # MAGIC_EFFECT_MIN_VERSION), 例如 {"placeable": "0.19.5"} 或 {"dye": null} 删除。
    "effect_min_versions": {},
    # ugc_force_send: 0/缺省 = 只下发账号库中真实拥有的魔法(默认, 各账号互不影响);
    # 1 = 旧行为, 把全部 UGC(留言/纸船/留影/空间)无条件补发给每一个账号
    # (会造成"只给一个账号发的魔法, 所有账号含新号都有")。
    "ugc_force_send": 0,
}

# 官方抓包样本: lootbox_common_* 开出的魔法 === energy (701255829 = fnv1a("energy"))。
# 见 static_responses/account__lootboxes__redeem_box.json 与
# account__consumable__consume_item1.json(buff_id 1750685908 也正是 energy 的 buff)。
_BOX_COMMON_CONSUMABLE_ID = 701255829

# 宝箱开箱的"内容物池"。官方那张"哪个宝箱开出哪件魔法"的表我们拿不到(抓包里只有
# 一次 lootbox_common -> energy 的样本), 所以按稀有度分两个池, 池内**确定性**挑一个
# (同一个宝箱永远开出同一件魔法, 不会每次变)。
#   - 池里的名字都是 static_responses 定义表里真实存在的(下面 _pool_defs 会过滤);
#   - 老客户端(<0.26.0)的定义子集 defs_subset() 正好覆盖这两个池, 所以无论开出
#     池里哪一件, 客户端都能在定义表里查到它, 不会出现"拿到了不认识的魔法"。
BOX_COMMON_POOL = (
    "energy", "glow", "glow_rainbow", "grow", "shrink", "huge", "tiny", "gravity",
    "fireworks", "dnd", "bonfire", "beachball", "rainbow", "pinwheel", "table",
    "seesaw", "swing", "candle_trick", "crab_rock_trick", "currency_trick",
    "ground_trail_rainbow", "butterfly_pastel", "bloom_sakura", "resize_potion",
)
BOX_RARE_POOL = (
    "glow_tgc", "hairtint_rainbow", "trail_rainbow", "message_boat", "message_crane",
    "message_gondola", "message_lantern", "message_lotus", "message_rose",
    "dark_dragon_firework", "firework_crab", "firework_jelly", "firework_manta",
    "sharedspace_candle", "outfit_prop_twinribbonposter", "height_small_3x_handhold",
    "potion_growth_huge", "potion_growth_tiny", "potion_growth_keychain",
    "teleport_forest_elder", "teleport_prairie_elder", "teleport_sanctuary_island",
)

_VER_RE = re.compile(r"Sky-[^/\s]+/(\d+(?:\.\d+)*)")
_CACHE = {}


# ============ 基础: 定义表 ============

def fnv1a(text):
    """FNV-1a 32bit。官方就是用它把物品名字哈希成 id(实测 507 条魔法 / 532 条 buff 全部符合)。"""
    h = 2166136261
    for b in str(text).encode("utf-8"):
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _load_list(path, key):
    """按 mtime 缓存读取 {"<key>": [...]} 形式的定义表。失败返回 []。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    hit = _CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    items = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get(key), list):
            items = data[key]
    except Exception:
        items = []
    _CACHE[path] = (mtime, items)
    return items


def consumable_defs():
    """全部魔法定义(507 条)。"""
    return _load_list(CONSUMABLE_DEFS_FILE, "get_consumable_defs")


def buff_defs():
    """全部 buff 定义(532 条)。"""
    return _load_list(BUFF_DEFS_FILE, "get_buff_defs")


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# 公开别名: 接口 handler 里也要用(名字带下划线的不该被跨模块 import)。
to_int = _to_int


def consumable_def(consumable_id):
    """按 id 取魔法定义, 没有返回 None。"""
    cid = _to_int(consumable_id)
    if not cid:
        return None
    for c in consumable_defs():
        if _to_int(c.get("id")) == cid:
            return c
    return None


def consumable_def_by_name(name):
    """按名字取魔法定义, 没有返回 None。"""
    if not name:
        return None
    for c in consumable_defs():
        if c.get("name") == name:
            return c
    return None


def buff_def(buff_id):
    """按 id 取 buff 定义, 没有返回 None。"""
    bid = _to_int(buff_id)
    if not bid:
        return None
    for b in buff_defs():
        if _to_int(b.get("id")) == bid:
            return b
    return None


def buff_def_by_name(name):
    """按名字取 buff 定义, 没有返回 None。"""
    if not name:
        return None
    for b in buff_defs():
        if b.get("name") == name:
            return b
    return None


# ============ 版本分流 ============

def request_payload():
    """容错地取当前请求的 JSON body(解析不了就返回 {})。"""
    try:
        from flask import request
        data = request.get_json(force=True, silent=True)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def client_version():
    """从 User-Agent 解析客户端版本 (major, minor, patch); 失败返回 None。

    统一走 client_version 纠偏(见 config/client_version_overrides.json):
    有的魔改老客户端谎报版本(实测 0.13.4 报 0.23.6.354648); 不纠偏就会给它
    发该版本没有的魔法, 点开/兑换时闪退(用户点名反馈)。
    """
    try:
        from helpers.client_version import client_version_from_request
        return client_version_from_request()
    except Exception:
        pass
    try:
        from flask import request
        ua = request.headers.get("User-Agent", "") if request else ""
    except Exception:
        ua = ""
    m = _VER_RE.search(ua or "")
    if not m:
        return None
    nums = []
    for p in m.group(1).split(".")[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def full_defs_allowed():
    """是否给当前客户端下发完整定义表(见 FULL_DEFS_MIN_VERSION)。"""
    v = client_version()
    if v is None:
        return False
    return v >= FULL_DEFS_MIN_VERSION


# ============ 版本门控: 不把"这个版本没有的魔法"发下去 ============
#
# 2026-10-06 用户实测反馈: "0.12.1 部分获得的魔法是那个版本没有的 会导致崩溃, 低版本都要
# 注意不发没有的魔法"。
#
# 判据(与 account/get_relationship_abilities 同一套方法): D:\工具\SO 里全部
# libBootloader*.so 反编译 .c 逐个扫**魔法事件类的名字串**。客户端把 def 里的
# event 当**类名**去查表, 查不到就断言崩溃 —— 0.12.2/0.12.3/0.13.4 里的断言串就是这件事:
#       No event named %s
#       Event named %s is not a BuffEvent                (buff 的 event)
#       Event named %s is not a UseConsumableRpcEvent    (consumable 的 event)
#       Event named %s is not a UseConsumableRpc         (0.10.1~0.22.0)
#       Event named %s is not a ConsumableEvent          (0.6.5 的叫法)
# 所以 "def 里的 event 在这个版本的二进制里不存在" == 发下去必崩。实测串出现版本:
#       libBootloader065=0.6.5  0101=0.10.1  0110=0.11.0  0122=0.12.2  0123=0.12.3
#       0134=0.13.4  0195=0.19.5  0200=0.22.0  0337=0.33.7
# 下表的数字就是"第一次出现"的那个版本(=-= 最小支持版本)。0.6.5/0.10.1/0.11.0/0.12.x/
# 0.13.4 这几个版本的魔法代码全在 bootloader 里(证据完整: 缺 = 该版本没有); 0.17 之后
# 魔法代码搬出了 bootloader(缺失不再可靠), 所以那之后的只按"首次出现"取下限 ——
# **宁可少发, 不可发错**(少发只是少一件魔法, 发错是直接闪退)。
#
# 实测锚点(0.12.1 账号被"全解锁"后收到的正是这些):
#       memory_candle(RecordingCandleEvent, 0.19.5+)
#       sharedspace_candle(SharedSpaceCandleEvent, 0.19.5+)
#       message_rose(SocialMessageStones, 0.33.7+)
#       drop_wing(DropWingConsumableEvent, 0.19.5+) / figurineskykid_normaledition(0.19.5+)
#   外加 6 条 buff(birthdayhat_krill / outfit_prop_artbook{,2} / timeline_*) —— 都是
#   0.12.1 的类名表里没有的。
MAGIC_EVENT_MIN_VERSION = {
    # 0.6.5 就有(见下 UGC/社交说明): EnergyConsumableEvent / ResizeConsumableEvent /
    # SocialFireworks 不列入 = 永远放行。
    "ShoutBuffEvent": (0, 11, 0),
    "SocialMessageBoats": (0, 11, 0),
    "TeleportBuffEvent": (0, 11, 0),
    "BirthdayHatBuffEvent": (0, 19, 5),
    "DropWingConsumableEvent": (0, 19, 5),
    "RecordingCandleEvent": (0, 19, 5),
    "SharedSpaceCandleEvent": (0, 19, 5),
    "SkyFigurineConsumableEvent": (0, 19, 5),
    "TimelineBuffEvent": (0, 19, 5),
    "ArtBookBuffEvent": (0, 33, 7),
    "SocialMessageStones": (0, 33, 7),
}

_GATE_CACHE = {}


# effect 门控(只对 **<0.17.0** 的客户端生效, 见 effect_gate_applies)。
#
# 为什么要门控 effect: buff 定义里的 effect 是客户端的"效果实现类名"。老客户端拿到不认识的
# effect 不会断言(实测: 0.12.2/0.13.4 的字符串表里没有 "not a BuffEffect" 之类断言, 只有
# "Failed playing sound effect"), 但它会去建那件魔法对应的视觉/动作 —— 本项目的既定原则是
# "低版本不发它没有的魔法"(用户 2026-10-06), 所以这里按同一套 SO 扫描结果把只在新版里
# 出现过的 effect 一并挡掉(挡掉 = 少发 = 安全方向)。
#
# 数字同样是"libBootloader*.so.c 里第一次出现该 effect 串"的版本。**只在客户端 <0.17.0 时
# 使用这张表**: 0.17 起魔法代码搬出了 bootloader, "没扫到"不再等于"没有"(对 0.17+ 客户端
# 拿它当判据会误伤正在用的魔法), 而对 <0.17.0 的客户端, 上表的 0.6.5~0.13.4 这几个 dump
# 证据是完整的, 所以判定可靠 —— 这也正是用户反馈出问题的那个版本区间(0.12.1/0.7.1)。
MAGIC_EFFECT_MIN_VERSION = {
    "height_mult": (0, 10, 1),
    "outfit_body": (0, 11, 0),
    "outfit_hair": (0, 11, 0),
    "outfit_mask": (0, 11, 0),
    "outfit_neck": (0, 11, 0),
    "outfit_prop": (0, 11, 0),
    "outfit_wing": (0, 11, 0),
    "relationship_allow": (0, 11, 0),
    "outfit_horn": (0, 12, 2),
    "relationship_flair": (0, 12, 3),
    "trail_rainbow": (0, 13, 4),
    "candle_trick": (0, 19, 5),
    "cat_curse": (0, 19, 5),
    "crab_rock_trick": (0, 19, 5),
    "placeable": (0, 19, 5),
    "repellant_krill": (0, 19, 5),
    "dnd": (0, 22, 0),
    "birthdayhat": (0, 33, 7),
    "crab_curse": (0, 33, 7),
    "currency_trick": (0, 33, 7),
    "dragon_dance": (0, 33, 7),
    "dye": (0, 33, 7),
    "emitter_follow": (0, 33, 7),
    "glow_rainbow": (0, 33, 7),
    "glow_tgc": (0, 33, 7),
    "ground_trail_rainbow": (0, 33, 7),
    "hairtint": (0, 33, 7),
    "hairtint_rainbow": (0, 33, 7),
    "mote_color": (0, 33, 7),
    "outfit_face": (0, 33, 7),
    "outfit_feet": (0, 33, 7),
    "outfit_hat": (0, 33, 7),
    "rabbit_form": (0, 33, 7),
    "trail_bicolor": (0, 33, 7),
    "tv_angry": (0, 33, 7),
}

# effect 门控生效的最高版本(不含): 只给 <0.17.0 的客户端用(证据完整的那一段)。
EFFECT_GATE_BELOW = (0, 17, 0)


def event_min_versions():
    """门控表(默认 MAGIC_EVENT_MIN_VERSION, 可被 instance/magic_defs_config.json 的
    event_min_versions 增删改, 热读)。值是 "a.b.c" 或 [a,b,c]。"""
    table = dict(MAGIC_EVENT_MIN_VERSION)
    try:
        extra = defs_config().get("event_min_versions") or {}
        if isinstance(extra, dict):
            for k, v in extra.items():
                if v is None:
                    table.pop(str(k), None)
                    continue
                if isinstance(v, (list, tuple)):
                    nums = [int(x) for x in v][:3]
                else:
                    nums = [int(x) for x in str(v).split(".")[:3]]
                while len(nums) < 3:
                    nums.append(0)
                table[str(k)] = tuple(nums)
    except Exception:
        pass
    return table


def version_gate_enabled():
    """门控总开关(instance/magic_defs_config.json 的 version_gate, 默认开)。
    关掉 = 回到旧行为(把定义表里全部魔法都发下去, 低版本会闪退)。"""
    return bool(_to_int(defs_config().get("version_gate", 1)))


def blocked_events(version=None):
    """该客户端版本**不认识**的 event 类名集合(用 UA 版本, 缓存)。"""
    if version is None:
        version = client_version()
    if version is None or not version_gate_enabled():
        return frozenset()
    try:
        key = ("blocked", version, os.path.getmtime(CONSUMABLE_DEFS_FILE),
               os.path.getmtime(BUFF_DEFS_FILE),
               json.dumps(event_min_versions(), sort_keys=True))
    except OSError:
        key = ("blocked", version, json.dumps(event_min_versions(), sort_keys=True))
    hit = _GATE_CACHE.get(key)
    if hit is not None:
        return hit
    blocked = frozenset(ev for ev, mn in event_min_versions().items() if version < mn)
    _GATE_CACHE.clear()          # 单一条目就够, 别让它长胖(见"运行内存")
    _GATE_CACHE[key] = blocked
    return blocked


def event_min_versions_effect():
    """effect 门控表(默认 MAGIC_EFFECT_MIN_VERSION, 可被 instance/magic_defs_config.json
    的 effect_min_versions 增删改, 热读)。"""
    table = dict(MAGIC_EFFECT_MIN_VERSION)
    try:
        extra = defs_config().get("effect_min_versions") or {}
        if isinstance(extra, dict):
            for k, v in extra.items():
                if v is None:
                    table.pop(str(k), None)
                    continue
                if isinstance(v, (list, tuple)):
                    nums = [int(x) for x in v][:3]
                else:
                    nums = [int(x) for x in str(v).split(".")[:3]]
                while len(nums) < 3:
                    nums.append(0)
                table[str(k)] = tuple(nums)
    except Exception:
        pass
    return table


def blocked_effects(version=None):
    """该版本(仅 <0.17.0)不认识的 buff effect 名集合(缓存)。"""
    if version is None:
        version = client_version()
    if version is None or version >= EFFECT_GATE_BELOW or not version_gate_enabled():
        return frozenset()
    key = ("blocked_fx", version, json.dumps(event_min_versions_effect(), sort_keys=True))
    hit = _GATE_CACHE.get(key)
    if hit is not None:
        return hit
    blocked = frozenset(fx for fx, mn in event_min_versions_effect().items() if version < mn)
    _GATE_CACHE[key] = blocked
    return blocked


def effect_blocked(defn, blocked=None):
    """这条 buff 定义的 effect 是否该版本(仅 <0.17.0)不认识。"""
    if not isinstance(defn, dict):
        return False
    fx = str(defn.get("effect") or "")
    if not fx or fx == "none":
        return False
    if blocked is None:
        blocked = blocked_effects()
    return fx in blocked


def event_blocked(defn, blocked=None):
    """这条 def(魔法或 buff)的 event 是否该版本不认识。"""
    if not isinstance(defn, dict):
        return False
    ev = str(defn.get("event") or "")
    if not ev or ev == "none":
        return False
    if blocked is None:
        blocked = blocked_events()
    return ev in blocked


def buff_def_blocked(defn, blocked_ev=None, blocked_fx=None):
    """buff 定义是否该版本不认识(event 类名 或 effect)。"""
    if blocked_ev is None:
        blocked_ev = blocked_events()
    if blocked_fx is None:
        blocked_fx = blocked_effects()
    return event_blocked(defn, blocked_ev) or effect_blocked(defn, blocked_fx)


# ============ 版本门控: 图标(UI 资源名)也要对版本 ============
#
# 2026-10-07 用户实测: 0.12.1 兑换到"这个版本没有的魔法"后在背包里**查看**它 -> 闪退:
#       ASSERT! Hud.cpp (460): isImage || isRegion || isAnim,
#       "UiOutfitPropDOL23JellyFireworks" not a valid Image or ImageRegion or ImageAnim
# 这和上面的 event/effect 门控是**两回事**: 一件魔法的 event 类名叫 SocialFireworks
# (0.6.5 就有, 所以旧表里"永远放行"), 但它的 **icon** 名 0.20.1 才进客户端的资源表。
# 客户端画图标时拿名字去查 Image/ImageRegion/ImageAnim 表, 查不到就地断言 -> 闪退。
#
# 判据: config/icon_version_map.json = 每个版本 APK 的 Data/Resources/*.lua 里**真实的**
# resource/resourcedef "Image|ImageRegion|ImageAnim" 名单(0.6.5 ~ 0.34.5, 78 个版本)。
#   * 该版本资源表里**没有**这个 icon -> 定义 / 背包条目 / 回显 / 开箱 全部挡掉;
#   * 表里查不到的名字(比 0.34.5 更新的名字) -> **放行**(宁可少挡, 不可误挡)。
# 实测例: UiOutfitPropDOL23{Jelly,Crab,Heart,Manta}Fireworks / DOL23FlowerArch = 0.20.1 起,
#        所以 0.12.1 / 0.13.4 / 0.18.6 收到它们必崩; 而 UiOutfitPropFireworks 0.6.5 就有, 照发。
# 开关: instance/magic_defs_config.json 的 icon_gate(默认 1, 热读)。
_ICON_MAP_CACHE = {}


def _ver_nums(value):
    """把 "0.20.1" / [0,20,1] 这类值统一成 3 元组; 认不出返回 None。"""
    try:
        if isinstance(value, (list, tuple)):
            nums = [int(x) for x in value][:3]
        else:
            nums = [int(x) for x in str(value).split(".")[:3]]
    except (TypeError, ValueError):
        return None
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _icon_map_load():
    """读 config/icon_version_map.json -> (min_version 表, gap 表)。按 mtime 热读。"""
    try:
        mtime = os.path.getmtime(ICON_MAP_FILE)
    except OSError:
        return {}, {}
    if _ICON_MAP_CACHE.get("mtime") == mtime:
        return (_ICON_MAP_CACHE.get("table") or {},
                _ICON_MAP_CACHE.get("gap") or {})
    table, gaps = {}, {}
    try:
        with open(ICON_MAP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        for name, v in (data.get("min_version") or {}).items():
            nums = _ver_nums(v)
            if nums is not None:
                table[str(name)] = nums
        for name, vs in (data.get("gap") or {}).items():
            nums = set()
            for v in (vs or []):
                got = _ver_nums(v)
                if got is not None:
                    nums.add(got)
            if nums:
                gaps[str(name)] = frozenset(nums)
    except Exception:
        table, gaps = {}, {}
    _ICON_MAP_CACHE["mtime"] = mtime
    _ICON_MAP_CACHE["table"] = table
    _ICON_MAP_CACHE["gap"] = gaps
    return table, gaps


def icon_min_versions():
    """{图标资源名: 首次出现的版本} —— 按 mtime 热读 config/icon_version_map.json。"""
    return _icon_map_load()[0]


def icon_gap_versions():
    """{图标资源名: 出现过又在这些版本里缺失(改名/删图)} —— 这些版本上按"没有"算。"""
    return _icon_map_load()[1]


def icon_gate_enabled():
    """图标门控开关(instance/magic_defs_config.json 的 icon_gate, 默认开)。"""
    return bool(_to_int(defs_config().get("icon_gate", 1)))


def icon_available(name, version=None):
    """这个版本的客户端有没有这个图标(资源名)。表里查不到 = 放行。"""
    if not name:
        return True
    if version is None:
        version = client_version()
    if version is None or not icon_gate_enabled():
        return True
    key = str(name)
    table, gaps = _icon_map_load()
    first = table.get(key)
    if first is None:
        return True
    if version < first:
        return False
    miss = gaps.get(key)
    if miss and version in miss:
        return False
    return True


def icon_blocked(defn, version=None):
    """这条 def(consumable/buff)的 icon 在该版本画不出来(= 画了就断言闪退)。"""
    if not isinstance(defn, dict):
        return False
    return not icon_available(defn.get("icon"), version)


def icon_blocker(version=None):
    """给批处理用: 返回判定函数 (defn)->bool; 门控关掉/版本未知时返回 None。

    单独拿它是因为 icon_blocked() 每次都会去 stat 配置表(mtime 热读),
    507 条定义逐条调就是 507 次 stat —— 这里只查一次表, 后面全是内存比较。
    """
    if version is None:
        version = client_version()
    if version is None or not icon_gate_enabled():
        return None
    table, gaps = _icon_map_load()
    if not table:
        return None

    def check(defn):
        if not isinstance(defn, dict):
            return False
        name = defn.get("icon")
        if not name:
            return False
        name = str(name)
        first = table.get(name)
        if first is None:
            return False
        if version < first:
            return True
        miss = gaps.get(name)
        return bool(miss and version in miss)

    return check


def _icon_stamp():
    """图标表 mtime, 只用来给下面几个缓存键加'热读'维度。"""
    try:
        return os.path.getmtime(ICON_MAP_FILE)
    except OSError:
        return 0.0


def filter_defs_for_version(defs, blocked=None):
    """把该版本不认识的 **buff** def 丢掉(event 类名 / effect / icon 任一命中)。"""
    if blocked is None:
        blocked_ev = blocked_events()
        blocked_fx = blocked_effects()
    else:
        blocked_ev, blocked_fx = blocked, blocked_effects()
    icon_on = icon_blocker()
    if not blocked_ev and not blocked_fx and icon_on is None:
        return list(defs or [])
    return [d for d in (defs or [])
            if not buff_def_blocked(d, blocked_ev, blocked_fx)
            and not (icon_on and icon_on(d))]


def filter_magic_defs(defs, kind="consumable"):
    """按当前请求的 UA 版本剔除"该版本没有的魔法/buff 定义"。

    kind="consumable": 判 event 类名, **外加** 它引用的 buff 被挡(老客户端连那个效果都没有,
        留着它 = 玩家能用一件没效果的魔法, 而且用的时候客户端拿不到 buff 定义),
        **外加 icon 该版本资源表里没有**(见上面"图标也要对版本", 0.12.1 查看
        UiOutfitPropDOL23JellyFireworks 必崩 Hud.cpp (460));
        —— 注意 UGC(留言/纸船/留影)没有 buff_id, 不受第二条影响。
    kind="buff": 判 event 类名 + effect + icon。
    通用开关见 defs_config().version_gate / defs_config().icon_gate。
    """
    defs = list(defs or [])
    if not defs or client_version() is None:
        return defs
    icon_on = icon_blocker()
    blocked_ev = blocked_events()
    blocked_fx = blocked_effects()
    if kind == "buff":
        return filter_defs_for_version(defs, blocked_ev)
    if not blocked_ev and icon_on is None:
        return defs
    blocked_bids = blocked_buff_ids()
    out = []
    for d in defs:
        # icon 该版本资源表里没有 -> 背包里一画就断言闪退(见上面"图标也要对版本")。
        if icon_on and icon_on(d):
            continue
        if event_blocked(d, blocked_ev):
            continue
        bid = _to_int((d or {}).get("buff_id"))
        if bid and bid in blocked_bids:
            continue
        out.append(d)
    return out


def version_filter_consumable_defs(defs):
    """给 handler 用: 按当前请求的 UA 版本过滤魔法定义表(全量表那条路径)。"""
    return filter_magic_defs(defs, "consumable")


def version_filter_buff_defs(defs):
    """给 handler 用: 按当前请求的 UA 版本过滤 buff 定义表(全量表那条路径)。"""
    return filter_magic_defs(defs, "buff")


def blocked_consumable_ids(version=None):
    """该版本不认识的魔法 id 集合(背包/定义/使用 全部按它挡)。mtime+版本缓存。

    三条判据(任中其一):
      * 魔法自己的 event 类名该版本没有(如 0.12.1 没有 RecordingCandleEvent);
      * 它引用的 buff 被上面挡掉(事件类名或 effect 该版本没有, 如 0.7.1 的 outfit_prop
        系列) —— 留着它玩家能拿到"点了没效果的魔法", 而且用的时候客户端拿不到 buff 定义;
      * 它的 icon 在该版本的资源表里不存在 —— 背包里一画就断言闪退(Hud.cpp (460),
        实测 UiOutfitPropDOL23JellyFireworks 在 0.12.1 上崩)。
    UGC(留言/纸船/留影)没有 buff_id, 不受第二条影响。
    """
    if version is None:
        version = client_version()
    blocked = blocked_events(version)
    blocked_bids = blocked_buff_ids(version)
    icon_on = icon_blocker(version)
    if not blocked and not blocked_bids and icon_on is None:
        return frozenset()
    try:
        key = ("blocked_c", version, os.path.getmtime(CONSUMABLE_DEFS_FILE),
               json.dumps(event_min_versions(), sort_keys=True),
               json.dumps(event_min_versions_effect(), sort_keys=True),
               _icon_stamp())
    except OSError:
        key = ("blocked_c", version)
    hit = _GATE_CACHE.get(key)
    if hit is not None:
        return hit
    ids = set()
    for c in consumable_defs():
        cid = _to_int(c.get("id"))
        if not cid:
            continue
        bid = _to_int(c.get("buff_id"))
        if (event_blocked(c, blocked) or (bid and bid in blocked_bids)
                or (icon_on and icon_on(c))):
            ids.add(cid)
    ids = frozenset(ids)
    _GATE_CACHE[key] = ids
    return ids


def blocked_buff_ids(version=None):
    """该版本不识别的 buff id 集合(buff 列表下发 / buff 定义 / 魔法引用 都要用它)。"""
    if version is None:
        version = client_version()
    blocked_ev = blocked_events(version)
    blocked_fx = blocked_effects(version)
    icon_on = icon_blocker(version)
    if not blocked_ev and not blocked_fx and icon_on is None:
        return frozenset()
    try:
        key = ("blocked_b", version, os.path.getmtime(BUFF_DEFS_FILE),
               json.dumps(event_min_versions(), sort_keys=True),
               json.dumps(event_min_versions_effect(), sort_keys=True),
               _icon_stamp())
    except OSError:
        key = ("blocked_b", version)
    hit = _GATE_CACHE.get(key)
    if hit is not None:
        return hit
    ids = frozenset(_to_int(b.get("id")) for b in buff_defs()
                    if _to_int(b.get("id")) and (
                        buff_def_blocked(b, blocked_ev, blocked_fx)
                        or (icon_on and icon_on(b))))
    _GATE_CACHE[key] = ids
    return ids


def consume_status_invalid_def():
    """用不了/不认识的魔法回给客户端的 consume_item 状态串。

    "invalid_consumable_def" 这个串是 0.11.0 起客户端才认识的(0.6.5/0.10.1 的表里没有,
    它们只有 "consume_item failed"), 所以按版本分流 —— 回一个老客户端不认识的串, 它
    只当"没成功"(客户端解析器只在 success 时往下解析), 不会崩, 但日志会难看。
    """
    v = client_version()
    if v is not None and v >= (0, 11, 0):
        return "invalid_consumable_def"
    return CONSUME_FAILED


# ============ 运行时可调参数 / 响应压缩 ============

def defs_config():
    """老客户端定义下发的可调参数(instance/magic_defs_config.json, 按 mtime 热读)。

    第一次读时文件不存在就用默认值**自动写出来**, 之后管理员改文件立即生效 ——
    调体积上限不需要重启服务器, 也不需要热重载(现场排障用)。
    """
    path = _DEFS_CONFIG_FILE
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        cfg = dict(_DEFS_CONFIG_DEFAULT)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            _DEFS_CONFIG_CACHE["data"] = (os.path.getmtime(path), cfg)
        except Exception:
            pass
        return cfg
    hit = _DEFS_CONFIG_CACHE.get("data")
    if hit and hit[0] == mtime:
        return hit[1]
    cfg = dict(_DEFS_CONFIG_DEFAULT)
    try:
        with open(path, "r", encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            for k, v in got.items():
                cfg[k] = v
    except Exception:
        pass
    _DEFS_CONFIG_CACHE["data"] = (mtime, cfg)
    return cfg


def buff_defs_budget():
    """本次下发 buff 定义子集的字节预算(<=0 或不合法则用默认值)。"""
    try:
        n = int(defs_config().get("buff_defs_budget", BUFF_DEFS_SUBSET_BUDGET))
    except (TypeError, ValueError):
        n = BUFF_DEFS_SUBSET_BUDGET
    return n if n > 0 else 0            # 0 = 不限制


def _client_accepts_gzip():
    try:
        from flask import request
        return "gzip" in (request.headers.get("Accept-Encoding", "") or "").lower()
    except Exception:
        return False


def json_resp(payload, status=200, gzip_over=None):
    """JSON 响应; 体积超过 gzip_over(默认取配置) 且客户端支持 gzip 时压缩下发。

    只在**大响应**上压缩: 小响应照旧明文(保持与官方一致的抓包形态), 大响应靠
    gzip 把网络字节数压到很小, 绕开"老客户端收不下大响应"的限制(如果那是字节数
    限制的话)。客户端不声明 gzip 就绝不压缩(声明了却不支持属于客户端 bug)。
    """
    import gzip as _gzip
    from flask import Response
    data = json.dumps(payload, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    headers = {}
    try:
        limit = int(gzip_over if gzip_over is not None
                    else defs_config().get("gzip_over_bytes", GZIP_OVER_BYTES))
    except (TypeError, ValueError):
        limit = GZIP_OVER_BYTES
    if limit > 0 and len(data) > limit and _client_accepts_gzip():
        try:
            data = _gzip.compress(data, 6)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        except Exception:
            data = json.dumps(payload, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            headers = {}
    return Response(data, status=status, mimetype="application/json", headers=headers)


# ============ 协议层: 6 个魔法接口的请求/响应 ============
#
# 这里集中放"这 6 个接口长什么样"的唯一一份定义, 6 个 handler 只负责取参数/鉴权,
# 然后调用本节的 build_*_response()。字段形状来自两处**权威**来源(不靠猜):
#
# 1) 0.20.0 反编译客户端的解析器(E:\工具\SO\libBootloader0200.so.c):
#      sub_3FC974  消费响应总入口: 认 "consume_item"(状态串) + "get_consumables" + "get_buffs"
#      sub_3FA684  状态串 -> 内部状态; 只有 "success" 才继续解析后面的 buff/背包
#      sub_3FACD8  消耗品条目 = {consumable_id, cooldown_until, quantity}
#      sub_52372C  buff 条目  = {buff_id, active_until, cooldown_until, giver_user_id}
#      sub_524FC4  shared_results 条目 = {uuid, result}
#      sub_3FDB24  new_height(浮点, 缺省 1.0)
#      sub_3FC638  consume_item1 请求 = {consumable_id, consumable_count, receiver_uuid}
#      sub_52678C  remove_buff1 请求  = {buff_id, giver}
# 2) 官方抓包(static_responses/account__consumable__consume_item1.json, 逐字节对齐):
#      consumable.cooldown_until = 使用时刻 + **magic 定义 cooldown(分钟)** * 60
#      buff.active_until         = 使用时刻 + **buff 定义 duration(分钟)** * 60
#      buff.cooldown_until       = 使用时刻 + **buff 定义 cooldown(分钟)** * 60
#    该样本逐项自洽: energy 的 consumable.cooldown=15 -> +900s, buff.duration=10 -> +600s,
#    buff.cooldown=0 -> +0s(抓包里三项正好是 +900 / +600 / +0)。
#
# **单位陷阱(2026-09-19 修)**: 定义表里的 cooldown / duration **都是分钟** —— 官方 507 条
# 魔法(buff_id/cooldown/event/icon/id/is_toy/name/param/type)的 cooldown 取值 0.01~720,
# 621 条 buff 的 duration 取值 0.05~1440, 全都只有"分钟"一种解释。旧实现把魔法定义的
# cooldown 当**秒**用(cooldown_until = now + 15), 于是能量药水的冷却只有 15 秒而不是
# 15 分钟(客户端画的冷却圈跟服务端对不上, 玩家能连点)。
DEF_MINUTE = 60

# 定义表里查不到的 buff id(历史脏数据 / 管理员手写的 buff)才用的兜底时长(分钟)。
DEFAULT_BUFF_DURATION_MIN = 20

# consume_item1 的状态串(客户端 sub_3FA684 只认 "success"; 非 success 时客户端
# **直接跳过** get_buffs / get_consumables 的解析 —— 见 sub_3FC974 开头的提前 return)。
CONSUME_SUCCESS = "success"
CONSUME_FAILED = "failed"

# remove_buff1 的 remove_buff 状态串: 客户端拿到 "invalid" 会打警告日志, 所以**永远回
# success**(官方 removeBuff 找不到目标也照样回 success)。
REMOVE_BUFF_SUCCESS = "success"


def sec_from_minutes(value, fallback=0.0):
    """定义表里的**分钟** -> 秒(见上面"单位陷阱")。非法值(空/NaN/负数)取 fallback 秒。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if f != f or f < 0:                     # NaN / 负数
        return float(fallback)
    return f * DEF_MINUTE


def body_user_id(data):
    """请求体里的 user(部分客户端/工具发 user_id), 取不到返回 ""。"""
    if not isinstance(data, dict):
        return ""
    return data.get("user") or data.get("user_id") or ""


def session_ok(user_id, session):
    """校验 user + session 是否对得上(与本项目其它接口同一套严格校验)。"""
    if not user_id or not session:
        return False
    try:
        from database.models import User
        return User.query.filter_by(id=user_id, session=session).first() is not None
    except Exception:
        return False


def buffs_response(user_id):
    """/account/buff/get_buffs{,1} 的响应体 = {get_buffs, get_buffs_sign}。"""
    buffs, sign = buffs_payload(user_id)
    return {"get_buffs": buffs, "get_buffs_sign": sign}


def consumables_response(user_id, include_ids=None):
    """/account/consumable/get_consumables{,1} 的响应体 = {get_consumables}。"""
    return {"get_consumables": consumables_inventory(user_id, include_ids=include_ids)}


def remove_buff_response(user_id):
    """remove_buff1 的响应体(客户端按固定 3 个键解析)。"""
    body = buffs_response(user_id)
    body["remove_buff"] = REMOVE_BUFF_SUCCESS
    return body


def consume_response(user_id, status=CONSUME_SUCCESS, consumable_id=0,
                     shared_results=None):
    """consume_item1 的响应体(6 个键, 见上面协议层说明)。

    * get_consumables **必须回显被消耗的那一件**: 官方抓包里 quantity 已扣到 0 也照样
      回显, 老实现把它过滤掉 -> 客户端"刚用过的那件魔法"凭空消失 -> 留言/纸船/留影
      流程闪退(2026-09-13 实测, 见 consumables_inventory 的 include_ids)。
    * shared_results 是 [{uuid, result}](客户端 sub_524FC4); 官方抓包与两个参考实现
      (that-sky-server / 十二)都**忽略 receiver_uuid**, 一律回空表 —— 这里保持一致。
    * new_height 是浮点, 客户端 sub_3FDB24 缺省 1.0; 只有体型类魔法才会变。
    """
    body = {"consume_item": status}
    body.update(buffs_response(user_id))
    body["get_consumables"] = consumables_inventory(
        user_id, include_ids=[consumable_id] if consumable_id else None)
    body["shared_results"] = list(shared_results or [])
    body["new_height"] = new_height(user_id)
    return body


def reroll_height(user_id):
    """resize_potion 用的身高重掷(写 UserOutfit.height)。返回新身高。

    参考实现(十二 player.rerollHeight)是 [-2,2] 的三角分布 —— 那是它自己那套以身高的
    **偏移**为中心的刻度; 本服务端库里 UserOutfit.height 是"身高刻度"本身(80 个账号里
    79 个是 1.935~1.9354, models 默认 1.9818), 照搬 [-2,2] 会得到 0 附近的坏档, 所以
    这里仍保持原来的 0.85~1.15 区间 —— 它是客户端**能接受**的(库里已有账号落在 1.0735,
    正是这个区间产生的值), 只是比常见身高矮, 更接近"变小"那档。
    **不在这里改玩法**: 真要改成"围绕常见身高的微调", 请先确认客户端对各个 height 值的
    渲染表现, 不要凭空改。
    """
    import random
    new_h = 1.0
    try:
        from database.models import UserOutfit
        outfit = UserOutfit.query.filter_by(user_id=user_id).first()
        if outfit is None:
            return new_h
        new_h = round(random.uniform(0.85, 1.15), 6)
        outfit.height = new_h
    except Exception:
        pass
    return new_h


def consume_consumable(user_id, consumable_id, count=1, giver=None):
    """使用一件魔法: 扣数量 + 记冷却 + 挂 buff。返回 (状态串, 说明)。

    与官方抓包 / 参考实现(that-sky-server、十二)对齐的语义:
      * **库里没有这件(或数量已为 0)** -> 不扣、不挂 buff、不写冷却, 返回 failed。
        参考实现 clearConsumable 找不到就什么都不干(clearItem -> consumeItem 直接 return),
        旧实现却照样挂 buff —— 等于"从没拥有过的魔法也能白用"(管理员只给一个号发魔法,
        别的号也能用同一件)。**例外**: UGC 一族(留言/纸船/留影/空间)在下发时数量是按
        UGC_MIN_QUANTITY 兜底的(见 _ugc_entry), 客户端显示 99 却库里为 0, 所以判定
        "拥有"时也要按同一个数量来, 否则"纸船能点开但一用就失败/闪退"会复发。
      * 定义表里 buff_id == 0 的 17 件魔法不挂 buff; 其中只有 resize_potion 会重掷身高
        (参考实现一致: `if (!buffId) { if (name == "resize_potion") rerollHeight(); return; }`)。
      * 冷却 = 使用时刻 + **定义表 cooldown(分钟)** * 60(见 DEF_MINUTE)。
      * buff 的 active_until / cooldown_until 由 add_buff 按 buff 定义的分钟数算。
      * 认不出的 consumable_id(定义表里没有)直接 failed: 客户端查不到定义, 用它必崩。
    """
    cid = _to_int(consumable_id)
    if not user_id or not cid:
        return CONSUME_FAILED, "unknown item"
    cdef = consumable_def(cid)
    if cdef is None:
        return consume_status_invalid_def(), "no definition for %d" % cid
    # 版本门控: 这个客户端版本根本没有这件魔法的事件类 -> 用了必崩(见 MAGIC_EVENT_MIN_VERSION),
    # 直接按"非法定义"拒绝(不扣数量、不挂 buff), 库里那件保持不动。
    if cid in blocked_consumable_ids():
        return consume_status_invalid_def(), "unsupported event for client version: %s" % (cdef.get("event"),)
    try:
        from database.models import db, UserConsumable
        row = UserConsumable.query.filter_by(user_id=user_id, consumable_id=cid).first()
        owned = _to_int(getattr(row, "quantity", 0)) if row is not None else 0
        if row is not None and cid in ugc_consumable_ids():
            # 下发时是按 UGC_MIN_QUANTITY 兜底的, "看得见的数量"才算数(见上面的说明)
            owned = max(owned, UGC_MIN_QUANTITY)
        if owned <= 0:
            return CONSUME_FAILED, "not owned"

        n = _to_int(count) or 1
        row.quantity = max(_to_int(row.quantity) - n, 0)
        row.cooldown_until = int(time.time() + sec_from_minutes(cdef.get("cooldown")))
        row.updated_at = datetime.utcnow()

        buff_id = _to_int(cdef.get("buff_id"))
        if buff_id:
            add_buff(user_id, buff_id, buff_type="consumable", giver=giver or user_id)
        elif cdef.get("name") == "resize_potion":
            reroll_height(user_id)
        db.session.commit()
        return CONSUME_SUCCESS, ""
    except Exception:
        try:
            from database.models import db
            db.session.rollback()
        except Exception:
            pass
        return CONSUME_FAILED, "server error"


def cap_defs_by_bytes(defs, limit, size_of):
    """把定义表按**明文体积**从尾部裁到 limit 以内(limit <= 0 = 不裁)。

    size_of(defs) -> 这份表下发时的字节数。定义表(如 defs_subset)已把"必须发的"排在
    最前面, 所以从尾部丢不会丢掉玩家正在用的那部分。每次丢 15%, 几步就能压到位
    (逐条丢要几百次 JSON 序列化, 在这里不可接受 —— buff 表 621 条)。
    """
    if not defs or limit <= 0 or size_of(defs) <= limit:
        return defs
    out = list(defs)
    while out and size_of(out) > limit:
        if len(out) <= 4:
            out = out[:-1]
        else:
            out = out[:-max(1, int(len(out) * 0.15))]
    return out


# ============ buff: 查询 / 增 / 删 ============

def sign_buffs(buffs, user_id):
    """与官方一致的签名: sha256(规范JSON + user_id)。"""
    import hashlib
    payload = json.dumps(buffs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((payload + (user_id or "")).encode()).hexdigest()


def buffs_payload(user_id):
    """返回 (客户端期望的 get_buffs 列表, 签名)。

    列表项字段与官方 toJSON 完全一致(客户端 sub_52372C 按这些键解析, 少一个键/多一个键
    都可能出问题):
        {"active_until": int, "buff_id": int, "cooldown_until": int, "giver_user_id": str}
    旧实现返回的是 {buff_id, buff_type, value, duration, expires_at(ISO字符串)},
    客户端解析不了 -> 有 buff 的时候就会异常。

    active_until / cooldown_until 都按**定义表的分钟数**从起点算(官方抓包实测, 见协议层
    说明); "起点"是 UserBuff.created_at(naive UTC), 所以 add_buff 刷新 buff 时会连
    created_at 一起更新。
    giver_user_id 只能回玩家自己: UserBuff 表里**没有** giver 列(参考实现有 giverId)。
    客户端只把它当展示/归属用, 不影响 buff 是否生效。
    """
    rows = []
    try:
        from database.models import UserBuff
        now = datetime.utcnow()
        rows = UserBuff.query.filter_by(user_id=user_id).filter(
            (UserBuff.expires_at.is_(None)) | (UserBuff.expires_at > now)).all()
    except Exception:
        rows = []

    out = []
    # 版本门控: 该版本不认识的 buff 事件类(如 0.12.1 没有 TimelineBuffEvent/BirthdayHatBuffEvent)
    # 一律不下发 —— 客户端拿到 buff_id 会去查 "Event named %s is not a BuffEvent" 那一套断言,
    # 查不到类名直接崩(用户 2026-10-06 反馈的"低版本收到没有的魔法会崩溃")。
    blocked_bids = blocked_buff_ids()
    for b in rows:
        bid = _to_int(b.buff_id)
        if bid in blocked_bids:
            continue
        d = buff_def(bid) or {}
        # created_at/expires_at 是 naive UTC, 必须按 UTC 换算(否则差 8 小时 -> 已过期)
        start = _utc_epoch(getattr(b, "created_at", None)) or int(time.time())
        active_until = 0
        if getattr(b, "expires_at", None):
            active_until = _utc_epoch(b.expires_at)
        if not active_until:
            dur_sec = sec_from_minutes(d.get("duration"))
            active_until = start + int(dur_sec) if dur_sec else start
        out.append({
            "active_until": active_until,
            "buff_id": bid,
            # buff 定义的 cooldown 在官方 621 条里全是 0 -> cooldown_until == 起点(抓包一致)
            "cooldown_until": start + int(sec_from_minutes(d.get("cooldown"))),
            "giver_user_id": user_id or "",
        })
    out.sort(key=lambda x: x.get("buff_id") or 0)
    return out, sign_buffs(out, user_id)


def add_buff(user_id, buff_id, buff_type="spell", duration_seconds=None, giver=None,
             refresh=True):
    """给用户加一个 buff。返回 UserBuff 行或 None。

    duration: 优先用传参(秒), 否则取**buff 定义表的 duration(分钟)**; 定义表里查不到的
    buff id 才用 DEFAULT_BUFF_DURATION_MIN(20 分钟, 与参考实现的默认量级一致)。

    refresh=True(默认): 同一 buff 已存在时**刷新**(重掷起点与到期时间)。
    参考实现是 `if (this.buffs.has(id)) return;`(已存在就不理), 那样玩家"再用一次同款
    魔法"等于白用; 本服务端保留刷新语义(玩家预期"再用一次会续时长"), 但要刷新就必须把
    **起点一起更新** —— 客户端条目的 active_until / cooldown_until 都是
    "起点 + 定义表分钟数*60"算出来的(buffs_payload), 只改 expires_at 会让
    cooldown_until 停在第一次使用的时间上。真要关掉刷新改成参考语义: refresh=False。
    """
    bid = _to_int(buff_id)
    if not user_id or not bid:
        return None
    d = buff_def(bid) or {}
    if duration_seconds is None:
        dur_min = d.get("duration")
        duration_seconds = (sec_from_minutes(dur_min)
                            if dur_min else DEFAULT_BUFF_DURATION_MIN * DEF_MINUTE)
    duration_seconds = max(int(duration_seconds), 1)
    try:
        from database.models import db, UserBuff
        row = UserBuff.query.filter_by(user_id=user_id, buff_id=str(bid)).first()
        if row is not None and not refresh:
            return row
        now = datetime.utcnow()
        expires = now + timedelta(seconds=duration_seconds)
        if row is not None:
            row.created_at = now          # 起点: buffs_payload 用它算 active/cooldown
            row.expires_at = expires
            row.duration = duration_seconds
            if buff_type:
                row.buff_type = buff_type
        else:
            row = UserBuff(user_id=user_id, buff_id=str(bid), buff_type=buff_type,
                           value=1.0, duration=duration_seconds, expires_at=expires)
            db.session.add(row)
        db.session.commit()
        return row
    except Exception:
        try:
            from database.models import db
            db.session.rollback()
        except Exception:
            pass
        return None


def remove_buff(user_id, identifier):
    """移除一个 buff。identifier 可以是 buff_id(int/数字串)或 buff 名字。
    返回是否真的删掉了。"""
    bid = _to_int(identifier)
    if not bid and isinstance(identifier, str) and identifier:
        d = buff_def_by_name(identifier)
        if d:
            bid = _to_int(d.get("id"))
    if not user_id or not bid:
        return False
    try:
        from database.models import db, UserBuff
        row = UserBuff.query.filter_by(user_id=user_id, buff_id=str(bid)).first()
        if row is None:
            return False
        db.session.delete(row)
        db.session.commit()
        return True
    except Exception:
        try:
            from database.models import db
            db.session.rollback()
        except Exception:
            pass
        return False


# ============ 开箱: 宝箱 -> 魔法 ============

def pick_box_consumable(box_name):
    """宝箱能开出哪件魔法: 返回**定义表里真实存在、且该客户端版本认识**的 consumable_id。

    官方"宝箱内容物"表我们拿不到(抓包里只有一次 lootbox_common -> energy 的样本),
    所以按稀有度分池、并在池内按 名字哈希 确定性挑一个(同一个宝箱永远开出同一件魔法):
        lootbox_common_* -> BOX_COMMON_POOL (energy/glow/... 常见魔法)
        lootbox_rare_*   -> BOX_RARE_POOL   (道具/外观类魔法)
    关键: 一定返回定义表里存在的 id —— 旧实现用 abs(hash(box_id)) 现造 id, Python
    的字符串 hash 每个进程还都不一样, 客户端查不到定义就直接崩。
    版本门控(2026-10-06): 该客户端版本没有的魔法(dnd / outfit_* / hairtint_rainbow …)
    不参与抽取 —— 抽出来也发不出去, 玩家只会看到一个开箱后"什么都没多"。
    """
    name = str(box_name or "")
    pool = BOX_RARE_POOL if "rare" in name.lower() else BOX_COMMON_POOL
    defs = _pool_defs(pool)
    blocked_cids = blocked_consumable_ids()
    if blocked_cids:
        allowed = [d for d in defs if _to_int(d.get("id")) not in blocked_cids]
        if allowed:
            defs = allowed
    if not defs:
        # 定义表读不到: 退回官方实测样本(energy), 绝不用现造的 hash id。
        return _BOX_COMMON_CONSUMABLE_ID
    picked = _to_int(defs[fnv1a(name) % len(defs)].get("id"))
    return picked or _BOX_COMMON_CONSUMABLE_ID


def _pool_defs(names):
    """把池里的**名字**解析成定义表里的真实定义(不存在的名字自动跳过)。"""
    by_name = {c.get("name"): c for c in consumable_defs()}
    return [by_name[n] for n in names if n in by_name]


def box_consumable_ids():
    """两个池里所有魔法 id(给 defs_subset 用)。"""
    return {_to_int(c.get("id")) for c in (_pool_defs(BOX_COMMON_POOL) + _pool_defs(BOX_RARE_POOL))}


def _recent_used_consumables(user_id, limit=40):
    """玩家**最近用过**的魔法 id(按 user_consumable.updated_at 倒序, 最近使用优先)。

    用来把有限的 buff 预算花在玩家手上正在玩的那一片: 客户端只有在收到某件魔法的
    buff 定义后, 用它对才能生效(服饰体验靠 buff 定义里的 effect/params 换装), 而
    老客户端一次吃不下全部 489 条 buff 定义。
    """
    if not user_id:
        return []
    try:
        from database.models import UserConsumable
        rows = (UserConsumable.query.filter_by(user_id=user_id)
                .order_by(UserConsumable.updated_at.desc()).limit(int(limit)).all())
    except Exception:
        return []
    out = []
    for r in rows:
        cid = _to_int(getattr(r, "consumable_id", 0))
        if cid:
            out.append(cid)
    return out


def defs_subset(user_id=None):
    """返回 (魔法定义, buff定义) 的子集 —— 给 <0.26.0 老客户端用, 控制响应体积。

    老客户端(<0.26.0, 例如 0.20.x/0.22.0/0.7.1)收到完整定义表体积太大(魔法 86KB +
    buff 244KB), 有"解析不完 -> 闪退"的风险(兑换树 165KB 就把老客户端打崩过, 见
    get_spirit_shops 的实测锚点; buff 表 224,799 字节也把 0.22.0 打崩过)。所以老客户端
    只发**用得上的**:
        魔法 = 宝箱内容物池 ∪ 玩家已拥有的魔法
        buff = 宝箱池魔法引用的 buff ∪ 玩家身上正在生效的 buff
               ∪ 已拥有魔法的 buff(按 buff_defs_budget() 字节预算尽量补,
                 并以"玩家最近使用的魔法"为中心向外扩散)
    新客户端(>=0.26.0)本来就走接口库的完整定义表, 不经过这里。
    """
    from database.models import UserConsumable
    owned = set()
    if user_id:
        try:
            owned = {_to_int(c.consumable_id)
                     for c in UserConsumable.query.filter_by(user_id=user_id).all()}
        except Exception:
            owned = set()
    # 版本门控: 该版本没有的魔法(事件类名 / buff effect, 见 MAGIC_EVENT_MIN_VERSION 与
    # MAGIC_EFFECT_MIN_VERSION)连定义都不发, 背包条目也一起挡(两处必须一致)。
    blocked = blocked_events()
    blocked_fx = blocked_effects()
    blocked_cids = blocked_consumable_ids()
    pool_ids = box_consumable_ids()
    # UGC 一族(留言/纸船/留影/空间)的定义**必发**: 玩家没拥有、宝箱也开不出来的那 3 件
    # (message_sunflower / message_vgflower / personality_message_boat)老实现漏发,
    # 客户端留言面板里那 3 件就是"查无此物"。见 UGC_SUBCATEGORY 上面的实测记录。
    # 注意: "必发"仅限**这个客户端版本认识**的那些 —— 0.12.1 认识 SocialMessageBoats
    # (0.11.0+), 但不认识 RecordingCandleEvent/SharedSpaceCandleEvent(0.19.5+)/
    # SocialMessageStones(0.33.7+), 发下去就是闪退(用户 2026-10-06 反馈的那批)。
    ugc_ids = {cid for cid in ugc_consumable_ids() if cid not in blocked_cids}
    wanted = pool_ids | owned | ugc_ids

    # 魔法定义的**下发顺序**(重要): 背包里真正会显示的那批排最前面 ——
    # 客户端的魔法面板渲染的就是 get_consumables1 发过去的背包条目, 下游还有体积闸门
    # (get_consumable_defs 里按明文 86KB 从**尾部**裁), 所以顺序 = 谁排在后面谁先被裁。
    # 之前按官方定义表顺序发, 一超限先丢的就是排在表尾的魔法(社交三件就在尾部),
    # 表现为"背包里有的魔法却没有定义/没反应"。
    bag_ids = []
    if user_id:
        try:
            bag_ids = [_to_int(e.get("consumable_id"))
                       for e in consumables_inventory(user_id)]
        except Exception:
            bag_ids = []
    _prio = {}
    for _cid in ugc_ids:
        # UGC 定义排最前: get_consumable_defs 里超体积上限时是从尾部裁的, 裁到谁谁
        # "没有 UI/点了没反应"。这 11 件体积很小(几百字节), 必须保住。
        _prio.setdefault(_cid, (0, -1))
    for _i, _cid in enumerate(bag_ids):
        _prio.setdefault(_cid, (0, _i))
    for _cid in pool_ids:
        _prio.setdefault(_cid, (1, 0))
    for _cid in wanted:
        _prio.setdefault(_cid, (2, 0))

    cdefs, seen = [], set()
    for c in sorted(consumable_defs(), key=lambda c: _prio.get(_to_int(c.get("id")), (3, 0))):
        cid = _to_int(c.get("id"))
        if cid in wanted and cid not in seen and cid not in blocked_cids:
            seen.add(cid)
            cdefs.append(c)

    # ---- buff 定义: 必发的那部分 ----
    #  1) 宝箱内容物池里的魔法引用的 buff —— 玩家随时可能从宝箱开出来, 客户端拿不到
    #     定义就渲染不了;
    #  2) 玩家身上正在生效的 buff(可能是别人给的 / 活动给的)。
    need = {_to_int(c.get("buff_id")) for c in cdefs
            if c.get("buff_id") and _to_int(c.get("id")) in pool_ids}
    if user_id:
        try:
            from database.models import UserBuff
            for b in UserBuff.query.filter_by(user_id=user_id).all():
                bid = _to_int(getattr(b, "buff_id", 0))
                if bid:
                    need.add(bid)
        except Exception:
            pass
    bdefs = [b for b in buff_defs()
             if _to_int(b.get("id")) in need
             and not buff_def_blocked(b, blocked, blocked_fx)]

    # ---- buff 定义: 已拥有魔法的 buff(按体积预算尽量补) ----
    # 踩过的坑: 把"玩家拥有的每一件魔法"引用的 buff 全发下去, 当账号拥有全部 507 件
    # 魔法时 buff 表会涨到 **224,799 字节**, 实测直接把 0.22.0 老客户端打崩(服务端日志
    # 16:19:45 get_buff_defs 224799 之后客户端就掉线重连了); 而 86,219 字节的完整魔法表
    # (同一台设备同一个客户端, 16:19:43)和 32,879 字节的 buff 子集都能正常解析。所以
    # 这里按**字节预算**补(默认 84KB, 见 buff_defs_budget)。
    #
    # 选谁进预算: 以"玩家最近用过的魔法"在定义表里的位置**为中心**向外扩散。
    # 为什么: 定义表是按类别分块排的(身体/脚/头发/帽子/角/面具/背饰/翅膀...), 玩家
    # 测试时是一件一件顺着试的, 所以"离他最近用过的那件越近"= 越可能马上被用到。
    # 没有使用记录的老账号则退回定义表顺序(行为与之前一致)。
    # 安全性: 真正"用得上"的 buff 一定在 need 里(池内 + 正在生效), 这里的 optional 只是
    # 让客户端多认识几件还没用的魔法, 少发不会导致已生效 buff 缺定义。
    by_id = {_to_int(b.get("id")): b for b in buff_defs()}
    pos_of_consumable = {}
    for i, c in enumerate(consumable_defs()):
        pos_of_consumable[_to_int(c.get("id"))] = i

    optional, seen_opt = [], set()
    for c in cdefs:
        bid = _to_int(c.get("buff_id"))
        if bid and bid not in need and bid not in seen_opt:
            seen_opt.add(bid)
            optional.append((pos_of_consumable.get(_to_int(c.get("id")), 0), bid))

    center = None
    used_ids = _recent_used_consumables(user_id)
    if used_ids:
        poss = [pos_of_consumable[i] for i in used_ids if i in pos_of_consumable]
        if poss:
            center = max(poss)
    if center is None:
        optional.sort(key=lambda t: t[0])
    else:
        optional.sort(key=lambda t: (abs(t[0] - center), t[0]))

    budget = buff_defs_budget()
    used = len(json.dumps(bdefs, ensure_ascii=False, separators=(",", ":")))
    for _pos, bid in optional:
        if budget and used >= budget:
            break
        b = by_id.get(bid)
        if b is None or buff_def_blocked(b, blocked, blocked_fx):
            continue
        size = len(json.dumps(b, ensure_ascii=False, separators=(",", ":"))) + 1
        if budget and used + size > budget:
            continue                    # 这条放不下, 继续试更靠后的(体积有差异)
        bdefs.append(b)
        used += size

    if not budget:
        # 不限制体积(buff_defs_budget=0)时下发**官方完整 buff 表**: 顺序、内容都与
        # >=0.26.0 客户端走接口库拿到的那份完全一致。这样玩家自己没买/暂时没生效的
        # buff(活动 buff、别人给的牵手服饰体验…)也都能解析。
        # 上面那段"按需 + 字节预算"是给老客户端的保守路线(buff_defs_budget>0)。
        return cdefs, filter_defs_for_version(list(buff_defs()), blocked)

    return cdefs, bdefs


# ============ 背包(consumables) ============

def consume_entry(row):
    """UserConsumable 行 -> 客户端期望的条目形状(官方抓包实测就是这个形状):
    {"consumable_id": int, "cooldown_until": int, "quantity": int}"""
    return {
        "consumable_id": _to_int(getattr(row, "consumable_id", 0)),
        "cooldown_until": _to_int(getattr(row, "cooldown_until", 0)),
        "quantity": _to_int(getattr(row, "quantity", 0)),
    }


def magic_list_max():
    """发给客户端的背包件数上限(0 = 不限制)。改 instance/magic_defs_config.json 即刻生效。"""
    try:
        n = int(defs_config().get("magic_list_max", MAGIC_LIST_MAX_DEFAULT))
    except (TypeError, ValueError):
        n = MAGIC_LIST_MAX_DEFAULT
    return n if n > 0 else 0


def _magic_tier(consumable_id, recent_ids):
    """背包条目优先级(数字越小越先发):
    0 UGC(留言/纸船/留影/空间)+ 社交三件 / 1 最近用过 / 2 服饰体验类 / 3 其余。

    UGC 单独给 tier 0: 它们件数少(11 件)但**缺哪一件都会让客户端的留言/纸船流程闪退**
    (见 UGC_SUBCATEGORY 上面的实测记录), 所以件数上限先裁别的。
    """
    cid = _to_int(consumable_id)
    c = consumable_def(cid) or {}
    nm = str(c.get("name") or "")
    if nm in SOCIAL_MAGIC_NAMES or cid in ugc_consumable_ids():
        return 0
    if cid in recent_ids:
        return 1
    ev = str(c.get("event") or "")
    if nm.startswith("outfit") or "Outfit" in ev or "Wardrobe" in ev:
        return 2
    return 3


def known_consumable_ids():
    """定义表里所有合法魔法 id 的集合(mtime 缓存)。"""
    try:
        mtime = os.path.getmtime(CONSUMABLE_DEFS_FILE)
    except OSError:
        return set()
    hit = _CACHE.get("known_ids")
    if hit and hit[0] == mtime:
        return hit[1]
    ids = {_to_int(c.get("id")) for c in consumable_defs()}
    ids.discard(0)
    _CACHE["known_ids"] = (mtime, ids)
    return ids


def ugc_consumable_ids():
    """定义表里 UGC 一族(留言/纸船/留影/空间)的全部魔法 id。

    判据见 is_ugc_def: subcategory == "UGC" **或** event 属于 UGC_EVENTS **或**名字是
    message_*/personality_message_*/memory_candle/sharedspace_candle —— 这样官方 507 条
    表(没有 subcategory)和私服 599 条表都能正确识别出同样这一族。
    mtime 缓存, 与 known_consumable_ids 一样从 static_responses 的定义表算出来, 不写库。
    """
    try:
        mtime = os.path.getmtime(CONSUMABLE_DEFS_FILE)
    except OSError:
        return set()
    hit = _CACHE.get("ugc_ids")
    if hit and hit[0] == mtime:
        return hit[1]
    ids = {_to_int(c.get("id")) for c in consumable_defs() if is_ugc_def(c)}
    ids.discard(0)
    _CACHE["ugc_ids"] = (mtime, ids)
    return ids


# 0.22.0 时代定义表里**没有**的 3 个字段。私服那份 599 条表每条都带(平均 234B),
# 而 0.22.0 客户端实测吃下的形状是每条 170B(19:16:12 "get_consumable_defs" 200 86,219
# 字节 = 完整 599 条的**精简**形状量级, 之后 19:18:18 成功创建留影)。
# 所以体积不够时先砍这 3 个字段(0.22.0 本来就不认识它们, 只影响背包里的分类/排序,
# 不影响图标/名字/能不能用), 保证**背包里一件魔法都不缺定义**; 只有精简后仍超限时
# 才从列表尾部整条丢掉(顺序已保证 UGC + 背包条目在最前, 丢的都是客户端不渲染的)。
CONSUMABLE_DEF_OPTIONAL_FIELDS = ("category", "subcategory", "order_in_subcategory")


def slim_consumable_def(d):
    """去掉 0.22.0 定义表里本来就没有的 3 个字段(见上)。"""
    if not isinstance(d, dict):
        return d
    return {k: v for k, v in d.items() if k not in CONSUMABLE_DEF_OPTIONAL_FIELDS}


def defs_json_size(defs):
    """魔法定义表下发时的明文 JSON 体积(含外层 {"get_consumable_defs": ...})。"""
    return len(json.dumps({"get_consumable_defs": list(defs)}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))


def fit_consumable_defs(defs, limit):
    """把魔法定义表压进 limit 字节, 但**优先保住条数与覆盖面**。

    1) 全部精简字段(见 CONSUMABLE_DEF_OPTIONAL_FIELDS) —— 条数比字段更重要, 缺条目的
       魔法在客户端就是查无此物/没有 UI/一点就闪退;
    2) 精简后仍超限 -> 从**尾部**整条丢(顺序由 defs_subset 保证 UGC 一族 + 背包条目
       在最前, 所以丢掉的都是不在背包里、客户端也不会渲染的定义)。
    """
    defs = list(defs or [])
    if not defs:
        return defs
    if defs_json_size(defs) <= limit:
        return defs
    out = [slim_consumable_def(d) for d in defs]
    while out and defs_json_size(out) > limit:
        out = out[:-1] if len(out) <= 8 else out[:-max(1, int(len(out) * 0.06))]
    return out


def _ugc_entry(consumable_id, row=None):
    """UGC 魔法在背包里下发的条目。

    与 consume_entry 的唯一差别: **数量至少 UGC_MIN_QUANTITY**。库里被用光(quantity=0)
    的纸船按 99 发, 这样客户端在留言面板里永远查得到它(查不到 = 一点就闪退, 实测);
    数据库那一行本身不动(仍然 0), 回滚只需去掉这个函数的使用处。
    """
    q = _to_int(getattr(row, "quantity", 0)) if row is not None else 0
    return {
        "consumable_id": _to_int(consumable_id),
        "cooldown_until": _to_int(getattr(row, "cooldown_until", 0)) if row is not None else 0,
        "quantity": q if q > UGC_MIN_QUANTITY else UGC_MIN_QUANTITY,
    }


def consumables_inventory(user_id, include_ids=None):
    """返回该用户背包里的魔法(客户端条目形状) —— 件数不超过 magic_list_max()。

    旧实现里 get_consumables1 永远返回 [], 于是玩家买/开箱拿到魔法后背包依旧空白
    (客户端认为服务端同步过来的背包是空的, 会把手里的魔法丢掉/显示异常)。

    **只返回定义表里真实存在的 id**: 历史版本 redeem_box 用 abs(hash(box_id)) 现造过
    一批 id(数据库里已经躺着这种脏数据), 把这些 id 发给客户端 = 客户端查不到定义
    = 闪退。发不出去的脏数据直接跳过(不删库, 只不展示; 见报告里的清理建议)。
    **数量 0 的也不发**(官方的魔法是"用完即从背包消失", 我们库里只是扣到 0);
    **例外是 UGC 一族**(见 _ugc_entry: 用光的纸船按 99 发, 否则客户端的留言流程闪退)。
    UGC 的"补数量"只对**库里确实有行**的账号生效(2026-09-19 起); 旧实现会给所有
    账号无条件补全部 UGC, 表现为"只给一个账号发的魔法, 所有账号含新号都有"。

    **件数上限**: 客户端崩不崩与响应字节数无关, 只与"它要在 UI 里建条目/拉图标"的件数
    有关(2026-09-13 实测锚点: 169 件放心, 490 件时客户端曾崩过)。但 2026-09-13 19:16
    那次**带着完整 599 条定义 + 490 件背包**的登录里, 玩家成功创建了留影(录影->使用->
    发布->列表全通), 而之后每次"件数被截到 160"的登录都在创建留言/纸船时闪退 —— 闪退
    的真因是 message_* 那 6 件被裁掉(见 UGC_SUBCATEGORY)。所以现在的策略是:
      * UGC 11 件永远排 tier 0, 不会被裁;
      * 其余件数用 magic_list_max 控(instance/magic_defs_config.json, 热读);
        默认 0 = 不限制(把玩家拥有的 497 件全发, 与 19:16 那次唯一成功创建留影的
        配置一致); 若某天再遇到"背包一大就崩", 把它调回 160/200 逐档试即可。
    **调法**: instance/magic_defs_config.json 的 magic_list_max(热读)。
    """
    try:
        from database.models import UserConsumable
        rows = UserConsumable.query.filter_by(user_id=user_id).all()
    except Exception:
        rows = []
    known = known_consumable_ids()
    ugc = ugc_consumable_ids()
    # 版本门控: 该版本没有的魔法事件类(如 0.12.1 没有 RecordingCandleEvent 留影蜡烛)既
    # 不发定义也不发背包条目 —— 只发条目不发定义 = 客户端查不到 = 闪退(见 UGC 那段实测),
    # 而发定义又直接踩 event 类名断言, 所以两处必须一起挡(用户 2026-10-06 反馈)。
    blocked_cids = blocked_consumable_ids()
    out = []
    seen = set()
    for r in rows:
        entry = consume_entry(r)
        cid = entry["consumable_id"]
        if known and cid not in known:
            continue
        if cid in blocked_cids:
            continue
        is_ugc = cid in ugc
        # 数量 0 的条目不发(官方是用完即从背包里消失); UGC 例外, 见 _ugc_entry。
        if entry["quantity"] <= 0 and not is_ugc:
            continue
        if is_ugc:
            entry = _ugc_entry(cid, r)
        seen.add(cid)
        out.append(entry)
    # UGC 一族: 默认**只发库里真有行的**(见下面的 ugc_force_send)。
    #
    # 旧实现是无条件把全部 UGC(留言/纸船/留影/空间, 8~11 件)追加给**每一个**账号,
    # 于是出现 2026-09-19 的反馈: "只给 A 账号发了魔法, 所有账号(包括刚注册的)背包里
    # 都有它" —— 其实数据库里只有 A 有 user_consumable 行, 是这里凭空补出来的。
    #
    # 真正要挡的是"有行但被过滤掉"的那种闪退: 数量 0 的 message_* 旧代码会被
    # "数量 0 不发" 干掉 -> 客户端拿着刚消耗的那件却查不到 -> 留言/纸船流程闪退。
    # 那个场景**行是存在的**, 已被上面 `is_ugc` 分支 + _ugc_entry(数量补到 99)覆盖,
    # 不需要靠"给所有人补"。
    #
    # 万一某客户端确实需要"没拥有也下发"才不会崩, 把 instance/magic_defs_config.json
    # 的 ugc_force_send 设成 true 即可恢复旧行为(热读, 改完立即生效)。
    force_ugc = bool(_to_int(defs_config().get("ugc_force_send", 0)))
    for cid in sorted(ugc):
        if cid in seen or (known and cid not in known) or cid in blocked_cids:
            continue
        if not force_ugc:
            continue            # 该账号没拥有这件 UGC -> 不再凭空下发
        out.append(_ugc_entry(cid))
        seen.add(cid)
    # consume_item1 用: 官方响应里的 get_consumables 会**回显被消耗的那一件**
    # (抓包 static_responses/account__consumable__consume_item1.json: quantity 已经扣到 0
    #  也照样回显)。老实现扣到 0 就把它过滤掉, 于是客户端手里"刚用过的那件"凭空消失。
    for cid in (include_ids or []):
        cid = _to_int(cid)
        if not cid or cid in seen or (known and cid not in known):
            continue
        # 该版本不认识这件魔法(事件类名不存在) -> 连"刚用过的那件"也不回显: 回显等于
        # 又给它发一次没有定义的 id, 客户端查不到定义同样闪退。
        if cid in blocked_cids:
            continue
        row = None
        for r in rows:
            if _to_int(getattr(r, "consumable_id", 0)) == cid:
                row = r
                break
        out.append(_ugc_entry(cid, row) if cid in ugc else
                   (consume_entry(row) if row is not None else
                    {"consumable_id": cid, "cooldown_until": 0, "quantity": 0}))
        seen.add(cid)
    out.sort(key=lambda x: x.get("consumable_id") or 0)

    cap = magic_list_max()
    if cap and len(out) > cap:
        out = _prioritized_inventory(out, user_id)[:cap]
    return out


def _prioritized_inventory(entries, user_id):
    """按"先社交三件、再最近用过、服饰类与其它类轮着来"排, 再交给件数上限截断。

    单按 tier 排的话服饰类(311 件)会把动作/法术/道具(116 件)全挤掉, 而玩家这几天的
    测试里两者都要用, 所以 tier 2/3 轮转取件。
    """
    recent = set(_recent_used_consumables(user_id, limit=80))
    buckets = {0: [], 1: [], 2: [], 3: []}
    for e in entries:
        buckets[_magic_tier(e.get("consumable_id"), recent)].append(e)
    for k in buckets:
        buckets[k].sort(key=lambda e: e.get("consumable_id") or 0)
    out = list(buckets[0]) + list(buckets[1])
    i = 0
    while buckets[2] or buckets[3]:
        if buckets[2] and (i % 2 == 0 or not buckets[3]):
            out.append(buckets[2].pop(0))
        elif buckets[3]:
            out.append(buckets[3].pop(0))
        i += 1
    return out


def give_consumable(user_id, consumable_id, count=1):
    """发放(或叠加)一件魔法, 返回 UserConsumable 行或 None。

    只接受**定义表里存在**的 id(幂等: 同一 id 叠加数量)。现造 id 会让客户端查不到
    定义而异常, 所以这里直接挡住。
    """
    cid = _to_int(consumable_id)
    if not user_id or not cid:
        return None
    try:
        from database.models import db, UserConsumable
        row = UserConsumable.query.filter_by(user_id=user_id, consumable_id=cid).first()
        if row is not None:
            row.quantity = (row.quantity or 0) + int(count or 1)
            row.updated_at = datetime.utcnow()
        else:
            row = UserConsumable(user_id=user_id, consumable_id=cid, cooldown_until=0,
                                 quantity=int(count or 1),
                                 created_at=datetime.utcnow(),
                                 updated_at=datetime.utcnow())
            db.session.add(row)
        db.session.commit()
        return row
    except Exception:
        try:
            from database.models import db
            db.session.rollback()
        except Exception:
            pass
        return None


def resolve_consumable_id(*candidates):
    """从若干候选(数字 id / 名字, 按顺序)里解析出定义表里真实存在的 consumable_id。

    客户端不同接口传的字段名不一样(consumable_id / item_name / item_id / name),
    handler 把可能的值都丢进来, 按顺序找第一个能落到定义表上的。
    返回 0 表示没找到。
    """
    for c in candidates:
        if c is None or c == "":
            continue
        cid = _to_int(c)
        if cid and consumable_def(cid) is not None:
            return cid
    for c in candidates:
        if isinstance(c, str) and c:
            d = consumable_def_by_name(c)
            if d is not None:
                return _to_int(d.get("id"))
    return 0


# ============ 其它 ============

def new_height(user_id):
    """玩家当前身高(官方 consume_item1 响应里的 new_height, 体型魔法要用)。"""
    try:
        from database.models import UserOutfit
        o = UserOutfit.query.filter_by(user_id=user_id).first()
        if o is not None and getattr(o, "height", None) is not None:
            return float(o.height)
    except Exception:
        pass
    return 1.0