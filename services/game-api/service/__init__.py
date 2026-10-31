# -*- coding: utf-8 -*-
"""
Service接口模块
所有service相关的接口都在这里导入和注册
"""

from .auth.token import get_auth_token
from .infraction.get_infractions_status_decorated import get_infractions_status_decorated
from .message.get_all import get_all_messages_v2
from .message.get_allv1 import get_all_messages_v1
from .message.get import get_message
from .message.mark_received import mark_received
from .message.mark_seen import mark_seen
from .quest.check_for_previous_completion import check_for_previous_completion
from .quest.get_player_quests import get_player_quests
from .quest.get_player_quests_by_name import get_player_quests_by_name
from .quest.get_quest_schedule import get_quest_schedule
from .quest.create import create as quest_create
from .quest.update_quest import update_quest as quest_update
from .relationship.get_all_friend_gist import get_all_friend_gist
from .relationship.get_follower_count import get_follower_count
from .relationship.update_friend_constellation_pages import (
    update_friend_constellation_pages,
    update_friend_constellation_pages_entry,
    update_friend_constellation_pages_options,
)
# 好友赠礼(心火/爱心): send + get_pending + claim。
# 注意: 以前这里还额外 import 了旧包 service.relationship_free_gifts.* —— 那个包的
# send 只有 CORS 空壳、get_pending 用错字段名。同名的两次 from import 会把名字**覆盖**
# 成旧包版本, 于是下面 register 挂上去的其实是旧实现(用户实测"赠送爱心和心火"没用)。
# 现在只保留新包。
from .relationship.free_gifts.get_pending import get_pending_free_gifts
from .relationship.free_gifts.send import send_free_gift_options
from .relationship.free_gifts.claim import claim_free_gift
from .reservation.reserve import reserve
# 留影蜡烛: 上传留影 / 取回留影(旧实现没有这两个路由 -> 兜底回 {} -> 留影创建不出来)
from .recording.create import recording_create
from .recording.get import recording_get
# 留影蜡烛 v2(0.30+ 客户端): 分块上传 + seek_table + /ugc Range 拉取。
# 之前只有 v1, 高版本客户端 create/get 掉进 JSON 兜底 {}, 留影录完不出现、点了没反应。
from .recording.v2 import (recording_v2_create, recording_v2_update,
                           recording_v2_get, serve_recording_ugc)
from .social_feed.get_curated_feeds import get_curated_feed
from .social_feed.get_user_feeds import v1get_user_feeds
from .social_feed.get_by_ids import v1_get_by_ids
# 共享空间(stage): 取摆放 + 保存摆放 + 公寓进出 —— set/apartment 原来没注册,
# 实测客户端发 /service/stage/api/v1/set 得到 404, 共享空间里摆的东西存不住。
from .stage.get import get_stage
from .stage.set import set_stage
from .stage.apartment import (friends_in_apartments, leave_apartment,
                              update_apartment_status)
# social-feed 家族其余小接口(点赞计数/评论/举报/笔友), 形状照官方抓包, 见 misc.py
from .social_feed.misc import (social_feed_get_reactions, social_feed_comments,
                               social_feed_report, social_feed_get_penpals,
                               social_feed_get_penpal_by_id)
from .status.add_unlock import add_unlock
from .status.delete_unlock import delete_unlock
from .status.get_unlocks import get_unlocks_status
from .trust.get_capped.get_capped import get_capped
# trust 查询(羁绊/亲密度)。官方抓包里这条路径回的是 {"npcs":[],"players":[]};
# 本服原来没注册 -> 兜底 200 {} -> 0.34.0 新好友树取不到 npcs/players 两个成员。
from .trust.get.get_trust import get_trust

# 高版本(> 0.26.5)抓包重写新增
from .social_feed.social_feed_create import social_feed_create
from .social_feed.social_feed_hide import social_feed_hide
from .social_feed.social_feed_like import social_feed_like as svc_social_feed_like
from .social_feed.social_feed_super_like import social_feed_super_like
from .social_feed.social_feed_delete import social_feed_delete
# 替换("站在已经放过留言/纸船/留影的地方再放一次"走的这条) —— 原来没注册, 请求
# 落到兜底 200 {} , 客户端拿不到 result/social_feed_id
from .social_feed.social_feed_edit import social_feed_edit
from .relationship.relationship_add_follow import relationship_add_follow
from .relationship.relationship_set_follow_name import relationship_set_follow_name
from .relationship.relationship_delete_follow import relationship_delete_follow
from .inventory.remove_dye import inventory_remove_dye


def register_service_routes(app):
    """
    注册所有service路由到Flask应用
    
    参数:
        app: Flask应用实例
    """
    app.add_url_rule('/service/auth/api/v1/token', 'get_auth_token', get_auth_token, methods=['POST'])
    app.add_url_rule('/service/infraction/api/v1/get_infractions_status_decorated', 'get_infractions_status_decorated', get_infractions_status_decorated, methods=['POST'])
    app.add_url_rule('/service/message/api/v2/get_all', 'get_all_messages_v2', get_all_messages_v2, methods=['POST'])
    app.add_url_rule('/service/message/api/v1/get_all', 'get_all_messages_v1', get_all_messages_v1, methods=['POST'])
    app.add_url_rule('/service/message/api/v1/get', 'get_message', get_message, methods=['POST'])
    app.add_url_rule('/service/message/api/v1/mark_received', 'mark_received', mark_received, methods=['POST'])
    app.add_url_rule('/service/message/api/v1/mark_seen', 'mark_seen', mark_seen, methods=['POST'])
    app.add_url_rule('/service/quest/api/v1/check_for_previous_completion', 'check_for_previous_completion', check_for_previous_completion, methods=['POST'])
    app.add_url_rule('/service/quest/api/v1/get_player_quests', 'get_player_quests', get_player_quests, methods=['POST'])
    app.add_url_rule('/service/quest/api/v1/get_player_quests_by_name', 'get_player_quests_by_name', get_player_quests_by_name, methods=['POST'])
    app.add_url_rule('/service/quest/api/v1/get_quest_schedule', 'get_quest_schedule', get_quest_schedule, methods=['POST'])
    app.add_url_rule('/service/quest/api/v1/create', 'quest_create', quest_create, methods=['POST'])
    app.add_url_rule('/service/quest/api/v1/update_quest', 'quest_update', quest_update, methods=['POST'])
    app.add_url_rule('/service/relationship/api/v1/get_all_friend_gist', 'get_all_friend_gist', get_all_friend_gist, methods=['POST'])
    app.add_url_rule('/service/relationship/api/v1/get_follower_count', 'get_follower_count', get_follower_count, methods=['POST'])
    # 星座页(标星): 原来 POST 挂的是 CORS 预检处理器 -> 200 + 空 body ->
    # 客户端"设置玩家为标星好友闪退"。现在这条路径的视图函数是分派器:
    # OPTIONS 回预检, 其余(POST)走真业务处理。
    # 端点名沿用 update_friend_constellation_pages_options —— 运行中的 url_map 里挂的
    # 就是它, 热更新只替换视图函数不重建 url_map(详见该模块 update_..._entry 的说明)。
    app.add_url_rule('/service/relationship/api/v1/update_friend_constellation_pages',
                     'update_friend_constellation_pages_options',
                     update_friend_constellation_pages_entry,
                     methods=['POST', 'OPTIONS'])
    # 好友赠礼三条路径。send 的视图函数同时处理 OPTIONS(预检)与 POST(真业务),
    # 所以这里挂 POST+OPTIONS; claim 原来根本没注册(404), 现在补上。
    app.add_url_rule('/service/relationship/api/v1/free_gifts/get_pending', 'get_pending_free_gifts', get_pending_free_gifts, methods=['POST'])
    app.add_url_rule('/service/relationship/api/v1/free_gifts/send', 'send_free_gift_options', send_free_gift_options, methods=['POST', 'OPTIONS'])
    app.add_url_rule('/service/relationship/api/v1/free_gifts/claim', 'claim_free_gift', claim_free_gift, methods=['POST'])
    app.add_url_rule('/service/reservation/api/v1/reserve', 'reserve', reserve, methods=['POST'])
    app.add_url_rule('/service/recording/api/v1/create', 'recording_create', recording_create, methods=['POST'])
    app.add_url_rule('/service/recording/api/v1/get', 'recording_get', recording_get, methods=['POST'])
    # 留影 v2: 0.30+ 客户端的真实路径(分块上传), 以及客户端按 seek_table 拉块的 /ugc 地址
    app.add_url_rule('/service/recording/api/v2/create', 'recording_v2_create', recording_v2_create, methods=['POST'])
    app.add_url_rule('/service/recording/api/v2/update', 'recording_v2_update', recording_v2_update, methods=['POST'])
    app.add_url_rule('/service/recording/api/v2/get', 'recording_v2_get', recording_v2_get, methods=['POST'])
    app.add_url_rule('/ugc/<path:subpath>', 'serve_recording_ugc', serve_recording_ugc, methods=['GET'])
    app.add_url_rule('/service/social-feed/api/v2/get_curated_feeds', 'get_curated_feed', get_curated_feed, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v1/get_user_feeds', 'v1get_user_feeds', v1get_user_feeds, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v1/get_by_ids', 'v1_get_by_ids', v1_get_by_ids, methods=['POST'])
    app.add_url_rule('/service/stage/api/v1/get', 'get_stage', get_stage, methods=['POST'])
    # 共享空间: 保存摆放 + 公寓进出(原来没注册, 客户端拿 404 -> 摆下去的东西丢失)
    app.add_url_rule('/service/stage/api/v1/set', 'set_stage', set_stage, methods=['POST'])
    app.add_url_rule('/service/stage/api/v1/friends_in_apartments', 'friends_in_apartments', friends_in_apartments, methods=['POST'])
    app.add_url_rule('/service/stage/api/v1/leave_apartment', 'leave_apartment', leave_apartment, methods=['POST'])
    app.add_url_rule('/service/stage/api/v1/update_apartment_status', 'update_apartment_status', update_apartment_status, methods=['POST'])
    app.add_url_rule('/service/status/api/v1/add_unlock', 'add_unlock', add_unlock, methods=['POST'])
    app.add_url_rule('/service/status/api/v1/delete_unlock', 'delete_unlock', delete_unlock, methods=['POST'])
    app.add_url_rule('/service/status/api/v1/get_unlocks', 'get_unlocks_status', get_unlocks_status, methods=['POST'])
    app.add_url_rule('/service/trust/api/v1/get_capped', 'get_capped', get_capped, methods=['POST'])
    # trust 查询(羁绊): 官方形状 {"npcs":[],"players":[]}, 补掉兜底 {} 。
    app.add_url_rule('/service/trust/api/v1/get', 'get_trust', get_trust, methods=['POST'])
    # 高版本(> 0.26.5)抓包重写新增
    app.add_url_rule('/service/social-feed/api/v1/create', 'social_feed_create', social_feed_create, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v1/hide', 'social_feed_hide', social_feed_hide, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v1/like', 'svc_social_feed_like', svc_social_feed_like, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v1/super_like', 'social_feed_super_like', social_feed_super_like, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v1/delete', 'social_feed_delete', social_feed_delete, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v1/edit', 'social_feed_edit', social_feed_edit, methods=['POST'])
    app.add_url_rule('/service/relationship/api/v1/add_follow', 'relationship_add_follow', relationship_add_follow, methods=['POST'])
    app.add_url_rule('/service/relationship/api/v1/set_follow_name', 'relationship_set_follow_name', relationship_set_follow_name, methods=['POST'])
    app.add_url_rule('/service/relationship/api/v1/delete_follow', 'relationship_delete_follow', relationship_delete_follow, methods=['POST'])
    app.add_url_rule('/service/inventory/api/v1/unlocks/remove_dye', 'inventory_remove_dye', inventory_remove_dye, methods=['POST'])
    # social-feed 家族其余小接口(原来没注册 -> 兜底 200 {} -> 客户端弹"非法请求"或卡住)
    app.add_url_rule('/service/social-feed/api/v1/get_reactions', 'social_feed_get_reactions', social_feed_get_reactions, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v2/comments', 'social_feed_comments', social_feed_comments, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v1/report', 'social_feed_report', social_feed_report, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v2/get_penpals', 'social_feed_get_penpals', social_feed_get_penpals, methods=['POST'])
    app.add_url_rule('/service/social-feed/api/v2/get_penpal_by_id', 'social_feed_get_penpal_by_id', social_feed_get_penpal_by_id, methods=['POST'])


__all__ = ['get_auth_token', 'get_infractions_status_decorated', 'get_all_messages_v2', 'get_all_messages_v1', 'mark_received', 'check_for_previous_completion', 'get_player_quests', 'get_quest_schedule', 'get_all_friend_gist', 'get_follower_count', 'update_friend_constellation_pages', 'update_friend_constellation_pages_options', 'get_pending_free_gifts', 'send_free_gift_options', 'claim_free_gift', 'reserve', 'recording_create', 'recording_get', 'recording_v2_create', 'recording_v2_update', 'recording_v2_get', 'serve_recording_ugc', 'get_curated_feed', 'v1get_user_feeds', 'v1_get_by_ids', 'get_stage', 'set_stage', 'friends_in_apartments', 'leave_apartment', 'update_apartment_status', 'social_feed_get_reactions', 'social_feed_comments', 'social_feed_report', 'social_feed_get_penpals', 'social_feed_get_penpal_by_id', 'add_unlock', 'get_unlocks_status', 'get_capped', 'get_trust', 'social_feed_create', 'social_feed_hide', 'svc_social_feed_like', 'social_feed_super_like', 'social_feed_delete', 'social_feed_edit', 'relationship_add_follow', 'relationship_set_follow_name', 'relationship_delete_follow', 'inventory_remove_dye', 'register_service_routes']
