# -*- coding: utf-8 -*-
"""
统一货币响应构建器。

背景(修复扣费动画异常):
  游戏客户端把每个返回 currency 的接口响应都当作"货币最新快照",
  并按字段差值播放数字滚动动画。若不同接口返回的 currency 字段集合
  不一致(例如 get_currency 带 season_heart / season_heart_<N>,
  而 purchase_spirit_shop_item 不带, 还塞了一堆 season_candle_14..23:0),
  客户端在购买后会先把"缺失/被置0的字段"动画到 0, 下一次 get_currency
  再把真实值动画涨回来 —— 表现为"消费3个爱心后, 爱心先掉光再涨回正常值"。

修复:
  所有接口统一用 build_currency() 产出同一套字段、同一套真实值,
  且只下发"当前赛季"的带编号季节货币(season_candle_<N>/season_heart_<N>),
  不再下发任何写死的 0 字段。这样客户端每次拿到的快照一致, 不再来回跳。
"""

# 基础货币字段 -> UserCurrency 模型属性名
_BASE_FIELDS = (
    "candles",
    "heart",
    "heart_wax",
    "prestige",
    "prestige_vip",
    "prestige_wax",
    "season_candle",
    "season_wax",
    "season_wax_14",
    "season_heart",
    "season_pass_token",
    "vip",
    "wax",
)


def _current_season():
    """当前赛季号(>0 时给季节货币带编号下发); 读不到返回 0。"""
    try:
        from business.helpers.game_control_helper import get_season_config
        return int(get_season_config().get("current_season", 0) or 0)
    except Exception:
        return 0


def default_currency_values():
    """新账号默认货币 —— 管理面板『账号默认数据』的值。

    来源: config/game_control.json -> season.finalize_currency
    (game_control_panel.py 的"保存新账号默认货币"写的就是这里, 例如 candles=300)。
    缺失字段回退: wax=3, 其余 0 (与 game_control_helper.DEFAULT_FINALIZE_CURRENCY 一致)。

    [2026-10-06 修正] 这份配置以前只被 get_season_config() 读出来, 从来没有任何
    建号/建货币行的地方用它 —— 面板把新号蜡烛设成 300, 新号进游戏还是 0 蜡烛。
    """
    vals = {f: 0 for f in _BASE_FIELDS}
    vals["wax"] = 3
    try:
        from business.helpers.game_control_helper import get_season_config
        cur = get_season_config().get("finalize_currency") or {}
        for f in _BASE_FIELDS:
            if f in cur:
                try:
                    vals[f] = int(cur[f] or 0)
                except (TypeError, ValueError):
                    pass
    except Exception:
        # 配置读不到: 用上面的安全默认值, 不能因为这个把建号打回去
        pass
    return vals


def ensure_user_currency(user_id):
    """取该用户的 UserCurrency 行; 没有就按面板『账号默认数据』建一行(不 commit)。

    所有"兜底建货币行"的接口都该走这里, 新号一进游戏才有面板设的初始蜡烛/爱心/
    季蜡, 而不是各接口里写死的 0。
    """
    from infrastructure.database.models import db, UserCurrency
    row = UserCurrency.query.filter_by(user_id=user_id).first()
    if row is None:
        row = UserCurrency(user_id=user_id, **default_currency_values())
        db.session.add(row)
    return row


def build_currency(user_currency):
    """根据 UserCurrency 行构建统一的 currency dict。

    user_currency 为空时返回全 0(wax 给 0)。所有接口共用此函数,
    保证客户端每次拿到的货币快照字段一致, 不再出现"掉光再涨回"。
    """
    cur = {}
    for f in _BASE_FIELDS:
        cur[f] = (getattr(user_currency, f, 0) or 0) if user_currency is not None else 0

    # 季节货币按"当前赛季"带编号下发: 季节面板读 season_candle_<N> / season_heart_<N>。
    sn = _current_season()
    if sn > 0:
        cur[f"season_candle_{sn}"] = cur["season_candle"]
        cur[f"season_heart_{sn}"] = cur["season_heart"]
    return cur
