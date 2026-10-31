# -*- coding: utf-8 -*-
"""
接口: /account/userdata
函数: get_userdata()
获取(并按需保存)用户存档。

之前实现忽略客户端上传的 data, 永远返回同一份写死的静态 blob, 导致存档(教程进度、
常用表情、每日任务 revealedQuestTimes/daily_quests_activated/interaction_epoch 等)
从不持久化 -> 向导每日任务的互动状态点了存不下、下次又被清零, 表现为"点互动无反应"。
本实现: 若请求带 data 则先保存; 然后返回该用户已保存的存档, 新账号无存档时给默认 blob。
"""
from flask import request, jsonify
import json
from business.account.sync_user_data.sync_user_data import DEFAULT_USERDATA_B64, _save_userdata, _load_userdata


def get_userdata():
    try:
        raw_data = request.get_data(as_text=True)
        content_type = request.headers.get('Content-Type')

        request_data = None
        try:
            request_data = request.get_json(force=True)
        except Exception:
            try:
                request_data = json.loads(raw_data) if raw_data else None
            except json.JSONDecodeError as json_err:
                print(f"JSON decode error: {json_err}")

        if not request_data:
            return jsonify({
                'error': 'Invalid request body',
                'content_type': content_type,
                'raw_data_length': len(raw_data) if raw_data else 0,
                'raw_data_preview': raw_data[:100] if raw_data else 'None'
            }), 400

        if 'user' not in request_data:
            return jsonify({
                'error': 'Missing required fields',
                'received_keys': list(request_data.keys()) if request_data else 'No data',
                'expected': 'user field is required'
            }), 400

        user_id = request_data.get('user')
        client_time = request_data.get('time')
        client_version = request_data.get('version')
        encoded_data = request_data.get('data')

        # 客户端有时在 userdata 请求里也带 data(自动保存), 一并持久化。
        if encoded_data:
            _save_userdata(user_id, encoded_data, client_time, client_version)

        # 返回该用户已保存的存档; 无则默认 blob。
        saved, st, sv = _load_userdata(user_id)
        if saved:
            out_data = saved
            out_time = st or 1738138669
            out_version = sv or 3
        else:
            out_data = DEFAULT_USERDATA_B64
            out_time = 1738138669
            out_version = 3

        return jsonify({
            "userdata": {
                "time": out_time,
                "version": out_version,
                "data": out_data,
            }
        }), 200

    except Exception as e:
        print(f"Error in get_userdata: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
