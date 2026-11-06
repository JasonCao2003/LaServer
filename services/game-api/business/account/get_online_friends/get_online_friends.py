from flask import request, Response
from infrastructure.database.models import db, User, Friendship, FriendStatus, ConstellationFriendPage
import logging
import time
import json
import threading

# 原来这两处 except 里写的是 app.logger.error, 但本模块根本没有 import app ->
# 一旦真出异常就再抛 NameError(接口 500)。换成模块 logger。
logger = logging.getLogger(__name__)

_session_cache = {}
_session_cache_lock = threading.Lock()
_session_cache_ttl = 60
_session_cache_cleanup_counter = 0
_pending_messages_lock = threading.Lock()
pending_messages = {}

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user


def _total_friends(user_id, friendships):
    """好友数(见 response_body 里那段注释): 只数"真加过好友"的那批。

    注意 online_friends 这个数组本身**不过滤**: 它记录的是"我认识/点亮过的玩家在不在线"
    (客户端 sub_7C1540 拿它建在线表, 决定同房间里对方是不是亮着的), 把点亮的陌生人
    从这里剔除会让刚点亮的玩家变回小黑。要动的只是 total_friends 这个展示值。
    """
    try:
        import business.helpers.friend_helper as friend_helper
        count = friend_helper.count_added_friends(user_id)
        if count is not None:
            return int(count)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_online_friends: 统计真实好友数失败: {e}")
    return len(friendships)

def get_online_friends():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session_token = request_data.get('session')
        
        current_user = get_cached_user(user_id, session_token)
        if not current_user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        friendships = Friendship.query.filter_by(user_id=user_id, status='active').all()

        online_friends = []

        # 在线判定: 以 UDP 节点(真正连着 ENet 的玩家)为准。
        # 下面"方法1/2/3"用的 _session_cache / user_online_status 从来没有被写过,
        # 所以线上永远判成离线 —— 好友列表里一个"在线"都没有。
        try:
            import business.helpers.friend_helper as friend_helper
            online_ids = {p.get('uuid') for p in friend_helper.node_players() if p.get('uuid')}
        except Exception:
            online_ids = set()

        for friendship in friendships:
            friend_id = friendship.friend_id

            # 获取好友的用户信息（包括session）
            friend_user = User.query.filter_by(id=friend_id).first()

            if not friend_user:
                continue

            # 检查好友是否有有效的session
            friend_session = friend_user.session

            if not friend_session:
                continue

            # 方法0(最准): UDP 节点里还有这个 uuid -> 真的在线上
            is_online = friend_id in online_ids

            # Online presence is determined by the live UDP registry. A nickname
            # edit, friendship timestamp or queued message cannot prove presence;
            # treating them as online suppresses the next offline-to-online edge.
            if is_online:
                online_friends.append(friend_id)

        # 客户端(libBootloader0.22.0 的 sub_40AE50)对 "online_friends" 的解析:
        #   数组 -> 逐元素判断是不是"字符串"(节点类型 2), 是的话交给 sub_7C1540 当作
        #   uuid 文本直接写进在线好友表(level_id=0); 不是字符串的元素整个丢掉。
        # 以前这里回的是带 nickname/outfit 的字典数组 -> 客户端一个都解析不出来 ->
        # FriendOnlineBarn 恒为空:
        #   * 已经是好友的账号上线, 这边收不到"好友上线"提示;
        #   * 同一个房间里对方被当成没点亮的陌生人(小黑)。
        # 只有之后 give_candle 的响应补发 "update_online_friends"(对象数组, 由
        # sub_7C1408 解析)时才把好友塞进同一个表 —— 这就是"重新拿蜡烛加一次好友
        # 才显示在线/才出现好友树"的原因。
        #
        # "refresh_online_friends": sub_40AE50 只有收到这个键才会安排下一次轮询
        # (sub_7BE084(a4, 30) 取秒数, 30 是客户端自己的默认值)。request_log 实测
        # 一次登录只会请求一次 /account/get_online_friends, 不给这个键, 之后好友
        # 上线/下线就永远不再刷新。
        response_body = {
            "online_friends": online_friends,
            "refresh_online_friends": 30,
            "total_online": len(online_friends),
            # 好友数必须和好友树/列表下发的那批条目一致: `friendships` 里混着"点亮"
            # 登记出来的关系(status='active' 但 abilities=[], 见 friend_helper.
            # sync_lit_relationships), 直接数它会报出"明明一个好友条目都没有, 好友数
            # 却是 2"。统一走 friend_helper.count_added_friends(失败才退回老写法)。
            "total_friends": _total_friends(user_id, friendships),
            "timestamp": int(time.time()),
            "user_id": user_id
        }
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        logger.error(f"Error in get_online_friends: {str(e)}")
        return Response(
            response=json.dumps({"error": "Internal server error", "details": str(e)}),
            status=500,
            mimetype='application/json'
        )