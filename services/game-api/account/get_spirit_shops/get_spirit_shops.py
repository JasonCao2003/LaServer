import os
import re
import json
from flask import jsonify, Response, request

# 兑换树(先祖复刻)静态库文件: 完整兑换树(当前 2451 节点 / 228 个先祖)。
# 当版本分流没把请求短路到接口库(例如客户端版本无法解析),
# 请求会落到本动态路由。旧逻辑只返回 {"shops": []}, 而游戏客户端期望的键是
# "spirit_shops"(含 total_count/version), 键名不符导致客户端拿到空树 ->
# 点击兑换树后既不出现先祖也不出现兑换树, 没有任何反应。
# 修复: 动态路由回退到静态库兑换树(键名正确), 保证任意版本点击都能出树。
#
# 按版本适配体积(见 _VERSION_TREE_LIMITS):
#   2451 节点全量发出去, 实测 >= 0.26.0 没问题, < 0.26.0 直接闪退(用户反馈)。
#   历史锚点: 早期给所有客户端发 500 节点(123KB)不闪退; 初始备份里的
#   config/spirit_shops.json 是 662 节点 / 68 个先祖 / 141KB。故对 < 0.26.0
#   取"节点数上限 660"的一档。
#
# 关键: 低于 0.26.0 的这一档**不是"砍节点"**, 而是"整棵整棵地取先祖" ——
#   库里的先祖顺序 = 上线顺序(先基础先祖 point/wave/...、再 elders、再 questap02..),
#   所以按下限取前 N 个先祖, 每个先祖都拿到**完整子树**, 不会有先祖被削掉半棵树。
#   反面教材(已修): 早期按"全局广度优先"裁到 500, 把 228 个先祖的根先全塞进去,
#   结果 100 个先祖只剩 1 个节点、20 个正好 2 个 —— 用户反馈的
#   "兑换树节点很少, 一个先祖甚至只有两个节点"就是这么来的。
_LIB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "resources", "static_responses", "account__get_spirit_shops.json",
)

# 缓存: (mtime, 解析后的 dict)
_LIB_CACHE = {}

# 老版本分流阈值: 低于本版本的老客户端(<0.20.0)视为"老/弱客户端"。
_OLD_VERSION = (0, 20, 0)

# <0.20.0 老客户端返回什么?
#   True  -> 返回真实兑换树(按 _VERSION_TREE_LIMITS 控制体积) —— 兑换树能打开,
#            完成度不再是 NaN%。【当前默认】
#   False -> 完全照 E:\beifen\Sky初始备份 的旧实现返回 {"shops": []}。
#            (旧行为: 客户端拿到"空树" -> 点兑换树没反应 + 完成度 0/0 = NaN%。)
_OLD_CLIENT_GET_TREE = True

# ===== 分版本的兑换树体积上限 =====
# 表项: (最低客户端版本, 最大节点数)。按版本从高到低匹配, 命中第一条即用。
#   最大节点数 = 0 表示不限制(发完整 2451 节点树)。
#   取树方式见 _limit_tree: 按库中顺序**整棵整棵地取先祖**, 所以每个被返回的先祖
#   子树都是完整的 —— 上限只决定"能显示到第几个先祖", 不决定"每个先祖有几个节点"。
#
# 各档位对应的容量(库里先祖顺序 = 上线顺序):
#      451 节点 -> 前  53 个先祖     500 -> 前 59 个      660 -> 前 75 个
#      700 节点 -> 前  77 个先祖     800 -> 前 86 个     1000 -> 前 102 个
#     1200 节点 -> 前 124 个先祖    1500 -> 前 149 个     2451 -> 全部 228 个
#
# 调法(实测后逐个版本微调):
#   某版本仍闪退 -> 把该档数字调小;
#   某版本树太少 -> 把该档数字调大(新加一档也行, 例如 ((0,20,0), 900))。
_VERSION_TREE_LIMITS = [
    ((0, 26, 0), 0),     # >= 0.26.0: 完整树。实测全量 2451 节点无问题。
    ((0, 20, 0), 500),   # 0.20.0 ~ 0.25.x: 500 节点(实测该档客户端吃过 497 节点
                         #   完整子树 81354 字节, 登录全程正常)。
    ((0, 10, 0), 250),   # 0.10.x ~ 0.19.x: 250 节点。
    ((0, 0, 0), 120),    # < 0.10.0 (0.7.1 / 0.6.5 等极老客户端): 120 节点。
                         #   实测: 0.7.1 拉 497 节点(81489 字节)后客户端闪退 ——
                         #   服务端日志里该会话到 get_spirit_shops 就断(客户端连拉
                         #   两次树 -> 两次 get_vars -> 重启 -> 再连拉两次树 -> 死)。
                         #   120 节点 ≈ 该年代(2019)真正存在的先祖数量级, 既完整又轻。
]
# 【实测锚点 —— 这一档的数字是"闪退阈值"实测出来的, 别凭感觉调大】
#   658 节点 / 165445 字节 -> 客户端在登录拉取兑换树后立刻崩溃
#      (服务端日志里该会话到 get_spirit_shops 就断, 下一条请求再也没有
#       -> 客户端死在解析/建树这一步)。
#   497 节点 / 126708 字节 -> 0.13.4 那一档客户端能继续往下走(08:44 那次会话
#      拿到 126708 后还继续拉了 wing_buffs/collectibles/status/outfit/
#      find_previous_or_empty); 但 0.7.1 拉 497 节点照样闪退, 故 <0.10.0 单独降到 120。
#   想要先祖更多 -> 一次加 50 节点实测(例: ((0,10,0), 300)), 只要出现
#   "登录后闪退/进游戏闪退/点兑换树闪退"就往回调小; 每档都可能不一样, 实测为准。


def _client_version():
    """从 User-Agent 解析客户端版本元组, 如 0.12.2 -> (0,12,2)。解析失败返回 None。"""
    ua = request.headers.get("User-Agent", "") if request else ""
    m = re.search(r"Sky-[^/\s]+/(\d+)\.(\d+)\.(\d+)", ua)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _load_library_tree():
    """读取兑换树静态库(完整兑换树, 当前 2451 节点 / 228 个先祖), 失败返回 None。
    按 mtime 缓存。"""
    try:
        mtime = os.path.getmtime(_LIB_FILE)
    except OSError:
        return None
    cached = _LIB_CACHE.get("data")
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        with open(_LIB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    nodes = data.get("spirit_shops")
    if not isinstance(nodes, list):
        return None
    # 修正 total_count 与实际节点数一致, 避免高版本客户端因 "声明数>实际数"
    data["spirit_shops_total_count"] = len(nodes)
    _LIB_CACHE["data"] = (mtime, data)
    return data


def _limit_tree(nodes, max_nodes):
    """按 max_nodes 上限取兑换树, **整棵整棵地取先祖**(每个返回的先祖子树都完整)。

    节点: dep=父节点 id(0=根, 即先祖)。

    库里的先祖顺序就是上线顺序(先基础先祖 point/wave/..., 再 elders, 再 questap02..),
    所以"从上往下取到预算用完"等价于"给这个版本返回它那个年代已知的先祖", 并且
    每个先祖的子树都是完整的一棵, 不会被削掉一半。

    旧实现是"全局广度优先裁节点"(先把 228 个根全塞进去, 再一层层补), 结果是
    2451 裁到 500 时 100 个先祖只剩根节点 1 个、20 个正好 2 个 —— 即用户反馈的
    "兑换树节点很少, 一个先祖甚至只有两个节点"。本函数不再有这个问题。

    max_nodes <= 0 或本来就装得下时原样返回。
    """
    if max_nodes <= 0 or len(nodes) <= max_nodes:
        return nodes

    by_id = {}
    children = {}
    roots = []
    for n in nodes:
        by_id[n.get("id")] = n
        dep = n.get("dep", 0)
        if not dep:
            roots.append(n)
        else:
            children.setdefault(dep, []).append(n)

    # 每个节点归属哪个先祖(自底向上走 dep 链, 带记忆化)。
    root_of = {}
    for n in nodes:
        chain = []
        cur = n
        root = None
        while cur is not None:
            cid = cur.get("id")
            if cid in root_of:
                root = root_of[cid]
                break
            chain.append(cid)
            dep = cur.get("dep", 0)
            if not dep:
                root = cid
                break
            cur = by_id.get(dep)
        for cid in chain:
            root_of[cid] = root

    def subtree_size(root_id):
        count = 0
        stack = [root_id]
        while stack:
            x = stack.pop()
            count += 1
            stack.extend(k.get("id") for k in children.get(x, []))
        return count

    # 按顺序整棵整棵地取先祖, 装不下下一个就停在当前(不做半棵)。
    keep = set()
    total = 0
    for r in roots:
        s = subtree_size(r.get("id"))
        if keep and total + s > max_nodes:
            break
        keep.add(r.get("id"))
        total += s

    # 保持库中原始顺序输出(父节点在子节点之前)。
    return [n for n in nodes if root_of.get(n.get("id")) in keep]


def _is_guide(n):
    """向导先祖节点判定: spirit 字段以 'questap' 开头。

    向导先祖(questap02..questap30)是季节/活动的引导先祖, 玩家应能"免费直接
    点击"推进其任务/解锁(teleport/任务/服饰节点全部免费), 而不需要先累计奉献
    季蜡。经核实, 库里所有带 teleport_to 的先祖都是 questap*, 且非向导的纯兑换树
    (动作先祖 hearthands/breakdance、复刻先祖 ap24_family 等)spirit 都不以
    questap 开头 —— 故此判定不会误伤兑换树。
    """
    return str(n.get("spirit", "")).startswith("questap")


def _is_free_clickable(n):
    """向导/任务类节点: 应"直接点击即解锁", 不参与兑换(保持 cst=0)。

    判定: 向导先祖节点(_is_guide), 或 typ == 'quest' (向导任务节点),
    或 ta 以 'zero_trust' 开头 (zero_trust_unlock / zero_trust_quest 即引导节点)。
    这些节点抬价到 1 会让客户端要求花季蜡才能点 -> 向导卡住。
    """
    typ = n.get("typ", "")
    ta = str(n.get("ta", ""))
    return _is_guide(n) or typ == "quest" or ta.startswith("zero_trust")


def _neutralize_free(nodes):
    """把 cst=0 的节点成本抬到 1(需要 1 个货币/季蜡解锁), 防止客户端把"免费(cst=0)"
    节点当成"已认领/已点亮"而不显示 —— 这正是兑换树/任务树空白的根因:
    1000 节点库里 102 个先祖根节点有 95 个 cst=0, 被客户端判为已点亮 -> 不进待点亮
    列表 -> 整棵树空白。抬到 1 后, 先祖显示为"待点亮(花1解锁)", 树正常出现。
    cst>0 的节点保持原价不变。所有版本一致。

    例外(向导任务节点): typ=='quest' 或 ta 以 'zero_trust' 开头的节点保持 cst=0,
    使其"直接点击即解锁"、无需兑换 —— 向导任务流程才能正常推进。
    """
    out = []
    for n in nodes:
        # 向导先祖节点: 强制"免费直接点击"——cst 归 0, 且把季蜡解锁门槛
        # (ta=spirit_unlock_200/400/600 等)降为 zero_trust_unlock, 使节点
        # 无需先累计奉献季蜡即可点亮。teleport/任务/服饰节点一并处理。
        # zero_trust_quest 是向导任务的特殊标记, 本身已是零门槛, 保留不动。
        if _is_guide(n):
            ta = str(n.get("ta", ""))
            if n.get("cst", 0) != 0 or ta.startswith("spirit_unlock"):
                n = dict(n)
                n["cst"] = 0
                if ta.startswith("spirit_unlock"):
                    n["ta"] = "zero_trust_unlock"
            out.append(n)
            continue
        if n.get("cst", None) == 0 and not _is_free_clickable(n):
            n = dict(n)
            n["cst"] = 1
        out.append(n)
    return out


def _tree_node_limit(ver):
    """按客户端版本查兑换树节点上限。0 表示不限制(完整树)。

    ver 为 None(版本解析失败)时按最高档处理(发完整树), 与改动前的行为一致。
    """
    if ver is None:
        return 0
    for floor, limit in _VERSION_TREE_LIMITS:
        if ver >= floor:
            return limit
    return 0


def _paginate(nodes, req):
    """按客户端分页参数 l(limit)/o(offset) 切片。

    客户端分页拉取兑换树(抓包: l=1000, o=0/1000/2000)。必须按 offset 返回对应分页,
    且 total_count 永远是"整棵树的真实总数"(不是本页数量), 否则:
      - 若每页都返回全部节点, 客户端按 offset 合并会出现重复根节点 -> 崩溃;
      - 若 total_count 只报本页数, 客户端会误判已拉完, 拿不到后续分页。
    """
    total = len(nodes)
    try:
        limit = int(req.get("l", total)) if req else total
    except (TypeError, ValueError):
        limit = total
    try:
        offset = int(req.get("o", 0)) if req else 0
    except (TypeError, ValueError):
        offset = 0
    if limit <= 0:
        limit = total
    if offset < 0:
        offset = 0
    page = nodes[offset:offset + limit]
    return page, total


def get_spirit_shops():
    """先祖复刻商店(兑换树)。

    版本适配: 按客户端版本决定发多"大"的树(见 _VERSION_TREE_LIMITS) ——
      >= 0.26.0 : 完整 2451 节点树(实测无问题);
      <  0.26.0 : 上限 660 节点, 按库中顺序整棵整棵地取先祖(前 75 个先祖的完整子树)。
    每个被返回的先祖子树都是完整的, 不存在"一个先祖只有两个节点"。
    自动认领修复: 不论高低版本, 都把 cst=0 免费节点抬到 cst=1, 客户端不再自动认领
    免费门/头发/项链/动作 -> 新号干净(不开全门、不多衣服), 但这些项仍在树里可购买。
    分页: 按客户端 l/o 返回对应分页, total_count 始终为"本次实际下发的整棵树总数"
    (客户端会分页拉取, 必须正确分页否则重复合并崩溃)。
    """
    ver = _client_version()
    is_old_client = (ver is not None and ver < _OLD_VERSION)
    limit = _tree_node_limit(ver)
    req = request.get_json(force=True, silent=True) or {}

    # 版本分流: <0.20.0 老客户端默认也发真实兑换树(_OLD_CLIENT_GET_TREE)。
    # 若要退回 E:\beifen\Sky初始备份 的旧行为(空 {"shops": []}), 把该常量改成 False。
    # 注: "shops" 键是旧实现的错误键名 —— 官方协议(见 that-sky-server 的
    # accountGetSpiritShopsRequest.js serialize())用的就是 spirit_shops /
    # spirit_shops_total_count / spirit_shops_version, 所以这里不再额外发 "shops"
    # 别名(别名会让低端老客户端收到双份树, 体积翻倍, 反而容易 OOM/崩溃)。
    if is_old_client and not _OLD_CLIENT_GET_TREE:
        return jsonify({"shops": []}), 200

    try:
        from helpers.game_control_helper import get_spirit_shops_config
        cfg = get_spirit_shops_config()
        if cfg.get("enabled") and cfg.get("shops"):
            shops = _neutralize_free(cfg["shops"])
            shops = _limit_tree(shops, limit)
            page, total = _paginate(shops, req)
            body = {
                "spirit_shops": page,
                "spirit_shops_total_count": total,
                "spirit_shops_version": 388242,
            }
            return Response(
                response=json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                status=200, mimetype="application/json")
    except Exception:
        pass

    # 回退: 接口库兑换树(正确键名 spirit_shops), 修复"点击无反应/不出树"。
    tree = _load_library_tree()
    if tree is not None:
        nodes = _neutralize_free(tree.get("spirit_shops", []))
        nodes = _limit_tree(nodes, limit)
        page, total = _paginate(nodes, req)
        body = {
            "spirit_shops": page,
            "spirit_shops_total_count": total,
            "spirit_shops_version": tree.get("spirit_shops_version", 0),
        }
        return Response(
            response=json.dumps(body, ensure_ascii=False, separators=(",", ":")),
            status=200, mimetype="application/json")

    # 静态库也读不到时, 退回旧的空响应(保持兼容, 不报错)。
    return jsonify({"spirit_shops": [], "spirit_shops_total_count": 0,
                    "spirit_shops_version": 0}), 200