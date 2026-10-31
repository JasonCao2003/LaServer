# -*- coding: utf-8 -*-
"""
接口: /account/buff/get_buff_defs
函数: get_buff_defs()

获取 buff(魔法效果)定义表。客户端靠它解析 buff_id(时长/冷却/图标/特效),
定义表里没有的 buff_id 会渲染异常。

旧实现 send_from_directory(config/buff_defs.json) —— 只有 15KB 的残缺表(官方 532 条),
于是玩家身上挂了 buff 之后客户端查不到定义 -> 魔法/效果界面异常 -> 闪退。

现在(2026-09-13 定稿 —— 明文 + 体积由配置控制):
  1. 明文下发, **不压缩**。客户端虽然声明 Accept-Encoding: gzip, 但收到 gzip 的表
     并不解压 -> 定义不生效 -> 服饰类魔法(靠 buff 定义里的 effect/params 换装)
     全部"点了没反应"。所以 gzip_over_bytes 默认 0(关)。
  2. **体积闸门**以 instance/magic_defs_config.json 的 buff_defs_budget 为准(热读):
     = 0 时下发完整表(与官方逐字节相同, 官方就是给这台 0.33.2 设备发 244,289 字节
     明文), > 0 时超限就换体积受控子集并裁到该字节数。
     为什么需要它: 玩家反馈"大量魔法都没用、只有用过的才有反应" —— 客户端拿不到
     buff 定义就没有 UI/没有反应, 所以能全发就全发; 万一某台设备吃不下(闪退),
     把 buff_defs_budget 改回 86016 立刻恢复受控子集(不用重启/热重载)。
  3. 老客户端(<0.26.0)另走 magic_helper.defs_subset 的字节预算路线(同一套子集逻辑)。

两条约束缺一不可: 压缩 -> 定义不生效(服饰类没反应); 明文体积要按设备上限来。
"""
from paths import CONFIG_DIR, SERVICE_ROOT
import json
import os
from flask import Response, send_from_directory
import business.helpers.magic_helper as magic_helper

# 明文响应的体积上限(字节)。**以 instance/magic_defs_config.json 的
# buff_defs_budget 为准**(热读, 改完立刻生效):
#   > 0 -> 明文超过这个字节数就换成体积受控子集 + 从尾部裁剪(SAFE_MAX_BYTES 只是
#          配置读不到时的兜底值);
#   = 0 -> **不裁剪**: 下发完整表(532 条, compact 后正好 244,289 字节 —— 与官方
#          服务器给同一台设备(UA 0.33.2.384474)发的 Content-Length 一字不差,
#          抓包见 monitor_exports/2026-06-2*.json; 官方就是明文 + 不压缩)。
# 2026-09-13 记录: 之前崩的是"静态库压缩出来的 244,425 字节"版本(比官方多 136 字节),
# 而 magic_helper.json_resp 序列化出来的完整表与官方文件**逐字节相同**(脚本比对过),
# 所以按官方体积、官方格式下发是安全的。
SAFE_MAX_BYTES = 86016

# 2026-09-13 结论修正(重要): 之前以为"完整表 244KB 会把客户端打崩", 查完抓包发现是
# **误判** —— monitor_exports 里那些"官方锚点"其实都是**我们自己服务器**经 trycloudflare
# 隧道收的(capture 里 Host=*.trycloudflare.com、remote_addr=::1), Content-Length 244289
# 就是 static_responses 里我们自己那份表的体积, 不能证明官方发给 0.33.2 客户端的是 244KB。
# 真正的实测证据(同版本 0.33.2):
#   * 6/28-7/04 背包为空时, 客户端收下完整 244,289 字节明文表后照常继续(登录后还有
#     65~140 个请求) -> **明文大表本身不会闪退**;
#   * 9/13 21:30 闪退那次, 背包里有 507 件魔法 -> 崩在"背包件数 x 定义"上, 不是字节数;
#   * 9/13 19:42 同样 507 件但只发 187 条定义 -> 不闪退(只是其余魔法没 UI/没反应)。
# 所以这里恢复: **完整明文表照发**(覆盖率优先), 闪退交给 magic_helper.consumables_inventory
# 的件数上限(magic_list_max)去治。老客户端(<0.26.0)仍然强制走体积受控子集。
def _max_bytes():
    """本次响应允许的明文体积上限(0 = 不限制)。

    = instance/magic_defs_config.json 的 buff_defs_budget(热读)。
    但**老客户端(<0.26.0)永远不放开**: 它们解析不完大表会闪退(0.22.0 被 224,799
    字节的 buff 表打崩过), 所以即使配置写了 0, 老客户端也按 SAFE_MAX_BYTES 受控。
    """
    try:
        b = int(magic_helper.buff_defs_budget())
    except (TypeError, ValueError):
        b = SAFE_MAX_BYTES
    if b <= 0:
        if magic_helper.full_defs_allowed():
            return 0
        return SAFE_MAX_BYTES
    return b


def _json(body, status=200):
    # 大响应可能触发 gzip(见 magic_helper.json_resp); 默认 gzip_over_bytes=0 = 明文
    return magic_helper.json_resp(body, status=status)


def _plain_size(defs):
    """这份 buff 表按明文下发时的字节数。"""
    return len(json.dumps({"get_buff_defs": defs}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))


def _cap(defs, limit):
    """把超限的表从**尾部**逐步丢掉, 直到明文体积 <= limit(实现在 magic_helper)。"""
    return magic_helper.cap_defs_by_bytes(defs, limit, _plain_size)


def _legacy_stub():
    """极老客户端完全不发定义表时, 退回原来的小文件(保持兼容, 不报错)。"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = SERVICE_ROOT
    config_dir = CONFIG_DIR
    return send_from_directory(directory=config_dir, path='buff_defs.json',
                               mimetype='application/json')


def get_buff_defs():
    data = magic_helper.request_payload()
    user_id = data.get("user") or data.get("user_id")

    if magic_helper.full_defs_allowed():
        # 版本门控: 同 get_consumable_defs —— 客户端不认识 buff 定义里的 event 类名
        # ("Event named %s is not a BuffEvent")会断言崩溃, 全量表这条路径也要筛。
        defs = magic_helper.version_filter_buff_defs(magic_helper.buff_defs())
    else:
        # 老客户端路线: 只发"用得上"的子集(玩家拥有的/挂着的 buff + 宝箱魔法的 buff)。
        _consumables, defs = magic_helper.defs_subset(user_id)

    limit = _max_bytes()
    full_size = _plain_size(defs)
    if limit and full_size > limit:
        # 体积闸门(**不论客户端版本**): 明文超限就换成体积受控的子集。
        # 子集里"玩家身上正在生效的 buff"和"宝箱池魔法"一定在最前面, 玩家正在用的
        # 那几件(含最近用过的服饰)不会缺定义。
        _c2, sub = magic_helper.defs_subset(user_id)
        if sub and _plain_size(sub) < full_size:
            defs = sub
        defs = _cap(defs, limit)

    if not defs:
        return _legacy_stub()
    return _json({"get_buff_defs": defs})