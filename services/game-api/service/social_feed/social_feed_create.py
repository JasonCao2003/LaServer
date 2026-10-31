# -*- coding: utf-8 -*-
"""
Service接口: /service/social-feed/api/v1/create  (高版本 > 0.26.5 新增)
创建纸船/留影蜡烛/留言。按抓包"遇境创建小船":
请求: {"user","session","pool_type":"MessageBoat","pool_name":"CandleSpace",
       "message":"...","private":false,"comments_enabled":true,"tags":[...],"extra_data":{...}}
响应: {"result":"success","social_feed_id":"<uuid>:<8hex>"}

extra_data(摆放坐标 pos/ori/scale、留影蜡烛的 resource_id、chunk_count 等)入库没有列,
所以存到旁路文件里(见 extra_store.py) —— 不存的话下发时只能回默认值: 小船位置/朝向不对,
留影蜡烛没有资源 id(客户端点击时发全 0 id -> "点了没内容")。
"""
from flask import request, Response
from database.models import db, User, SocialFeedPost
import json
import logging
import uuid
import time
import hashlib

from . import extra_store

logger = logging.getLogger(__name__)


def social_feed_create():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        pool_type = data.get("pool_type")
        pool_name = data.get("pool_name")
        # 留影蜡烛只有 resource_id(没有文字留言), 所以 message 允许为空。
        message = data.get("message")
        if message is None:
            message = ""
        extra_data = data.get("extra_data")

        if not user_id or not session:
            return Response(json.dumps({"result": "error", "error": "Invalid request body"}),
                            400, mimetype="application/json")
        if not (pool_type and pool_name):
            logger.info("social-feed create: 缺字段 pool_type/pool_name, body=%s",
                        {k: v for k, v in data.items() if k != "session"})
            return Response(json.dumps({"result": "error", "error": "Missing fields"}),
                            400, mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"result": "error", "error": "Invalid session"}),
                            401, mimetype="application/json")

        fid = str(uuid.uuid4())
        now = int(time.time())
        post = SocialFeedPost(
            user_id=user.id,
            social_feed_id=fid,
            pool_type=pool_type,
            pool_name=pool_name,
            message=message,
            created_at=now,
            expire_at=now + 1209600,
            likes_count=0, comments_count=0, report_count=0,
            hide_count=0, attempt_count=0, succeed_count=0, is_poisoned=False,
        )
        db.session.add(post)
        db.session.commit()

        # 抓包格式: "<uuid>:<8hex>"
        suffix = hashlib.md5((fid + message).encode("utf-8")).hexdigest()[:8]
        public_id = "%s:%s" % (fid, suffix)

        # extra_data(坐标/朝向/留影的 resource_id...)存旁路文件(数据库没有这一列)。
        # 存失败不影响创建, 只是下发时回默认值。
        if isinstance(extra_data, dict):
            extra_store.save_extra(public_id, extra_data,
                                   comments_enabled=data.get("comments_enabled"),
                                   tags=data.get("tags"),
                                   is_private=data.get("private"))
        logger.info("social-feed create: user=%s pool=%s/%s id=%s extra=%s",
                    user.id, pool_type, pool_name, public_id,
                    json.dumps(extra_data, ensure_ascii=False) if extra_data else "(无)")

        body = {"result": "success", "social_feed_id": public_id}
        return Response(json.dumps(body, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        db.session.rollback()
        return Response(json.dumps({"result": "error", "error": str(e)}), 500,
                        mimetype="application/json")
