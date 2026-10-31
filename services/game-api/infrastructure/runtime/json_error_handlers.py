# -*- coding: utf-8 -*-
"""
API 路径的 JSON 兜底(404/405/500 -> 200 + JSON)。

游戏客户端把**任何非 2xx**(以及解析不了的 HTML 响应体)都显示成客户端字符串
error_login_request —— 也就是玩家看到的"非法请求。\n请重试。"。而 Flask 默认对
未注册的路由返回一页 HTML 404, 对视图里抛出的异常返回 HTML 500。私服里只要客户端
请求了某个我们没实现的 /account/... 接口(例如后来的 /account/buff/remove_buff1、
/account/purchase_event_buff), 玩家就会看到"非法请求", 严重时直接闪退。

参考实现 that-sky-server 的做法: 不认识的请求一律回 2xx + 空 JSON 对象。
本模块照做, 但**只对游戏 API 前缀生效**(/account、/service、/starwatch),
后台面板(/admin)、首页、favicon 等 HTML 路径保持原样。

install(app) 是幂等的(重复调用只是覆盖同一批处理器), 由 register_account_routes
在启动和每次热更新时调用, 所以热更新后立刻生效。
"""
import json
import logging

from flask import Response, request
from werkzeug.exceptions import NotFound, MethodNotAllowed

logger = logging.getLogger(__name__)

# 游戏 API 前缀(这些路径的错误一律转成 2xx + JSON)
_API_PREFIXES = ("/account", "/service", "/starwatch")


def is_api_path(path):
    """是不是游戏 API 路径。"""
    path = path or ""
    for p in _API_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _empty_ok():
    return Response(response="{}", status=200, mimetype="application/json")


def install(app):
    """给 app 装上兜底处理器(幂等)。

    NotFound/MethodNotAllowed 同时按**状态码**和**异常类**注册: Flask 查处理器时
    先按 code 查(error_handler_spec[None][404]), 再按异常类查, 两处都放一份最稳。
    """
    def _handle_not_found(e):
        if is_api_path(request.path):
            logger.warning("未实现的接口 %s %s -> 200 {}", request.method, request.path)
            return _empty_ok()
        return e

    def _handle_method_not_allowed(e):
        if is_api_path(request.path):
            logger.warning("方法不允许 %s %s -> 200 {}", request.method, request.path)
            return _empty_ok()
        return e

    def _handle_internal_error(e):
        # 视图里抛异常: 客户端同样只会看到"非法请求", 所以这里也回 200 + {}。
        # 异常详情仍然写日志(便于排查), 只是不给客户端 5xx。
        if is_api_path(request.path):
            try:
                logger.error("接口异常 %s %s -> 200 {} (详情见下面 traceback)",
                             request.method, request.path, exc_info=True)
            except Exception:
                pass
            return _empty_ok()
        return e

    _register(app, 404, _handle_not_found)
    _register(app, NotFound, _handle_not_found)
    _register(app, 405, _handle_method_not_allowed)
    _register(app, MethodNotAllowed, _handle_method_not_allowed)
    _register(app, 500, _handle_internal_error)
    try:
        logger.info("JSON 错误兜底已装配(API 路径 404/405/500 -> 200 {})")
    except Exception:
        pass
    return app


def _register(app, code_or_exc, handler):
    """注册错误处理器, 热更新(已经处理过请求)后也能生效。

    Flask 3.x 的 register_error_handler 会调 _check_setup_finished: 应用处理过第一个
    请求之后就禁止再注册(抛 RuntimeError)。热更新场景下服务器已经跑起来了, 所以
    这里学 hot_reload 里"直接改 app.url_map"的做法, 绕过检查, 直接把处理器写进
    app.error_handler_spec(None=应用级, code=状态码, exc_class=异常类)。
    """
    if not getattr(app, "_got_first_request", False):
        # 启动阶段: 走正常注册
        app.register_error_handler(code_or_exc, handler)
        return
    # 运行中(热更新): 直接写 error_handler_spec
    exc_class, code = app._get_exc_class_and_code(code_or_exc)
    spec = app.error_handler_spec
    if code not in spec[None]:
        spec[None][code] = {}
    spec[None][code][exc_class] = handler