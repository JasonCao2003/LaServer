# -*- coding: utf-8 -*-
"""
Service接口: social-feed 家族里其余几条客户端会调的路径 —— 原来都没注册, 请求落到
JSON 兜底(200 {}), 客户端拿不到键就弹"非法请求。\n请重试。"或者界面一直转圈。
响应形状照抄官方抓包(static_responses/):

  /service/social-feed/api/v1/get_reactions  -> {"reactions":{8 个计数}, "user_reactions":null}
  /service/social-feed/api/v2/comments       -> {"comments":[], "local":[]}
  /service/social-feed/api/v1/report         -> {"result":""}
  /service/social-feed/api/v2/get_penpals    -> {"penpals":[]}
  /service/social-feed/api/v2/get_penpal_by_id -> {"feeds":[...], "result":"ok"}

get_reactions 按库里这条 feed 的真实计数回(likes_count / comments_count / hide_count /
report_count), 客户端点开纸船/留影/留言看到的"点赞数/举报数"就是真的; 评论我们没存
评论内容, 回空表(客户端显示"还没有评论")。笔友(penpal)本服没做, 回空表。
"""
from flask import request, Response
from database.models import User, SocialFeedPost
import json
import logging

logger = logging.getLogger(__name__)


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def _pick_id(data):
    """从请求体里取 feed id(各版本键名不一, 允许 "<uuid>:<8hex>")。"""
    for key in ("social_feed_id", "feed_id", "id", "social_feed_ids", "feed_ids", "ids"):
        if key not in data:
            continue
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x.strip():
                    return x.strip()
    return ""


def _zero_reactions():
    return {
        "is_poisoned": False,
        "total_comment_count": 0,
        "total_hide_count": 0,
        "total_like_count": 0,
        "total_like_reward_message_id": 0,
        "total_report_count": 0,
        "total_reported_comment_count": 0,
        "total_super_like_count": 0,
    }


def _reactions_of(post):
    return {
        "is_poisoned": bool(post.is_poisoned),
        "total_comment_count": int(post.comments_count or 0),
        "total_hide_count": int(post.hide_count or 0),
        "total_like_count": int(post.likes_count or 0),
        "total_like_reward_message_id": 0,
        "total_report_count": int(post.report_count or 0),
        "total_reported_comment_count": 0,
        "total_super_like_count": 0,
    }


def social_feed_get_reactions():
    """某条 feed 的点赞/评论/举报计数。永不回非 2xx。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        fid = _pick_id(data).split(":")[0]
        post = (SocialFeedPost.query.filter_by(social_feed_id=fid).first()
                if fid else None)
        return _resp({
            "reactions": _reactions_of(post) if post is not None else _zero_reactions(),
            "user_reactions": None,
        })
    except Exception as e:
        logger.error("get_reactions error: %s", e, exc_info=True)
        return _resp({"reactions": _zero_reactions(), "user_reactions": None})


def social_feed_comments():
    """评论列表(我们没存评论内容, 回空表)。永不回非 2xx。"""
    try:
        request.get_json(force=True, silent=True)
    except Exception:
        pass
    return _resp({"comments": [], "local": []})


def social_feed_report():
    """举报(本服不做内容审核, 官方成功时 result 就是空串)。"""
    try:
        request.get_json(force=True, silent=True)
    except Exception:
        pass
    return _resp({"result": ""})


def social_feed_get_penpals():
    """笔友列表(本服没做笔友, 回空表)。"""
    try:
        request.get_json(force=True, silent=True)
    except Exception:
        pass
    return _resp({"penpals": []})


def social_feed_get_penpal_by_id():
    """按 id 取笔友来信。本服没做笔友 -> 空表 + result ok(客户端不弹错)。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        fid = _pick_id(data).split(":")[0]
        post = (SocialFeedPost.query.filter_by(social_feed_id=fid).first()
                if fid else None)
        if post is not None and (post.pool_type or "") == "SocialFeedPenpal":
            # 万一库里有笔友池的 feed(比如从官方档导进来的), 原样回一条
            from .get_user_feeds import feed_item
            return _resp({"feeds": [feed_item(post)], "result": "ok"})
        return _resp({"feeds": [], "result": "ok"})
    except Exception as e:
        logger.error("get_penpal_by_id error: %s", e, exc_info=True)
        return _resp({"feeds": [], "result": "ok"})