from flask import request, jsonify
from database.models import db, User, SocialFeedPost, SocialFeedLike
import logging

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def social_feed_like():
    """
    点赞或取消点赞社交动态
    请求体：
    {
        "user": "用户UUID",
        "session": "会话令牌",
        "social_feed_id": "动态UUID",
        "author_id": "作者UUID",   # 冗余字段，仅验证时可用
        "liked": true/false
    }
    响应体：
    {
        "result": "ok"
    }
    """
    try:
        # ========== 1. 解析请求 ==========
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"result": "error", "message": "Invalid request body"}), 400

        user_id = data.get('user') or data.get('user_id')
        session_token = data.get('session')
        social_feed_id = data.get('social_feed_id')
        author_id = data.get('author_id')
        liked = data.get('liked')

        missing = []
        if not user_id: missing.append('user')
        if not session_token: missing.append('session')
        if not social_feed_id: missing.append('social_feed_id')
        if liked is None: missing.append('liked')
        if missing:
            return jsonify({"result": "error", "message": f"Missing fields: {', '.join(missing)}"}), 400

        # ========== 2. 验证用户会话 ==========
        user = get_cached_user(user_id, session_token)
        if not user:
            logger.warning(f"social_feed_like: 无效会话 user={user_id}")
            return jsonify({"result": "error", "message": "Invalid session"}), 401

        # ========== 3. 查询社交动态 ==========
        # 库里存纯 uuid; 新客户端可能把 "<uuid>:<8hex>" 传回来, 先按 ':' 前段找。
        bare_id = str(social_feed_id).split(":")[0]
        post = SocialFeedPost.query.filter_by(social_feed_id=bare_id).first()
        if not post and bare_id != social_feed_id:
            post = SocialFeedPost.query.filter_by(social_feed_id=social_feed_id).first()
        if not post:
            logger.warning(f"social_feed_like: 动态不存在 social_feed_id={social_feed_id}")
            return jsonify({"result": "error", "message": "Social feed post not found"}), 404

        # 可选：验证 author_id 是否匹配
        if author_id and post.user_id != author_id:
            logger.warning(f"social_feed_like: author_id 不匹配, post.user_id={post.user_id}, author_id={author_id}")

        # ========== 4. 执行点赞/取消点赞 ==========
        existing_like = SocialFeedLike.query.filter_by(
            user_id=user_id,
            social_feed_id=social_feed_id
        ).first()

        if liked:
            if not existing_like:
                new_like = SocialFeedLike(
                    user_id=user_id,
                    social_feed_id=social_feed_id
                )
                db.session.add(new_like)
                post.likes_count += 1
                logger.info(f"social_feed_like: 用户 {user_id} 点赞动态 {social_feed_id}")
            else:
                logger.debug(f"social_feed_like: 用户 {user_id} 已经点赞过动态 {social_feed_id}")
        else:
            if existing_like:
                db.session.delete(existing_like)
                # 减少动态的点赞计数（确保不小于0）
                post.likes_count = max(0, post.likes_count - 1)
                logger.info(f"social_feed_like: 用户 {user_id} 取消点赞动态 {social_feed_id}")
            else:
                logger.debug(f"social_feed_like: 用户 {user_id} 尚未点赞动态 {social_feed_id}")

        # ========== 5. 提交事务 ==========
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"social_feed_like: 数据库提交失败 {e}")
            return jsonify({"result": "error", "message": "Database error"}), 500

        # ========== 6. 返回成功响应 ==========
        return jsonify({"result": "ok"}), 200

    except Exception as e:
        logger.error(f"social_feed_like 未捕获异常: {e}", exc_info=True)
        return jsonify({"result": "error", "message": "Internal server error"}), 500