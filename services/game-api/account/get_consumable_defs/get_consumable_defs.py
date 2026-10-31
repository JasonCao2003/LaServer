# -*- coding: utf-8 -*-
"""
接口: /account/consumable/get_consumable_defs
函数: get_consumable_defs()

获取魔法(消耗品)定义表。客户端靠它把服务端发来的 consumable_id 解析成
名字/图标/冷却 —— 定义表里没有的 id 会渲染失败甚至闪退。

旧实现直接返回 {"get_consumable_defs": []}(空表), 于是:
  - 背包里的魔法 / 宝箱开出来的魔法 全部'查无此物' -> 魔法界面异常 -> 闪退;
  - 购买魔法(先祖兑换树)后客户端拿不到新物品定义 -> 崩溃(用户反馈的现象)。

现在:
  - >= 0.26.0 的客户端本来就走接口库里的官方完整定义表(本函数不会被执行);
  - < 0.26.0 下发子集 = 宝箱内容物池 + 玩家已拥有的魔法。玩家拥有全部 507 件时就是
    完整表(紧凑 JSON 约 86KB), 这个体积 0.22.0 客户端**实测能吃下**(服务端日志
    16:19:43 收到 86,219 字节后继续正常发请求); 而 224KB 的 buff 表会把它打崩 ——
    所以 buff 表那边走字节预算, 见 account/get_buff_defs 与 magic_helper.defs_subset。
"""
import json
from flask import Response
import helpers.magic_helper as magic_helper

# 老客户端(<0.26.0)魔法定义表的明文体积上限(字节, 含外层 {"get_consumable_defs":...})。
# 锚点(同一台 0.22.0 设备, 同一个账号, 服务端日志):
#   * 19:16:12 收下 **86,219 字节** -> 之后照常发请求, 并在 19:18:18 成功创建了留影
#     (recording create -> consume_item1 -> social-feed create -> 列表 全通);
#   * 22:04:06 收下 119,215 字节的定义表(私服那份 599 条表裁过的) + 76,352 字节 buff
#     表 -> 22:05:06 /service/recording/api/v1/create 之后客户端就没了。
# 所以上限就钉在**实测通过的那个数**: 86,195(86,219 去掉 24 字节外层包装)。
# 注意私服那份 599 条定义表的**完整字段**版是 140,152 字节 —— 直接发一定超限, 一超限
# 就从尾部裁, 被裁的 148 件背包魔法在客户端就"没有 UI/点了没反应"。所以这里走
# magic_helper.fit_consumable_defs: 先给 UGC + 最近用过的保留完整字段、其余精简字段
# (0.22.0 本来就不认识 category/subcategory/order_in_subcategory), 保证**一件不缺**,
# 精简后仍超才从尾部整条丢(丢的都是不在背包里、客户端不渲染的)。
SAFE_MAX_BYTES = 86195


def _json(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False,
                                        separators=(",", ":")),
                    status=status, mimetype="application/json")


def _cap(defs, limit):
    """把定义表压进 limit 字节(见 SAFE_MAX_BYTES 上面的说明)。

    体积一律用 magic_helper.defs_json_size(**字节数**)来量 —— 曾经这里用 len(str)
    量(字符数), 条目里有非 ASCII 的名字/图标时字符数比字节数小, 于是"看着没超限、
    实际发出去超了"。
    """
    if not defs or magic_helper.defs_json_size(defs) <= limit:
        return defs
    return magic_helper.fit_consumable_defs(defs, limit)


def get_consumable_defs():
    data = magic_helper.request_payload()
    user_id = data.get("user") or data.get("user_id")

    if magic_helper.full_defs_allowed():
        # 版本门控: 老客户端(0.12.1 等)拿到"该版本没有的魔法事件类"会直接崩, 所以
        # 全量表这条路径也要按 UA 版本筛(见 magic_helper.MAGIC_EVENT_MIN_VERSION)。
        defs = magic_helper.version_filter_consumable_defs(magic_helper.consumable_defs())
    else:
        # 默认路线: 只发"用得上"的子集(玩家拥有的 + 宝箱可能开出的 + UGC 一族),
        # 再按 SAFE_MAX_BYTES 压体积(保证背包里一件魔法都不缺定义)。
        defs, _buffs = magic_helper.defs_subset(user_id)
        defs = _cap(defs, SAFE_MAX_BYTES)

    if not defs:
        # 定义表读不到(文件缺失/损坏)时保持旧行为, 不报错。
        return _json({"get_consumable_defs": []})
    return _json({"get_consumable_defs": defs})