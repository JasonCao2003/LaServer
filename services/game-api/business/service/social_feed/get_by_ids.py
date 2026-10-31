# -*- coding: utf-8 -*-
"""
Service接口: /service/social-feed/api/v1/get_by_ids  (>=0.26.5)

按 id 批量取动态详情。客户端在拿到 create 返回的 social_feed_id 之后, 以及在世界里
需要还原某条留言(小船/冥想神龛留言)时会调它 —— 抓包在 static_responses/
service__social-feed__api__v1__get_by_ids.json:

  {"feeds": [ {comments_enabled, create_at, expire_at, extra_data, message,
               pool_name, pool_type, reactions, social_feed_id, state, tags,
               user_id} ], "result": "ok"}

旧行为: 这个路径**没有注册路由**, 请求落到 JSON 兜底 -> 返回 {} —— 客户端取不到
feeds, 刚放下的留言(纸船)在世界里就还原不出来。

请求体的 id 列表键名客户端各版本不一(social_feed_ids / feed_ids / ids /
social_feed_id), 所以这里不猜死: 先按常见键名取, 取不到就扫描请求体里所有
"字符串列表"字段。id 允许带 "<uuid>:<8hex>" 后缀, 按 ':' 前段查库。
"""
from flask import request, Response
from infrastructure.database.models import User, SocialFeedPost
import json
import logging
import time

from . import extra_store, recent_query
from .get_user_feeds import feed_item, feed_public_id

logger = logging.getLogger(__name__)

# 全 0 uuid: 客户端在世界里把留言物/留影蜡烛建成"没有 id"的物件时, 点击就用它来查详情。
NIL_ID = "00000000-0000-0000-0000-000000000000"
# 全 0 id 的请求回"该用户自己最近放下的留言"(见 _recent_local_feeds 的说明)。
# 想退回"一律回空"的老行为, 把这里改 False 即可。
NIL_ID_FALLBACK = True


def _recent_local_feeds(data, want_count):
    """全 0 id 请求的兜底: 回**该用户自己**最近放下的留言/留影。

    实测(2026-09-13 19:18:42, monitor_exports/cache/...get_by_ids.jsonc):
    客户端把世界里的船/蜡烛建成 "00000000-...-000000000000" 物件, 点击时发
    [全0, 全0] 来查详情 —— 按 id 查只能回空, 玩家看到的就是"点开没内容"。
    客户端既然自己也说不清是哪一个(条数它是知道的), 就按它要的条数回它自己最近放下的,
    至少点击有内容可看; 顺序是最新在前。

    2026-09-13 追加(玩家反馈"所有纸船出现内容是..."): 一开始试过把**请求里的 id
    原样回填**到 social_feed_id。**这个做法已废弃** —— 客户端在世界里问的 id 就是
    全 0 哨兵, 回它一个全 0 的 social_feed_id 等于下发一个官方服务器**永远不会发**
    的非法 id(官方永远是 "<uuid>:<8hex>", 见 create / get_user_feeds 抓包)。
    客户端拿到非法 id 后再去"替换/建物件/入 barn 队列"就会踩到 invalid 分支
    (反编译里有 `Can't add Shared Space To Queue due to invalid shared space id hash`,
    `userId.IsValid()`, `m_barnCount < kMaxSocialFeedBarns` 这类断言/错误日志)。
    所以下面统一回**这条 feed 真实的公开 id**(feed_public_id), 让客户端能把本地
    物件"认领"到一条真 id 上。
    """
    try:
        user_id = data.get("user") or data.get("user_id")
        q = SocialFeedPost.query.filter(SocialFeedPost.expire_at > int(time.time()))
        if user_id:
            q = q.filter(SocialFeedPost.user_id == user_id)
        n = max(1, min(int(want_count or 1), 10))
        return q.order_by(SocialFeedPost.created_at.desc()).limit(n).all()
    except Exception:
        return []


def _pool_feeds(data, want_count):
    """按"这个用户刚查过的池"回该池最新 N 条(**不限作者**)。

    客户端在世界里问的是全 0 id, 但它 1~2 秒前刚查过某个池
    (get_curated_feeds / get_user_feeds), 而且**条数与它这次要问的条数一致**
    (实测: 4 条纸船 -> 4 个全 0; 1 条留影 -> 1 个全 0, 见 recent_query.py)。
    用那个池的最新 N 条回它 —— 别人放的留影/留言也能被还原出来, 这就是
    "创建者看得见自己的、别的账号看不到"的正解(旧兜底只回**自己**的 feed, 别人
    在这个账号上一条都没有, 于是回空 -> 客户端不渲染/点了没内容)。
    """
    try:
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        pool_types, pool_names = recent_query.recall(user_id, session, want_count)
        if not pool_types and not pool_names:
            return []
        n = max(1, min(int(want_count or 1), 10))
        q = SocialFeedPost.query.filter(SocialFeedPost.expire_at > int(time.time()))
        if pool_types:
            q = q.filter(SocialFeedPost.pool_type.in_(pool_types))
        if pool_names:
            q = q.filter(SocialFeedPost.pool_name.in_(pool_names))
        return q.order_by(SocialFeedPost.created_at.desc()).limit(n).all()
    except Exception as e:
        logger.warning("get_by_ids: pool 兜底查询失败: %s", e)
        return []


def _json(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def _collect_ids(data):
    """从请求体里尽力找出 id 列表。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, str)]
    ids = []

    def _add(v):
        if isinstance(v, str) and v:
            ids.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x:
                    ids.append(x)

    for key in ("social_feed_ids", "feed_ids", "ids", "social_feed_id", "feed_id"):
        if key in data:
            _add(data.get(key))
    if not ids:
        for key, v in data.items():
            if key in ("user", "session", "user_id"):
                continue
            if isinstance(v, list):
                _add(v)
    return ids


def v1_get_by_ids():
    """按 id 返回 feeds(带 result=ok)。永不回非 2xx。"""
    try:
        data = request.get_json(force=True, silent=True)
        if not isinstance(data, (dict, list)):
            data = {}
        wanted = _collect_ids(data)
        if not wanted:
            return _json({"feeds": [], "result": "ok"})

        real = [w for w in wanted if w.split(":")[0] != NIL_ID]
        if not real and NIL_ID_FALLBACK:
            # 先按"刚查过的那个池"回(不限作者 —— 别人的留影/留言也要有内容);
            # 池记忆没命中(客户端换了关/进程刚重启)才退回"自己最近的 feed"。
            posts = _pool_feeds(data, len(wanted))
            if not posts:
                posts = _recent_local_feeds(data, len(wanted))
            feeds = []
            for i, w in enumerate(wanted):
                if i >= len(posts):
                    break
                item = feed_item(posts[i])
                # **不要把请求里的全 0 id 回填**(见 _recent_local_feeds 的说明):
                # 官方只会发 "<uuid>:<8hex>", 全 0 的 id 是非法的。这里回的
                # feed_item 自带 feed_public_id(真实公开 id), 保持不变。
                if item.get("social_feed_id"):
                    feeds.append(item)
            return _json({"feeds": feeds, "result": "ok"})

        fids = [w.split(":")[0] for w in wanted]
        # 客户端发来的 id 不一定就是 feed 的 uuid: 留影蜡烛点击时可能发 resource_id
        # (recording_id / uuid 形资源 id, 见 extra_store.resource_index 的说明)。
        # 两边都查, 谁来都认得。
        via_res = {}
        for w in wanted:
            fu = extra_store.feed_id_for_resource(w.split(":")[0])
            if fu:
                via_res[w] = fu
        lookup = fids + [f for f in via_res.values() if f]
        posts = SocialFeedPost.query.filter(
            SocialFeedPost.social_feed_id.in_(lookup)).all()
        by_id = {p.social_feed_id: p for p in posts}

        feeds, seen = [], set()
        for w in wanted:                      # 保持请求顺序
            base = w.split(":")[0]
            p = by_id.get(base) or by_id.get(via_res.get(w) or "")
            if p is not None and p.social_feed_id not in seen:
                seen.add(p.social_feed_id)
                feeds.append(feed_item(p))
        return _json({"feeds": feeds, "result": "ok"})
    except Exception as e:
        logger.error("get_by_ids(v1) 异常: %s", e, exc_info=True)
        return _json({"feeds": [], "result": "ok"})