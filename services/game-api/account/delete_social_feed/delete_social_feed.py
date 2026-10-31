from flask import request, jsonify
from database.models import db, User, SocialFeedPost
import logging

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def delete_social_feed():
    """
    删除用户创建的社交动态
    请求体：
    {
        "user": "用户UUID",
        "session": "会话令牌",
        "social_feed_id": "动态UUID",
        "author_id": "作者UUID"   # 冗余字段，用于快速验证
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

        missing = []
        if not user_id: missing.append('user')
        if not session_token: missing.append('session')
        if not social_feed_id: missing.append('social_feed_id')
        if not author_id: missing.append('author_id')
        if missing:
            return jsonify({"result": "error", "message": f"Missing fields: {', '.join(missing)}"}), 400

        # ========== 2. 验证用户会话 ==========
        user = get_cached_user(user_id, session_token)
        if not user:
            logger.warning(f"delete_social_feed: 无效会话 user={user_id}")
            return jsonify({"result": "error", "message": "Invalid session"}), 401

        # ========== 3. 查询社交动态 ==========
        # 库里存纯 uuid; 新客户端可能把 "<uuid>:<8hex>" 传回来, 先按 ':' 前段找。
        bare_id = str(social_feed_id).split(":")[0]
        post = SocialFeedPost.query.filter_by(social_feed_id=bare_id).first()
        if not post and bare_id != social_feed_id:
            post = SocialFeedPost.query.filter_by(social_feed_id=social_feed_id).first()
        if not post:
            logger.warning(f"delete_social_feed: 动态不存在 social_feed_id={social_feed_id}")
            return jsonify({"result": "error", "message": "Social feed post not found"}), 404

        # ========== 4. 权限验证 ==========
        if post.user_id != user_id:
            logger.warning(f"delete_social_feed: 用户 {user_id} 尝试删除非本人动态 {social_feed_id}, 作者为 {post.user_id}")
            return jsonify({"result": "error", "message": "Permission denied"}), 403

        # 可选：验证 author_id 是否与 post.user_id 一致（冗余校验）
        if author_id and post.user_id != author_id:
            logger.warning(f"delete_social_feed: author_id 不匹配, post.user_id={post.user_id}, author_id={author_id}")

        # ========== 5. 执行删除 ==========
        try:
            # 由于 SocialFeedLike 表已设置 ON DELETE CASCADE，直接删除动态即可
            db.session.delete(post)
            db.session.commit()
            logger.info(f"delete_social_feed: 用户 {user_id} 删除动态 {social_feed_id} 成功")
        except Exception as e:
            db.session.rollback()
            logger.error(f"delete_social_feed: 数据库删除失败 {e}")
            return jsonify({"result": "error", "message": "Database error"}), 500

        # ========== 6. 返回成功响应 ==========
        return jsonify({"result": "ok"}), 200

    except Exception as e:
        logger.error(f"delete_social_feed 未捕获异常: {e}", exc_info=True)
        return jsonify({"result": "error", "message": "Internal server error"}), 500