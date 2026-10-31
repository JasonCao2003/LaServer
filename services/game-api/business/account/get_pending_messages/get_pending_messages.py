# -*- coding: utf-8 -*-
"""
接口: /account/get_pending_messages
函数: get_pending_messages()
获取待发送的消息（HTTP轮询机制 - 支持长轮询）
"""
from flask import request, jsonify
import time
import uuid
import logging

logger = logging.getLogger(__name__)

pending_messages = {}
_pending_messages_lock = None

try:
    import threading
    _pending_messages_lock = threading.Lock()
except:
    import eventlet
    _pending_messages_lock = eventlet.semaphore.Semaphore()


def _shared_queue():
    """返回 (待发消息队列, 锁)。

    修复聊天"发出去但别人收不到"的根因:
      /account/chat/send 的广播最终写进 websocket_handler 里的全局队列
      (index.py 启动时通过 init_websocket 传进去的那一个), 而本模块自己又 new 了
      一个同名空 dict —— HTTP 轮询读的一直是"永远没人写入"的那个 dict, 所以
      set_recvd_messages 恒为空数组, 玩家永远收不到任何聊天/系统消息
      (request_log 里 222 次 /account/get_pending_messages 全部返回空)。
    这里优先使用 websocket_handler 的全局队列; 若它还没初始化(未调 init_websocket),
    退回本模块的 dict, 行为与原来一致。
    """
    try:
        import infrastructure.transport.websocket_handler as _wh
        q = getattr(_wh, "pending_messages", None)
        if isinstance(q, dict):
            return q, getattr(_wh, "_pending_messages_lock", None)
    except Exception:
        pass
    return pending_messages, _pending_messages_lock


def _acquire(lock):
    try:
        if lock is not None:
            lock.acquire()
    except Exception:
        pass


def _release(lock):
    try:
        if lock is not None:
            lock.release()
    except Exception:
        pass


def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    from infrastructure.database.models import User
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_pending_messages():
    """获取待发送的消息（HTTP轮询机制 - 支持长轮询）"""
    try:
        data = request.get_json(force=True)
        
        if not data or 'user' not in data or 'session' not in data:
            return jsonify({"error": "Missing user or session"}), 400
        
        user_id = data['user']
        session_token = data['session']
        
        wait_time = float(data.get('wait', 0))  # 等待时间（秒），默认0即立即返回
        max_wait = min(wait_time, 30.0)  # 最大等待30秒
        
        user = get_cached_user(user_id, session_token)
        if not user:
            return jsonify({"error": "Invalid session"}), 401
        
        start_time = time.time()
        check_interval = 0.1  # 每100ms检查一次
        queue, lock = _shared_queue()

        while max_wait > 0:
            _acquire(lock)
            try:
                user_messages = queue.get(user_id, [])
                if user_messages:
                    break
            finally:
                _release(lock)

            elapsed = time.time() - start_time
            if elapsed >= max_wait:
                break

            try:
                import eventlet
                eventlet.sleep(min(check_interval, max_wait - elapsed))
            except:
                time.sleep(min(check_interval, max_wait - elapsed))

        _acquire(lock)
        try:
            user_messages = queue.get(user_id, [])
            if user_id in queue:
                del queue[user_id]
        finally:
            _release(lock)
        
        recvd_messages = []
        for msg in user_messages:
            recvd_messages.append({
                'type': msg.get('type', 'chat'),
                'sender_id': msg.get('sender_id', 'unknown'),
                'msg_id': msg.get('msg_id', str(uuid.uuid4())),
                'msg': msg.get('msg', ''),
                'ch': msg.get('ch', 'global'),
                'timestamp': msg.get('timestamp', int(time.time() * 1000)),
                'result': 'success'
            })

        # 好友赠礼(心火/爱心): 官方响应里 set_sent_messages / set_recvd_messages 也带
        # 这些 message gift 条目(见 static_responses/account__give_candle.json 的
        # set_sent_messages 与 account__claim_message_gift.json 的 set_recvd_messages)。
        # 以前这里恒回空数组 -> 收件箱里看不到待领的心火/爱心。
        set_sent_messages = []
        try:
            import business.helpers.gift_helper as gift_helper
            sent_gifts, recvd_gifts = gift_helper.pending_gifts(user_id)
            set_sent_messages = [gift_helper.sent_entry(g) for g in sent_gifts]
            for gift in recvd_gifts:
                recvd_messages.append(gift_helper.recvd_message_entry(gift))
        except Exception as exc:  # noqa: BLE001 - 礼物读取失败不影响聊天消息
            logger.warning("get_pending_messages: 读取赠礼失败: %s", exc)

        logger.info(f"Returning {len(recvd_messages)} pending messages to user {user_id} (waited {time.time() - start_time:.2f}s)")

        response_data = {
            "set_sent_messages": set_sent_messages,
            "set_recvd_messages": recvd_messages
        }

        return jsonify(response_data), 200
        
    except Exception as e:
        logger.error(f"Error in get_pending_messages: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
