# -*- coding: utf-8 -*-
"""
衣服(clothing/外观)辅助模块

机制说明：
- 游戏里的外观件(头发/面具/斗篷/裤子/项链/道具/角等)解锁名统一以 CharSky 开头，
  例如 CharSkyKid_Hair_Bun、CharSkyKid_Wing_ClassicOne_Default。
- 需求：所有常驻基础服饰(基础斗篷/身体/面具/头发)都标为"未拥有"，新号衣柜里
  这些基础款显示未拥有；全部外观需由管理层通过 /admin/api/push_full_unlocks
  推送才能获得，和"全动作/全光翼/开全门"一样不默认种入。

本模块提供：
- is_clothing(name)      判断是否为外观解锁项(CharSky 开头)
- default_clothes_names() 新号默认应保留的外观件 name 集合
"""

# 需求: 所有常驻基础服饰(基础斗篷/身体/面具/头发)一律标为"未拥有", 即新号衣柜里
#       这些基础款都显示为未拥有, 需由管理层通过 /admin/api/push_full_unlocks 推送
# 注意: 角色"默认穿戴"的初始装扮由 get_outfit.py / models.py 的 UserOutfit 默认 ID
#       控制, 与这里的"拥有"列表相互独立 —— 即使衣柜显示未拥有, 角色身上仍穿着初始
# 仅保留道具空槽(Prop_None), 让道具槽为空、正常渲染, 它不是可购买的常驻服饰。
DEFAULT_CLOTHES = frozenset({
    "CharSkyKid_Prop_None",                 # 道具空槽(不给乐器, 仅占位让道具槽为空)
})


def is_clothing(name):
    """判断某解锁名是否为外观(衣服)项。"""
    return bool(name) and name.startswith("CharSky")


def default_clothes_names():
    """新号默认应保留的外观件 name 集合。"""
    return set(DEFAULT_CLOTHES)
