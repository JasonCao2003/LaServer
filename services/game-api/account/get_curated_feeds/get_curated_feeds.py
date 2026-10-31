# -*- coding: utf-8 -*-
"""旧客户端(0.15.x) account 层: 精选社交动态(好友+公开+随机混合)。

0.15.x 客户端既读扁平 feeds, 也读 inner_ring.public(新版才能看到别人的纸船/共享空间
蜡烛); 条目形状统一由 account/social_feed_compat.py 生成。
"""
from flask import request, jsonify
from database.models import User, SocialFeedPost, Friendship
from account.social_feed_compat import feed_item
import random
import time
import logging

logger = logging.getLogger(__name__)


def _as_list(v):
    if isinstance(v, str):
        return [v] if v else []
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str) and x]
    return []


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user


def is_friend(user_id, friend_id):
    """判断两人是否为好友（双向任一 active 关系）"""
    f1 = Friendship.query.filter_by(user_id=user_id, friend_id=friend_id, status='active').first()
    if f1:
        return True
    f2 = Friendship.query.filter_by(user_id=friend_id, friend_id=user_id, status='active').first()
    return f2 is not None


def _empty_curated():
    return {
        "feeds": [], "local": [], "random": [], "friends": [], "followed": [],
        "total_friends": 0, "total_available_results": 0,
        "inner_ring": {"followed": None, "friends": None, "local": None,
                       "public": [], "total_available_results": 0,
                       "total_followed": 0, "total_friends": 0},
        "outer_ring": None, "result": "ok",
    }


def get_curated_feeds():
    """获取精选社交动态（混合好友/公开/随机）"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get('user') or data.get('user_id')
        session_token = data.get('session')
        pool_types = _as_list(data.get('pool_type')) or _as_list(data.get('pool_types'))
        pool_names = (_as_list(data.get('pool_name'))
                      or _as_list(data.get('pool_names'))
                      or _as_list(data.get('level_id')))
        try:
            n_public = max(1, min(int(data.get('n_public') or 32), 200))
        except (TypeError, ValueError):
            n_public = 32
        try:
            n_friends = max(0, min(int(data.get('n_friends') or 32), 200))
        except (TypeError, ValueError):
            n_friends = 32
        try:
            n_random = max(0, min(int(data.get('n_random') or 6), 200))
        except (TypeError, ValueError):
            n_random = 6

        if not all([user_id, session_token]):
            return jsonify(_empty_curated()), 200

        user = get_cached_user(user_id, session_token)
        if not user:
            return jsonify(_empty_curated()), 200
        viewer = user.id

        current_time = int(time.time())
        q = SocialFeedPost.query.filter(
            (SocialFeedPost.expire_at == 0) | (SocialFeedPost.expire_at > current_time)
        )
        if pool_types:
            q = q.filter(SocialFeedPost.pool_type.in_(pool_types))
        if pool_names:
            q = q.filter(SocialFeedPost.pool_name.in_(pool_names))
        all_posts = q.order_by(SocialFeedPost.created_at.desc()).all()
        total_available = len(all_posts)

        friend_posts = []
        non_friend_posts = []
        for post in all_posts:
            if is_friend(viewer, post.user_id):
                friend_posts.append(post)
            else:
                non_friend_posts.append(post)

        selected = []
        seen = set()

        # 取最新 n_friends 条好友动态
        for post in friend_posts[:n_friends]:
            if post.social_feed_id not in seen:
                selected.append(post)
                seen.add(post.social_feed_id)

        # 取最新 n_public 条非好友动态
        for post in non_friend_posts[:n_public]:
            if post.social_feed_id not in seen:
                selected.append(post)
                seen.add(post.social_feed_id)

        # 从剩余非好友动态中随机取 n_random 条
        remaining = [p for p in non_friend_posts if p.social_feed_id not in seen]
        if remaining and n_random > 0:
            selected.extend(random.sample(remaining, min(n_random, len(remaining))))

        selected.sort(key=lambda x: x.created_at, reverse=True)

        feeds = []
        friend_count = 0
        for post in selected:
            is_friend_flag = is_friend(viewer, post.user_id)
            if is_friend_flag:
                friend_count += 1
            feeds.append(feed_item(post, viewer_id=viewer, is_friend=is_friend_flag))

        local = [f for f in feeds if f.get("local_creation")]
        return jsonify({
            "feeds": feeds,
            "local": local,
            "random": [],
            "friends": [f for f in feeds if f.get("friend")],
            "followed": [],
            "total_friends": friend_count,
            "total_available_results": total_available,
            "inner_ring": {
                "followed": None,
                "friends": None,
                "local": None,
                "public": feeds,
                "total_available_results": total_available,
                "total_followed": 0,
                "total_friends": friend_count,
            },
            "outer_ring": None,
            "result": "ok",
        }), 200
    except Exception as e:
        logger.error(f"get_curated_feeds 异常: {e}", exc_info=True)
        return jsonify(_empty_curated()), 200
