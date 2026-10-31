# -*- coding: utf-8 -*-
"""
接口: /account/sync_user_data
函数: sync_user_data()
同步用户数据
"""
from flask import request, jsonify, Response
from database.models import db, User
import os
import json
import time
import sqlite3
import threading

_ud_lock = threading.Lock()


def _ud_db_path():
    """userdata 存档库路径 = Flask 实例目录下的 users.db (与 response_override 同库)。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "instance", "users.db")


def _ud_ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_data (
            user_id TEXT PRIMARY KEY,
            data TEXT NOT NULL DEFAULT '',
            data_time INTEGER DEFAULT 0,
            data_version INTEGER DEFAULT 3,
            updated_at INTEGER DEFAULT 0
        )
    """)

# 新账号(无存档)首次拉取时用的默认存档 blob。base64(JSON)。
# 内含 daily_quests_activated/revealedQuestTimes 等初始为空字段, 与官方一致。
DEFAULT_USERDATA_B64 = (
    "eyJhY2hpZXZlbWVudHMiOnsiZGFpbHlfcXVlc3RzX2FjdGl2YXRlZCI6W2ZhbHNlLGZhbHNlLGZhbHNlLGZhbHNlXSwiZGFpbHlfcXVlc3RzX2ludGVyYWN0aW9uX2Vwb2NoX3RpbWUiOjAsImRhaWx5X3F1ZXN0c19yZXdhcmRfZ2l2ZW4iOltmYWxzZSxmYWxzZSxmYWxzZSxmYWxzZV19LCJhdXRvc2F2ZV9zZXF1ZW5jZSI6eyJiZWF0X2NvdW50IjoxNn0sImVsZGVyX2l0ZW1faHBzIjpbNCwzLDRdLCJlbGRlcl9pdGVtX2lkcyI6WzQyNDc4NzgwMDksNzc2OTQ0MTk3LDg1MDE1NTg5M10sIm1lbnUiOnsiZW1vdGVfZ3JvdXBzIjpbeyJlbW90ZXMiOlt7ImlkIjoyMjUzOTMzODI0LCJsZXZlbCI6MSwidHlwZSI6MX0seyJpZCI6MCwibGV2ZWwiOjAsInR5cGUiOjB9LHsiaWQiOjMzMjUwMzQ4NDcsImxldmVsIjoxLCJ0eXBlIjoxfSx7ImlkIjowLCJsZXZlbCI6MCwidHlwZSI6MH0seyJpZCI6MzUzNjM3MjM2NiwibGV2ZWwiOjEsInR5cGUiOjF9LHsiaWQiOjAsImxldmVsIjowLCJ0eXBlIjowfSx7ImlkIjoyMzUyNzEzMTAsImxldmVsIjoxLCJ0eXBlIjoxfSx7ImlkIjowLCJsZXZlbCI6MCwidHlwZSI6MH1dLCJuYW1lIjoiRmF2b3JpdGVzIiwidXNlckVkaXRlZCI6ZmFsc2V9XSwiZnJpZW5kQ29uc3RlbGxhdGlvblByZWZlcnJlZCI6dHJ1ZSwibmVzdFdlbGNvbWVMZXZlbCI6MH0sIm1pc2MiOnsicHJlZmVycmVkSG9tZUxldmVsIjowLCJzdWJtaXRUdXRvcmlhbEV2ZW50cyI6dHJ1ZX0sInJlY2VudF9jb2xsZWN0aWJsZXMiOlszMzI1MDM0ODQ3XSwicmV2ZWFsZWRRdWVzdFRpbWVzIjpbMCwwLDAsMF0sInR1dG9yaWFsIjp7ImJyZWF0aCI6MCwiYnVkZHlSZXEiOjEsImRpdmUiOjAsImRpdmVNb3ZlIjoxLCJlZGl0bW9kZSI6MCwiZmYiOmZhbHNlLCJmb3JnZSI6MiwibGFzdEpvaW5SYW5kb21HYW1lVGltZSI6MzQ1MzI0MTcxMiwibGFzdFRlbGVwb3J0SG9tZVRpbWUiOjI5NjkxMzg0MTYsIm1lbW9yeVNocmluZXNWaWV3ZWQiOjAsIm1lbW9yeVNwZWxsc1ZpZXdlZCI6MCwibWVudSI6MSwic2hvcCI6MCwic2hvdXQiOjAsInNwYWNlU2hyaW5lc1ZpZXdlZCI6MCwic3BhY2VTcGVsbHNWaWV3ZWQiOjAsIndlYlJlbW90ZSI6MH19"
)


def _save_userdata(user_id, encoded_data, client_time, client_version):
    """把客户端上传的存档 blob 持久化到 user_data 表 (按 user_id upsert)。
    encoded_data 为空/None 时不覆盖已有存档 (避免心跳空包清档)。
    用原生 sqlite3(与 response_override 同库), 避免依赖热更新不会重载的 models 表映射。"""
    if not user_id or not encoded_data:
        return
    try:
        ct = int(client_time) if client_time is not None else 0
    except (TypeError, ValueError):
        ct = 0
    try:
        cv = int(client_version) if client_version is not None else 3
    except (TypeError, ValueError):
        cv = 3
    try:
        with _ud_lock:
            conn = sqlite3.connect(_ud_db_path())
            _ud_ensure_table(conn)
            conn.execute("""
                INSERT INTO user_data (user_id, data, data_time, data_version, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    data=excluded.data, data_time=excluded.data_time,
                    data_version=excluded.data_version, updated_at=excluded.updated_at
            """, (user_id, encoded_data, ct, cv, int(time.time())))
            conn.commit()
            conn.close()
    except Exception:
        pass


def _load_userdata(user_id):
    """读取该用户已保存的存档, 返回 (data_b64, time, version)。无则 (None, 0, 0)。"""
    if not user_id:
        return None, 0, 0
    try:
        with _ud_lock:
            conn = sqlite3.connect(_ud_db_path())
            _ud_ensure_table(conn)
            row = conn.execute(
                "SELECT data, data_time, data_version FROM user_data WHERE user_id=?",
                (user_id,)).fetchone()
            conn.close()
        if row and row[0]:
            return row[0], row[1] or 0, row[2] or 3
    except Exception:
        pass
    return None, 0, 0


def sync_user_data():
    try:
        raw_data = request.get_data(as_text=True)
        content_type = request.headers.get('Content-Type')
        
        print(f"Content-Type: {content_type}")
        print(f"Raw request data: {raw_data[:200]}...")
        
        # 尝试解析 JSON，不检查Content-Type
        request_data = None
        try:
            # 使用force=True强制解析JSON，不依赖Content-Type
            request_data = request.get_json(force=True)
        except Exception as e:
            # 如果get_json失败，尝试手动解析
            try:
                request_data = json.loads(raw_data) if raw_data else None
            except json.JSONDecodeError as json_err:
                print(f"JSON decode error: {json_err}")
        
        print(f"Parsed data: {request_data}")
        
        if not request_data:
            error_detail = {
                'error': 'Invalid request body',
                'content_type': content_type,
                'raw_data_length': len(raw_data) if raw_data else 0,
                'raw_data_preview': raw_data[:100] if raw_data else 'None'
            }
            return jsonify(error_detail), 400
        
        if 'user' not in request_data:
            error_detail = {
                'error': 'Missing required fields',
                'received_keys': list(request_data.keys()) if request_data else 'No data',
                'expected': 'user field is required'
            }
            return jsonify(error_detail), 400
        
        user_id = request_data.get('user')
        session_id = request_data.get('session')
        
        print(f"Processing request for user: {user_id}, session: {session_id}")
        
        # ========== 检查用户授权状态 ==========
        user = User.query.filter_by(id=user_id).first()
        
        if not user:
            print(f"用户 {user_id} 不存在")
            return jsonify({
                "error": "User not found",
                "message": "用户不存在"
            }), 404
        
        # ========== 检查封禁状态 ==========
        if user.is_banned == 1:
            try:
                current_time = int(time.time())
                ban_end_time = user.ban_end_time
                
                # 永久封禁 或 未到期 → 返回封禁信息 + 404 响应码
                if ban_end_time == 0 or current_time < ban_end_time:
                    print(f"用户 {user_id} 被封禁，禁止同步数据")
                    
                    ban_info = {
                        "baninfo": {
                            "user_id": user.id,
                            "end_date": ban_end_time,
                            "type": "account"
                        },
                        "user": user.id,
                        "end_date": ban_end_time,
                        "message": "账号已被封禁，无法同步数据"
                    }
                    
                    if ban_end_time > 0:
                        remaining = ban_end_time - current_time
                        ban_info["baninfo"]["remaining_seconds"] = remaining
                        ban_info["baninfo"]["remaining_days"] = remaining // 86400
                    
                    return jsonify(ban_info), 404
                
                else:
                    print(f"用户 {user_id} 封禁已过期，自动解封")
                    user.is_banned = 0
                    user.ban_duration = 0
                    user.ban_start_time = 0
                    user.ban_end_time = 0
                    db.session.commit()
                    
            except Exception as e:
                print(f"处理封禁状态失败: {str(e)}")
        
        # ========== 授权状态校验 ==========
        if user.is_authorized == 0:
            print(f"账号 {user_id} 未授权，禁止同步数据")
            current_time = int(time.time())
            ban_end_time = 0  # 永久封禁
            
            return jsonify({
                "baninfo": {
                    "user_id": user.id,
                    "end_date": ban_end_time,
                    "type": "unauthorized",
                    "message": "账号尚未授权，请联系管理员"
                },
                "user": user.id,
                "end_date": ban_end_time,
                "message": "账号未授权，无法同步数据"
            }), 404
        
        # 可选：验证session（如果需要更强的安全性）
        if session_id and hasattr(user, 'session'):
            if user.session != session_id:
                print(f"用户 {user_id} 的session不匹配，可能已过期")
                # 可以选择返回401或继续处理，这里我们只记录警告
                print(f"警告：session不匹配。用户session: {user.session[:8]}...，请求session: {session_id[:8]}...")
        
        # ========== 已授权且未封号，返回用户数据 ==========
        client_time = request_data.get('time')
        client_version = request_data.get('version')
        encoded_data = request_data.get('data')
        
        print(f"用户 {user_id} 已授权，返回同步数据")

        # 保存客户端上传的存档(若本次带了 data), 实现真正持久化。
        _save_userdata(user_id, encoded_data, client_time, client_version)

        # 返回该用户已保存的存档; 无则给默认 blob。
        saved, st, sv = _load_userdata(user_id)
        if saved:
            out_data = saved
            out_time = st or 1738138669
            out_version = sv or 3
        else:
            out_data = DEFAULT_USERDATA_B64
            out_time = 1738138669
            out_version = 3

        response_data = {
            "userdata": {
                "time": out_time,
                "version": out_version,
                "data": out_data,
            }
        }

        return jsonify(response_data), 200
        
    except Exception as e:
        print(f"Error in sync_user_data: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
