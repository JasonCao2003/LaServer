# -*- coding: utf-8 -*-
"""
Sky游戏服务器主入口
所有接口已模块化，此文件仅负责初始化和启动
"""
from paths import CERTS_DIR, DATA_DIR, SERVICE_ROOT, SERVER_CONFIG_PATH

from cryptography.hazmat.primitives.asymmetric import ec
from flask import session
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from flask_sock import Sock
from flask_socketio import SocketIO, emit, join_room, leave_room, send
import eventlet
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask import send_from_directory
from sqlalchemy import Column, String, Integer, ForeignKey, create_engine, select, BigInteger, Boolean, Float, Text, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from sqlalchemy import inspect
import requests
import uuid
import json
import os
import time
import secrets
import sqlite3
import hashlib
import jwt
import bcrypt
import base64
import logging
import glob
import random
from functools import lru_cache
import eventlet
from eventlet import semaphore
import re

# ========== 优雅处理 TLS 握手失败 ==========
# 服务器用自签名证书。当客户端(如 cloudflared 校验、互联网扫描器、或证书
# SAN 不匹配的连接)在 TLS 握手阶段拒绝证书并发回 bad_certificate 警报时,
# eventlet 的 process_request 只捕获 socket.timeout, 未捕获 ssl.SSLError,
# 导致异常逃逸到 greenthread hub 被打印成整段 traceback, 刷屏且无意义。
# 这里 monkey-patch process_request, 让这类良性握手失败只记一行 debug 日志。
try:
    import ssl as _ssl
    import eventlet.wsgi as _ev_wsgi

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

    _ev_wsgi.Server.process_request = _quiet_process_request
except Exception as _patch_err:
    logging.getLogger(__name__).warning(
        "无法应用 TLS 握手静默补丁: %s", _patch_err)

from urllib.parse import parse_qs, unquote
import binascii
from infrastructure.database.models import db

# 导入WebSocket处理模块
import infrastructure.transport.websocket_handler as websocket_handler

from infrastructure.database import migrate_database

from business.account import register_account_routes
from business.service import register_service_routes
from business.starwatch import register_starwatch_routes

from infrastructure.database.models import *

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Flask应用初始化
app = Flask(__name__, instance_path=os.path.join(DATA_DIR, "instance"))
sock = Sock(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['JSON_AS_ASCII'] = False  # 确保JSON不转义非ASCII字符
app.config['JSONIFY_MIMETYPE'] = 'application/json; charset=utf-8'

# SQLAlchemy
db.init_app(app)

shujuku = 'sqlite:///' + os.path.join(DATA_DIR, 'legacy', 'users.db').replace('\\', '/')
engine = create_engine(shujuku)

CONFIG_PATH = SERVER_CONFIG_PATH

@lru_cache(maxsize=1)
def load_config():
    """加载并缓存全局配置（避免重复读盘，提高性能）"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

# ========== 全局变量 ==========

chat_clients = {}
native_ws_clients = {}
mqtt_clients = set()

pending_messages = {}
_pending_messages_lock = semaphore.Semaphore()

_session_cache = {}
_session_cache_lock = semaphore.Semaphore()
_session_cache_ttl = 60
_session_cache_cleanup_counter = 0

_SIZE_PATTERN = re.compile(r'^s:\s*(-?[\d\.]+)\s+h:\s*(-?[\d\.]+)$')
_CURRENCY_PATTERNS = {
    '蜡烛': re.compile(r'^蜡烛(-?[\d\.]+)$'),
    '爱心': re.compile(r'^爱心(-?[\d\.]+)$'),
    '烛火': re.compile(r'^烛火(-?[\d\.]+)$'),
    '爱心烛火': re.compile(r'^爱心烛火(-?[\d\.]+)$')
}

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（性能优化）"""
    global _session_cache, _session_cache_cleanup_counter, _session_cache_lock, _session_cache_ttl
    
    _session_cache_lock.acquire()
    try:
        if _session_cache is None or not isinstance(_session_cache, dict):
            _session_cache = {}
        if _session_cache_cleanup_counter is None:
            _session_cache_cleanup_counter = 0
    finally:
        _session_cache_lock.release()
    
    cache_key = f"{user_id}:{session_token}"
    current_time = time.time()
    
    _session_cache_lock.acquire()
    try:
        if isinstance(_session_cache, dict) and cache_key in _session_cache:
            cached_data, cached_time = _session_cache[cache_key]
            if current_time - cached_time < _session_cache_ttl:
                return cached_data
    finally:
        _session_cache_lock.release()
    
    user = User.query.filter_by(id=user_id, session=session_token).first()
    
    if user:
        _session_cache_lock.acquire()
        try:
            if not isinstance(_session_cache, dict):
                _session_cache = {}
            
            _session_cache[cache_key] = (user, current_time)
            _session_cache_cleanup_counter += 1
            
            if _session_cache_cleanup_counter >= 100:
                _session_cache_cleanup_counter = 0
                if isinstance(_session_cache, dict):
                    old_cache = _session_cache.copy()
                    _session_cache = {k: v for k, v in old_cache.items() 
                                    if current_time - v[1] < _session_cache_ttl}
        finally:
            _session_cache_lock.release()
    
    return user


# ========== 注册所有路由 ==========
# 最基础(客户端游戏必需): 账号 / 服务 / 观星
register_account_routes(app)
register_service_routes(app)
register_starwatch_routes(app)


# 接口库版本分流: 高版本(>=0.30.0)走接口库静态响应, 旧版完全不受影响(走原动态源码)
# 排除表(登录/鉴权/开全图开全动作/季节&公告等)无论版本高低都保持动态。
try:
    from infrastructure.responses import register_static_responses
    register_static_responses(app)
except Exception as _e:
    logger.warning(f"接口库版本分流注册跳过: {_e}")

# 热更新(后台源码更新): 注册 /admin/api/hot_reload, 运行中重载接口源码不需重启
try:
    from infrastructure.runtime.hot_reload import register_hot_reload
    register_hot_reload(app)
    logger.info("热更新接口已注册: /admin/api/hot_reload")
except Exception as _e:
    logger.warning(f"热更新接口注册跳过: {_e}")


@app.route('/')
def _landing_page():
    """用户用浏览器直接访问域名时的引导页(标题"土豆", 教如何改包接入私服)。"""
    return Response(_LANDING_HTML, status=200, mimetype="text/html; charset=utf-8")


@app.route('/favicon.ico')
def _favicon_landing():
    """浏览器访问 /favicon.ico 时也返回引导页(而非 404), 与首页一致。"""
    return Response(_LANDING_HTML, status=200, mimetype="text/html; charset=utf-8")


_LANDING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>土豆</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Microsoft YaHei",system-ui,sans-serif;
 background:linear-gradient(135deg,#1a1c2e 0%,#2d2f4a 100%);
 color:#e6edf3;min-height:100vh;display:flex;align-items:center;
 justify-content:center;padding:24px}
.card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);
 border-radius:16px;max-width:680px;width:100%;padding:36px 32px;
 box-shadow:0 8px 40px rgba(0,0,0,.4)}
h1{font-size:38px;text-align:center;
 background:linear-gradient(90deg,#ffd54f,#ff9800);
 -webkit-background-clip:text;background-clip:text;color:transparent}
.sub{text-align:center;color:#8b949e;font-size:16px;margin:8px 0 28px}
ol{line-height:2;font-size:15px;padding-left:22px}
li{margin-bottom:10px}
code{background:rgba(255,255,255,.1);padding:2px 8px;border-radius:6px;
 font-family:Consolas,monospace;color:#7ee787;word-break:break-all}
.hl{color:#ffd54f;font-weight:600}
.tip{margin-top:24px;padding:14px 16px;background:rgba(255,213,79,.08);
 border-left:3px solid #ffd54f;border-radius:6px;font-size:14px;color:#c9d1d9}
.path{color:#79c0ff}
</style>
</head>
<body>
<div class="card">
  <h1>土豆</h1>
  <div class="sub">你走错路啦</div>
  <ol>
    <li>请将域名去掉 <code>https://</code> 后改入包体。
        例如 <code>https://a-ipaggy-asd.com</code> 只保留 <code>a-ipaggy-asd.com</code></li>
    <li>打开安装包(<span class="hl">.apk</span> 文件)</li>
    <li>点击里面后缀是 <span class="hl">.dex</span> 的文件,选 <span class="hl">Dex 编辑器++</span></li>
    <li>然后<span class="hl">全选</span>,再依次打开文件夹:
        <span class="path">com → tgc.sky → (文件) BuildConfig</span>
        <br>完整路径 <code>com/tgc/sky/BuildConfig</code></li>
    <li>打开该文件后找到 <code class="hl">SKY_SERVER_HOSTNAME</code>,
        将后面的域名改成<span class="hl">私服域名</span></li>
    <li>然后<span class="hl">退出 → 保存 → 再安装</span></li>
  </ol>
  <div class="tip">
    找不到 <code>SKY_SERVER_HOSTNAME</code> 时,可以看看有没有 <span class="hl">live</span>
    或 <span class="hl">beta</span> 开头的域名,有就替换这个域名。
  </div>
</div>
</body>
</html>"""
if __name__ == '__main__':
    from infrastructure.monitoring.request_monitor_middleware import init_request_monitor
    init_request_monitor(app)

    # ?????? GUI?????????????
    from infrastructure.monitoring.request_monitor_gui import start_monitor_gui
    start_monitor_gui()

    with app.app_context():
        db.create_all()
        migrate_database(db)
        # 房间池: 服务器启动时自动创建并保持 3 个空闲房间(所有用户通用)。
        try:
            from business.helpers.room_helper import ensure_idle_rooms
            from infrastructure.database.models import GameServer, ServerAssignment
            n = ensure_idle_rooms(db, GameServer, ServerAssignment)
            logger.info("房间池就绪, 当前空闲房间数: %s", n)
        except Exception as _e:
            logger.warning("房间池初始化失败: %s", _e)
    
    # 初始化WebSocket处理器
    websocket_handler.init_websocket(
        app=app,
        socketio_instance=socketio,
        sock_instance=sock,
        user_model=User,
        db_instance=db,
        chat_clients_dict=chat_clients,
        native_ws_clients_dict=native_ws_clients,
        mqtt_clients_set=mqtt_clients,
        pending_messages_dict=pending_messages,
        pending_messages_lock=_pending_messages_lock,
        session_cache_lock=_session_cache_lock
    )
    
    # 启动服务器（使用eventlet支持WebSocket）
    # 端口可由环境变量 SKY_PORT 指定(管理面板多开时各实例用不同端口); 默认 8443。
    # 多开关键: 探测必须用 eventlet.listen(与 socketio.run 内部相同的绑定调用, 会设
    # SO_REUSEADDR)。Windows 上普通 socket.bind 的结果和 eventlet 不一致, 会误判端口
    # 可用 -> socketio 仍报 10048。这里用 eventlet.listen 探测: 绑得上才用, 绑不上
    # (端口被占)就自增; 探到可用端口后关闭探测 socket, 再交给 socketio.run 正式绑定。
    import os as _os
    _base = int(_os.environ.get("SKY_PORT", "8443") or "8443")
    _host = _os.environ.get("SKY_HOST", "::")

    # ---- Windows 上让 "::" 监听变成双栈(IPv6 + IPv4) ----
    # 根因: Windows 的 IPV6_V6ONLY 默认是 1(和 Linux 相反)。原来的
    # socketio.run(host='::') 内部(flask_socketio.SocketIO.run)是这样绑的:
    #     eventlet.listen(getaddrinfo('::', port)[0][4], family=AF_INET6)
    # 拿到的就是 IPv6-only 套接字, 实测(2026-10-03):
    #     ::1:8443         -> OK      (cloudflared 隧道走这条进 origin)
    #     127.0.0.1:8443   -> 10061 拒绝
    #     192.168.10.5:8443-> 10061 拒绝   <-- 局域网客户端全体被挡
    # 而客户端聊天用的域名是 API 域名插 ".ws" 得来(libBootloader sub_42FE58),
    # 例 192-168-10-5.sslip.io -> 192-168-10-5.ws.sslip.io -> 都是 192.168.10.5,
    # 所以必须让这个端口在 IPv4 上也收连接, 否则局域网聊天永远建不起 ws。
    # 做法: 把 eventlet.listen 包一层 —— 建 AF_INET6 套接字后、bind 之前关掉
    # IPV6_V6ONLY, 同一个 "::" 套接字就同时收 IPv4(内核映射成 ::ffff:192.168.x.x)
    # 与 IPv6; 隧道那条 ::1 不受影响。非 AF_INET6 的调用原样走老实现。
    _real_eventlet_listen = eventlet.listen

    def _listen_dual(addr, family=eventlet.green.socket.AF_INET, backlog=50,
                     reuse_addr=True, reuse_port=None):
        _gs = eventlet.green.socket
        if family != _gs.AF_INET6:
            return _real_eventlet_listen(addr, family=family, backlog=backlog,
                                         reuse_addr=reuse_addr,
                                         reuse_port=reuse_port)
        _sock = _gs.socket(family, _gs.SOCK_STREAM)
        try:
            _sock.setsockopt(_gs.IPPROTO_IPV6, _gs.IPV6_V6ONLY, 0)
        except OSError:
            # 该选项在个别环境下不可用: 退回原实现, 至少不比以前差
            _sock.close()
            return _real_eventlet_listen(addr, family=family, backlog=backlog,
                                         reuse_addr=reuse_addr,
                                         reuse_port=reuse_port)
        if reuse_addr and _os.name != "nt":   # 与 eventlet 一致: Windows 不设 SO_REUSEADDR
            _sock.setsockopt(_gs.SOL_SOCKET, _gs.SO_REUSEADDR, 1)
        if reuse_port and hasattr(_gs, "SO_REUSEPORT"):
            try:
                _sock.setsockopt(_gs.SOL_SOCKET, _gs.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            _sock.bind(addr)
            _sock.listen(backlog)
        except Exception:
            try:
                _sock.close()
            except Exception:
                pass
            raise
        return _sock

    eventlet.listen = _listen_dual

    _chosen = None
    _last_err = None
    for _p in range(_base, _base + 30):
        try:
            _family = (eventlet.green.socket.AF_INET6 if ":" in _host
                       else eventlet.green.socket.AF_INET)
            _probe = eventlet.listen((_host, _p), family=_family)
        except Exception as _e:
            _last_err = _e
            print("端口 %d 被占用, 尝试下一个..." % _p, flush=True)
            continue
        _probe.close()   # 立即释放, 交给 socketio.run 正式绑定(顺序启动下竞态窗口极小)
        _chosen = _p
        break
    if _chosen is None:
        print("找不到可用端口(%d~%d): %s" % (_base, _base + 29, _last_err), flush=True)
        raise SystemExit(1)
    print("SKY_LISTEN_PORT=%d" % _chosen, flush=True)
    socketio.run(
        app,
        host=_host,
        port=_chosen,
        debug=False,   # 2026-10-23: 关掉 Werkzeug 调试器(内存/安全); 异常仍写日志
        use_reloader=False,
        keyfile=os.path.join(CERTS_DIR, 'privkey.pem'),
        certfile=os.path.join(CERTS_DIR, 'fullchain.pem')
    )
