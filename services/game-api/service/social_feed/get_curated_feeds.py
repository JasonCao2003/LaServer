# -*- coding: utf-8 -*-
"""
Service接口: /service/social-feed/api/v2/get_curated_feeds  (>=0.26.5)

客户端在大厅/遇境拉"社交动态"(留言小船、冥想神龛留言等)时调它。旧实现是占位符
`return {"shops": []}` —— 键名是照抄商店接口的, 官方抓包(static_responses/
service__social-feed__api__v2__get_curated_feeds.json)顶层是:

  {"inner_ring": {"followed":..., "friends":..., "local":..., "public":[...],
                  "total_available_results":N, "total_followed":0,
                  "total_friends":0},
   "outer_ring": null}

客户端按 inner_ring/public 取值, 拿到 {"shops": []} 时既没有 inner_ring 也没有
feeds —— 世界里的留言小船/留言就没有数据可渲染。这里按官方形状补齐, 并把库里
未过期的 feed 作为 public 下发。

v2 家族的时间是 ISO 字符串("2024-07-08T09:03:16.976Z"), 与 v1 的毫秒整数不同
(见 get_user_feeds.py 的说明)。extra_data 同 v1, 走 extra_store 旁路文件(存过就回
玩家自己摆的坐标/朝向/资源 id)。

**必须按 search_area.pool_types 过滤**(实测抓包, 0.33.7/0.22.0 都这么发):
    {"search_area": {"coordinates": "(1,0,4)", "pool_types": ["RecordingCandle"],
                     "pool_name": "CandleSpace", "level_id": "CandleSpace", ...}, ...}
旧实现完全忽略 pool_types/pool_name/level_id, 对任何查询都回同一批 feed —— 客户端问
"这个坐标附近有没有留影蜡烛(RecordingCandle)"时, 我们回了一堆 **MessageBoat**,
客户端拿 Boat 的数据去建 RecordingCandle 对象, 资源 id(extra_data.resource_id)又是空的,
结果就是点击蜡烛/小船时客户端发全 0 的 id 去 get_by_ids(实测抓包), 界面"点了没内容"。
"""
from flask import request, Response
from database.models import User, SocialFeedPost
import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from . import extra_store, recent_query

logger = logging.getLogger(__name__)


def _json(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def _iso(ts):
    """秒 -> 官方那种 ISO 字符串(带毫秒)。"""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        return "1970-01-01T00:00:00.000Z"


def _public_id(post):
    fid = post.social_feed_id or ""
    if ":" in fid:
        return fid
    suffix = hashlib.md5((fid + (post.message or "")).encode("utf-8")).hexdigest()[:8]
    return "%s:%s" % (fid, suffix)


def _feed_item(post, me_id=None):
    return {
        "comments_enabled": extra_store.comments_enabled(post.social_feed_id),
        "create_at": _iso(post.created_at),
        "expire_at": _iso(post.expire_at),
        "extra_data": extra_store.feed_extra(post.social_feed_id),
        "followed": False,
        "friend": False,
        "local_creation": bool(me_id) and post.user_id == me_id,
        "message": post.message or "",
        "pool_name": post.pool_name or "",
        "pool_type": post.pool_type or "",
        "reactions": {
            "is_poisoned": bool(post.is_poisoned),
            "total_comment_count": int(post.comments_count or 0),
            "total_hide_count": int(post.hide_count or 0),
            "total_like_count": int(post.likes_count or 0),
            "total_like_reward_message_id": 0,
            "total_report_count": int(post.report_count or 0),
            "total_reported_comment_count": 0,
            "total_super_like_count": 0,
        },
        "social_feed_id": _public_id(post),
        "state": "normal",
        "tags": extra_store.tags(post.social_feed_id),
        "user_id": post.user_id,
    }


def _as_str_list(v):
    """把 pool_types / pool_name 归一化成字符串列表(也可能是单个字符串)。"""
    if isinstance(v, str):
        return [v] if v else []
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str) and x]
    return []


def get_curated_feed():
    """按官方形状返回 inner_ring/outer_ring。永不回非 2xx。

    查询条件取客户端实际发的 search_area(pool_types/pool_name/level_id), 也兼容
    老客户端把 pool_type/pool_name 放在顶层的写法。
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")

        area = data.get("search_area") if isinstance(data.get("search_area"), dict) else {}
        pool_types = _as_str_list(area.get("pool_types")) or _as_str_list(data.get("pool_types"))
        pool_type = data.get("pool_type")
        if not pool_types and pool_type:
            pool_types = [pool_type]
        pool_name = area.get("pool_name") or data.get("pool_name")
        level_id = area.get("level_id") or data.get("level_id")

        limit = data.get("batch_size") or data.get("count") or 64
        try:
            limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            limit = 64

        me = (User.query.filter_by(id=user_id, session=session).first()
              if (user_id and session) else None)

        q = SocialFeedPost.query.filter(SocialFeedPost.expire_at > int(time.time()))
        # 池类型必须过滤: 客户端按池类型建不同的世界物件(MessageBoat=纸船/
        # RecordingCandle=留影蜡烛/SharedSpaceCandle=共享空间留言), 类型不符会让
        # 客户端用错误的数据建物件(点击时发全 0 id)。
        if pool_types:
            q = q.filter(SocialFeedPost.pool_type.in_(pool_types))
        # 池名(=关卡名/大厅名)。客户端两种写法都可能发(search_area.pool_name 或顶层),
        # 都取不到时用 level_id(通常是同一个值, 如 "CandleSpace")。
        # 值可能是字符串也可能是数组, 一律归一化成列表用 in_ 过滤(老实现用 == 比字符串,
        # 客户端发数组时就永远查不到东西 -> 世界里的纸船/留影/留言空着)。
        pool_names = _as_str_list(pool_name) or _as_str_list(level_id)
        logger.info("get_curated_feeds(v2): pool_types=%r pool_names=%r limit=%s user=%s",
                    pool_types, pool_names, limit, str(user_id or "")[:8])
        if pool_names:
            q = q.filter(SocialFeedPost.pool_name.in_(pool_names))
        posts = q.order_by(SocialFeedPost.created_at.desc()).limit(limit).all()
        logger.info("get_curated_feeds(v2): hits=%d", len(posts))
        # 记住"这个用户刚查了这个池、回了 N 条": 客户端紧接着会拿全 0 id 来问详情
        # (见 service/social_feed/recent_query.py 的说明)。
        recent_query.remember(user_id, session, pool_types, pool_names, len(posts))
        public = [_feed_item(p, me.id if me else None) for p in posts]

        return _json({
            "inner_ring": {
                "followed": None,
                "friends": None,
                "local": None,
                "public": public,
                "total_available_results": len(public),
                "total_followed": 0,
                "total_friends": 0,
            },
            "outer_ring": None,
        })
    except Exception as e:
        logger.error("get_curated_feeds(v2) 异常: %s", e, exc_info=True)
        return _json({
            "inner_ring": {"followed": None, "friends": None, "local": None,
                           "public": [], "total_available_results": 0,
                           "total_followed": 0, "total_friends": 0},
            "outer_ring": None,
        })