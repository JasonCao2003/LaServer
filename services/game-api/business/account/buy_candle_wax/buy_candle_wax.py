"""
接口: /account/buy_candle_wax (锻造蜡烛 / 熔炼烛火)
响应: {"result":"ok", "currency":{...}, "update_forge_rates":[{...}]}   # 官方抓包形状

**请求体真相**(2026-09-13 实测抓包 monitor_exports/cache/account__buy_candle_wax.jsonc):
  {"user","session","currency":"heart","forge_currency":"heart_wax","count":4,"cost":300}
    currency        = 要锻造出的货币(heart / prestige / season_candle / candles …)
    forge_currency  = 用哪种"蜡"当原料(heart_wax / prestige_wax / season_wax / wax)
    cost            = **单个**要花的原料数(客户端从 get_forge_rates 档位算出)
    count           = 客户端算出来的"**还能锻造几个**"(= 现有原料 // cost)。
                      官方服务端是一次请求锻造 1 个, 客户端下次把 count 减 1 再发一发
                      (实测 4→3→2→1, 季蜡 219→218→217…, 升华 328→327→326…)。
                      本服按玩家要求 "锻造蜡烛之类的要一直全锻造完 且没有变化" 改成
                      **一次请求按 count 全锻造完**(见 FORGE_ALL_AT_ONCE): 扣
                      count*cost 份原料、产 count 个货币, 并把最新 currency 回给客户端,
                      这样原料见底、"可锻造" 立刻消失。想回官方那种一发一个的行为,
                      把 FORGE_ALL_AT_ONCE 改成 False 即可(一行)。

用户反馈"锻造爱心蜡烛时弹非法请求。\n请重试。":
  客户端按服务端下发的锻造档位(get_forge_rates: wax 档 current_tier=26/level=4,
  与数据库 forge_rate 表一致)算出一次锻造要 300 wax, 而该账号只有 4 wax, 于是
  旧实现返回 **HTTP 400 + INSUFFICIENT_WAX** -> 客户端把任何非 2xx 都显示成
  客户端字符串 error_login_request("非法请求。\n请重试。")。

修复:
  1. 任何情况都不再返回 4xx/5xx(全部 200 + JSON)。
  2. wax 不足时按 FORGE_SHORTAGE_MODE 处理, 默认 "cap": 只扣玩家现有的 wax(至少
     要 1), 锻造照样成功 —— 玩家不会再看到"非法请求", 也不会凭空多出发放的 wax。
     想改成"补足差额"或"直接拒绝", 改下面那个常量即可(一行)。
  3. wax 为 0 时锻造不成功(200 + result=error), 避免 0 成本无限刷蜡烛。

用户反馈"我的爱心季蜡季节爱心和升华蜡烛一直显示可锻造状态":
  这 4 种货币各有自己的"蜡"做原料, 而玩家那 4 个 *_wax 一直是 1314(从来没被扣过),
  客户端就一直显示"可锻造"; 而旧实现**只认 wax 一种**(而且只看 cost, 不看来源),
  于是玩家一按锻造就走白蜡分支 -> wax=0 -> INSUFFICIENT_WAX -> 界面上什么都没变。
  现在按请求体里的来源(优先 forge_currency; 其次 currency/source_type/wax_type…,
  找不到时扫字符串值)分别处理:
      wax->蜡烛, heart_wax->爱心, season_wax->季蜡,
      season_wax_14->季节爱心, prestige_wax/prestige_vip->升华蜡烛。
  修复后实测: 爱心蜡 1314→114、季蜡 1314→1128、升华蜡 1314→1266 都在正确扣减,
  对应货币逐个 +1,"一直可锻造"会随着原料见底自然消失。
  识别不出来源时仍按白蜡走(保持原行为), 并把请求体打进日志(去掉 session)以便校准。
"""
from flask import request, Response
from infrastructure.database.models import db, User, UserCurrency, ForgeRate
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

# wax 不足时的策略:
#   "cap"    -> 只扣玩家现有的 wax(至少 1), 锻造成功 【当前默认】
#   "topup"  -> 补足差额(等于发免费 wax)再锻造成功
#   "reject" -> 不锻造, 返回 200 + result=error(客户端不弹"非法请求")
FORGE_SHORTAGE_MODE = "cap"

# 一次锻造几个(玩家要求: "锻造蜡烛之类的要一直全锻造完 且没有变化")。
#   True  -> 按客户端这次的 count(它算出来的"还能锻造几个")**一次全锻造完**:
#            扣 count*cost 份原料、产 count 个货币, 并把最新 currency 回给客户端
#            -> 原料见底, 界面上的"可锻造"立刻消失(不再是一次只 +1、看起来"没变化")。
#   False -> 老行为: 每次只锻造 1 个(count 只当提示)。
FORGE_ALL_AT_ONCE = True

# 锻造来源 -> (要扣的蜡字段, 锻造产出的货币字段)
#   客户端对每种货币都有自己的"蜡"作为原料, 玩家那 4 种货币一直显示"可锻造"
#   就是因为对应的 *_wax 一直是 1314(从没被扣过), 而下面这些蜡都能扣。
FORGE_TARGETS = {
    "wax":           ("wax", "candles"),        # 白蜡  -> 蜡烛
    "heart_wax":     ("heart_wax", "heart"),            # 爱心蜡 -> 爱心
    "season_wax":    ("season_wax", "season_candle"),   # 季节蜡 -> 季蜡
    "season_wax_14": ("season_wax_14", "season_heart"), # 季节蜡(14) -> 季节爱心
    "prestige_wax":  ("prestige_wax", "prestige"),      # 升华蜡 -> 升华蜡烛
    "prestige_vip":  ("prestige_vip", "prestige"),
}

# 找不到 source_type 时可能出现的字段名(get_forge_rates 那份列表的下标也认)
# 顺序有意义: 先认实锤的 forge_currency(实测抓包里的原料字段), 再认各种猜法。
# "currency" 放在后面 —— 它是**产出**货币名, 只在凑巧是同名原料(如 wax)时才有提示作用。
_SOURCE_HINT_KEYS = (
    "forge_currency", "source_type", "source", "forge_type", "forge_source",
    "wax_type", "wax", "currency", "currency_type",
    "candle", "candle_type",
    "type", "kind", "item_type", "resource", "resource_type", "target", "target_type",
)
# 产出货币 -> 它的"蜡"原料。有些客户端版本不发 forge_currency, 只发 currency(要锻造
# 出哪种货币), 这时按这张表反推原料: 爱心 -> 爱心蜡, 季蜡 -> 季节蜡, 升华 -> 升华蜡。
# 实测抓包(0.33.x)是发 forge_currency 的, 这张表只是兜底。
TARGET_TO_SOURCE = {
    "candles": "wax", "candle": "wax",
    "heart": "heart_wax",
    "season_candle": "season_wax", "season_candles": "season_wax",
    "season_heart": "season_wax_14",
    "prestige": "prestige_wax",
}

# get_forge_rates 下发的固定顺序(客户端若发下标, 0 = wax)
_FORGE_SOURCE_ORDER = ["wax", "color_yellow_wax", "color_white_wax", "color_red_wax",
                       "color_magenta_wax", "color_green_wax", "color_cyan_wax",
                       "color_blue_wax", "color_black_wax"]


def _detect_forge_source(request_data):
    """从请求体里判断这次锻造用的是哪种蜡(识别不出来返回 None = 按老逻辑 wax)。

    没有任何地方记录过 buy_candle_wax 的请求体, 所以这里尽量宽容: 先按已知字段名找,
    再扫所有字符串值, 最后按 get_forge_rates 的顺序解释数字。
    真正的请求形状会在 server_runtime_8443.log 里以 INFO 打出来, 供下次校准。
    """
    names = set(FORGE_TARGETS)
    for key in _SOURCE_HINT_KEYS:
        if key not in request_data:
            continue
        val = request_data.get(key)
        if isinstance(val, bool) or val is None:
            continue
        if isinstance(val, str):
            v = val.strip().lower()
            if v in names:
                return v
        elif isinstance(val, int):
            if 0 <= val < len(_FORGE_SOURCE_ORDER):
                return _FORGE_SOURCE_ORDER[val]
    for val in request_data.values():
        if isinstance(val, str):
            v = val.strip().lower()
            if v in names:
                return v
    # 还没认出来: 有的版本不发 forge_currency, 只发 currency=要锻造出的货币名,
    # 就按"产出货币 -> 它的原料蜡"反推(heart -> heart_wax 等)。放在真正的原料名
    # 之后: 万一请求里既有原料名又有产出名, 以原料名为准。
    for val in request_data.values():
        if isinstance(val, str):
            src = TARGET_TO_SOURCE.get(val.strip().lower())
            if src:
                return src
    return None


def _log_body(request_data, source):
    safe = {k: v for k, v in request_data.items() if k != "session"}
    logger.info("buy_candle_wax: 请求体(已去掉session)=%s -> 锻造来源=%s",
                safe, source or "(未识别, 按 wax 处理)")


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user


def _resp(body, status=200):
    return Response(response=json.dumps(body, sort_keys=False, ensure_ascii=False),
                    status=status, mimetype='application/json')


def _currency_dict(user_currency):
    """货币字典: 与其他接口统一用 currency_helper.build_currency。"""
    try:
        from business.helpers.currency_helper import build_currency
        data = build_currency(user_currency)
        if data:
            return data
    except Exception:
        pass
    if user_currency is None:
        return {}
    return {
        "candles": user_currency.candles,
        "heart": user_currency.heart,
        "heart_wax": user_currency.heart_wax,
        "prestige": user_currency.prestige,
        "prestige_vip": user_currency.prestige_vip,
        "prestige_wax": user_currency.prestige_wax,
        "season_candle": user_currency.season_candle,
        "season_wax": user_currency.season_wax,
        "season_wax_14": user_currency.season_wax_14,
        "vip": user_currency.vip,
        "wax": user_currency.wax,
    }


def _forge_rate_payload(forge_rates):
    return {
        "source_type": forge_rates.source_type,
        "current_tier": forge_rates.current_tier,
        "current_level": forge_rates.current_level,
        "tier_completion": forge_rates.tier_completion,
        "level_completion": forge_rates.level_completion,
        "level_tier_index": forge_rates.level_tier_index,
        "level_tier_count": forge_rates.level_tier_count,
        "reset_acknowledged": forge_rates.reset_acknowledged,
        "enabled": forge_rates.enabled,
    }


def buy_candle_wax():
    try:
        request_data = request.get_json(force=True, silent=True) or {}

        user_id = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        cost_amount = request_data.get('cost')

        if not user_id or not session_token or cost_amount is None:
            # 不再 400: 客户端遇到非 2xx 会弹"非法请求"
            logger.warning("buy_candle_wax: 请求缺少 user/session/cost")
            return _resp({"result": "error", "message": "缺少参数", "currency": {},
                          "update_forge_rates": []})

        try:
            cost_amount = int(cost_amount)
        except (ValueError, TypeError):
            return _resp({"result": "error", "message": "无效的成本参数", "currency": {},
                          "update_forge_rates": []})
        if cost_amount <= 0:
            cost_amount = 1

        user = get_cached_user(user_id, session_token)
        if not user:
            logger.warning(f"buy_candle_wax: 用户 {user_id} 会话无效或用户不存在")
            return _resp({"result": "error", "message": "无效的会话或用户不存在",
                          "currency": {}, "update_forge_rates": []})

        user_currency = UserCurrency.query.filter_by(user_id=user_id).first()
        if not user_currency:
            user_currency = UserCurrency(
                user_id=user_id, candles=0, heart=0, heart_wax=0, prestige=0,
                prestige_vip=0, prestige_wax=0, season_candle=0, season_wax=0,
                season_wax_14=0, vip=0, wax=3)
            db.session.add(user_currency)
            logger.info(f"buy_candle_wax: 为用户 {user_id} 创建新的货币记录")

        # 这次锻造用的是哪种蜡(白蜡/爱心蜡/季节蜡/升华蜡…), 产出对应货币
        source = _detect_forge_source(request_data)
        _log_body(request_data, source)
        spend_field, gain_field = FORGE_TARGETS.get(source or "wax", ("wax", "candles"))

        # 客户端的 "count" = 它算出来的"还能锻造几个"(= 现有原料 // cost)。
        # FORGE_ALL_AT_ONCE 时按它一次全锻造完(见常量说明)。
        client_count = request_data.get("count")
        try:
            want_n = int(client_count)
        except (TypeError, ValueError):
            want_n = 1
        if want_n < 1:
            want_n = 1

        have_wax = int(getattr(user_currency, spend_field, 0) or 0)

        def _insufficient():
            return _resp({"result": "error", "message": "wax不足",
                          "code": "INSUFFICIENT_" + spend_field.upper(),
                          "source_type": source or "wax",
                          "current": have_wax, "required": cost_amount,
                          "current_wax": have_wax, "required_wax": cost_amount,
                          "currency": _currency_dict(user_currency),
                          "update_forge_rates": []})

        per = max(1, int(cost_amount))          # 单个的原料成本(客户端报价)
        forge_n = 1                             # 本次锻造几个
        charge = per

        if FORGE_ALL_AT_ONCE:
            afford = have_wax // per            # 现有原料最多能锻造几个
            forge_n = min(want_n, afford)
            if forge_n >= 1:
                charge = forge_n * per
                if charge > have_wax:           # 只剩零头时只扣现有的
                    charge = have_wax
            elif have_wax >= 1:
                # 一个都锻造不起: 按 cap/topup/reject 三种策略处理
                if FORGE_SHORTAGE_MODE == "cap":
                    forge_n, charge = 1, have_wax
                elif FORGE_SHORTAGE_MODE == "topup":
                    forge_n, charge = want_n, per * want_n
                else:
                    return _insufficient()
            else:
                # 一点原料都没有: 不锻造(200 + result=error, 客户端不弹"非法请求")
                logger.info("buy_candle_wax: 用户 %s %s=0, 不锻造", user_id, spend_field)
                return _insufficient()
        elif have_wax < per:
            if FORGE_SHORTAGE_MODE == "cap":
                if have_wax < 1:
                    # 一点蜡都没有: 不锻造(但仍然 200, 不弹"非法请求")
                    logger.info(f"buy_candle_wax: 用户 {user_id} {spend_field}=0, 不锻造")
                    return _insufficient()
                charge = have_wax
            elif FORGE_SHORTAGE_MODE == "topup":
                charge = per
            else:  # reject
                return _insufficient()
        else:
            charge = per

        candles_earned = int(forge_n)  # 每次锻造 1 份 cost 得 1 个对应货币
        setattr(user_currency, spend_field, have_wax - charge)
        setattr(user_currency, gain_field,
                int(getattr(user_currency, gain_field, 0) or 0) + candles_earned)
        user_currency.updated_at = datetime.utcnow()

        # 只有白蜡有自己的锻造档位(get_forge_rates 里也只有 wax + 8 种颜色蜡),
        # 其它货币没有档位记录, 就按原样回 [] —— 不凭空造一条客户端不认识的档位。
        forge_rates = None
        if spend_field == "wax":
            forge_rates = ForgeRate.query.filter_by(user_id=user_id, source_type='wax').first()
            if not forge_rates:
                forge_rates = ForgeRate(
                    user_id=user_id, source_type='wax', current_tier=1, current_level=1,
                    tier_completion=0.0, level_completion=0.0, level_tier_index=0,
                    level_tier_count=5, reset_acknowledged=True, enabled=True,
                    last_updated=datetime.utcnow())
                db.session.add(forge_rates)
                logger.info(f"buy_candle_wax: 为用户 {user_id} 创建默认锻造率记录")
            else:
                forge_rates.last_updated = datetime.utcnow()

        db.session.commit()
        logger.info(f"buy_candle_wax: 用户 {user_id} 一次锻造 {forge_n} 个, 扣 {charge} "
                    f"{spend_field}(客户端报价 cost={cost_amount} count={client_count}), "
                    f"得 {candles_earned} {gain_field}")

        return _resp({
            "result": "ok",
            "currency": _currency_dict(user_currency),
            "update_forge_rates": [_forge_rate_payload(forge_rates)] if forge_rates else [],
        })

    except Exception as e:
        logger.error(f"buy_candle_wax: 服务器内部错误: {e}", exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return _resp({"result": "error", "message": "服务器内部错误",
                      "currency": {}, "update_forge_rates": []})