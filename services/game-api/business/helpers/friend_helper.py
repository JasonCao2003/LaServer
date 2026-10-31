# -*- coding: utf-8 -*-
"""好友关系 / "点亮" 的自动补全工具
=================================

为什么需要它(2026-10-01 实测, 日志 server_runtime_8443.log):

  客户端在关卡里"点亮"一个玩家之后, 会用 /account/get_friends
  {"players":["<对方uuid>"]} **每 200~300ms 轮询一次**追问对方信息(实测 690 次,
  两个账号互相轮询)。它期望 set_friends 里出现这个 uuid, 好把对方画到好友树/星座上。

  但"点亮"这个动作在客户端侧**不会调用任何会写 friendship 表的接口**:
    - accept_invite  是邀请码/扫码流程;
    - give_candle    只发一条礼物消息, 不建关系;
    - send_message   也是"给已有好友发消息"。
  于是数据库里 friendship / friend_status 恒为 0 行(实测确实都是 0),
  结果:
    - set_friends 恒为空        -> 好友树空白, 客户端无限重试(250ms 一次的风暴);
    - get_friend_statues 空     -> 星座/好友树还是没有东西;
    - get_online_friends 空     -> 看不到"在线好友";
    - 加好友 / 聊天也都被卡住(没有关系就没有可发的通道)。

  这里的做法: 凡是我们有理由认为"这两个人已经互相点亮/同处一个关卡"的,
  就补上双向的 Friendship + FriendStatus 记录(与 accept_invite 建的表结构一致),
  让服务器的世界状态和客户端看到的对上。开关(config/friend_config.json, 改完即生效;
  环境变量 SKY_AUTO_FRIEND / SKY_AUTO_FRIEND_ALL / SKY_LIGHT_PLACEHOLDER_USER 仍可用,
  但**重启进程才生效**):
      auto_friend=0        关掉自动建关系
      auto_light_all=false 只处理 get_friends 显式点名的 players, 不自动点亮全场
      placeholder_users    见下面 2026-10-03 那段

  2026-10-05 修正 "两个新建的号默认为好友":
    auto_light_all 以前默认开, 它把"同 levelId 的在线玩家"全点亮; 开了人人同房
    (single_shared_room)之后全服同一个 levelId, 于是任何两个账号只要同时在线就
    自动互为好友 —— 用户实测新号什么都没做就出现在对方好友列表里, "这是错误的"。
    现在 auto_light_all 默认 false; 而且自动点亮建的关系**不带任何能力**(abilities=[]),
    "点亮"只是让对方那个条目能被解析, 不再等于加好友(加好友节点 1 要靠送蜡烛买,
    与 give_candle 第 3 步 grant_abilities 的分工一致)。

  2026-10-03 追加(实测): 上面这套还不够。客户端点名的玩家如果**从来没登录过本服**,
  user 表里没有它, friendship/friend_status 的外键就建不起来 —— 一个 uuid 被客户端
  追问了 945 次, 每次服务器都只能回 set_friends:{} , 点亮状态根本落不了库, 换地图后
  必须重新点火。所以对"客户端在 players 里明确点名"的 uuid, 现在会补一行只填 id 的
  占位账号(user 表除 id 外都可空), 再建关系。关闭: SKY_LIGHT_PLACEHOLDER_USER=0
"""
from paths import CONFIG_DIR

import json
import logging
import os
import re
import threading
import time
import uuid

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# 客户端好友树上的"能力"位。
#
# 官方"用蜡烛加好友"那一次的静态样本(static_responses/account__give_candle.json)里
# update_friends[0].abilities = [1, 2, 9, 10, 19] —— 也就是加好友这一个节点展开后的
# 集合(1=nickname 加好友, give_also=[2,9,10,19] = 牵手/收藏/屏蔽/举报)。
#
# 老值是 [19, 6, 9, 2, 27, 1, 10], 多带了 6(chat 聊天) 和 27(chat_mute 聊天免打扰)。
# 这两个节点官方要各花 3 根蜡烛(见 config/relationship_abilities.json: 6.cost=3,
# pre_reqs=[3], give_also=[27])买到, 不能白送。实测后果(2026-10-04): 新加的好友
# 一上来聊天就是亮的 —— "房员解锁的节点不是只解锁了一个, 而是聊天都被解锁了"。
DEFAULT_ABILITIES = [1, 2, 9, 10, 19]
DEFAULT_FRIEND_LEVEL = 1

# 好友树上"该给 TA 起个名字"那个节点的 id。
#
# 官方抓包对比(static_responses/):
#   account__give_candle.json      hints = [1, 2, 9, 10, 19]   <- 送蜡烛那一步带着 1
#   account__set_friend_name.json  hints = [2, 9, 10, 19]      <- 起完名 1 就不见了
# 也就是说: 命名这一步的副作用就是把 hint 1 从待确认列表里去掉。客户端没看到 1
# 才认为"这个名字已经定下来了", 不会再弹命名框。
#
# 老代码只改 nickname、不碰 hints, 于是客户端本地那个"待命名"标记一直在, 下次
# 进好友树/收到 update_friends 时又照着 hints 里的 1 把命名框弹一遍 —— 用户实测
# "需要反复给好友设置备注, 设置完后一会又会弹出来"。
NAMING_HINT_ID = 1

_node_cache = {"at": 0.0, "players": []}
_node_lock = threading.Lock()


def auto_create_account_enabled():
    """**无库就建号**: 客户端拿着一个账号库"里没有"的 id 来请求时, 用同一个 id 补一行。

    默认 **True**(用户 2026-10-05 明确要求: "我是要你实现无库就建号的功能")。
    用在两处:
      1) /account/auth/login —— 库里没有这个 id 时按同 id 建号再继续登录(见 auth_login.py)。
         清空/换账号库之后, 客户端手里还揣着旧 id, 以前登录直接回 not_found, 客户端当场
         停在第一步, 后面 ~80 个预热请求一个都不发, 账号就再也回不来了(实测日志: 那条
         not_found 就是该客户端的最后一条请求)。
      2) /service/trust/api/v1/get_capped —— 老早就是"不存在则建号"(同 id, 不校验 session)。
    关掉: config/friend_config.json 里 auto_create_account=false(或 SKY_AUTO_CREATE_ACCOUNT=0)。
    """
    cfg = _friend_config()
    if "auto_create_account" in cfg:
        return bool(cfg.get("auto_create_account"))
    return os.environ.get("SKY_AUTO_CREATE_ACCOUNT", "1") != "0"


def lit_grants_abilities_enabled():
    """被"点亮"的玩家算不算加了好友(即条目里带不带整套好友树能力)。

    默认 **False** —— 点亮只是"我看见过这个人", 不是好友:
      abilities=[] 时客户端拿到的是一条没有好友树节点的登记, 好友树里不出现
      "加好友(1)"那个节点; 真要成为好友, 用户在游戏里花蜡烛加好友
      (/account/give_candle) —— grant_abilities 会把买到的节点并进这一行,
      那一侧的下一次轮询就能看到能力/hints 了(与 give_candle 第 3 步一致)。
    老行为(点亮即送 DEFAULT_ABILITIES)在 2026-10-05 被用户否掉:
    "两个新建的号为什么会默认为好友, 这是错误的" / "不用默认点亮" /
    "让用户自己点亮加好友"。要临时回老行为: config/friend_config.json 里
    lit_grants_abilities=true(或环境变量 SKY_LIT_ABILITIES=1)。
    """
    cfg = _friend_config()
    if "lit_grants_abilities" in cfg:
        return bool(cfg.get("lit_grants_abilities"))
    return os.environ.get("SKY_LIT_ABILITIES", "0") != "0"


def _friend_config():
    """可选配置文件 config/friend_config.json —— 每次调用都重新读, 改完存盘立刻生效。"""
    path = os.path.join(CONFIG_DIR, "friend_config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def auto_friend_enabled():
    cfg = _friend_config()
    if "auto_friend" in cfg:
        return bool(cfg.get("auto_friend"))
    return os.environ.get("SKY_AUTO_FRIEND", "1") != "0"


def auto_light_all_enabled():
    """是否在没有显式 players 时, 把"当前同关卡的在线玩家"全部自动点亮(=建好友关系)。

    2026-10-05 起**默认关闭**(config/friend_config.json 的 auto_light_all=false):
    lit_candidates() 会把 UDP 节点里"和我同 levelId 的在线玩家"全算成已点亮, 然后
    ensure_relationship 给他们建双向 friendship + FriendStatus。开了"人人同房"
    (config/room_config.json single_shared_room=true)之后全服玩家都在同一个 levelId,
    于是**任何两个账号只要同时在线就自动互为好友**。

    用户实测(2026-10-05): "两个新建的号为什么会默认为好友, 这是错误的" —— 新号什么都
    没做, 好友列表里就互相出现(friendship 里已经有双向行)。这不是客户端的行为, 就是
    这条"全场点亮"造成的。要回到旧行为: config/friend_config.json 里 auto_light_all 设 true,
    或环境变量 SKY_AUTO_FRIEND_ALL=1。
    """
    cfg = _friend_config()
    if "auto_light_all" in cfg:
        return auto_friend_enabled() and bool(cfg.get("auto_light_all"))
    return auto_friend_enabled() and os.environ.get("SKY_AUTO_FRIEND_ALL", "0") != "0"


def placeholder_users_enabled():
    """客户端点名的"点亮玩家"从没登录过本服时, 是否补一条占位账号。

    实测(2026-10-03 查 monitor_exports/cache/account__get_friends.jsonc):
    客户端点亮某个玩家后, 会拿它的 uuid 每 200~300ms 追问一次 get_friends,
    其中一个 uuid(41f54dba-4778-4b89-9c8a-14d4adb3dc68) 被追问了 945 次,
    而这个 id 从来没在 user 表里出现过 —— 于是 friendship / friend_status 的外键
    (都指向 user.id) 建不起来, 以前这里直接"不凭空造人"跳过, 结果 945 次全是
    set_friends:{} , 点亮结果落不了库, 换地图以后只能重新点火。

    user 表除 id 以外所有列都允许为空, 所以补一行"只填 id + 备注"的占位账号是安全的;
    对方哪天真的用这个 id 登录, user 表里已经有这一行, 登录流程会直接落到它上面。
    关闭: SKY_LIGHT_PLACEHOLDER_USER=0
    """
    cfg = _friend_config()
    if "placeholder_users" in cfg:
        return auto_friend_enabled() and bool(cfg.get("placeholder_users"))
    return auto_friend_enabled() and os.environ.get("SKY_LIGHT_PLACEHOLDER_USER", "1") != "0"


def ensure_placeholder_user(friend_id):
    """给"客户端明确点名、但从未登录过本服"的玩家补一行占位账号。返回是否新建。"""
    from infrastructure.database.models import db, User

    if not placeholder_users_enabled():
        return False
    if not isinstance(friend_id, str) or not _UUID_RE.match(friend_id):
        return False
    if User.query.filter_by(id=friend_id).first() is not None:
        return False

    db.session.add(User(
        id=friend_id,
        # session 有唯一索引, 不能给占位账号留空串, 否则第二个占位账号会撞索引
        session=uuid.uuid4().hex,
        platform="unknown",
        created_at=int(time.time()),
        remark="点亮占位账号(客户端 get_friends 的 players 列表登记)",
        device_name="未登记玩家-%s" % friend_id[:8],
    ))
    try:
        db.session.flush()
    except Exception as e:
        db.session.rollback()
        logger.warning("friend_helper: 建立占位账号 %s 失败: %s", friend_id, e)
        return False
    logger.info("friend_helper: 点亮玩家 %s 从没登录过, 已登记占位账号", friend_id)
    return True


def _fetch_node_players(base):
    """真正去 UDP 节点拉一次 /players。**阻塞**调用, 只允许在 tpool 线程里跑(见下)。"""
    import urllib.request
    with urllib.request.urlopen(base + "/players", timeout=0.6) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def node_players(ttl=2.0):
    """读取 UDP 节点(udp源码, 默认 :8080)的在线玩家列表。

    节点 /players 返回 {"count":n,"players":[{id,uuid,levelId,...}]}。
    节点没起来/超时都返回 [] —— 自动点亮只是"锦上添花", 不能因为节点挂了就让
    接口报错。结果缓存 ttl 秒, 避免 250ms 一次的轮询把节点打爆。

    2026-10-05 (用户实测 "无库新建账号卡接口了" / "一个接口这么慢"):
      这里**绝不能**在当前 greenlet 里直接 urlopen —— 它是**阻塞 I/O**。
      私服是单进程 eventlet: 一个 greenlet 阻塞, 整个 hub 一起停摆 timeout 秒,
      期间**所有玩家**的接口全部卡住(与 websocket_handler._ws_receive_poll 同一类
      问题, 那边已经用轮询/让出修过)。get_friends / get_online_friends /
      get_blocked_friends 每次都会走到这里, 而客户端是 ~250ms 一次轮询:
      实测访问日志里到处是 0.70s / 1.1s / 3.8s 的响应(时间正好等于这里的
      timeout=0.6s + 开销), 全是别的请求在替这次 urlopen 挨卡。
      新号登录要串行发十几个请求(create -> ... -> get_achievements ...),
      每个都被这样拖一下, 20 秒都走不完加载 -> 客户端判定超时 ->
      停在加载界面(用户看到的"卡接口")。
      修法: 丢进 eventlet 的 tpool 线程池执行, hub 在等待期间照常服务其他请求;
      tpool 不可用时退回直连(等价旧行为, 不会报错)。
    """
    now = time.time()
    with _node_lock:
        if now - _node_cache["at"] < ttl:
            return _node_cache["players"]

    players = []
    base = os.environ.get("SKY_UDP_HTTP", "http://127.0.0.1:8080").rstrip("/")
    try:
        try:
            import eventlet
            import eventlet.tpool
            payload = eventlet.tpool.execute(_fetch_node_players, base)
        except Exception as _e_tpool:
            logger.debug("friend_helper: tpool 调用失败, 退回直连: %s", _e_tpool)
            payload = _fetch_node_players(base)
        players = payload.get("players") or []
        if not isinstance(players, list):
            players = []
    except Exception as e:
        logger.debug("friend_helper: 读取 UDP 节点在线列表失败: %s", e)
        players = []

    with _node_lock:
        _node_cache["at"] = time.time()
        _node_cache["players"] = players
    return players


def lit_candidates(user_id, players=None, same_level_only=True):
    """返回"应该与 user_id 互为点亮关系"的 uuid 列表(去重, 不含自己)。

    - 显式传入的 players(get_friends 请求体里的那批)一定会保留;
    - 另外把 UDP 节点里"和我在同一个 levelId"的在线玩家也加进来(默认点亮全场)。
    """
    ordered = []
    seen = set()

    def add(value):
        if not isinstance(value, str):
            return
        value = value.strip()
        if not value or value == user_id or value in seen:
            return
        seen.add(value)
        ordered.append(value)

    for item in (players if isinstance(players, list) else []):
        add(item)

    if auto_light_all_enabled():
        online = node_players()
        my_level = None
        for player in online:
            if player.get("uuid") == user_id:
                my_level = player.get("levelId")
                break
        for player in online:
            if same_level_only and my_level is not None and player.get("levelId") != my_level:
                continue
            add(player.get("uuid"))

    return ordered


def ensure_relationship(user_id, friend_id, commit=True, allow_placeholder=False,
                        abilities=None, nickname=None):
    """为 user_id <-> friend_id 补齐双向 Friendship + FriendStatus。

    返回 True 表示这次真的写入了。目标玩家在 user 表里不存在时: 默认跳过
    (不会凭空造人); allow_placeholder=True(仅用于客户端在 get_friends 里点名的
    players)则先补一条占位账号, 让"点亮"结果真正落库 —— 详见
    placeholder_users_enabled() 的说明。
    并发下(两个客户端各 250ms 一次)可能撞唯一索引, 这里吞掉并回滚, 下次请求
    再补, 不让接口 500。

    abilities: 新建 FriendStatus 时写进 abilities 列的能力 id 列表(默认
    DEFAULT_ABILITIES)。/account/give_candle 送蜡烛加好友时传的是该节点展开后的
    id(如 [1,2,9,10,19]), 因为客户端好友树就认这份 —— 详见 give_candle.py。
    已经存在的行不会被改写(合并由调用方负责)。

    nickname: 新建 FriendStatus 时的初始昵称。默认 ""(留空, 由客户端自己命名) ——
      加好友(/account/give_candle)和点亮都走这条: 用户明确要求"不要固定好友名, 由客户端
      确定"(见 give_candle.py 头注释), 而且两台测试机的 device_name 一模一样
      ("READBOY Readboy_C30"), 拿它当初始名既会让命名框预填成对方设备名, 又会把设备名
      当昵称下发给客户端(用户实测 2026-10-05: "陌生人状态下显示设备名")。
    """
    from infrastructure.database.models import db, User, Friendship, FriendStatus

    if not user_id or not friend_id or user_id == friend_id:
        return False

    try:
        ability_list = [int(a) for a in (DEFAULT_ABILITIES if abilities is None else abilities)]
    except (TypeError, ValueError):
        ability_list = list(DEFAULT_ABILITIES)

    def _load_users():
        return {row.id: row for row in User.query.filter(User.id.in_([user_id, friend_id])).all()}

    users = _load_users()
    if friend_id not in users and allow_placeholder and ensure_placeholder_user(friend_id):
        users = _load_users()
    if user_id not in users or friend_id not in users:
        return False

    now_ts = int(time.time())
    changed = False

    for owner_id, other_id in ((user_id, friend_id), (friend_id, user_id)):
        other = users[other_id]

        friendship = Friendship.query.filter_by(user_id=owner_id, friend_id=other_id).first()
        if friendship is None:
            db.session.add(Friendship(user_id=owner_id, friend_id=other_id, status="active"))
            changed = True
        elif friendship.status == "pending":
            friendship.status = "active"
            changed = True
        # **绝不能**把 'blocked' 改回 'active'!
        #
        # 这个函数被两条 250ms 轮询路径反复调用(get_friends / get_friend_statues 的
        # 自动点亮), 老代码写成 `elif friendship.status != "active"` —— 于是
        # /account/set_friend_block 刚把 status 置成 'blocked', 下一次轮询就把它改回
        # 'active'。用户实测(2026-10-05): "一方把另一方拉黑后 对方显示没有拉黑 且
        # 拉黑对方的人不能解除拉黑" —— 屏蔽状态从来活不过一次轮询, 拉黑方的好友列表里
        # 那个人又冒出来(标着未拉黑), 自然也就没有"解除拉黑"这个动作可做。
        # 只有 'pending'(建关系时的中间态)才自动转 'active'; 'blocked' 保持不动,
        # 解屏蔽只认 /account/set_friend_block 里 blocked=false 那一次调用。
        # 'removed'(主控删好友留下的墓碑行, 见 set_friendship_state)同理保持不动:
        # 行只要存在就走不到上面 `friendship is None` 那一支, 所以主控删掉的关系
        # 不会在下一次 250ms 轮询里被"自动点亮"重新加回来。

        status = FriendStatus.query.filter_by(user_id=owner_id, friend_id=other_id).first()
        if status is None:
            # 名字一律留空(由客户端命名), **绝不**拿 device_name 兜底 —— 见
            # build_friend_entry 里的说明(用户实测"陌生人状态下显示设备名")。
            # nickname 参数保留只是给调用方一个显式覆盖的口子。
            initial_name = str(nickname or "")
            db.session.add(FriendStatus(
                user_id=owner_id,
                friend_id=other_id,
                given=0,
                recvd=0,
                nickname=str(initial_name)[:100],
                when_created=now_ts,
                level=DEFAULT_FRIEND_LEVEL,
                abilities=json.dumps(ability_list),
                hints=json.dumps([]),
                outfit=json.dumps({}),
                player_badge_type=0,
                local_soft_deleted=False,
                attitude="0",
            ))
            changed = True

    if not changed:
        return False

    if commit:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning("friend_helper: 建立好友关系失败(%s <-> %s): %s",
                           user_id, friend_id, e)
            return False
    return True


def sync_lit_relationships(user_id, players=None, allow_placeholder=False):
    """把被点亮的玩家批量落库。返回实际建立/修复过关系的 uuid 列表。

    allow_placeholder=True 时, 只对"客户端在 players 里明确点名"的 uuid 补占位账号
    (UDP 节点在线列表里的 id 不做这件事 —— 那批 id 来源不明, 不能凭它建账号)。
    """
    if not auto_friend_enabled():
        return []

    named = set()
    for item in (players if isinstance(players, list) else []):
        if isinstance(item, str) and item.strip():
            named.add(item.strip())

    created = []
    for friend_id in lit_candidates(user_id, players):
        try:
            allow = bool(allow_placeholder and friend_id in named)
            # 点亮**不等于**加好友: 这里建的关系刻意不带任何能力(abilities=[]),
            # 名字也留空由客户端自己定 —— 和 give_candle 第 1 步的写法一致。
            # 老代码不传 abilities, ensure_relationship 就按 DEFAULT_ABILITIES 填
            # [1,2,9,10,19], 于是"点亮"白送了整套好友树(含加好友节点 1), 客户端
            # 就把对方画成好友 —— 用户实测"两个新建的号默认为好友, 这是错误的"。
            # give_candle 里早就为这件事打过补丁("自动点亮白送的能力不算数"),
            # 这里从源头去掉。真要加好友: 送蜡烛 / 主控面板的"加好友"。
            if ensure_relationship(user_id, friend_id, allow_placeholder=allow,
                                   abilities=(None if lit_grants_abilities_enabled() else []),
                                   nickname=""):
                created.append(friend_id)
        except Exception as e:
            logger.warning("friend_helper: 自动点亮 %s 失败: %s", friend_id, e)
    if created:
        logger.info("friend_helper: 用户 %s 自动点亮/补建好友关系 %s", user_id, created)
    return created


# ---------------- 主控(中控面板)直接改好友关系 ----------------
# 游戏里"加好友"走送蜡烛(/account/give_candle), 游戏里根本没有"删好友"接口;
# 主控面板要能自由加/删两个账号之间的好友(见 admin/__init__.py 的 /admin/api/*),
# 所以这里给一组直接改库的入口。
FRIEND_STATE_ADD = "add"
FRIEND_STATE_REMOVE = "remove"
FRIEND_STATE_RESTORE = "restore"


def set_friendship_state(user_id, friend_id, state=FRIEND_STATE_ADD, nickname="", commit=True):
    """建立/断开两个账号之间的好友关系 —— **双向**一起改。返回 (ok, detail)。

    为什么必须双向: 客户端的好友列表只认自己那一行 friendship(user_id=我)。
    "删除账号一与账号二的好友"如果只删账号一那一行, 账号二的好友列表里还挂着账号一,
    游戏里表现就是"删了但对方还看得见我"。所以 add / remove 都写两行。

    为什么删除写 'removed' 墓碑行而不是 DELETE:
      friendship 行**不存在**时, ensure_relationship(get_friends / get_friend_statues
      每 250ms 一次的自动点亮)会立刻把关系重新建出来 —— 直接 DELETE 的话, 只要
      双方有一方在线, 删掉的好友几秒后自己就长回来了。写一行 status='removed' 的行,
      那个分支就进不去(它只在行不存在时建行), 删除才真的稳。反悔用 state='restore'。

    state:
      'add' / 'restore' -> 双向 Friendship.status='active'(缺行补建) + FriendStatus 补齐;
      'remove'          -> 双向 Friendship.status='removed'(缺行也补一条墓碑行)。
    nickname: 新建 FriendStatus 时的初始昵称。默认 ""=留空, 由客户端自己命名
      (与 /account/give_candle 一致, 见 ensure_relationship 里的说明)。
    """
    from infrastructure.database.models import db, User, Friendship, FriendStatus

    user_id = str(user_id or "").strip()
    friend_id = str(friend_id or "").strip()
    if not user_id or not friend_id:
        return False, "缺少账号 id"
    if user_id == friend_id:
        return False, "同一个账号: 不能和自己建立好友关系"

    state = str(state or FRIEND_STATE_ADD).strip().lower()
    if state not in (FRIEND_STATE_ADD, FRIEND_STATE_REMOVE, FRIEND_STATE_RESTORE):
        return False, "未知操作: %s" % state

    existing = {row.id for row in User.query.filter(User.id.in_([user_id, friend_id])).all()}
    missing = [uid for uid in (user_id, friend_id) if uid not in existing]
    if missing:
        return False, "账号不存在: %s" % ", ".join(missing)

    pairs = ((user_id, friend_id), (friend_id, user_id))
    changed = 0

    if state == FRIEND_STATE_REMOVE:
        try:
            for owner, other in pairs:
                row = Friendship.query.filter_by(user_id=owner, friend_id=other).first()
                if row is None:
                    db.session.add(Friendship(user_id=owner, friend_id=other,
                                              status="removed"))
                    changed += 1
                elif (row.status or "") != "removed":
                    row.status = "removed"
                    changed += 1
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            return False, "删除失败: %s" % exc
        # friend_status.local_soft_deleted 跟着一起置位: 它是回应给客户端的
        # soft_deleted 标志的落库镜像(见 build_friend_entry 的 soft_deleted 参数),
        # 这样以后光看库就知道这段关系是"主控删掉的"。
        try:
            for owner, other in pairs:
                fs = FriendStatus.query.filter_by(user_id=owner, friend_id=other).first()
                if fs is not None:
                    if not fs.local_soft_deleted:
                        fs.local_soft_deleted = True
                        changed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("friend_helper: 置 local_soft_deleted 失败: %s", exc)
        detail = "双向置为已删除(改动 %d 行)" % changed
    else:
        # 先让 ensure_relationship 把缺的行 / FriendStatus 补齐(它不会动 'blocked',
        # 也不会动 'removed' —— 状态由下面的循环统一收口)。
        try:
            ensure_relationship(user_id, friend_id, commit=False, nickname=nickname)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            return False, "补齐好友行失败: %s" % exc
        try:
            for owner, other in pairs:
                row = Friendship.query.filter_by(user_id=owner, friend_id=other).first()
                if row is None:
                    db.session.add(Friendship(user_id=owner, friend_id=other,
                                              status="active"))
                    changed += 1
                elif (row.status or "") != "active":
                    # 'removed'(墓碑)和 'blocked'(拉黑)都清掉: 主控明确要求
                    # "加为好友", 那就以主控的意图为准。
                    row.status = "active"
                    changed += 1
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            return False, "写入失败: %s" % exc
        # 恢复/新加时清掉落库的 soft_deleted 镜像, 与回给客户端的标志保持一致。
        try:
            for owner, other in pairs:
                fs = FriendStatus.query.filter_by(user_id=owner, friend_id=other).first()
                if fs is not None and fs.local_soft_deleted:
                    fs.local_soft_deleted = False
                    changed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("friend_helper: 清 local_soft_deleted 失败: %s", exc)
        detail = "双向置为好友(改动 %d 行)" % changed

    if commit:
        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("friend_helper: set_friendship_state 提交失败(%s <-> %s, %s): %s",
                           user_id, friend_id, state, exc)
            return False, "写库失败: %s" % exc
    logger.info("friend_helper: 主控改好友关系 %s <-> %s -> %s (%s)",
                user_id, friend_id, state, detail)
    return True, detail


def block_flags(user_id, friend_id, friendship=None):
    """返回 (blocked, local_blocked) —— 客户端好友条目的 +691 / +692 两个标志位。

    反编译 libBootloader0200.so(sub_7C0890, line 1336396/1336397):
        *(a1 + 691) = sub_7BCD20(a2, "blocked", 0) & 1;
        *(a1 + 692) = sub_7BCD20(a2, "local_blocked", 0) & 1;
    好友树只在 `!rec || (!rec[691] && !rec[692])` 时才建节点(line 1336737 /
    1491477), 拉黑按钮的文案也只看 691(sub_8B1AE0 line 1551865: v4[691]^1 决定
    "relationship_block" 还是 "relationship_unblock")。

    语义(与服务端 friendship.status 的用法对齐):
      blocked        —— **我**拉黑了对方 (自己的那一行 friendship.status=='blocked');
      local_blocked  —— **对方**拉黑了我 (反方向那一行 status=='blocked')。

    这就是用户说的"一方把另一方拉黑后 对方显示没有拉黑"缺的那一位: 被拉黑的一方
    拿到的条目里从来不带 692, 客户端默认 0 -> 他那边看起来一切正常(树照建、还能
    互动)。
    """
    from infrastructure.database.models import Friendship

    if friendship is None:
        friendship = Friendship.query.filter_by(user_id=user_id, friend_id=friend_id).first()
    blocked = bool(friendship is not None and (friendship.status or "") == "blocked")

    reverse = Friendship.query.filter_by(user_id=friend_id, friend_id=user_id).first()
    local_blocked = bool(reverse is not None and (reverse.status or "") == "blocked")
    return blocked, local_blocked


# ── friends_updated: 让客户端**立刻**重拉好友条目 ──────────────────────────────
#
# 为什么需要(用户反馈 "被拉黑的仍然无反应", 2026-10-05):
#   服务端从上一个版本起就已经把 blocked/local_blocked 写进 get_friends /
#   get_friend_statues 的条目里了(见 block_flags), 但客户端只有**重新请求**这两个
#   接口才会看到; 而它的好友数据在进游戏时拉过一遍之后就缓存住了(好友 barn), 没人
#   通知它的话不会主动重拉 —— 于是被拉黑的一方界面上"一切照旧"(树照建、还能互动)。
#   官方服务端的做法就是在关系变化时推一条 friends_updated。
#
# 反编译证据(libBootloader0337.so.c, 待发消息分发):
#   line 2447442  if ( sub_D26668(&v200, "friends_updated") )  goto LABEL_258;
# LABEL_258 会作废好友缓存(好友 barn +1704/+1696)并重新拉取 —— **不用带任何字段**,
# 只要待发消息里的 "type" 是 friends_updated 就会被命中。
# /account/get_pending_messages 会把队列里每条消息的 type 原样放进
# set_recvd_messages[].type(见该文件里的 recvd_messages.append), 所以这里只要把
# {"type": "friends_updated"} 塞进它用的那个全局队列即可。
#
# 风险与开关: 这是往客户端消息管道里塞一条私服以前从没发过的消息类型, 万一某些版本
# 对未知 type 有断言, 需要能**立刻**关掉 ——
#   环境变量 SKY_PUSH_FRIENDS_UPDATED=0, 或
#   config/room_config.json 里 "push_friends_updated": false(每次读, 不用重启)。
def friends_updated_push_enabled():
    if os.environ.get("SKY_PUSH_FRIENDS_UPDATED", "1") == "0":
        return False
    try:
        cfg = os.path.join(CONFIG_DIR, "room_config.json")
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return bool(data.get("push_friends_updated", True))
    except Exception:
        return True


def _pending_queue():
    """取出 (待发消息队列 dict, 锁) —— 与 /account/get_pending_messages 用的是同一个。

    优先 websocket_handler 的全局队列(和 get_pending_messages._shared_queue 一致);
    拿不到时再在 sys.modules 里找任何暴露了 _shared_queue() 的模块, 最后退回空实现。
    返回 (None, None) 表示当前没有可用队列(消息会丢, 但绝不影响调用方的业务结果)。
    """
    try:
        import infrastructure.transport.websocket_handler as _wh
        q = getattr(_wh, "pending_messages", None)
        if isinstance(q, dict):
            return q, getattr(_wh, "_pending_messages_lock", None)
    except Exception:
        pass

    import sys
    for mod in list(sys.modules.values()):
        fn = getattr(mod, "_shared_queue", None)
        if not callable(fn):
            continue
        try:
            q, lock = fn()
        except Exception:
            continue
        if isinstance(q, dict):
            return q, lock
    return None, None


def push_friends_updated(*user_ids):
    """给这些用户各压一条 friends_updated 待发消息。返回是否至少压成功一条。"""
    if not friends_updated_push_enabled():
        return False

    queue, lock = _pending_queue()
    if not isinstance(queue, dict):
        logger.debug("friend_helper: 没有可用的待发消息队列, 跳过 friends_updated")
        return False

    now_ms = int(time.time() * 1000)
    pushed = False
    for user_id in user_ids:
        if not user_id:
            continue
        acquired = False
        try:
            if lock is not None:
                lock.acquire()
                acquired = True
        except Exception:
            pass
        try:
            queue.setdefault(user_id, []).append({
                "type": "friends_updated",
                "sender_id": "server",
                "msg_id": uuid.uuid4().hex,
                "msg": "",
                "ch": "system",
                "timestamp": now_ms,
            })
            pushed = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("friend_helper: 压 friends_updated 失败(%s): %s", user_id, exc)
        finally:
            if acquired:
                try:
                    lock.release()
                except Exception:
                    pass
    if pushed:
        logger.info("friend_helper: 推送 friends_updated -> %s", ", ".join(
            str(u) for u in user_ids if u))
    return pushed


def notify_block_changed(user_id, friend_id):
    """拉黑/解除拉黑后通知双方客户端重拉好友条目(见上面的长注释)。"""
    try:
        return push_friends_updated(user_id, friend_id)
    except Exception as exc:  # noqa: BLE001 - 推送失败不允许影响屏蔽结果
        logger.warning("friend_helper: notify_block_changed 失败: %s", exc)
        return False


def friend_outfit(outfit):
    """把 UserOutfit 行转成客户端好友树用的 outfit 结构。"""
    if not outfit:
        return None
    return {
        "arms": {"id": outfit.arms_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "attitude": outfit.attitude or "0",
        "body": {"id": outfit.body_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "face": {"id": outfit.face_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "feet": {"id": outfit.feet_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "hair": {"id": outfit.hair_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "hat": {"id": outfit.hat_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "height": outfit.height or 1.0,
        "horn": {"id": outfit.horn_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "mask": {"id": outfit.mask_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "neck": {"id": outfit.neck_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "prop": {"id": outfit.prop_id or 0, "mask": 0, "pat": 0, "tex": 0},
        "refreshversion": outfit.refreshversion or 0,
        "scale": outfit.scale or 0.0,
        "seed": outfit.seed or 0,
        "voice": outfit.voice or 0,
        "wing": {"id": outfit.wing_id or 0, "mask": 0, "pat": 0, "tex": 0},
    }


def build_friend_entry(user_id, friend_id, friendship=None, friend_status=None, outfit=None,
                       online_ids=None, blocked=None, local_blocked=None,
                       soft_deleted=False):
    """构造 set_friends / 好友树里单个好友的字典。

    即使库里没有 FriendStatus(刚自动点亮、还没来得及补), 也返回一份**结构完整**的
    默认值 —— 客户端拿不到字段会一直重试, 这就是 250ms 轮询风暴的来源。

    soft_deleted: 这段关系已被主控删除(墓碑行 status='removed', 见
      set_friendship_state)。客户端点名要资料时**必须照回**(不回它就无限重试),
      但要带上 0.33.7 条目解析器的 +1237/+1238 两个 soft_deleted 位, 告诉客户端
      "这段关系已经没了", 否则删除后客户端列表里那个人还在。
    """
    from infrastructure.database.models import User

    friend_user = User.query.filter_by(id=friend_id).first()
    if not friend_user:
        return None

    # 名字: 库里没起过名就回**空串**。
    # **绝不能**再拿 device_name / friend_id[:8] 兜底 —— 老代码那样写, 于是
    # "陌生人/刚点亮还没命名"的条目里直接把对方的设备名当昵称下发, 用户实测
    # (2026-10-05 20:43:40 的抓包, nickname 就是 "READBOY Readboy_C30"):
    #   "陌生人状态下显示设备名" —— 既是隐私问题, 又让客户端以为这段关系已经有
    #   名字了(它就不会再弹命名框)。官方(用户抓包参考 sky_cn_api_reference)
    #   没名字的玩家 nickname 就是空值; 客户端拿不到名字会自己显示"旅人/未命名",
    #   给完蜡烛才由 /account/set_friend_name 写进来。
    nickname = ""
    if friend_status is not None and friend_status.nickname:
        nickname = str(friend_status.nickname)

    # 能力数组: 有 FriendStatus 就照库里的回(点亮出来的行刻意是 abilities=[],
    # 见 sync_lit_relationships); **库里根本没有这一行时也必须是 []**, 不能拿
    # DEFAULT_ABILITIES 兜底!
    # 老代码在 friend_status is None 时回 [1,2,9,10,19] —— 那是"加好友"节点的
    # 整套好友树: get_friends 的"点名兜底"(客户端点亮谁就问谁)合成的条目于是被
    # 客户端画成一个**完整好友**(带好友树), 用户实测"两个新建的号默认为好友,
    # 这是错误的"/"让用户自己点亮加好友"。要点亮得到好友树只有一条路:
    # 游戏里送蜡烛(/account/give_candle -> friend_helper.grant_abilities)。
    abilities = []
    if friend_status is not None and friend_status.abilities:
        try:
            parsed = json.loads(friend_status.abilities)
            if isinstance(parsed, list):
                abilities = [int(a) for a in parsed]
        except Exception:
            pass

    # hints 和 abilities 一样, 客户端的条目解析器(sub_7C0890)是按 **int 数组** 读的,
    # 读不到 / 类型不对就整条合并失败。以前这里只回 abilities 不回 hints, 于是另一侧
    # 永远拿不到"对方刚解锁、还没被确认"的节点。补上, 与 get_friend_statues 保持一致。
    hints = parse_int_list(friend_status.hints) if friend_status is not None else []

    is_online = False
    if online_ids:
        is_online = friend_id in online_ids

    # 拉黑标志(见 block_flags): 必须显式回, 缺键 = 客户端按 0 处理 = "没被拉黑"。
    # 调用方(get_friends / get_friend_statues)是每 200~300ms 轮询一次的接口, 所以
    # 允许把批量查好的结果传进来, 避免每个好友再打两次 SELECT。
    if blocked is None or local_blocked is None:
        try:
            _blk, _lblk = block_flags(user_id, friend_id, friendship)
        except Exception:
            _blk, _lblk = False, False
        if blocked is None:
            blocked = _blk
        if local_blocked is None:
            local_blocked = _lblk

    entry = {
        "friend_id": friend_id,
        "user_id": friend_id,
        "nickname": nickname,
        "platform": friend_user.platform or "unknown",
        "device": friend_user.device or "",
        "is_online": bool(is_online),
        "last_seen": None,
        "created_at": friendship.created_at.isoformat() if friendship and friendship.created_at else None,
        "relationship": "friend",
        "status": "active",
        "given": int(friend_status.given) if friend_status and friend_status.given else 0,
        "recvd": int(friend_status.recvd) if friend_status and friend_status.recvd else 0,
        "level": int(friend_status.level) if friend_status and friend_status.level else DEFAULT_FRIEND_LEVEL,
        "when_created": int(friend_status.when_created) if friend_status and friend_status.when_created else int(time.time()),
        "player_badge_type": int(friend_status.player_badge_type) if friend_status and friend_status.player_badge_type else 0,
        "attitude": friend_status.attitude or "0" if friend_status else "0",
        "abilities": abilities,
        "hints": hints,
        # 主控删好友(墓碑行)时三个键一起给: 0.33.7 的条目解析器读 +1237 "soft_deleted"、
        # +1238 "local_soft_deleted"(libBootloader0337.so.c line 2914876~2914889),
        # "they_soft_deleted" 是本模块早先自己起的别名(老客户端/旧代码在用), 三个同值,
        # 反正客户端是按名字取键、取不到就用默认 0。
        "soft_deleted": bool(soft_deleted),
        "they_soft_deleted": bool(soft_deleted),
        "local_soft_deleted": bool(soft_deleted),
        "blocked": blocked,
        "local_blocked": local_blocked,
        # 0.33 客户端的好友条目解析器(libBootloader0337.so.c line 2914883/2914884)是:
        #   *(a1 + 1236)  = sub_165F4F0(a2, "local_blocked", 0) & 1;
        #   *(a1 + 1236) |= sub_165F4F0(a2, "they_blocked", 0) & 1;
        # 即"对方拉黑了我"这一位同时认 local_blocked(0.22 的 a1+692)与 they_blocked(0.33)
        # 两个键(按位或)。本模块算出的 local_blocked 语义正是"对方拉黑了我", 所以两个键
        # 都给上: 老客户端只认 local_blocked, 新客户端两个都认 —— 缺 they_blocked 时新
        # 客户端仍靠 local_blocked 命中, 但显式补上更贴近官方响应形状。
        "they_blocked": bool(local_blocked),
    }

    formatted = friend_outfit(outfit)
    if formatted:
        entry["outfit"] = formatted
        entry["last_seen_outfit"] = formatted
    return entry


def parse_int_list(text):
    """friend_status.abilities / hints 列(TEXT, 存的是 JSON 数组) -> [int]。

    库里是字符串 "[1, 2, 9]"; 官方响应里是 int 数组 [1, 2, 9]。直接把列原样塞进
    响应 = 客户端按数组解析失败(实测直接崩)。
    """
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def is_added_friend(friend_status):
    """这段关系是**真的加过好友**吗? —— "只是点亮"不算。

    判定依据(三条满足一条即可):
      1. abilities 里有 **id 1**。1 = "nickname" 节点, 就是客户端好友树上那个
         "加好友"根节点: 官方 static_responses/account__set_friend_name.json /
         account__remove_relationship_hint.json 里刚加完好友那条 update_friends
         的 abilities 就是 [1,2,9,10,19] —— 带 1。而"点亮"登记出来的行刻意是
         abilities=[](见 sync_lit_relationships), 绝不含 1。
      2. given > 0: 我送过蜡烛。
      3. recvd > 0: 对方送过我蜡烛。

    为什么要这个判定: 好友列表/好友树/星座页三个地方以前是**只看库里有没有这一行**
    就往下发的, 于是"点亮"出来的行(client.get_friends 里 players 点名的同关卡玩家,
    见 sync_lit_relationships)也被当成好友条目下发 —— 客户端就把它画成好友,
    用户实测(2026-10-05)两连问:
      "两个新建的号为什么会默认为好友 这是错误的"
      "为什么陌生人状态下会出现设置备注的按钮"
    (第二问的直接来源: get_friend_statues 只按 user_id 捞 FriendStatus, 不看关系
     是不是真加过; 客户端拿到条目后就给了"设置备注", 点下去就是 set_friend_name。)
    官方行为: 陌生人(没换过蜡烛)在 get_friends/get_friend_constellation 里根本
    不出现 —— 名字都没有, 更没有备注按钮。
    """
    if friend_status is None:
        return False
    if 1 in parse_int_list(friend_status.abilities):
        return True
    try:
        if int(friend_status.given or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    try:
        if int(friend_status.recvd or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return False


def count_added_friends(user_id):
    """好友数 —— 必须和好友树/列表**下发出去的那批条目**一一对应。

    判定用的就是 get_friend_statues 里那套筛选(见那里的注释):
      * 主控删掉的墓碑行(status='removed')排除;
      * is_added_friend 的算好友; 拉黑相关的条目也保留(拉黑标志要能显示)。
    "点亮"登记出来的行(status='active' 但 abilities=[])不算 —— 以前
    give_candle 的 set_friend_count / get_online_friends 的 total_friends 是直接
    `Friendship(status='active').count()`, 于是**默默点亮过的陌生人也进了好友数**:
    客户端拿到的好友数不为 0、好友树却一个条目都没有。
    """
    try:
        from infrastructure.database.models import Friendship, FriendStatus

        removed_ids = {row.friend_id for row in Friendship.query.filter_by(
            user_id=user_id, status="removed").all()}
        removed_ids |= {row.user_id for row in Friendship.query.filter_by(
            friend_id=user_id, status="removed").all()}

        blocked_by_me = {row.friend_id for row in Friendship.query.filter_by(
            user_id=user_id, status="blocked").all()}
        blocked_me = {row.user_id for row in Friendship.query.filter_by(
            friend_id=user_id, status="blocked").all()}

        rows = FriendStatus.query.filter_by(user_id=user_id).all()
        return sum(
            1 for row in rows
            if row.friend_id not in removed_ids
            and (is_added_friend(row)
                 or row.friend_id in blocked_by_me
                 or row.friend_id in blocked_me)
        )
    except Exception:
        # 计数只是回给客户端的展示值, 出错时不要连累主流程(调用方各有兜底)。
        return None


def grant_abilities(friend_status, ability_ids):
    """把能力 id 并进 friend_status.abilities, 返回**这次新增**的 id 列表。

    好友树上的节点是双向的: 谁送的蜡烛, 节点对两个人一起亮。所以调用方
    (/account/give_candle) 要对"自己那一行"和"对方那一行"各调一次。

    hints 只追加这次新增的 id, 不再把整份 abilities 回写:
      hints 是"客户端还没确认过的新节点"标记, 客户端确认完会调
      /account/remove_relationship_hint 把它去掉。老代码每次送蜡烛都把
      hints 覆盖成整份 abilities, 于是客户端已经确认掉的 hint 1(加好友/命名)
      下一根蜡烛又被下发一次 —— 客户端就又把命名框弹出来一次, 实测
      "房员输入名字后没被储存, 需要不断重新备注"(名字其实存进去了,
      只是命名框被反复弹出来, 见 monitor_exports 里 08:59:03 确认 hint 1
      -> 08:59:31 送蜡烛把 hint 1 又塞回来 -> 08:59:38 又确认 hint 1)。
    """
    if friend_status is None:
        return []

    incoming = []
    for value in (ability_ids or []):
        try:
            incoming.append(int(value))
        except (TypeError, ValueError):
            continue
    if not incoming:
        return []

    current = parse_int_list(friend_status.abilities)
    new_ids = sorted(set(incoming) - set(current))
    friend_status.abilities = json.dumps(sorted(set(current) | set(incoming)))
    if new_ids:
        friend_status.hints = json.dumps(
            sorted(set(parse_int_list(friend_status.hints)) | set(new_ids))
        )
    return new_ids


def clear_hint(friend_status, hint_id, commit=False):
    """把 hint_id 从 friend_status.hints 里去掉, 返回它原来在不在。

    给"客户端已经确认掉的节点"用: 命名(set_friend_name)要消掉 NAMING_HINT_ID,
    免得好友树上那个"起名字"的提示一直亮着、命名框反复弹(见 NAMING_HINT_ID 注释)。
    """
    if friend_status is None:
        return False
    hint_id = int(hint_id)
    hints = parse_int_list(friend_status.hints)
    if hint_id not in hints:
        return False
    friend_status.hints = json.dumps([h for h in hints if h != hint_id])
    if commit:
        import infrastructure.database.models as models
        models.db.session.commit()
    return True


def created_at_iso(when_created):
    """friend_status.when_created(unix 秒) -> "2024-07-16T15:03:44.803Z"。

    官方 /account/set_friend_name 与 /account/remove_relationship_hint 的
    update_friends[] 里这个字段叫 created_at(ISO8601 字符串), 不是 give_candle
    那份里的 when_created(unix 秒)。
    """
    import datetime
    try:
        dt = datetime.datetime.fromtimestamp(int(when_created), datetime.timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        dt = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
    dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def compact_friend_entry(friend_status, nickname=False):
    """/account/set_friend_name 与 /account/remove_relationship_hint 的
    update_friends[] 条目, 逐字段对齐官方抓包:

      static_responses/account__set_friend_name.json:
        {"abilities":[1,2,9,10,19], "created_at":"...Z", "friend_id":"...",
         "hints":[2,9,10,19], "last_seen_outfit":null, "nickname":"UWAAA",
         "soft_deleted":null, "they_soft_deleted":null}
      static_responses/account__remove_relationship_hint.json: 同上, 少一个 nickname。

    注意这套键名和 give_candle 那份(given/level/recvd/unlinked_star_sku/
    when_created/outfit)不是一套; 老代码把库里 TEXT 原样回过去(abilities/hints 是
    字符串、outfit 是字符串 "{}"、when_created 是整数), 客户端按 int 数组解析时直接
    崩溃 —— 实测"房员输入完名字点确定就崩, 而且节点没被解锁, 只是多了个名字"。
    """
    entry = {
        "abilities": parse_int_list(friend_status.abilities),
        "created_at": created_at_iso(friend_status.when_created),
        "friend_id": friend_status.friend_id,
        "hints": parse_int_list(friend_status.hints),
        "last_seen_outfit": None,
        "soft_deleted": None,
        "they_soft_deleted": None,
    }
    if nickname:
        entry["nickname"] = friend_status.nickname or ""
    return entry