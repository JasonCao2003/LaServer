# -*- coding: utf-8 -*-
"""
Account接口模块
所有account相关的接口都在这里导入和注册
"""

from .auth_create.auth_create import create_account
from .create.create import account_create
from .get_motd.get_motd import get_motd
from .auth_login.auth_login import logini
from .login.login import account_login
from .get_currency.get_currency import get_currency
from .get_rank.get_rank import get_rank
from .userdata.userdata import get_userdata
from .sync_user_data.sync_user_data import sync_user_data
from .device_capabilities_lookup.device_capabilities_lookup import lookup
from .get_latest_build_version.get_latest_build_version import get_latest_build_version
from .get_vars.get_vars import get_vars
from .support_has_reply.support_has_reply import has_reply
from .get_achievement_stats_tracking.get_achievement_stats_tracking import get_achievement_stats_tracking
from .get_event_currency_defs.get_event_currency_defs import get_event_currency_defs
from .get_blocked_friends.get_blocked_friends import get_blocked_friends
from .set_friend_name.set_friend_name import set_friend_name
from .set_friend_block.set_friend_block import set_friend_block
from .get_questionnaires.get_questionnaires import get_questionnaires
from .set_achievement_stats.set_achievement_stats import set_achievement_stats
from .get_surveys.get_surveys import get_surveys
from .get_shop.get_shop import get_shop
from .get_lootboxes.get_lootboxes import get_lootboxes
from .redeem_box.redeem_box import redeem_box
from .get_external_friends.get_external_friends import get_external_friends
from .get_serendipity.get_serendipity import get_serendipity
from .get_star_tags.get_star_tags import get_star_tags
from .get_pending_messages.get_pending_messages import get_pending_messages
from .claim_message_gift.claim_message_gift import claim_message_gift
from .get_generic_shops.get_generic_shops import generic_shop_defs
from .get_consumable_defs.get_consumable_defs import get_consumable_defs
from .get_consumables.get_consumables import get_consumables
from .get_forge_rates.get_forge_rates import get_forge_rates
from .get_achievements.get_achievements import get_achievements
from .get_achievement_stats.get_achievement_stats import get_achievement_stats
from .get_relationship_abilities.get_relationship_abilities import get_relationship_abilities
from .redeem_reward.redeem_reward import redeem_reward
from .get_unlocks.get_unlocks import get_unlocks
from .get_event_schedule.get_event_schedule import get_event_schedule
from .iaplist.iaplist import iap_list
from .commerce.commerce import (commerce_receipt, commerce_fulfill, commerce_update,
                                   commerce_product_info)
from .get_collectibles.get_collectibles import get_collectibles
from .get_spirit_shops.get_spirit_shops import get_spirit_shops
from .get_questionnaire_groups.get_questionnaire_groups import get_questionnaire_groups
from .purchase_spirit_shop_item.purchase_spirit_shop_item import purchase_spirit_shop_item
from .get_user_geonotes.get_user_geonotes import get_user_geonotes
from .get_user_geonotes2.get_user_geonotes2 import get_user_geonotes2
from .request_geonotes_for_n_friends.request_geonotes_for_n_friends import request_geonotes_for_n_friends
from .create_geonote2.create_geonote2 import create_geonote2, create_bug_report_geonote
from .geonote_ops.geonote_ops import (delete_geonote, delete_bug_report_geonote,
                                      edit_message_geonote, social_geonote2,
                                      request_n_bug_report_geonotes)
from .legacy_stubs.legacy_stubs import (get_daily_quests, get_weekly_quests,
                                        get_season_quests_v2, get_season_pass_defs,
                                        get_currency_defs, get_event_currency)
from .get_wing_buffs.get_wing_buffs import get_wing_buffs
from .wing_buffs_ops.wing_buffs_ops import wing_buffs_collect, wing_buffs_drop, wing_buffs_deposit, wing_buffs_convert
from .collect_collectible.collect_collectible import collect_collectible
from .get_app_badge_number.get_app_badge_number import get_app_badge_number
from .get_buffs1.get_buffs1 import get_buffs1
from .get_buffs.get_buffs import get_buffs
from .remove_buff1.remove_buff1 import remove_buff1
from .purchase_event_buff.purchase_event_buff import purchase_event_buff
from .purchase_generic_shop_item.purchase_generic_shop_item import purchase_generic_shop_item
from .get_checkpoints.get_checkpoints import get_checkpoints
from .get_invites.get_invites import get_invites
from .get_online_friends.get_online_friends import get_online_friends
from .purchase_unlock.purchase_unlock import purchase_unlock
from .get_achievement_defs.get_achievement_defs import get_achievement_defs
from .get_friend_statues.get_friend_statues import get_friend_statues
from .get_friends.get_friends import get_friends
from .join_friend_game.join_friend_game import join_friend_game
from .get_consumables1.get_consumables1 import get_consumables1
from .consume_item1.consume_item1 import consume_item1
from .get_trust_tier_defs.get_trust_tier_defs import get_trust_tier_defs
from .get_map_defs.get_map_defs import get_map_defs
from .get_video_defs.get_video_defs import get_video_defs
from .get_radiance_spawn_defs.get_radiance_spawn_defs import get_radiance_spawn_defs
from .get_outfit.get_outfit import get_outfit
from .get_level_pickups.get_level_pickups import get_level_pickups
from .find_previous_or_empty.find_previous_or_empty import find_previous_or_empty
from .ack_forge_rate_reset.ack_forge_rate_reset import ack_forge_rate_reset
from .get_quest_defs.get_quest_defs import get_quest_defs
from .get_trust_group_defs.get_trust_group_defs import get_trust_group_defs
from .season_finalize.season_finalize import season_finalize
from .season_start.season_start import season_start
from .activate_season_quest.activate_season_quest import activate_season_quest
from .claim_season_quest_reward.claim_season_quest_reward import claim_season_quest_reward
from .ack_collectible.ack_collectible import ack_collectible
from .upload.upload import upload_remote_config
from .get_season_quests.get_season_quests import get_season_quests
from .get_trust_activity_defs.get_trust_activity_defs import get_trust_activity_defs
from .get_buff_defs.get_buff_defs import get_buff_defs
from .get_account_world_quests.get_account_world_quests import get_account_world_quests
from .claim_quest_reward.claim_quest_reward import claim_quest_reward
from .set_outfit.set_outfit import set_outfit
from .set_joinable.set_joinable import set_joinable
from .set_checkpoint.set_checkpoint import set_checkpoint
from .collect_pickup_batch.collect_pickup_batch import collect_pickup_batch
from .buy_candle_wax.buy_candle_wax import buy_candle_wax
from .create_invite.create_invite import create_invite
from .verify.verify import verify_invite_code
from .check_invite.check_invite import check_invite
from .accept_invite.accept_invite import accept_invite
from .delete_invite.delete_invite import delete_invite
from .create_social_feed.create_social_feed import create_social_feed
from .get_user_feeds.get_user_feeds import get_user_feeds
from .get_curated_feeds.get_curated_feeds import get_curated_feeds
from .get_social_feed_reactions.get_social_feed_reactions import get_social_feed_reactions
from .social_feed_like.social_feed_like import social_feed_like
from .delete_social_feed.delete_social_feed import delete_social_feed
from .ack_unlock.ack_unlock import ack_unlock
from .send.send import handle_chat_message, handle_send_message
from .translate.translate import handle_translate
from .set_device_token.set_device_token import set_device_token

# 高版本(> 0.26.5)抓包重写新增
from .get_remote_outfit.get_remote_outfit import get_remote_outfit
from .give_candle.give_candle import give_candle
from .remove_relationship_hint.remove_relationship_hint import remove_relationship_hint
from .get_generic_shop_item_tracking_info.get_generic_shop_item_tracking_info import get_generic_shop_item_tracking_info
from .purchase_dye.purchase_dye import purchase_dye

def register_account_routes(app):
    """
    注册所有account路由到Flask应用

    参数:
        app: Flask应用实例
    """
    # API 路径的 JSON 兜底(404/405/500 -> 200 + {}): 客户端对任何非 2xx 都显示
    # "非法请求。\n请重试。"(字符串 error_login_request), 所以未实现的接口不能再
    # 让 Flask 回 HTML 404。放在这里是为了启动和每次热更新都会重新装上(幂等)。
    try:
        import json_error_handlers
        json_error_handlers.install(app)
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).error(
            "json_error_handlers.install 失败: %s", _e, exc_info=True)

    app.add_url_rule('/account/auth/create', 'create_account', create_account, methods=['POST'])
    app.add_url_rule('/account/create', 'account_create', account_create, methods=['POST'])
    app.add_url_rule('/account/get_motd', 'get_motd', get_motd, methods=['POST'])
    app.add_url_rule('/account/auth/login', 'logini', logini, methods=['POST'])
    app.add_url_rule('/account/login', 'account_login', account_login, methods=['POST'])
    app.add_url_rule('/account/get_currency', 'get_currency', get_currency, methods=['POST'])
    app.add_url_rule('/account/get_rank', 'get_rank', get_rank, methods=['POST'])
    app.add_url_rule('/account/userdata', 'get_userdata', get_userdata, methods=['POST'])
    app.add_url_rule('/account/sync_user_data', 'sync_user_data', sync_user_data, methods=['POST'])
    app.add_url_rule('/account/device_capabilities/lookup', 'lookup', lookup, methods=['POST'])
    app.add_url_rule('/account/get_latest_build_version', 'get_latest_build_version', get_latest_build_version, methods=['POST'])
    app.add_url_rule('/account/get_vars', 'get_vars', get_vars, methods=['POST'])
    app.add_url_rule('/account/support/has_reply', 'has_reply', has_reply, methods=['POST'])
    app.add_url_rule('/account/get_achievement_stats_tracking', 'get_achievement_stats_tracking', get_achievement_stats_tracking, methods=['POST'])
    app.add_url_rule('/account/get_event_currency_defs', 'get_event_currency_defs', get_event_currency_defs, methods=['POST'])
    app.add_url_rule('/account/get_blocked_friends', 'get_blocked_friends', get_blocked_friends, methods=['POST'])
    app.add_url_rule('/account/set_friend_name', 'set_friend_name', set_friend_name, methods=['POST'])
    # 屏蔽/取消屏蔽好友: 原来没注册 -> 兜成 200 {} -> 屏蔽静默失败(抓包 2026-10-04 12:03:17)。
    # 响应形状来自客户端 sub_441C90: {"result":true,"update_friends":[{...blocked}]}
    app.add_url_rule('/account/set_friend_block', 'set_friend_block', set_friend_block, methods=['POST'])
    app.add_url_rule('/account/get_questionnaires', 'get_questionnaires', get_questionnaires, methods=['POST'])
    app.add_url_rule('/account/set_achievement_stats', 'set_achievement_stats', set_achievement_stats, methods=['POST'])
    app.add_url_rule('/account/get_surveys', 'get_surveys', get_surveys, methods=['POST'])
    app.add_url_rule('/account/get_shop', 'get_shop', get_shop, methods=['POST'])
    app.add_url_rule('/account/lootboxes/get', 'get_lootboxes', get_lootboxes, methods=['POST'])
    app.add_url_rule('/account/lootboxes/redeem_box', 'redeem_box', redeem_box, methods=['POST'])
    app.add_url_rule('/account/get_external_friends', 'get_external_friends', get_external_friends, methods=['POST'])
    app.add_url_rule('/account/serendipity/get', 'get_serendipity', get_serendipity, methods=['POST'])
    app.add_url_rule('/account/star/get', 'get_star_tags', get_star_tags, methods=['POST'])
    app.add_url_rule('/account/get_pending_messages', 'get_pending_messages', get_pending_messages, methods=['POST'])
    app.add_url_rule('/account/get_generic_shops', 'generic_shop_defs', generic_shop_defs, methods=['POST'])
    app.add_url_rule('/account/consumable/get_consumable_defs', 'get_consumable_defs', get_consumable_defs, methods=['POST'])
    app.add_url_rule('/account/consumable/get_consumables', 'get_consumables', get_consumables, methods=['POST'])
    app.add_url_rule('/account/consumable/consume_item1', 'consume_item1', consume_item1, methods=['POST'])
    app.add_url_rule('/account/get_forge_rates', 'get_forge_rates', get_forge_rates, methods=['POST'])
    app.add_url_rule('/account/get_achievements', 'get_achievements', get_achievements, methods=['POST'])
    app.add_url_rule('/account/get_achievement_stats', 'get_achievement_stats', get_achievement_stats, methods=['POST'])
    app.add_url_rule('/account/get_relationship_abilities', 'get_relationship_abilities', get_relationship_abilities, methods=['POST'])
    app.add_url_rule('/account/redeem_reward', 'redeem_reward', redeem_reward, methods=['POST'])
    app.add_url_rule('/account/get_unlocks', 'get_unlocks', get_unlocks, methods=['POST'])
    app.add_url_rule('/account/get_event_schedule', 'get_event_schedule', get_event_schedule, methods=['POST'])
    app.add_url_rule('/account/iaplist', 'iap_list', iap_list, methods=['POST'])
    app.add_url_rule('/account/commerce/receipt', 'commerce_receipt', commerce_receipt, methods=['POST'])
    app.add_url_rule('/account/commerce/fulfill', 'commerce_fulfill', commerce_fulfill, methods=['POST'])
    app.add_url_rule('/account/commerce/update', 'commerce_update', commerce_update, methods=['POST'])
    app.add_url_rule('/account/commerce/product_info', 'commerce_product_info', commerce_product_info, methods=['POST', 'GET'])
    app.add_url_rule('/account/get_collectibles', 'get_collectibles', get_collectibles, methods=['POST'])
    app.add_url_rule('/account/get_spirit_shops', 'get_spirit_shops', get_spirit_shops, methods=['POST'])
    app.add_url_rule('/account/get_questionnaire_groups', 'get_questionnaire_groups', get_questionnaire_groups, methods=['POST'])
    app.add_url_rule('/account/purchase_spirit_shop_item', 'purchase_spirit_shop_item', purchase_spirit_shop_item, methods=['POST'])
    app.add_url_rule('/account/get_user_geonotes', 'get_user_geonotes', get_user_geonotes, methods=['POST'])
    app.add_url_rule('/account/geonote/get_user_geonotes', 'get_user_geonotes2', get_user_geonotes2, methods=['POST'])
    app.add_url_rule('/account/geonote/request_geonotes_for_n_friends', 'request_geonotes_for_n_friends', request_geonotes_for_n_friends, methods=['POST'])
    app.add_url_rule('/account/geonote/create_geonote2', 'create_geonote2', create_geonote2, methods=['POST'])
    app.add_url_rule('/account/create_geonote2', 'create_geonote2_flat', create_geonote2, methods=['POST'])
    app.add_url_rule('/account/geonote/create_bug_report_geonote', 'create_bug_report_geonote', create_bug_report_geonote, methods=['POST'])
    app.add_url_rule('/account/create_bug_report_geonote', 'create_bug_report_geonote_flat', create_bug_report_geonote, methods=['POST'])
    app.add_url_rule('/account/geonote/social_geonote2', 'social_geonote2', social_geonote2, methods=['POST'])
    app.add_url_rule('/account/social_geonote2', 'social_geonote2_flat', social_geonote2, methods=['POST'])
    app.add_url_rule('/account/geonote/delete_geonote', 'delete_geonote', delete_geonote, methods=['POST'])
    app.add_url_rule('/account/delete_geonote', 'delete_geonote_flat', delete_geonote, methods=['POST'])
    app.add_url_rule('/account/geonote/delete_bug_report_geonote', 'delete_bug_report_geonote', delete_bug_report_geonote, methods=['POST'])
    app.add_url_rule('/account/delete_bug_report_geonote', 'delete_bug_report_geonote_flat', delete_bug_report_geonote, methods=['POST'])
    app.add_url_rule('/account/geonote/edit_message_geonote', 'edit_message_geonote', edit_message_geonote, methods=['POST'])
    app.add_url_rule('/account/edit_message_geonote', 'edit_message_geonote_flat', edit_message_geonote, methods=['POST'])
    app.add_url_rule('/account/geonote/request_n_bug_report_geonotes', 'request_n_bug_report_geonotes', request_n_bug_report_geonotes, methods=['POST'])
    app.add_url_rule('/account/request_n_bug_report_geonotes', 'request_n_bug_report_geonotes_flat', request_n_bug_report_geonotes, methods=['POST'])
    app.add_url_rule('/account/get_daily_quests', 'get_daily_quests', get_daily_quests, methods=['POST'])
    app.add_url_rule('/account/get_weekly_quests', 'get_weekly_quests', get_weekly_quests, methods=['POST'])
    app.add_url_rule('/account/get_season_quests_v2', 'get_season_quests_v2', get_season_quests_v2, methods=['POST'])
    app.add_url_rule('/account/get_season_pass_defs', 'get_season_pass_defs', get_season_pass_defs, methods=['POST'])
    app.add_url_rule('/account/get_currency_defs', 'get_currency_defs', get_currency_defs, methods=['POST'])
    app.add_url_rule('/account/get_event_currency', 'get_event_currency', get_event_currency, methods=['POST'])
    app.add_url_rule('/account/wing_buffs/get', 'get_wing_buffs', get_wing_buffs, methods=['POST'])
    app.add_url_rule('/account/wing_buffs/collect', 'wing_buffs_collect', wing_buffs_collect, methods=['POST'])
    app.add_url_rule('/account/collect_collectible', 'collect_collectible', collect_collectible, methods=['POST'])
    app.add_url_rule('/account/wing_buffs/drop', 'wing_buffs_drop', wing_buffs_drop, methods=['POST'])
    app.add_url_rule('/account/wing_buffs/deposit', 'wing_buffs_deposit', wing_buffs_deposit, methods=['POST'])
    app.add_url_rule('/account/wing_buffs/convert', 'wing_buffs_convert', wing_buffs_convert, methods=['POST'])
    app.add_url_rule('/account/get_app_badge_number', 'get_app_badge_number', get_app_badge_number, methods=['POST'])
    app.add_url_rule('/account/buff/get_buffs1', 'get_buffs1', get_buffs1, methods=['POST'])
    app.add_url_rule('/account/buff/get_buffs', 'get_buffs', get_buffs, methods=['POST'])
    # 魔法链路补齐的接口(原来没有这些路由 -> HTML 404 -> 客户端"非法请求"/闪退)
    app.add_url_rule('/account/buff/remove_buff1', 'remove_buff1', remove_buff1, methods=['POST'])
    app.add_url_rule('/account/buff/remove_buff', 'remove_buff', remove_buff1, methods=['POST'])
    app.add_url_rule('/account/purchase_event_buff', 'purchase_event_buff', purchase_event_buff, methods=['POST'])
    app.add_url_rule('/account/purchase_generic_shop_item', 'purchase_generic_shop_item', purchase_generic_shop_item, methods=['POST'])
    app.add_url_rule('/account/get_checkpoints', 'get_checkpoints', get_checkpoints, methods=['POST'])
    app.add_url_rule('/account/get_invites', 'get_invites', get_invites, methods=['POST'])
    app.add_url_rule('/account/get_online_friends', 'get_online_friends', get_online_friends, methods=['POST'])
    app.add_url_rule('/account/purchase_unlock', 'purchase_unlock', purchase_unlock, methods=['POST'])
    app.add_url_rule('/account/get_achievement_defs', 'get_achievement_defs', get_achievement_defs, methods=['POST'])
    app.add_url_rule('/account/get_friend_statues', 'get_friend_statues', get_friend_statues, methods=['POST', 'OPTIONS'])
    app.add_url_rule('/account/get_friends', 'get_friends', get_friends, methods=['POST'])
    app.add_url_rule('/account/join_friend_game', 'join_friend_game', join_friend_game, methods=['POST'])
    app.add_url_rule('/account/consumable/get_consumables1', 'get_consumables1', get_consumables1, methods=['POST'])
    app.add_url_rule('/account/trust/get_trust_tier_defs', 'get_trust_tier_defs', get_trust_tier_defs, methods=['POST'])
    app.add_url_rule('/account/get_map_defs', 'get_map_defs', get_map_defs, methods=['POST'])
    app.add_url_rule('/account/get_video_defs', 'get_video_defs', get_video_defs, methods=['POST'])
    app.add_url_rule('/account/get_radiance_spawn_defs', 'get_radiance_spawn_defs', get_radiance_spawn_defs, methods=['POST'])
    app.add_url_rule('/account/get_outfit', 'get_outfit', get_outfit, methods=['POST'])
    app.add_url_rule('/account/get_level_pickups', 'get_level_pickups', get_level_pickups, methods=['POST'])
    app.add_url_rule('/account/find_previous_or_empty', 'find_previous_or_empty', find_previous_or_empty, methods=['POST'])
    app.add_url_rule('/account/ack_forge_rate_reset', 'ack_forge_rate_reset', ack_forge_rate_reset, methods=['POST'])
    app.add_url_rule('/account/quest/get_quest_defs', 'get_quest_defs', get_quest_defs, methods=['POST'])
    app.add_url_rule('/account/trust/get_trust_group_defs', 'get_trust_group_defs', get_trust_group_defs, methods=['POST'])
    app.add_url_rule('/account/season_finalize', 'season_finalize', season_finalize, methods=['POST'])
    app.add_url_rule('/account/season_start', 'season_start', season_start, methods=['POST'])
    app.add_url_rule('/account/activate_season_quest', 'activate_season_quest', activate_season_quest, methods=['POST'])
    app.add_url_rule('/account/claim_season_quest_reward', 'claim_season_quest_reward', claim_season_quest_reward, methods=['POST'])
    app.add_url_rule('/account/ack_collectible', 'ack_collectible', ack_collectible, methods=['POST'])
    app.add_url_rule('/account/remote_config/upload', 'upload_remote_config', upload_remote_config, methods=['POST'])
    app.add_url_rule('/account/get_season_quests', 'get_season_quests', get_season_quests, methods=['POST'])
    app.add_url_rule('/account/trust/get_trust_activity_defs', 'get_trust_activity_defs', get_trust_activity_defs, methods=['POST'])
    app.add_url_rule('/account/buff/get_buff_defs', 'get_buff_defs', get_buff_defs, methods=['POST'])
    app.add_url_rule('/account/get_account_world_quests', 'get_account_world_quests', get_account_world_quests, methods=['POST'])
    app.add_url_rule('/account/claim_quest_reward', 'claim_quest_reward', claim_quest_reward, methods=['POST'])
    app.add_url_rule('/account/set_outfit', 'set_outfit', set_outfit, methods=['POST'])
    app.add_url_rule('/account/set_joinable', 'set_joinable', set_joinable, methods=['POST'])
    app.add_url_rule('/account/set_checkpoint', 'set_checkpoint', set_checkpoint, methods=['POST'])
    app.add_url_rule('/account/collect_pickup_batch', 'collect_pickup_batch', collect_pickup_batch, methods=['POST'])
    app.add_url_rule('/account/buy_candle_wax', 'buy_candle_wax', buy_candle_wax, methods=['POST'])
    app.add_url_rule('/account/create_invite', 'create_invite', create_invite, methods=['POST'])
    app.add_url_rule('/account/code/verify', 'verify_invite_code', verify_invite_code, methods=['POST'])
    app.add_url_rule('/account/check_invite', 'check_invite', check_invite, methods=['POST'])
    app.add_url_rule('/account/accept_invite', 'accept_invite', accept_invite, methods=['POST'])
    app.add_url_rule('/account/delete_invite', 'delete_invite', delete_invite, methods=['POST'])
    app.add_url_rule('/account/social_feed/create', 'create_social_feed', create_social_feed, methods=['POST'])
    app.add_url_rule('/account/social_feed/get_user_feeds', 'get_user_feeds', get_user_feeds, methods=['POST'])
    app.add_url_rule('/account/social_feed/get_curated_feeds', 'get_curated_feeds', get_curated_feeds, methods=['POST'])
    app.add_url_rule('/account/social_feed/get_reactions', 'get_social_feed_reactions', get_social_feed_reactions, methods=['POST'])
    app.add_url_rule('/account/social_feed/like', 'social_feed_like', social_feed_like, methods=['POST'])
    app.add_url_rule('/account/social_feed/delete', 'delete_social_feed', delete_social_feed, methods=['POST'])
    app.add_url_rule('/account/ack_unlock', 'ack_unlock', ack_unlock, methods=['POST'])
    app.add_url_rule('/account/chat/send', 'handle_chat_message', handle_chat_message, methods=['POST'])
    # 0.22 客户端另外用 /account/send_message (AccountSendMessageRequest, 字段
    # target/gift_type) 和历史上的 /account/send。这两条原来没有路由 -> 落到
    # "JSON 兜底 200 {}" -> 客户端拿不到 result, 认为消息没发出去。
    app.add_url_rule('/account/send', 'handle_send', handle_send_message, methods=['POST'])
    app.add_url_rule('/account/send_message', 'handle_send_message', handle_send_message, methods=['POST'])
    app.add_url_rule('/account/translate', 'handle_translate', handle_translate, methods=['POST'])
    app.add_url_rule('/account/set_device_token', 'set_device_token', set_device_token, methods=['POST'])
    # 高版本(> 0.26.5)抓包重写新增
    app.add_url_rule('/account/get_remote_outfit', 'get_remote_outfit', get_remote_outfit, methods=['POST'])
    app.add_url_rule('/account/give_candle', 'give_candle', give_candle, methods=['POST'])
    # 领取好友赠礼(心火/爱心)。以前这两条路径没注册 -> 404 -> 收礼方永远领不到
    # (request_log 实测 /account/claim_message_gifts 404)。
    app.add_url_rule('/account/claim_message_gift', 'claim_message_gift', claim_message_gift, methods=['POST'])
    app.add_url_rule('/account/claim_message_gifts', 'claim_message_gifts', claim_message_gift, methods=['POST'])
    # 加好友链路: 客户端消掉"给 TA 起个名字"那个提示(以前没注册 -> 被兜成 {} -> 节点解不开)
    app.add_url_rule('/account/remove_relationship_hint', 'remove_relationship_hint', remove_relationship_hint, methods=['POST'])
    app.add_url_rule('/account/get_generic_shop_item_tracking_info', 'get_generic_shop_item_tracking_info', get_generic_shop_item_tracking_info, methods=['POST'])
    app.add_url_rule('/account/purchase_dye', 'purchase_dye', purchase_dye, methods=['POST'])
    
    

__all__ = [
    'create_account',
    'account_create',
    'get_motd',
    'logini',
    'account_login',
    'get_currency',
    'get_rank',
    'get_userdata',
    'sync_user_data',
    'lookup',
    'get_latest_build_version',
    'get_vars',
    'has_reply',
    'get_achievement_stats_tracking',
    'get_event_currency_defs',
    'get_blocked_friends',
    'set_friend_name',
    'set_friend_block',
    'get_questionnaires',
    'set_achievement_stats',
    'get_surveys',
    'get_shop',
    'get_lootboxes',
    'redeem_box',
    'get_external_friends',
    'get_serendipity',
    'get_star_tags',
    'get_pending_messages',
    'claim_message_gift',
    'generic_shop_defs',
    'get_consumable_defs',
    'get_consumables',
    'register_account_routes',
    'get_forge_rates',
    'get_achievements',
    'get_achievement_stats',
    'get_relationship_abilities',
    'redeem_reward',
    'get_unlocks',
    'get_event_schedule',
    'iap_list',
    'commerce_receipt',
    'commerce_fulfill',
    'commerce_update',
    'commerce_product_info',
    'get_collectibles',
    'get_spirit_shops',
    'get_questionnaire_groups',
    'purchase_spirit_shop_item',
    'get_user_geonotes',
    'create_geonote2',
    'create_bug_report_geonote',
    'social_geonote2',
    'request_n_bug_report_geonotes',
    'delete_bug_report_geonote',
    'delete_geonote',
    'edit_message_geonote',
    'get_user_geonotes2',
    'request_geonotes_for_n_friends',
    'get_daily_quests',
    'get_weekly_quests',
    'get_season_quests_v2',
    'get_season_pass_defs',
    'get_currency_defs',
    'get_event_currency',
    'get_wing_buffs',
    'get_app_badge_number',
    'get_buffs1',
    'get_buffs',
    'remove_buff1',
    'purchase_event_buff',
    'purchase_generic_shop_item',
    'get_checkpoints',
    'get_invites',
    'get_online_friends',
    'purchase_unlock',
    'get_achievement_defs',
    'get_friend_statues',
    'get_friends',
    'join_friend_game',
    'get_consumables1',
    'get_trust_tier_defs',
    'get_map_defs',
    'get_video_defs',
    'get_radiance_spawn_defs',
    'get_outfit',
    'get_level_pickups',
    'find_previous_or_empty',
    'ack_forge_rate_reset',
    'get_quest_defs',
    'get_trust_group_defs',
    'season_finalize',
    'season_start',
    'activate_season_quest',
    'claim_season_quest_reward',
    'ack_collectible',
    'upload_remote_config',
    'get_season_quests',
    'get_trust_activity_defs',
    'get_buff_defs',
    'get_account_world_quests',
    'claim_quest_reward',
    'set_outfit',
    'set_joinable',
    'set_checkpoint',
    'collect_pickup_batch',
    'buy_candle_wax',
    'create_invite',
    'verify_invite_code',
    'check_invite',
    'accept_invite',
    'delete_invite',
    'create_social_feed',
    'get_user_feeds',
    'get_curated_feeds',
    'get_social_feed_reactions',
    'social_feed_like',
    'delete_social_feed',
    'ack_unlock',
    'handle_chat_message',
    'handle_send_message',
    'handle_translate',
    'set_device_token',
    'get_remote_outfit',
    'give_candle',
    'remove_relationship_hint',
    'get_generic_shop_item_tracking_info',
    'purchase_dye'
]
