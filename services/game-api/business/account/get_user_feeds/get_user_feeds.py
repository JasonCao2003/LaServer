# -*- coding: utf-8 -*-
"""旧客户端(0.15.x) account 层: 按池子拉取社交动态。

0.15.x 客户端只认 account 层形状(set 见 account/social_feed_compat.py), 且 pool_type /
pool_name 既可能发字符串也可能发数组; 只发 level_id 时用 level_id 当 pool_name。
"""
from flask import request, jsonify
from infrastructure.database.models import User, SocialFeedPost
from business.account.social_feed_compat import feed_item
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


def get_user_feeds():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get('user') or data.get('user_id')
        session_token = data.get('session')
        pool_types = _as_list(data.get('pool_type')) or _as_list(data.get('pool_types'))
        pool_names = (_as_list(data.get('pool_name'))
                      or _as_list(data.get('pool_names'))
                      or _as_list(data.get('level_id')))
        try:
            skip = max(0, int(data.get('skip') or 0))
        except (TypeError, ValueError):
            skip = 0
        try:
            batch_size = max(1, min(int(data.get('batch_size') or 10), 200))
        except (TypeError, ValueError):
            batch_size = 10

        if not all([user_id, session_token]):
            return jsonify({"feeds": [], "result": "ok", "total_available_results": 0}), 200

        user = get_cached_user(user_id, session_token)
        if not user:
            return jsonify({"feeds": [], "result": "ok", "total_available_results": 0}), 200

        current_time = int(time.time())
        q = SocialFeedPost.query.filter(
            (SocialFeedPost.expire_at == 0) | (SocialFeedPost.expire_at > current_time)
        )
        if pool_types:
            q = q.filter(SocialFeedPost.pool_type.in_(pool_types))
        if pool_names:
            q = q.filter(SocialFeedPost.pool_name.in_(pool_names))
        total = q.count()
        posts = q.order_by(SocialFeedPost.created_at.desc()).offset(skip).limit(batch_size).all()

        feeds = [feed_item(p, viewer_id=user.id) for p in posts]
        return jsonify({"feeds": feeds, "result": "ok",
                        "total_available_results": total}), 200
    except Exception as e:
        logger.error(f"get_user_feeds 异常: {e}", exc_info=True)
        return jsonify({"feeds": [], "result": "ok", "total_available_results": 0}), 200
