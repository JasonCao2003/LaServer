# -*- coding: utf-8 -*-
"""
Service接口: /service/social-feed/api/v1/super_like  (高版本 > 0.26.5 新增)
纸船送心(超级点赞, 消耗蜡烛并给作者送心)。按抓包"纸船送心":
请求: {"user","session","social_feed_id","author_id","super_liked":true}
响应: {"result":"success","update_currency":{"candles":<最新蜡烛数>}}
"""
from flask import request, Response
from database.models import db, User, UserCurrency, SocialFeedPost, SocialFeedLike
import json


SUPER_LIKE_COST = 3  # 送心消耗 3 根蜡烛


def social_feed_super_like():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        social_feed_id = data.get("social_feed_id")
        super_liked = data.get("super_liked")
        if not user_id or not session:
            return Response(json.dumps({"result": "error"}), 400,
                            mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"result": "error"}), 401,
                            mimetype="application/json")

        fid = (social_feed_id or "").split(":")[0]
        cur = UserCurrency.query.filter_by(user_id=user.id).first()
        if not cur:
            cur = UserCurrency(user_id=user.id, candles=0, heart=0, heart_wax=0,
                               prestige=0, prestige_vip=0, prestige_wax=0,
                               season_candle=0, season_wax=0, season_wax_14=0,
                               season_heart=0, vip=0, wax=0)
            db.session.add(cur)
            db.session.flush()

        if super_liked:
            cur.candles = max(0, (cur.candles or 0) - SUPER_LIKE_COST)
            existing = SocialFeedLike.query.filter_by(user_id=user.id,
                                                      social_feed_id=fid).first()
            if not existing:
                db.session.add(SocialFeedLike(user_id=user.id, social_feed_id=fid))
            post = SocialFeedPost.query.filter_by(social_feed_id=fid).first()
            if post:
                post.likes_count = (post.likes_count or 0) + 1
        db.session.commit()

        body = {"result": "success",
                "update_currency": {"candles": cur.candles or 0}}
        return Response(json.dumps(body, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"result": "error", "error": str(e)}), 500,
                        mimetype="application/json")
