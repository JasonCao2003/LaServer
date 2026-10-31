"""
WebSocket处理模块
包含所有WebSocket相关的处理逻辑
"""
from paths import DATA_DIR, SERVICE_ROOT
from flask_sock import Sock
from flask_socketio import SocketIO, emit, join_room, leave_room, send
from flask import request
import eventlet
import json
import uuid
import time
import base64
import logging
from urllib.parse import parse_qs, unquote

logger = logging.getLogger(__name__)

# 全局变量（将从index.py传入）
chat_clients = {}
native_ws_clients = {}
mqtt_clients = set()
pending_messages = {}
_pending_messages_lock = None
_session_cache = {}
_session_cache_lock = None
_session_cache_ttl = 60
_session_cache_cleanup_counter = 0

# 这些对象将在init_websocket中初始化
socketio = None
sock = None
User = None
db = None
# Flask app(init_websocket 注入)。eventlet.spawn 出来的广播协程没有请求上下文,
# 直接 User.query 会抛 "Working outside of application context", 结果广播给
# 0 个用户("Message queued for 0/0 users") —— 聊天消息因此永远进不了队列。
_app = None


def _safe_acquire(lock):
    try:
        if lock is not None:
            lock.acquire()
    except Exception:
        pass


def _safe_release(lock):
    try:
        if lock is not None:
            lock.release()
    except Exception:
        pass


def _all_user_ids():
    """取全部用户 id(广播排队用)。

    优先在 Flask 应用上下文里查 User.query; 拿不到 app(协程里没有请求上下文)时,
    直接只读连 sqlite 取 user.id —— 广播排队只在内存 dict 里 append, 不需要 ORM,
    这样即使调用方没有应用上下文, 消息也能正常进队列。
    """
    # 1) 有 app -> 走 ORM
    try:
        from flask import has_app_context, current_app
        app = _app
        if app is None and has_app_context():
            app = current_app._get_current_object()
        if app is not None and User is not None:
            with app.app_context():
                return [u.id for u in User.query.all()]
    except Exception as e:
        logger.warning("枚举用户(ORM)失败, 改用 sqlite 兜底: %s", e)

    # 2) 兜底: 只读 sqlite
    try:
        import sqlite3, os
        uri = ""
        try:
            from flask import has_app_context, current_app
            if has_app_context():
                uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "") or ""
        except Exception:
            pass
        path = uri.replace("sqlite:///", "").replace("sqlite://", "")
        base = SERVICE_ROOT
        if not path:
            path = os.path.join(DATA_DIR, "instance", "users.db")
        elif not os.path.isabs(path):
            path = os.path.join(DATA_DIR, "instance", path)
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=5)
        try:
            return [r[0] for r in con.execute("SELECT id FROM user").fetchall()]
        finally:
            con.close()
    except Exception as e:
        logger.error("枚举用户(sqlite 兜底)也失败: %s", e)
        return []


def init_websocket(app, socketio_instance, sock_instance, user_model, db_instance, 
                   chat_clients_dict, native_ws_clients_dict, mqtt_clients_set,
                   pending_messages_dict, pending_messages_lock, session_cache_lock):
    """初始化WebSocket处理器"""
    global socketio, sock, User, db, _app
    global chat_clients, native_ws_clients, mqtt_clients, pending_messages
    global _pending_messages_lock, _session_cache_lock

    socketio = socketio_instance
    sock = sock_instance
    User = user_model
    db = db_instance
    _app = app
    
    chat_clients = chat_clients_dict
    native_ws_clients = native_ws_clients_dict
    mqtt_clients = mqtt_clients_set
    pending_messages = pending_messages_dict
    _pending_messages_lock = pending_messages_lock
    _session_cache_lock = session_cache_lock
    
    # 注册所有WebSocket处理器
    register_socketio_handlers()
    register_sock_handlers(app)
    
    logger.info("WebSocket handlers initialized")


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


def broadcast_message_to_all_clients(message):
    """广播消息给所有客户端 - 使用HTTP轮询队列方式（绕过WebSocket）"""
    global pending_messages
    
    logger.info(f"=== Broadcasting message via polling queue ===")
    logger.info(f"Original message: {message}")
    
    sender_id = message.get('sender_id', 'unknown')
    msg_content = message.get('msg', '')
    channel = message.get('ch', 'global')
    msg_id = message.get('msg_id', str(uuid.uuid4()))
    msg_type = message.get('type', 'chat')
    
    game_format = {
        'type': msg_type,
        'sender_id': sender_id,
        'result': 'success',
        'msg_id': msg_id,
        'msg': msg_content,
        'ch': channel,
        'timestamp': int(time.time() * 1000)
    }
    
    # 修复: 原实现直接 User.query.all(), 一旦本函数在没有请求/应用上下文的协程里
    # 被调用(eventlet.spawn), 就会抛 "Working outside of application context",
    # 于是 user_ids=[] -> "Message queued for 0/0 users" -> 谁都收不到消息。
    # 改用 _all_user_ids(): 应用上下文里走 ORM, 否则只读 sqlite 兜底。
    try:
        user_ids = _all_user_ids()
        user_count = len(user_ids)
    except Exception as db_error:
        logger.error(f"Database error querying users: {db_error}")
        user_ids = []
        user_count = 0

    _safe_acquire(_pending_messages_lock)
    try:
        queued_count = 0
        for user_id in user_ids:
            if user_id not in pending_messages:
                pending_messages[user_id] = []

            pending_messages[user_id].append(game_format.copy())
            queued_count += 1

            if len(pending_messages[user_id]) > 1000:
                pending_messages[user_id] = pending_messages[user_id][-1000:]

        logger.info(f"Message queued for {queued_count}/{user_count} users via polling mechanism")
        logger.info(f"Message content: {msg_content[:100]}...")

    except Exception as e:
        logger.error(f"Error queuing message: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _safe_release(_pending_messages_lock)


def broadcast_message_to_websocket_clients(message):
    """向后兼容函数，调用新的广播函数"""
    broadcast_message_to_all_clients(message)


def start_ping_pong(sid):
    """启动心跳检测 - 优化版本，修复连接中断问题"""
    def ping_task():
        last_ping_time = time.time()
        consecutive_failures = 0
        max_failures = 10
        ping_interval = 60
        timeout_threshold = 300
        check_interval = 10
        
        while True:
            try:
                if sid not in chat_clients:
                    logger.debug(f'Client {sid} no longer exists, stopping ping task')
                    break
                
                client = chat_clients.get(sid)
                if not client:
                    break
                
                current_time = time.time()
                last_ping = client.get('last_ping', current_time)
                time_since_last_pong = current_time - last_ping
                
                if time_since_last_pong > timeout_threshold and consecutive_failures >= max_failures:
                    logger.warning(f'No pong received for {time_since_last_pong:.1f}s with {consecutive_failures} failures, terminating connection for {client.get("user", "unknown")}')
                    try:
                        if sid in chat_clients:
                            del chat_clients[sid]
                    except:
                        pass
                    try:
                        socketio.emit('disconnect_reason', {'reason': 'timeout'}, room=sid, namespace='/account/ws')
                    except:
                        pass
                    try:
                        socketio.disconnect(sid, namespace='/account/ws')
                    except:
                        pass
                    break
                
                if current_time - last_ping_time >= ping_interval:
                    try:
                        socketio.emit('ping', {
                            'type': 'ping',
                            'timestamp': int(current_time * 1000)
                        }, room=sid, namespace='/account/ws')
                        last_ping_time = current_time
                        consecutive_failures = max(0, consecutive_failures - 1)
                        logger.debug(f'Sent ping to {client.get("user", "unknown")}')
                    except Exception as e:
                        consecutive_failures += 1
                        logger.warning(f'Error sending ping to {client.get("user", "unknown")} (failure {consecutive_failures}/{max_failures}): {e}')
                        if consecutive_failures >= max_failures * 2:
                            logger.error(f'Too many ping failures, terminating connection for {client.get("user", "unknown")}')
                            try:
                                if sid in chat_clients:
                                    del chat_clients[sid]
                            except:
                                pass
                            break
                
            except KeyError:
                logger.debug(f'Client {sid} disconnected, stopping ping task')
                break
            except Exception as e:
                logger.error(f'Error in ping task for sid {sid}: {e}')
                consecutive_failures += 1
                if consecutive_failures >= max_failures * 3:
                    try:
                        if sid in chat_clients:
                            del chat_clients[sid]
                    except:
                        pass
                    break
                if consecutive_failures % 5 == 0:
                    consecutive_failures = max(0, consecutive_failures - 2)
            
            eventlet.sleep(check_interval)
    
    eventlet.spawn(ping_task)


def register_socketio_handlers():
    """注册所有Socket.IO处理器"""
    
    @socketio.on('connect', namespace='/account/ws')
    def handle_chat_websocket_connect():
        """处理聊天WebSocket连接 - 优化版本"""
        try:
            auth_header = request.headers.get('Authorization', '')
            user = None
            session_token = None
            
            if auth_header and auth_header.startswith('Basic '):
                try:
                    credentials = auth_header[6:]
                    decoded_credentials = base64.b64decode(credentials).decode('utf-8')
                    user, session_token = decoded_credentials.split(':', 1)
                except Exception as e:
                    logger.warning(f'Failed to decode Basic auth: {e}')
            
            if not user or not session_token:
                user = request.args.get('user')
                session_token = request.args.get('session')
            
            if not user or not session_token:
                auth_data = request.event.get('auth', {}) if hasattr(request, 'event') else {}
                user = auth_data.get('user')
                session_token = auth_data.get('session')
            
            if not user or not session_token:
                logger.warning(f'No authentication provided (sid: {request.sid})')
                emit('error', {'type': 'auth_error', 'message': 'Authentication required'})
                return False
            
            user_record = get_cached_user(user, session_token)
            if not user_record:
                logger.warning(f'Invalid session for user: {user} (sid: {request.sid})')
                emit('error', {'type': 'auth_error', 'message': 'Invalid session'})
                return False
            
            if user_record.is_banned:
                ban_end_time = user_record.ban_end_time if hasattr(user_record, 'ban_end_time') else 0
                current_time = int(time.time())
                if ban_end_time > 0 and current_time < ban_end_time:
                    logger.warning(f'Banned user attempted to connect: {user}')
                    emit('error', {'type': 'banned', 'message': 'Account is banned'})
                    return False
            
            chat_clients[request.sid] = {
                'user': user,
                'session': session_token,
                'is_alive': True,
                'last_ping': time.time(),
                'channels': set(),
                'connected_at': time.time()
            }
            
            chat_clients[request.sid]['channels'].add('global')
            chat_clients[request.sid]['channels'].add('local')
            
            logger.info(f'New chat WebSocket connection: {user} (sid: {request.sid})')
            
            emit('connected', {
                'type': 'connected',
                'user': user,
                'status': 'success',
                'timestamp': int(time.time() * 1000)
            })
            
            _pending_messages_lock.acquire()
            try:
                user_pending = pending_messages.get(user, [])
                if user_pending:
                    logger.info(f'Sending {len(user_pending)} pending messages to {user} via Socket.IO')
                    for msg in user_pending:
                        try:
                            emit('message', {
                                'type': msg.get('type', 'chat'),
                                'sender_id': msg.get('sender_id', 'unknown'),
                                'msg_id': msg.get('msg_id', str(uuid.uuid4())),
                                'msg': msg.get('msg', ''),
                                'ch': msg.get('ch', 'global'),
                                'timestamp': msg.get('timestamp', int(time.time() * 1000))
                            }, namespace='/account/ws')
                        except Exception as e:
                            logger.error(f'Failed to send pending message to {user}: {e}')
                    del pending_messages[user]
            finally:
                _pending_messages_lock.release()
            
            start_ping_pong(request.sid)
            
            return True
            
        except Exception as e:
            logger.error(f'Error in handle_chat_websocket_connect: {e}')
            emit('error', {'type': 'connection_error', 'message': str(e)})
            return False

    @socketio.on('disconnect', namespace='/account/ws')
    def handle_chat_websocket_disconnect():
        """处理聊天WebSocket断开连接 - 优化版本"""
        try:
            if request.sid in chat_clients:
                user = chat_clients[request.sid]['user']
                connected_at = chat_clients[request.sid].get('connected_at', time.time())
                connection_duration = time.time() - connected_at
                del chat_clients[request.sid]
                logger.info(f'Chat client disconnected: {user} (sid: {request.sid}, duration: {connection_duration:.1f}s)')
        except Exception as e:
            logger.error(f'Error handling disconnect: {e}')

    @socketio.on('ping', namespace='/account/ws')
    def handle_ping(data=None):
        """处理ping消息 - 优化版本"""
        try:
            if request.sid in chat_clients:
                chat_clients[request.sid]['is_alive'] = True
                chat_clients[request.sid]['last_ping'] = time.time()
                emit('pong', {
                    'type': 'pong',
                    'timestamp': int(time.time() * 1000)
                })
        except Exception as e:
            logger.error(f'Error handling ping: {e}')

    @socketio.on('pong', namespace='/account/ws')
    def handle_pong(data=None):
        """处理pong消息"""
        try:
            if request.sid in chat_clients:
                chat_clients[request.sid]['is_alive'] = True
                chat_clients[request.sid]['last_ping'] = time.time()
        except Exception as e:
            logger.error(f'Error handling pong: {e}')

    @socketio.on('join_channel', namespace='/account/ws')
    def handle_join_channel(data):
        """加入聊天频道 - 优化版本"""
        try:
            if request.sid not in chat_clients:
                emit('error', {'type': 'not_connected', 'message': 'Not connected'})
                return
            
            if isinstance(data, str):
                channel = data
            else:
                channel = data.get('channel') if isinstance(data, dict) else None
            
            if not channel:
                emit('error', {'type': 'invalid_channel', 'message': 'Channel name required'})
                return
            
            chat_clients[request.sid]['channels'].add(channel)
            user = chat_clients[request.sid]['user']
            logger.info(f"User {user} joined channel: {channel}")
            
            emit('channel_joined', {
                'channel': channel,
                'result': 'success',
                'timestamp': int(time.time() * 1000)
            })
            
        except Exception as e:
            logger.error(f'Error joining channel: {e}')
            emit('error', {'type': 'channel_error', 'message': str(e)})

    @socketio.on('leave_channel', namespace='/account/ws')
    def handle_leave_channel(data):
        """离开聊天频道 - 优化版本"""
        try:
            if request.sid not in chat_clients:
                emit('error', {'type': 'not_connected', 'message': 'Not connected'})
                return
            
            if isinstance(data, str):
                channel = data
            else:
                channel = data.get('channel') if isinstance(data, dict) else None
            
            if not channel:
                emit('error', {'type': 'invalid_channel', 'message': 'Channel name required'})
                return
            
            if channel in chat_clients[request.sid]['channels']:
                chat_clients[request.sid]['channels'].remove(channel)
                user = chat_clients[request.sid]['user']
                logger.info(f"User {user} left channel: {channel}")
                
                emit('channel_left', {
                    'channel': channel,
                    'result': 'success',
                    'timestamp': int(time.time() * 1000)
                })
            
        except Exception as e:
            logger.error(f'Error leaving channel: {e}')
            emit('error', {'type': 'channel_error', 'message': str(e)})

    @socketio.on('message', namespace='/account/ws')
    def handle_websocket_message(data):
        """处理从WebSocket客户端发送的消息 - 优化版本"""
        try:
            logger.info(f"=== Received WebSocket message ===")
            logger.info(f"From SID: {request.sid}")
            logger.info(f"Data type: {type(data)}")
            logger.info(f"Data content: {data}")
            
            if request.sid not in chat_clients:
                logger.warning(f'Received message from unauthenticated client: {request.sid}')
                emit('error', {'type': 'unauthorized', 'message': 'Not authenticated'})
                return
            
            client = chat_clients[request.sid]
            user = client['user']
            
            client['last_ping'] = time.time()
            client['is_alive'] = True
            
            if isinstance(data, dict):
                logger.info(f"Message keys: {list(data.keys())}")
                msg_type = data.get('type', 'message')
                
                if msg_type == 'chat' or 'msg' in data:
                    msg_content = data.get('msg', '')
                    channel = data.get('ch', 'global')
                    msg_id = data.get('msg_id', str(uuid.uuid4()))
                    
                    chat_message = {
                        'type': 'chat',
                        'sender_id': user,
                        'msg_id': msg_id,
                        'msg': msg_content,
                        'ch': channel,
                        'timestamp': int(time.time() * 1000)
                    }
                    
                    broadcast_message_to_websocket_clients(chat_message)
                    
                    emit('ack', {
                        'type': 'ack',
                        'msg_id': msg_id,
                        'status': 'sent',
                        'timestamp': int(time.time() * 1000)
                    })
                
                else:
                    emit('ack', {
                        'type': 'ack',
                        'msg_id': data.get('msg_id', 'unknown'),
                        'status': 'received',
                        'timestamp': int(time.time() * 1000)
                    })
            else:
                emit('ack', {
                    'type': 'ack',
                    'status': 'received',
                    'timestamp': int(time.time() * 1000)
                })
                
        except Exception as e:
            logger.error(f'Error handling WebSocket message: {e}')
            try:
                emit('error', {
                    'type': 'message_error',
                    'message': str(e),
                    'timestamp': int(time.time() * 1000)
                })
            except:
                pass

    @socketio.on('chat', namespace='/account/ws')
    def handle_chat_event(data):
        """处理chat事件 - 优化版本"""
        try:
            logger.info(f"Received 'chat' event: {data}")
            if isinstance(data, dict):
                handle_websocket_message(data)
        except Exception as e:
            logger.error(f'Error handling chat event: {e}')

    @socketio.on('chat_message', namespace='/account/ws')
    def handle_chat_message_event(data):
        """处理chat_message事件 - 优化版本"""
        try:
            logger.info(f"Received 'chat_message' event: {data}")
            if isinstance(data, dict):
                handle_websocket_message(data)
        except Exception as e:
            logger.error(f'Error handling chat_message event: {e}')

    @socketio.on('connect', namespace='/mqtt')
    def handle_mqtt_websocket_connect():
        """处理MQTT WebSocket连接"""
        mqtt_clients.add(request.sid)
        logger.info('New MQTT WebSocket connection')

    @socketio.on('disconnect', namespace='/mqtt')
    def handle_mqtt_websocket_disconnect():
        """处理MQTT WebSocket断开连接"""
        if request.sid in mqtt_clients:
            mqtt_clients.remove(request.sid)
            logger.info('MQTT client disconnected')

    @socketio.on('message', namespace='/mqtt')
    def handle_mqtt_message(data):
        """处理MQTT消息"""
        try:
            if isinstance(data, dict):
                data_len = len(str(data))
            else:
                data_len = len(data)
            
            if data_len == 163:
                emit('message', bytes.fromhex('20020000'), namespace='/mqtt')
            else:
                logger.debug(f"MQTT data length: {data_len}")
        except Exception as e:
            logger.error(f'Error handling MQTT message: {e}')


def _decode_basic(value):
    """把 "Basic base64(user:session)" / 纯 base64 / "user:session" 解成 (user, session)。"""
    if not value:
        return None, None
    value = str(value).strip()
    if value.lower().startswith('basic '):
        value = value[6:].strip()

    decoded = None
    try:
        padded = value + '=' * (-len(value) % 4)
        decoded = base64.b64decode(padded).decode('utf-8', 'replace')
    except Exception:
        decoded = None

    candidates = [c for c in (decoded, value) if c and ':' in c]
    for candidate in candidates:
        user, _, session_token = candidate.partition(':')
        user = user.strip()
        session_token = session_token.strip()
        # 宽松校验: 避免把随手粘来的子协议/base64 乱码当成凭据(会误判成已认证)。
        if len(user) >= 8 and len(session_token) >= 8:
            return user, session_token
    return None, None


def _all_ws_headers(env):
    """把握手请求的全部头收集成 {小写名: 值}。

    为什么不能只看 environ 的 HTTP_*: 本服务器的 WSGI 是 eventlet, 它同时在
    environ 里放了 `headers_raw`(原始 (名, 值) 元组)。实测日志里 `/account/ws`
    的 environ 键只列出 `HTTP_HOST` 一个 HTTP_* 头就截断了, 靠 HTTP_* 找凭据
    不可靠; headers_raw 是原始逐一解析的结果, 更可信。两边都收, 后者覆盖前者。
    """
    hdrs = {}
    for key, value in (env or {}).items():
        if isinstance(key, str) and key.startswith('HTTP_'):
            hdrs[key[5:].replace('_', '-').lower()] = value

    raw = (env or {}).get('headers_raw')
    if raw:
        try:
            for item in raw:
                if not isinstance(item, (tuple, list)) or len(item) != 2:
                    continue
                name = str(item[0]).strip()
                if not name:
                    continue
                hdrs[name.lower()] = str(item[1])
        except Exception as e:
            logger.debug(f'Native WS: 解析 headers_raw 失败: {e}')
    return hdrs


def _extract_ws_credentials(ws):
    """从握手请求里尽最大努力取 (user, session)。

    0.22 客户端在实测里发的握手**既没有 Authorization 也没有 query 参数**
    (server_runtime_8443.log: "Native WS: No authentication provided"),
    于是连接建好就被我们踢掉 —— 聊天通道永远建不起来。不同客户端版本把凭据放在
    不同地方, 这里把能想到的位置全试一遍。返回 (user, session)。
    """
    env = getattr(ws, 'environ', {}) or {}
    headers = _all_ws_headers(env)

    # 1) Authorization: Basic base64(user:session)
    for key in ('authorization', 'auth', 'x-authorization'):
        user, session_token = _decode_basic(headers.get(key))
        if user and session_token:
            logger.info(f'Native WS: 使用 {key} 头里的 Basic 凭据 - user: {user[:8]}...')
            return user, session_token

    # 2) query string (?user=..&session=.. 或 ?private_ip=true 那种老格式)
    query_string = env.get('QUERY_STRING', '') or ''
    if query_string:
        try:
            params = parse_qs(query_string)
            user = (params.get('user') or params.get('user_id') or [None])[0]
            session_token = (params.get('session') or params.get('sessionToken') or [None])[0]
            if user and session_token:
                logger.info(f'Native WS: 使用 query 参数认证 - user: {str(user)[:8]}...')
                return unquote(str(user)), unquote(str(session_token))
        except Exception as e:
            logger.warning(f'Native WS: 解析 query string 失败: {e}')

    # 3) Sec-WebSocket-Protocol 子协议(部分是 "base64(user:session)" 或 "user:session")
    protocols = headers.get('sec-websocket-protocol', '') or ''
    for protocol in str(protocols).split(','):
        user, session_token = _decode_basic(protocol)
        if user and session_token:
            logger.info(f'Native WS: 使用子协议里的凭据认证 - user: {user[:8]}...')
            return user, session_token

    # 4) Cookie
    cookie = headers.get('cookie', '') or ''
    if cookie:
        try:
            jar = {}
            for part in cookie.split(';'):
                if '=' in part:
                    k, _, v = part.partition('=')
                    jar[k.strip()] = v.strip()
            user = jar.get('user') or jar.get('user_id')
            session_token = jar.get('session') or jar.get('sessionToken')
            if user and session_token:
                logger.info(f'Native WS: 使用 Cookie 认证 - user: {str(user)[:8]}...')
                return unquote(user), unquote(session_token)
        except Exception as e:
            logger.warning(f'Native WS: 解析 Cookie 失败: {e}')

    # 5) 自定义头(部分版本把凭据直接放头里)
    user = headers.get('x-user') or headers.get('x-user-id') or headers.get('user')
    session_token = (headers.get('x-session') or headers.get('x-session-token')
                     or headers.get('session'))
    if user and session_token and len(str(user)) >= 8 and len(str(session_token)) >= 8:
        logger.info(f'Native WS: 使用自定义头认证 - user: {str(user)[:8]}...')
        return str(user), str(session_token)

    return None, None


def _ws_receive_poll(ws, idle_sleep=0.05):
    """非阻塞收一条 WS 消息; 没有消息就睡眠一小段, 把控制权交还 eventlet hub。

    **不要**直接用 ws.receive(timeout=1.0): simple_websocket 的 receive() 内部是
    threading.Event.wait(timeout) —— 在 eventlet 的 greenlet 里(正在处理这个 WSGI
    请求的那个)调用会把整个 hub 卡住 timeout 秒, 期间**所有玩家**的接口全部停摆。
    simple_websocket 自己有一个后台读线程把数据塞进 input_buffer, 所以这里用
    timeout=0 轮询 + eventlet.sleep 让出, 拿到消息的时机完全一样, 但零阻塞。
    """
    message = ws.receive(timeout=0)
    if message:
        return message
    try:
        import eventlet
        eventlet.sleep(idle_sleep)
    except Exception:
        time.sleep(idle_sleep)
    return None


def _await_ws_auth(ws, timeout=8.0):
    """等客户端在连接建立后补发一条认证消息(而不是放在握手头里)。

    实测那一版客户端握手不带任何认证, 原来我们立刻 return 把连接关掉。宁可在
    这里多等几秒: 拿到认证就继续, 拿不到再关。非认证消息直接丢弃(此时还没注册,
    也投递不到任何人)。等待期间不阻塞 eventlet hub(见 _ws_receive_poll)。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = _ws_receive_poll(ws, 0.05)
        except Exception as e:
            logger.debug(f'Native WS: 等待认证消息时连接结束: {e}')
            return None, None
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            logger.info('Native WS: 收到非 JSON 的认证消息(已忽略)')
            continue
        if not isinstance(data, dict):
            continue

        auth = data.get('auth') if isinstance(data.get('auth'), dict) else {}
        user = data.get('user') or data.get('user_id') or data.get('uid') or auth.get('user')
        session_token = (data.get('session') or data.get('sessionToken')
                         or data.get('token') or auth.get('session'))
        if user and session_token:
            logger.info(f'Native WS: 收到客户端补发的认证消息 - user: {str(user)[:8]}...')
            return str(user), str(session_token)
        logger.info(f'Native WS: 认证前的消息已忽略, keys={list(data.keys())}')

    return None, None


def register_sock_handlers(app):
    """注册原生WebSocket处理器"""

    @sock.route('/account/ws')
    def native_chat_websocket(ws):
        """原生WebSocket聊天通信 - 游戏客户端可能使用此协议"""
        ws_id = str(uuid.uuid4())
        user = None
        session_token = None

        logger.info(f"=== Native WebSocket connection attempt ===")
        logger.info(f"WebSocket ID: {ws_id}")
        try:
            # 把整个握手头打出来: WS 连上却被踢时, 只能靠它看出客户端把凭据放哪了
            # HTTP_* 头在实测日志里可能只列出 HTTP_HOST 一个(被截断/被中间层改写),
            # 所以改用 headers_raw(原始逐一解析)+HTTP_* 合并后的完整头表。
            _headers = _all_ws_headers(ws.environ or {})
            for _k in ('authorization', 'cookie', 'sec-websocket-protocol'):
                if _headers.get(_k) and len(str(_headers[_k])) > 40:
                    _headers[_k] = str(_headers[_k])[:40] + '...'
            _extra = {
                'QUERY_STRING': (ws.environ or {}).get('QUERY_STRING', ''),
                'RAW_PATH_INFO': (ws.environ or {}).get('RAW_PATH_INFO', ''),
                'REMOTE_ADDR': (ws.environ or {}).get('REMOTE_ADDR', ''),
                'headers_raw_count': len((ws.environ or {}).get('headers_raw') or ())
            }
            logger.info(f"Native WS: request headers: {_headers} extra: {_extra}")
        except Exception as e:
            logger.debug(f"Native WS: 记录握手头失败: {e}")

        try:
            user, session_token = _extract_ws_credentials(ws)

            if not user or not session_token:
                logger.info('Native WS: 握手未带认证, 等待客户端补发认证消息(最多 8 秒)...')
                user, session_token = _await_ws_auth(ws, timeout=8.0)

            if not user or not session_token:
                logger.warning(f'Native WS: No authentication provided (ws_id: {ws_id})')
                try:
                    ws.send(json.dumps({"error": "Authentication required"}))
                except Exception:
                    pass
                return

            user_record = get_cached_user(user, session_token)
            if not user_record:
                logger.warning(f'Native WS: Invalid session for user: {user} (ws_id: {ws_id})')
                ws.send(json.dumps({"error": "Invalid session"}))
                return
            
            if user_record.is_banned:
                ban_end_time = user_record.ban_end_time if hasattr(user_record, 'ban_end_time') else 0
                current_time = int(time.time())
                if ban_end_time > 0 and current_time < ban_end_time:
                    logger.warning(f'Native WS: Banned user attempted to connect: {user}')
                    ws.send(json.dumps({"error": "Account is banned"}))
                    return
            
            native_ws_clients[ws_id] = {
                'user': user,
                'session': session_token,
                'ws': ws,
                'channels': {'global', 'local'},
                'connected_at': time.time()
            }
            
            logger.info(f'Native WebSocket connected: {user} (ws_id: {ws_id})')
            logger.info(f'Native WS client added to native_ws_clients. Total native WS clients: {len(native_ws_clients)}')
            
            try:
                ws.send(json.dumps({
                    "type": "connected",
                    "user": user,
                    "status": "success",
                    "timestamp": int(time.time() * 1000)
                }))
                logger.debug(f'Native WS: Sent connected message to {user}')
            except Exception as e:
                logger.error(f'Native WS: Failed to send connected message to {user}: {e}')
            
            _pending_messages_lock.acquire()
            try:
                user_pending = pending_messages.get(user, [])
                if user_pending:
                    logger.info(f'Sending {len(user_pending)} pending messages to {user}')
                    for msg in user_pending:
                        try:
                            ws.send(json.dumps({
                                'type': msg.get('type', 'chat'),
                                'sender_id': msg.get('sender_id', 'unknown'),
                                'msg_id': msg.get('msg_id', str(uuid.uuid4())),
                                'msg': msg.get('msg', ''),
                                'ch': msg.get('ch', 'global'),
                                'timestamp': msg.get('timestamp', int(time.time() * 1000)),
                                'result': 'success'
                            }))
                        except Exception as e:
                            logger.error(f'Failed to send pending message to {user}: {e}')
                    del pending_messages[user]
            finally:
                _pending_messages_lock.release()
            
            while True:
                try:
                    # 用轮询版接收: ws.receive(timeout=1.0) 会把 eventlet hub
                    # 卡住 1 秒(所有玩家的接口跟着卡), 见 _ws_receive_poll。
                    client_message = _ws_receive_poll(ws)
                    if not client_message:
                        continue
                    
                    try:
                        message_data = json.loads(client_message)
                        msg_type = message_data.get('type', 'message')
                        
                        if msg_type == 'ping':
                            ws.send(json.dumps({
                                "type": "pong",
                                "timestamp": int(time.time() * 1000)
                            }))
                            continue
                        
                        msg_content = message_data.get('msg', '')
                        channel = message_data.get('ch', 'global')
                        msg_id = message_data.get('msg_id', str(uuid.uuid4()))
                        
                        if msg_content:
                            chat_message = {
                                'type': 'chat',
                                'sender_id': user,
                                'msg_id': msg_id,
                                'msg': msg_content,
                                'ch': channel,
                                'timestamp': int(time.time() * 1000)
                            }
                            
                            broadcast_message_to_all_clients(chat_message)
                            
                            ws.send(json.dumps({
                                "type": "ack",
                                "msg_id": msg_id,
                                "result": "success",
                                "status": "sent"
                            }))
                    
                    except json.JSONDecodeError:
                        ws.send(json.dumps({"error": "Invalid JSON format"}))
                    except Exception as e:
                        logger.error(f'Error processing native WS message: {e}')
                        ws.send(json.dumps({"error": str(e)}))
                        
                except Exception as e:
                    if 'timeout' not in str(e).lower():
                        logger.error(f'Native WS error: {e}')
                        break
        
        except Exception as e:
            import traceback
            logger.error(f'Native WebSocket connection error: {e}')
            logger.error(f'Traceback: {traceback.format_exc()}')
            try:
                ws.send(json.dumps({"error": "Connection error", "message": str(e)}))
            except:
                pass
        finally:
            if ws_id in native_ws_clients:
                user_info = native_ws_clients[ws_id].get("user", "unknown")
                logger.info(f'Native WebSocket disconnected: {user_info} (ws_id: {ws_id})')
                del native_ws_clients[ws_id]
                logger.info(f'Removed native WS client. Remaining: {len(native_ws_clients)}')
            else:
                logger.warning(f'Native WebSocket cleanup: ws_id {ws_id} not found in native_ws_clients')

