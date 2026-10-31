# -*- coding: utf-8 -*-
"""
Service接口: /service/social-feed/api/v1/hide  (高版本 > 0.26.5 新增)
隐藏一条纸船/动态。按抓包"隐藏评论":
请求: {"user","session","social_feed_id","author_id","hidden":true}
响应: {"result":""}
"""
from flask import request, Response
from infrastructure.database.models import db, User, SocialFeedPost
import json


def social_feed_hide():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        social_feed_id = data.get("social_feed_id")
        if not user_id or not session:
            return Response(json.dumps({"result": "error"}), 400,
                            mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"result": "error"}), 401,
                            mimetype="application/json")

        fid = (social_feed_id or "").split(":")[0]
        if fid:
            post = SocialFeedPost.query.filter_by(social_feed_id=fid).first()
            if post:
                post.hide_count = (post.hide_count or 0) + 1
                db.session.commit()
        return Response(json.dumps({"result": ""}, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"result": "error", "error": str(e)}), 500,
                        mimetype="application/json")
