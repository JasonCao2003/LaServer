from flask import request, jsonify, current_app as app
from database.models import db, User, UserOutfit, UserCurrency, UserWingBuff, UserInitialization
import uuid
import time
import logging
import eventlet
import os
import json
import re

logger = logging.getLogger(__name__)

_SIZE_PATTERN = re.compile(r'^s:\s*(-?[\d\.]+)\s+h:\s*(-?[\d\.]+)$')
_CURRENCY_PATTERNS = {
    '蜡烛': re.compile(r'^蜡烛(-?[\d\.]+)$'),
    '爱心': re.compile(r'^爱心(-?[\d\.]+)$'),
    '烛火': re.compile(r'^烛火(-?[\d\.]+)$'),
    '爱心烛火': re.compile(r'^爱心烛火(-?[\d\.]+)$')
}

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话"""
    return User.query.filter_by(id=user_id, session=session_token).first()

def broadcast_message_to_websocket_clients(message):
    """广播消息到WebSocket客户端。

    修复聊天"发出去、别人收不到"的根因:
      原来这里是 eventlet.spawn(...) 异步执行; 而 Flask 的应用上下文是
      greenlet-local 的 —— 新协程里没有请求上下文(本模块的 current_app 只是个
      LocalProxy, 在协程里连 _get_current_object() 都取不到 app), 于是
      websocket_handler 里的 User.query.all() 抛
          "Working outside of application context"
      广播变成 "Message queued for 0/0 users", 消息进不了任何人的队列
      -> 谁(包括发送者自己)都收不到 (见 server_runtime_8443.log)。
      改为**在当前请求上下文内同步执行**——广播只是往内存 dict 里 append,
      开销极小, 不阻塞网络。
    """
    try:
        import websocket_handler
        websocket_handler.broadcast_message_to_all_clients(message)
    except Exception as e:
        logger.error(f"Failed to broadcast message: {e}")

def handle_chat_message():
    # 保持 200 状态码，所有错误信息都在响应体中体现
    status_code = 200  # 统一设置为 200
    
    try:
        # 获取请求数据（性能优化：使用force=True避免多次解析）
        data = request.get_json(force=True)
        if not data:
            return jsonify({"result": "error", "message": "Invalid request data"}), status_code
        
        # 验证必需参数（性能优化：一次性检查，使用集合提高查找速度）
        # 注意: 'ch'(频道) 不是必需字段 —— 客户端不同版本不一定带, 原来缺了就直接
        # 返回 "Missing parameters: ch", 客户端拿不到 result 就当没发出去。缺省 global。
        required_params = {'user', 'session'}
        missing_params = required_params - set(data.keys())
        if missing_params:
            return jsonify({"result": "error", "message": f"Missing parameters: {', '.join(missing_params)}"}), status_code

        user_id = data['user']
        session_token = data['session']
        message = (data.get('msg') or data.get('chat_msg')
                   or data.get('message') or '')
        channel = data.get('ch') or data.get('channel') or 'global'

        user = get_cached_user(user_id, session_token)
        if not user:
            return jsonify({"result": "error", "message": "Invalid session"}), status_code
        
        # 检查是否为"身高回归默认"命令（性能优化：提前检查，避免不必要的处理）
        message_stripped = message.strip()
        if message_stripped == "身高回归默认":
            try:
                DEFAULT_SCALE = 0.22549559
                DEFAULT_HEIGHT = 1.93536222
                
                # 查找用户现有的服装数据（使用get_or_create模式优化）
                user_outfit = UserOutfit.query.filter_by(user_id=user_id).first()
                
                if not user_outfit:
                    user_outfit = UserOutfit(user_id=user_id, scale=DEFAULT_SCALE, height=DEFAULT_HEIGHT)
                    db.session.add(user_outfit)
                else:
                    user_outfit.scale = DEFAULT_SCALE
                    user_outfit.height = DEFAULT_HEIGHT
                
                # 使当前session失效（优化：批量更新）
                new_session = str(uuid.uuid4())
                user.session = new_session
                
                db.session.commit()
                
                msg_id = str(uuid.uuid4())
                current_timestamp = int(time.time() * 1000)
                system_message = {
                    'type': 'system',
                    'sender_id': 'system',
                    'msg_id': msg_id,
                    'msg': f"已恢复默认身高: 比例={DEFAULT_SCALE}, 高度={DEFAULT_HEIGHT}。设置已生效，请重新登录。",
                    'ch': channel,
                    'timestamp': current_timestamp
                }
                
                # 广播系统消息(同步执行: 必须留在请求上下文里, 否则查不到用户列表)
                try:
                    broadcast_message_to_websocket_clients(system_message)
                except:
                    pass  # 广播失败不影响响应
                
                return jsonify({
                    'result': 'success',
                    'msg_id': msg_id,
                    'system_message': '身高已恢复默认值，请重新登录以生效'
                }), status_code
                
            except Exception as e:
                logger.error(f"Error processing reset height command: {e}")
                db.session.rollback()  # 回滚事务
        
        # 格式: s:x h:y (其中x和y为数字，可以是正数、负数、整数或小数)
        size_match = _SIZE_PATTERN.match(message.strip())
        
        if size_match:
            try:
                scale_value = float(size_match.group(1))
                height_value = float(size_match.group(2))
                
                user_outfit = UserOutfit.query.filter_by(user_id=user_id).first()
                
                if not user_outfit:
                    user_outfit = UserOutfit(user_id=user_id)
                    db.session.add(user_outfit)
                
                user_outfit.scale = scale_value
                user_outfit.height = height_value
                
                # 使当前session失效
                user.session = str(uuid.uuid4())  # 生成新的随机session值
                
                db.session.commit()
                
                current_timestamp = int(time.time() * 1000)
                system_message = {
                    'type': 'system',
                    'sender_id': 'system',
                    'msg_id': str(uuid.uuid4()),
                    'msg': f"已更新角色尺寸: 比例={scale_value}, 高度={height_value}。设置已生效，请重新登录。",
                    'ch': channel,
                    'timestamp': current_timestamp
                }
                
                # 广播系统消息(同步执行: 必须留在请求上下文里, 否则查不到用户列表)
                try:
                    broadcast_message_to_websocket_clients(system_message)
                except:
                    pass
                
                response_data = {
                    'result': 'success',
                    'msg_id': str(uuid.uuid4()),
                    'system_message': '角色尺寸已更新，请重新登录以生效'
                }
                
                return jsonify(response_data), status_code
                
            except ValueError as e:
                print(f"ValueError in processing outfit command: {e}")
                pass
            except Exception as e:
                print(f"Error processing outfit command: {e}")
        
        # 格式: 蜡烛x, 爱心x, 烛火x, 爱心烛火x (x可以为任意数值，包括负数)
        currency_patterns_db = {
            '蜡烛': 'candles',
            '爱心': 'heart', 
            '烛火': 'wax',
            '爱心烛火': 'heart_wax'
        }
        
        currency_match = None
        currency_type = None
        currency_value = None
        
        message_stripped = message.strip()  # 只strip一次，避免重复操作
        for chinese_key, db_key in currency_patterns_db.items():
            pattern = _CURRENCY_PATTERNS.get(chinese_key)
            if pattern:
                match = pattern.match(message_stripped)
                if match:
                    currency_match = match
                    currency_type = db_key
                    try:
                        currency_value = float(match.group(1))
                    except ValueError:
                        currency_value = None
                    break
        
        if currency_match and currency_value is not None:
            try:
                user_currency = UserCurrency.query.filter_by(user_id=user_id).first()
                
                if not user_currency:
                    user_currency = UserCurrency(
                        user_id=user_id,
                        candles=0,
                        heart=0,
                        heart_wax=0,
                        prestige=0,
                        prestige_vip=0,
                        prestige_wax=0,
                        season_candle=0,
                        season_wax=0,
                        season_wax_14=0,
                        vip=0,
                        wax=3
                    )
                    db.session.add(user_currency)
                
                if currency_type == 'candles':
                    user_currency.candles = currency_value
                elif currency_type == 'heart':
                    user_currency.heart = currency_value
                elif currency_type == 'wax':
                    user_currency.wax = currency_value
                elif currency_type == 'heart_wax':
                    user_currency.heart_wax = currency_value
                
                # 使当前session失效
                user.session = str(uuid.uuid4())  # 生成新的随机session值
                
                db.session.commit()
                
                currency_display_names = {
                    'candles': '蜡烛',
                    'heart': '爱心',
                    'wax': '烛火',
                    'heart_wax': '爱心烛火'
                }
                
                current_timestamp = int(time.time() * 1000)
                system_message = {
                    'type': 'system',
                    'sender_id': 'system',
                    'msg_id': str(uuid.uuid4()),
                    'msg': f"已更新{currency_display_names[currency_type]}为: {currency_value}。设置已生效，请重新登录。",
                    'ch': channel,
                    'timestamp': current_timestamp
                }
                
                # 广播系统消息(同步执行: 必须留在请求上下文里, 否则查不到用户列表)
                try:
                    broadcast_message_to_websocket_clients(system_message)
                except:
                    pass
                
                response_data = {
                    'result': 'success',
                    'msg_id': str(uuid.uuid4()),
                    'system_message': f'{currency_display_names[currency_type]}已更新，请重新登录以生效'
                }
                
                return jsonify(response_data), status_code
                
            except ValueError as e:
                print(f"ValueError in processing currency command: {e}")
                pass
            except Exception as e:
                print(f"Error processing currency command: {e}")
        
        if message.strip() == "无翼":
            try:
                print(f"处理'无翼'命令，用户ID: {user_id}")
                
                deleted_count = UserWingBuff.query.filter_by(user_id=user_id).delete()
                print(f"删除了 {deleted_count} 条翼之净化记录")
                
                user_init = UserInitialization.query.filter_by(user_id=user_id).first()
                if not user_init:
                    user_init = UserInitialization(user_id=user_id, is_initialized=True)
                    db.session.add(user_init)
                else:
                    user_init.is_initialized = True
                    user_init.updated_at = int(time.time())
                
                db.session.commit()
                print(f"数据库提交完成")
                
                current_timestamp = int(time.time() * 1000)
                system_message = {
                    'type': 'system',
                    'sender_id': 'system',
                    'msg_id': str(uuid.uuid4()),
                    'msg': f"已清空所有光翼数据。设置已生效。",
                    'ch': channel,
                    'timestamp': current_timestamp
                }
                
                # 广播系统消息(同步执行: 必须留在请求上下文里, 否则查不到用户列表)
                try:
                    broadcast_message_to_websocket_clients(system_message)
                except:
                    pass
                
                response_data = {
                    'result': 'success',
                    'msg_id': str(uuid.uuid4()),
                    'system_message': '已清空所有光翼数据'
                }
                return jsonify(response_data), status_code
                
            except Exception as e:
                print(f"Error processing '无翼' command: {e}")
                import traceback
                traceback.print_exc()
        
        if message.strip() == "恢复光翼":
            try:
                print(f"处理'恢复光翼'命令，用户ID: {user_id}")
                
                UserWingBuff.query.filter_by(user_id=user_id).delete()
                
                config_path = os.path.join(app.root_path, 'config', 'all_wing_buffs.json')
                
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                        initial_buffs = config_data.get('wing_buffs', [])
                    
                    print(f"从配置文件读取到 {len(initial_buffs)} 个初始光翼")
                    
                    # 重新创建翼之净化数据，collected设为true
                    created_count = 0
                    wing_buffs_to_insert = []
                    
                    for buff_data in initial_buffs:
                        wing_buff = UserWingBuff(
                            user_id=user_id,
                            name=buff_data.get('name', ''),
                            collected=True,  # 强制设为true
                            deposited=buff_data.get('deposited', False),
                            last_conversion=buff_data.get('last_conversion', 0),
                            deposit_id=buff_data.get('deposit_id', 0),
                            created_at=int(time.time()),
                            updated_at=int(time.time())
                        )
                        wing_buffs_to_insert.append(wing_buff)
                        created_count += 1
                    
                    db.session.add_all(wing_buffs_to_insert)
                    
                    user_init = UserInitialization.query.filter_by(user_id=user_id).first()
                    if not user_init:
                        user_init = UserInitialization(user_id=user_id, is_initialized=True)
                        db.session.add(user_init)
                    else:
                        user_init.is_initialized = True
                        user_init.updated_at = int(time.time())
                    
                    db.session.commit()
                    
                    print(f"插入了 {created_count} 条新记录")
                    
                    current_timestamp = int(time.time() * 1000)
                    system_message = {
                        'type': 'system',
                        'sender_id': 'system',
                        'msg_id': str(uuid.uuid4()),
                        'msg': f"已从配置文件中恢复{created_count}个光翼。设置已生效。",
                        'ch': channel,
                        'timestamp': current_timestamp
                    }
                    
                    # 广播系统消息(同步执行: 必须留在请求上下文里, 否则查不到用户列表)
                    try:
                        broadcast_message_to_websocket_clients(system_message)
                    except:
                        pass
                    
                    response_data = {
                        'result': 'success',
                        'msg_id': str(uuid.uuid4()),
                        'system_message': f'已恢复{created_count}个光翼'
                    }
                    
                    return jsonify(response_data), status_code
                    
                except FileNotFoundError:
                    app.logger.error(f"Wing buffs config file not found: {config_path}")
                    response_data = {
                        'result': 'error',
                        'system_message': '配置文件不存在，无法恢复光翼'
                    }
                    return jsonify(response_data), status_code
                except Exception as e:
                    db.session.rollback()
                    app.logger.error(f"恢复光翼失败: {e}")
                    response_data = {
                        'result': 'error',
                        'system_message': '恢复光翼失败'
                    }
                    return jsonify(response_data), status_code
                
            except Exception as e:
                print(f"Error processing '恢复光翼' command: {e}")
                import traceback
                traceback.print_exc()
        
        # 如果不是命令，或者命令处理失败，按普通聊天消息处理
        # 性能优化：一次性生成时间戳，避免多次调用time.time()
        current_timestamp = int(time.time() * 1000)
        msg_id = str(uuid.uuid4())
        chat_message = {
            'type': 'chat',
            'sender_id': user_id,
            'msg_id': msg_id,
            'msg': message,
            'ch': channel,
            'timestamp': current_timestamp
        }
        
        # 广播消息(同步执行: 必须留在请求上下文内, 否则 websocket_handler 查不到
        # 用户列表, 会变成 "Message queued for 0/0 users" —— 这正是"聊天发出去但
        # 谁都收不到"的根因。广播只是往内存队列 append, 开销极小, 不阻塞网络。)
        try:
            broadcast_message_to_websocket_clients(chat_message)
        except Exception as e:
            logger.error(f"Error broadcasting chat message: {e}")
            # 即使广播失败，仍然返回成功（HTTP接口成功）
        
        response_data = {
            'result': 'success',
            'msg_id': msg_id,
            'timestamp': current_timestamp
        }
        
        return jsonify(response_data), status_code
        
    except Exception as e:
        # 发生错误时返回错误信息，但保持200状态码
        response_data = {
            "result": "error",
            "message": str(e)
        }
        return jsonify(response_data), status_code


def handle_send_message():
    """`/account/send` 与 `/account/send_message` 的统一入口。

    0.22 客户端的反编译里两条路径都存在(libBootloader0200.so.c:704125 与 :698005):
      - AccountSendChatMessageRequest -> /account/chat/send       (字段 user/session/msg/ch)
      - AccountSendMessageRequest     -> /account/send_message    (字段 target/gift_type)
    旧服务器只注册了 /account/chat/send, 另外两条落到"JSON 兜底 200 {}",
    客户端拿不到 result 就认为没发出去(实测日志里这两条路径一次都没出现过)。
    聊天形态直接转给 handle_chat_message; target 形态按"给好友发东西"处理。
    """
    data = request.get_json(force=True, silent=True) or {}
    if data.get('msg') or data.get('chat_msg') or data.get('message'):
        return handle_chat_message()
    return _send_target_message(data)


def _send_target_message(data):
    """target + gift_type 形态(/account/send_message): 好友赠礼(心火/爱心)。

    客户端(sub_442FB0 组包 {target,gift_type}; 响应处理在 704419 起, 收
    result/currency/sent_message)。官方样本 static_responses/account__send_message.json:
      {"currency":{...}, "result":"ok",
       "sent_message":{"currency_count":3,"currency_type":"candles","gift_id":...,
                       "msg_id":...,"recvd":false,"to_id":"...","type":"gift"}}
    —— 这正是"赠送爱心"(3 蜡烛 -> 对方 1 爱心)。旧实现只往内存队列塞一条 'gift',
    不落库、不给收礼方加货币、响应里也没有 currency/sent_message —— 于是
    "好友之间无法赠送爱心和心火"。现在统一走 gift_helper。
    """
    import helpers.gift_helper as gift_helper
    from helpers.currency_helper import build_currency
    from database.models import UserCurrency

    user_id = data.get('user') or data.get('user_id')
    session_token = data.get('session')
    target = data.get('target') or data.get('recv_user') or data.get('friend_id')

    if not user_id or not session_token:
        return jsonify({"result": "error", "error": "Invalid request body"}), 200
    if not target:
        return jsonify({"result": "error", "error": "Missing target"}), 200

    user = get_cached_user(user_id, session_token)
    if not user:
        return jsonify({"result": "error", "error": "Invalid session"}), 200

    target_id = str(target).split(':')[0]
    now_ms = int(time.time() * 1000)
    raw_gift_type = data.get('gift_type') if data.get('gift_type') is not None else data.get('type')
    name = gift_helper.normalize_gift_type(raw_gift_type)

    def _currency_snapshot():
        try:
            return build_currency(UserCurrency.query.filter_by(user_id=user_id).first())
        except Exception:
            return {}

    if not name:
        logger.warning("send_message: 未知 gift_type=%r (user=%s)", raw_gift_type, user_id)
        return jsonify({"result": "error", "error": "Unknown gift_type",
                        "currency": _currency_snapshot()}), 200

    # 对方可能还没登录过本服: 补占位账号
    if User.query.filter_by(id=target_id).first() is None:
        try:
            import helpers.friend_helper as friend_helper
            friend_helper.ensure_placeholder_user(target_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("send_message: 建占位账号 %s 失败: %s", target_id, exc)
            db.session.rollback()

    try:
        ok, pay_cur, pay_amount = gift_helper.charge_sender(user_id, name)
        if not ok:
            logger.warning("send_message: %s %s 不足(%s), 拒发", user_id, pay_cur, pay_amount)
            return jsonify({"result": "error", "error": "Not enough currency",
                            "currency": _currency_snapshot()}), 200
        gift = gift_helper.create_gift(user_id, target_id, name,
                                       pay_currency=pay_cur, pay_amount=pay_amount)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.error("send_message: 落库失败: %s", exc, exc_info=True)
        return jsonify({"result": "error", "error": "server_error",
                        "currency": _currency_snapshot()}), 200

    entry = gift_helper.sent_entry(gift)
    entry['timestamp'] = now_ms

    # 顺手把消息推进对方的待收队列 + WS 广播(客户端在线时能立刻弹提示)。
    try:
        import websocket_handler
        queue = getattr(websocket_handler, 'pending_messages', None)
        lock = getattr(websocket_handler, '_pending_messages_lock', None)
        if isinstance(queue, dict):
            acquired = False
            if lock is not None:
                try:
                    lock.acquire()
                    acquired = True
                except Exception:
                    acquired = False
            try:
                queue.setdefault(target_id, []).append({
                    'type': 'gift',
                    'sender_id': user_id,
                    'msg_id': str(entry['msg_id']),
                    'msg': name,
                    'ch': 'gift',
                    'timestamp': now_ms,
                    'result': 'success',
                })
            finally:
                if acquired:
                    try:
                        lock.release()
                    except Exception:
                        pass
    except Exception as e:
        logger.error(f"handle_send_message: 投递到 {target_id} 的队列失败: {e}")

    try:
        broadcast_message_to_websocket_clients({
            'type': 'gift',
            'sender_id': user_id,
            'msg_id': str(entry['msg_id']),
            'msg': '',
            'ch': 'gift',
            'timestamp': now_ms,
        })
    except Exception:
        pass

    logger.info("send_message: %s -> %s %s (gift_id=%s)", user_id, target_id, name, gift.gift_id)
    return jsonify({
        "res": "ok",
        "result": "ok",
        "currency": _currency_snapshot(),
        "sent_message": entry,
        "timestamp": now_ms,
    }), 200