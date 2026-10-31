from flask import request, jsonify
from database.models import db, User, SocialFeedPost
from service.social_feed import extra_store
import uuid
import time
import logging

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def create_social_feed():
    try:
        data = request.get_json(force=True, silent=True) or {}
        # 优先取客户端传入的 user，但必须与验证后的 session 匹配
        client_user_id = data.get('user') or data.get('user_id')
        session_token = data.get('session')
        pool_type = data.get('pool_type')
        pool_name = data.get('pool_name')
        message = data.get('message')
        extra_data = data.get('extra_data')
        if message is None:
            # 留影蜡烛只有 resource_id, 没有文字留言(0.15.x 也允许空 message)
            message = ""

        if not all([client_user_id, session_token, pool_type, pool_name]):
            return jsonify({"result": "error", "message": "Missing fields"}), 400

        user = get_cached_user(client_user_id, session_token)
        if not user:
            return jsonify({"result": "error", "message": "Invalid session"}), 401

        # ✅ 使用 user.id（从数据库验证后的真实ID）作为作者
        author_id = user.id

        # 生成唯一 social_feed_id
        social_feed_id = str(uuid.uuid4())
        now = int(time.time())
        expire_at = now + 1209600  # 14天

        new_post = SocialFeedPost(
            user_id=author_id,          # 关键：使用验证后的真实ID
            social_feed_id=social_feed_id,
            pool_type=pool_type,
            pool_name=pool_name,
            message=message,
            created_at=now,
            expire_at=expire_at,
            likes_count=0,
            comments_count=0,
            report_count=0,
            hide_count=0,
            attempt_count=0,
            succeed_count=0,
            is_poisoned=False
        )
        db.session.add(new_post)
        db.session.commit()

        # extra_data(小船/蜡烛的 pos/ori/scale/resource_id)、tags、comments_enabled
        # 数据库没有这些列, 与 service 层共用旁路文件, 否则下发时只能回默认坐标。
        if isinstance(extra_data, dict):
            extra_store.save_extra(social_feed_id, extra_data,
                                   comments_enabled=data.get('comments_enabled'),
                                   tags=data.get('tags'),
                                   is_private=data.get('private'))

        logger.info(f"create_social_feed: 用户 {author_id} 创建动态 {social_feed_id} extra={bool(extra_data)}")
        return jsonify({
            "result": "success",
            "social_feed_id": social_feed_id
        }), 200
    except Exception as e:
        logger.error(f"create_social_feed 异常: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({"result": "error", "message": "Internal error"}), 500