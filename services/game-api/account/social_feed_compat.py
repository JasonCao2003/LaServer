# -*- coding: utf-8 -*-
"""旧客户端(0.15.x) account 层社交动态的响应形状。

背景: `account/social_feed/*` 与 `service/social-feed/*` 是两套接口。0.15.x 客户端只调
account 层, 它解析的是 Sky-0.15.5-server-main 里 `_feed_item_compat` 的形状:
  * create_at 是带毫秒的 ISO 字符串, expire_at 是不带毫秒的 ISO(只给更新版 get_by_ids
    用毫秒时间戳);
  * 必须有 extra_data(小船/留影的 pos/ori/scale/resource_id), 否则纸船/蜡烛摆在默认点;
  * reactions 必须同时有扁平 total_* 和 score, 缺了旧 libBootloader 会判"没有互动数据";
  * 必须有 local_creation / followed / friend / comments_enabled / user_reactions。

这里只做"形状转换", 不碰数据库; extra_data 与 tags 从 service.social_feed.extra_store
(instance/feed_extra/<uuid>.json)读取, 与 service 层共用同一份旁路数据。
"""
from datetime import datetime

from service.social_feed import extra_store

DEFAULT_EXTRA = {
    "chunk_count": 0,
    "ori": "(0,0,0,1)",
    "pos": "(-10,0,-10)",
    "prompt_used": "",
    "resource_id": "",
    "scale": "(1,1,1)",
    "sub_type": 0,
}

_ZERO_REACTIONS = {
    "is_poisoned": False,
    "total_comment_count": 0,
    "total_hide_count": 0,
    "total_like_count": 0,
    "total_like_reward_message_id": 0,
    "total_report_count": 0,
    "total_reported_comment_count": 0,
    "total_super_like_count": 0,
}


def account_iso(ts, with_ms=False):
    """秒 -> 官方 ISO 时间串; 非法/0 返回 None。"""
    try:
        ts = int(ts or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts <= 0:
        return None
    try:
        base = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return None
    return (base + ".000Z") if with_ms else (base + "Z")


def feed_reactions(post):
    r = {
        "is_poisoned": bool(post.is_poisoned),
        "total_comment_count": int(post.comments_count or 0),
        "total_hide_count": int(post.hide_count or 0),
        "total_like_count": int(post.likes_count or 0),
        "total_like_reward_message_id": 0,
        "total_report_count": int(post.report_count or 0),
        "total_reported_comment_count": 0,
        "total_super_like_count": 0,
        "score": 0,
        "total_attempt_count": int(post.attempt_count or 0),
        "total_succeed_count": int(post.succeed_count or 0),
    }
    return r


def feed_item(post, viewer_id=None, is_friend=False):
    """把 SocialFeedPost 行转成 0.15.x 客户端能吃的条目。"""
    fid = post.social_feed_id
    reactions = feed_reactions(post)
    viewer = str(viewer_id or "")
    author = str(post.user_id or "")
    return {
        "social_feed_id": fid,
        "user_id": author,
        "author_id": author,
        "pool_type": post.pool_type or "",
        "pool_name": post.pool_name or "",
        "create_at": account_iso(post.created_at, True),
        "expire_at": account_iso(post.expire_at, False),
        "message": post.message or "",
        "reactions": reactions,
        "state": "normal",
        "tags": extra_store.tags(fid, None),
        "extra_data": extra_store.feed_extra(fid),
        "local_creation": bool(viewer and author and viewer == author),
        "followed": False,
        "friend": bool(is_friend),
        "comments_enabled": extra_store.comments_enabled(fid),
        "total_like_count": reactions["total_like_count"],
        "total_hide_count": reactions["total_hide_count"],
        "total_report_count": reactions["total_report_count"],
        "user_reactions": [],
    }
