# -*- coding: utf-8 -*-
"""
Service接口: /service/social-feed/api/v1/like  (高版本 > 0.26.5 新增)
点赞/取消点赞纸船。按抓包"取消点赞":
请求: {"user","session","social_feed_id","author_id","liked":false}
响应: {"result":"created"}
"""
from flask import request, Response
from infrastructure.database.models import db, User, SocialFeedPost, SocialFeedLike
import json


def social_feed_like():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        social_feed_id = data.get("social_feed_id")
        liked = data.get("liked")
        if not user_id or not session:
            return Response(json.dumps({"result": "error"}), 400,
                            mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"result": "error"}), 401,
                            mimetype="application/json")

        fid = (social_feed_id or "").split(":")[0]
        post = SocialFeedPost.query.filter_by(social_feed_id=fid).first()
        existing = SocialFeedLike.query.filter_by(user_id=user.id,
                                                  social_feed_id=fid).first()
        if liked:
            if not existing:
                db.session.add(SocialFeedLike(user_id=user.id, social_feed_id=fid))
                if post:
                    post.likes_count = (post.likes_count or 0) + 1
        else:
            if existing:
                db.session.delete(existing)
                if post:
                    post.likes_count = max(0, (post.likes_count or 0) - 1)
        db.session.commit()
        return Response(json.dumps({"result": "created"}, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"result": "error", "error": str(e)}), 500,
                        mimetype="application/json")
