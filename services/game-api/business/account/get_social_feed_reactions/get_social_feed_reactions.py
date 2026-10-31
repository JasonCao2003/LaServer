from flask import request, jsonify
# User 必带: get_cached_user() 会 User.query 校验会话, 漏 import 会
# NameError -> 整个接口 500(实测 /service/social-feed/api/v1/get_reactions)。
from infrastructure.database.models import SocialFeedPost, User
import logging

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user
    
def get_social_feed_reactions():
    """
    获取社交动态的互动统计数据
    请求体：
    {
        "user": "用户UUID",
        "session": "会话令牌",
        "social_feed_id": "动态UUID",
        "author_id": "作者UUID"   # 冗余字段，可用于快速验证，但主要使用 social_feed_id
    }
    响应体：
    {
        "reactions": { ... },   # 动态的统计数据
        "user_reactions": null  # 当前用户对此动态的互动状态（暂返回 null）
    }
    """
    try:
        # ========== 1. 解析请求 ==========
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Invalid request body"}), 400

        user_id = data.get('user') or data.get('user_id')
        session_token = data.get('session')
        social_feed_id = data.get('social_feed_id')
        author_id = data.get('author_id')   # 可选，此处仅做记录不强制使用

        if not user_id or not session_token or not social_feed_id:
            return jsonify({"error": "Missing required fields"}), 400

        # ========== 2. 验证用户会话 ==========
        user = get_cached_user(user_id, session_token)
        if not user:
            logger.warning(f"get_reactions: 无效会话 user={user_id}")
            return jsonify({"error": "Invalid session"}), 401

        # ========== 3. 查询社交动态 ==========
        # 库里存纯 uuid; 新客户端可能把 "<uuid>:<8hex>" 传回来, 先按 ':' 前段找。
        bare_id = str(social_feed_id).split(":")[0]
        post = SocialFeedPost.query.filter_by(social_feed_id=bare_id).first()
        if not post and bare_id != social_feed_id:
            post = SocialFeedPost.query.filter_by(social_feed_id=social_feed_id).first()
        if not post:
            logger.warning(f"get_reactions: 动态不存在 social_feed_id={social_feed_id}")
            return jsonify({"error": "Social feed post not found"}), 404

        # 可选：验证 author_id 是否匹配（如果提供）
        if author_id and post.user_id != author_id:
            logger.warning(f"get_reactions: author_id 不匹配, post.user_id={post.user_id}, author_id={author_id}")

        # ========== 4. 构建 reactions 对象 ==========
        reactions = {
            "score": 0,   # 暂未实现评分系统，固定 0
            "is_poisoned": post.is_poisoned,
            "total_hide_count": post.hide_count,
            "total_like_count": post.likes_count,
            "total_report_count": post.report_count,
            "total_attempt_count": post.attempt_count,
            "total_comment_count": post.comments_count,
            "total_succeed_count": post.succeed_count,
            "total_like_reward_message_id": 0   # 暂未实现，固定 0
        }

        # ========== 5. 构建响应 ==========
        response_data = {
            "reactions": reactions,
            "user_reactions": None   # 当前用户互动状态，示例中为 null
        }

        logger.info(f"get_reactions: user={user_id}, social_feed_id={social_feed_id}, likes={post.likes_count}")
        return jsonify(response_data), 200

    except Exception as e:
        logger.error(f"get_reactions 异常: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500