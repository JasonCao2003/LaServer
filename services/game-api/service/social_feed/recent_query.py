# -*- coding: utf-8 -*-
"""最近一次 feed 池查询的记忆 —— 专门给 get_by_ids 的"全 0 id"兜底用。

问题(实测抓包, monitor_exports/cache/service__social-feed__api__v1__get_by_ids.jsonc,
有记录的 54 次请求**全部**是全 0 id):
    客户端在世界里把别的玩家的留言小船/留影蜡烛建成"id 全 0"的物件, 渲染/点击时
    发 ["00000000-0000-0000-0000-000000000000", ...] 来查详情 —— 服务端按 id 只能
    回空表, 客户端拿不到 feeds 就不渲染/点了没内容。表现就是:
      "创建留影留言的人能看见自己的, 别的账号看不到"(自己那条是本地建的, 不需要回查)。

它不知道 id, 但**知道条数**, 而且是紧接着一次池查询之后问的(实测:
  get_curated_feeds(MessageBoat) hits=4  ->  get_by_ids 4 个全 0
  get_user_feeds(RecordingCandle) hits=1 ->  get_by_ids 1 个全 0)。
所以这里把"这个用户最近查了哪个池、各回了多少条"记下来, 全 0 请求就按条数就近匹配,
回那一池最新的 N 条(**不限作者** —— 这才是"别的账号也能看到"的关键)。

纯内存 dict + 锁, 进程重启即清空; 不落盘、不动 users.db 表结构。
"""
import threading
import time

# 记忆有效期(秒): 客户端从"拿到 feeds"到"回查详情"只隔 1~2 秒, 10 分钟绰绰有余。
TTL = 600.0
# 每个用户最多记多少条池查询(客户端会来回在纸船/留影/共享空间之间切)。
MAX_ENTRIES = 8
# 最多记多少个用户, 防止无限增长。
MAX_USERS = 512

_mem = {}
_lock = threading.Lock()


def _key(user_id, session):
    return str(user_id or session or "")


def remember(user_id, session, pool_types, pool_names, hits):
    """记一次池查询: 这个用户刚才查了哪些 pool、服务端回了几条。"""
    key = _key(user_id, session)
    if not key:
        return
    entry = {
        "types": [str(x) for x in (pool_types or []) if x],
        "names": [str(x) for x in (pool_names or []) if x],
        "hits": int(hits or 0),
        "ts": time.time(),
    }
    if not entry["types"] and not entry["names"]:
        return
    try:
        with _lock:
            now = time.time()
            for k in [k for k, v in _mem.items()
                      if not v or now - v[-1]["ts"] > TTL]:
                _mem.pop(k, None)
            entries = [e for e in _mem.get(key, []) if now - e["ts"] <= TTL]
            entries.append(entry)
            _mem[key] = entries[-MAX_ENTRIES:]
            while len(_mem) > MAX_USERS:
                oldest = min(_mem, key=lambda k: _mem[k][-1]["ts"])
                _mem.pop(oldest, None)
    except Exception:
        pass


def recall(user_id, session, want_count=0):
    """回 (pool_types, pool_names) —— 优先挑"条数对得上"的那次查询。

    条数对不上(比如客户端换了关)就退回最近一次; 什么都没有就回 (None, None),
    调用方自己决定怎么办。
    """
    key = _key(user_id, session)
    if not key:
        return None, None
    try:
        with _lock:
            entries = _mem.get(key) or []
    except Exception:
        return None, None
    if not entries:
        return None, None
    now = time.time()
    live = [e for e in entries if now - e["ts"] <= TTL]
    if not live:
        return None, None
    want = int(want_count or 0)
    for e in reversed(live):
        if want and e["hits"] == want:
            return e["types"], e["names"]
    e = live[-1]
    return e["types"], e["names"]


def forget_all():
    """测试/调试用: 清空全部记忆。"""
    with _lock:
        _mem.clear()