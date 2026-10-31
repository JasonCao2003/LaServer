# -*- coding: utf-8 -*-
"""
Service接口: /service/social-feed/api/v1/get_user_feeds  (>=0.26.5)

客户端"在遇境放下留言小船/纸船"之后 4 秒内就会调这个接口来刷新自己的动态列表 ——
服务端必须把刚存下的 feed 回给客户端, 否则客户端世界里**不会出现小船**: 消息已经
发出去(后台"留言管理"里看得到), 但玩家自己看不到船。

旧实现是占位符 `return {"user_feeds": []}`:
  1. 键名与官方抓包不符 —— 官方是 "feeds"(见 static_responses/
     service__social-feed__api__v1__get_by_ids.json), 客户端按 "feeds" 取值;
  2. 永远为空 —— 即便键对也没有任何 feed。
这就是"留言小船放出去了但没有显示"的直接原因。

响应形状按 v1 家族抓包: create_at/expire_at 是**毫秒整数**, 带 extra_data,
social_feed_id 是 "<uuid>:<8hex>"(与 /service/social-feed/api/v1/create 一致,
删除/点赞等接口也是按 ':' 前段查库的)。

extra_data 里的 pos/ori/scale 是"小船摆在哪/朝哪"的坐标。客户端在 create 时上报过,
但它不在 social_feed_post 表里(无此列) —— 所以这里走**旁路文件存储**
(service/social_feed/extra_store.py, 一条 feed 一个小 JSON, 不动数据库表结构):
存过就回玩家自己的坐标/朝向, 没存过就回默认值(DEFAULT_FEED_POS 等, 见 extra_store)。
注意默认 ori 必须是**四元数 4 个分量**(官方 "(0,0.998,0,0.063)"), 旧实现回 "(0,0,0)"
少一个分量, 客户端解析不了朝向 -> 世界里的留言物可能建不出正确 id。
"""
from flask import request, Response
from database.models import User, SocialFeedPost
import hashlib
import json
import logging
import time

from . import extra_store, recent_query

logger = logging.getLogger(__name__)

# 留言小船/纸船在关卡里的默认摆放坐标(真实坐标存在 extra_store 里; 这里只是默认值,
# 与 extra_store.DEFAULT_POS 同一处取值, 改 extra_store 里那一行即可)。
DEFAULT_FEED_POS = extra_store.DEFAULT_POS


def _json(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def feed_public_id(post):
    """客户端认的 id 形态 "<uuid>:<8hex>"(与 create 接口生成的完全一致)。"""
    fid = post.social_feed_id or ""
    if ":" in fid:
        return fid
    suffix = hashlib.md5((fid + (post.message or "")).encode("utf-8")).hexdigest()[:8]
    return "%s:%s" % (fid, suffix)


def feed_item(post):
    """一条 feed(v1 家族形状, 见官方 get_by_ids 抓包)。

    extra_data 优先回客户端创建时上传的值(旁路文件), 没有就回默认值。
    """
    return {
        "comments_enabled": extra_store.comments_enabled(post.social_feed_id),
        "create_at": int(post.created_at or 0) * 1000,
        "expire_at": int(post.expire_at or 0) * 1000,
        "extra_data": extra_store.feed_extra(post.social_feed_id),
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
        "social_feed_id": feed_public_id(post),
        "state": "normal",
        "tags": extra_store.tags(post.social_feed_id),
        "user_id": post.user_id,
    }


def _as_str_list(v):
    """把 pool_type/pool_name 归一化成字符串列表 —— 客户端有的版本发单个字符串,
    有的版本发数组(pool_types/pool_names)。"""
    if isinstance(v, str):
        return [v] if v else []
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str) and x]
    return []


def _as_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def v1get_user_feeds():
    """某个 pool 里未过期的 feed 列表(键名 "feeds", 与官方抓包一致)。

    2026-09 修: 老实现在"会话对不上"时**直接回 {"feeds": []}**(账号被删/重新登录
    换了 session 之后就永远只收到空表), 而且 pool_type/pool_name 用 `==` 比字符串 ——
    客户端把值发成数组时就永远查不到东西。这两条都会让"放下纸船/留影之后自己再也
    看不到内容"。现在: 会话不对只记日志照常查询, 过滤条件一律按列表用 in_。
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        pool_types = (_as_str_list(data.get("pool_type"))
                      or _as_str_list(data.get("pool_types")))
        pool_names = (_as_str_list(data.get("pool_name"))
                      or _as_str_list(data.get("pool_names")))
        skip = _as_int(data.get("skip"), 0)
        batch_size = max(1, min(_as_int(data.get("batch_size"), 20), 200))

        # 会话无效也不回非 2xx: 客户端把任何非 2xx 显示成"非法请求。\n请重试。"
        user = (User.query.filter_by(id=user_id, session=session).first()
                if (user_id and session) else None)
        if user is None:
            logger.warning("get_user_feeds(v1): session mismatch (user=%r), still querying by pool",
                           user_id)

        logger.info("get_user_feeds(v1): pool_type=%r pool_name=%r skip=%s batch=%s user=%s",
                    pool_types, pool_names, skip, batch_size, str(user_id or "")[:8])

        q = SocialFeedPost.query.filter(SocialFeedPost.expire_at > int(time.time()))
        if pool_types:
            q = q.filter(SocialFeedPost.pool_type.in_(pool_types))
        if pool_names:
            q = q.filter(SocialFeedPost.pool_name.in_(pool_names))
        posts = (q.order_by(SocialFeedPost.created_at.desc())
                  .offset(skip).limit(batch_size).all())
        logger.info("get_user_feeds(v1): hits=%d", len(posts))
        # 记住这次查询(池 + 条数), 给紧跟着的全 0 id 详情请求兜底。
        recent_query.remember(user_id, session, pool_types, pool_names, len(posts))
        return _json({"feeds": [feed_item(p) for p in posts]})
    except Exception as e:
        logger.error("get_user_feeds(v1) 异常: %s", e, exc_info=True)
        return _json({"feeds": []})