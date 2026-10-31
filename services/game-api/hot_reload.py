# -*- coding: utf-8 -*-
"""
热更新(后台源码更新): /admin/api/hot_reload

让管理员在私服运行中(玩家不断连)重新加载接口源码 —— 修 bug、改逻辑、加新接口
都不必重启服务器。原理:
  - 重新 import 各接口注册模块(account/service/starwatch/admin/static_responses)
  - 用新版函数覆盖 Flask 已注册端点的视图函数(app.view_functions[endpoint]=新函数)
  - 对全新接口(新增的 add_url_rule)直接注册到 app.url_map
"""
import importlib
import sys
from flask import jsonify


# 需要热重载的接口包(包级 register_* 入口)
_ROUTE_PACKAGES = [
    ("account", "register_account_routes"),
    ("service", "register_service_routes"),
    ("starwatch", "register_starwatch_routes"),
    ("admin", "register_admin_routes"),
]


def _reload_package_tree(pkg_name):
    """重新加载某个包及其所有已加载的子模块(先子后父)。返回重载数量。"""
    reloaded = 0
    for modname in list(sys.modules.keys()):
        if modname == pkg_name:
            continue
        if modname.startswith(pkg_name + "."):
            mod = sys.modules.get(modname)
            if mod is None:
                continue
            try:
                importlib.reload(mod)
                reloaded += 1
            except Exception:
                pass
    try:
        if pkg_name in sys.modules:
            importlib.reload(sys.modules[pkg_name])
            reloaded += 1
    except Exception:
        pass
    return reloaded


def _reregister(app, pkg_modules, errors):
    """重新执行各包的 register_*, 期间允许覆盖已存在端点(改接口)或新增端点(加接口)。"""
    count = 0
    orig_add = app.add_url_rule

    def _patched_add(rule, endpoint=None, view_func=None, **options):
        nonlocal count
        ep = endpoint or (view_func.__name__ if view_func else None)
        if not ep or view_func is None:
            return
        try:
            if ep in app.view_functions:
                # 已有接口: 直接覆盖视图函数, 立即生效(不动 url_map)
                app.view_functions[ep] = view_func
                count += 1
                return
            # 新接口: Flask 3.x 在首个请求后禁止 add_url_rule, 故直接操作 url_map +
            # view_functions, 绕过 setup 检查, 实现"运行中加新接口"。
            methods = options.get("methods") or ["GET"]
            methods = set(m.upper() for m in methods)
            methods.add("OPTIONS")
            if "GET" in methods:
                methods.add("HEAD")
            from werkzeug.routing import Rule as _Rule
            app.url_map.add(_Rule(rule, endpoint=ep, methods=methods))
            app.view_functions[ep] = view_func
            count += 1
        except Exception as e:
            try:
                app.view_functions[ep] = view_func
                count += 1
            except Exception as e2:
                errors.append("注册 %s 失败: %s / %s" % (rule, e, e2))

    app.add_url_rule = _patched_add
    try:
        for pkg, regname in [(p, r) for p, r in _ROUTE_PACKAGES if p in pkg_modules]:
            mod = pkg_modules.get(pkg)
            reg = getattr(mod, regname, None)
            if reg:
                try:
                    reg(app)
                except Exception as e:
                    errors.append("%s.%s: %s" % (pkg, regname, e))
        # 接口库静态响应: 重新注册会触发 before_request setup(Flask 3.x 已禁止),
        # 这里只重载其内部数据/响应映射, 不重复注册 before_request 钩子。
        try:
            sr = importlib.import_module("resources.static_responses")
            if hasattr(sr, "reload_library"):
                sr.reload_library()
        except Exception as e:
            errors.append("static_responses: %s" % e)
    finally:
        app.add_url_rule = orig_add
    return count


def _apply_tls_quiet_patch():
    """给运行中的进程打 TLS 握手静默补丁(幂等)。

    服务器用自签名证书。客户端(cloudflared 校验 SAN 不含 ::1、互联网扫描器、
    或任何拒绝自签名证书的 TLS 客户端)在握手阶段发回 bad_certificate 警报时,
    eventlet 的 process_request 只捕获 socket.timeout, 未捕获 ssl.SSLError,
    异常逃逸到 greenthread hub 被打印成整段 traceback, 刷屏且无意义。
    这里 monkey-patch 类方法 process_request, 让这类良性握手失败只记一行
    debug 日志。替换的是类方法, 运行中的 WSGI 实例下个请求即生效, 无需重启。
    返回 True 表示本次新打了补丁, False 表示已打过(幂等跳过)。
    """
    import ssl as _ssl
    import eventlet.wsgi as _ev_wsgi

    if getattr(_ev_wsgi.Server.process_request, "_tls_quiet_patched", False):
        return False

    _orig_process_request = _ev_wsgi.Server.process_request

    def _quiet_process_request(self, conn_state):
        try:
            _orig_process_request(self, conn_state)
        except _ssl.SSLError as _e:
            # TLS 握手失败(证书被拒/协议不符等), 属良性, 不打印 traceback
            try:
                conn_state[1].close()
            except Exception:
                pass
            try:
                self.log.debug('({}) TLS handshake failed {!r}: {}'.format(
                    self.pid, conn_state[0], _e))
            except Exception:
                pass

    _quiet_process_request._tls_quiet_patched = True
    _ev_wsgi.Server.process_request = _quiet_process_request
    return True


def hot_reload_routes(app):
    """重新加载并重新注册所有接口。返回 (重载模块数, 重注册端点数, 错误列表)。"""
    errors = []
    reloaded = 0
    # TLS 握手静默补丁: 给运行中的进程消除自签名证书被拒时刷屏的 traceback。
    # 放在热更新里, 这样调一次 hot_reload 就能给活着的服务器打补丁, 无需重启。
    try:
        _apply_tls_quiet_patch()
    except Exception as e:
        errors.append("tls_quiet_patch: %s" % e)
    # 独立模块(非接口包, 但被接口 import): 必须先于接口包重载, 否则接口拿到的还是旧代码。
    # room_helper 被 find_previous_or_empty import; game_control_helper 被季节接口 import,
    # full_unlock_helper/outfit_version_db 被 get_unlocks/get_unlocks_old/push_full_unlocks
    # import (版本分流全解锁)。改了它们也要一起热更新, 否则接口 import 新函数会失败。
    # magic_helper / json_error_handlers: 魔法(buff/消耗品/宝箱)与 API JSON 兜底的
    # 公共模块, 被 account 下的接口 import。改它们也要一起热更新, 否则接口拿到的
    # 还是旧代码(尤其是 magic_helper 里的定义表缓存与宝箱内容物规则)。
    # friend_helper: 好友关系/自动点亮的公共模块, 被 account/get_friends、
    # account/get_friend_statues、account/get_online_friends import。它没有需要保留的
    # 全局状态(只有一个 2 秒的在线列表缓存), 重载安全, 但**必须排在接口包之前**,
    # 否则接口拿到的是旧代码。
    for m in ("resources.static_responses",
              # UA 版本解析(带谎报纠偏)与版本分类辅助: 被 get_unlocks/_old、
              # full_unlock_helper、outfit_version_db、magic_helper、static_responses、
              # get_spirit_shops、get_event_schedule 等大量接口 import。
              # 改了 client_version_overrides.json 也要靠它生效 -> 必须先重载。
              "helpers.client_version", "helpers.version_helper",
              "helpers.room_helper", "helpers.game_control_helper", "helpers.file_override",
              # 货币公共模块: 统一货币响应 + 新号默认货币/兜底建货币行
              # (ensure_user_currency / default_currency_values, 被 get_currency、
              # season_finalize、auth_login 自动建号、create/auth_create 注册调用)。
              # 不在接口包里, 必须一起热更新, 否则接口 import 新函数会失败。
              "helpers.currency_helper",
              "helpers.full_unlock_helper", "helpers.outfit_version_db", "helpers.status_unlock_tombstone",
              "request_monitor_middleware", "helpers.magic_helper", "json_error_handlers",
              # 好友关系自动补全(自动点亮/好友树/好友状态)
              "helpers.friend_helper",
              # 好友赠礼(心火/爱心)公共模块, 被 account/send、account/give_candle、
              # account/claim_message_gift、service/relationship/free_gifts/* import。
              # 必须排在接口包之前, 否则接口拿到的是旧代码。
              "helpers.gift_helper",
              # 共享空间道具注入(被 service/status/get_unlocks 用)
              "helpers.shared_space_props"):
        try:
            reloaded += _reload_package_tree(m)
        except Exception as e:
            errors.append("%s: %s" % (m, e))
    pkg_modules = {}
    for pkg, _reg in _ROUTE_PACKAGES:
        try:
            reloaded += _reload_package_tree(pkg)
            pkg_modules[pkg] = importlib.import_module(pkg)
        except Exception as e:
            errors.append("%s: %s" % (pkg, e))
    reregistered = _reregister(app, pkg_modules, errors)
    return reloaded, reregistered, errors


def register_hot_reload(app):
    """注册 /admin/api/hot_reload 接口。"""
    def _endpoint():
        try:
            # 先重载 hot_reload 自身, 以最新逻辑执行(这样改了热更新逻辑也能热生效)
            try:
                import importlib as _il, sys as _sys
                if "hot_reload" in _sys.modules:
                    _newmod = _il.reload(_sys.modules["hot_reload"])
                    if hasattr(_newmod, "hot_reload_routes"):
                        reloaded, reregistered, errors = _newmod.hot_reload_routes(app)
                        ok = not errors
                        from flask import jsonify as _jf
                        return _jf({
                            "code": 200 if ok else 207,
                            "msg": "热更新完成" if ok else "热更新部分完成(有警告)",
                            "reloaded_modules": reloaded,
                            "reregistered_endpoints": reregistered,
                            "errors": errors[:20],
                        })
            except Exception:
                pass
            reloaded, reregistered, errors = hot_reload_routes(app)
            ok = not errors
            return jsonify({
                "code": 200 if ok else 207,
                "msg": "热更新完成" if ok else "热更新部分完成(有警告)",
                "reloaded_modules": reloaded,
                "reregistered_endpoints": reregistered,
                "errors": errors[:20],
            })
        except Exception as e:
            return jsonify({"code": 500, "msg": "热更新失败: %s" % e}), 500

    app.add_url_rule("/admin/api/hot_reload", "hot_reload", _endpoint,
                     methods=["POST", "GET"])
