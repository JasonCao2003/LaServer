# -*- coding: utf-8 -*-
"""
Service模块: social-feed
"""

from .get_curated_feeds import get_curated_feed
from .get_user_feeds import v1get_user_feeds
# 其余客户端会调的小接口(点赞计数/评论/举报/笔友) —— 原来没注册, 见 misc.py 说明
from .misc import (social_feed_get_reactions, social_feed_comments,
                   social_feed_report, social_feed_get_penpals,
                   social_feed_get_penpal_by_id)

__all__ = ['get_curated_feed', 'v1get_user_feeds', 'social_feed_get_reactions',
           'social_feed_comments', 'social_feed_report',
           'social_feed_get_penpals', 'social_feed_get_penpal_by_id']
