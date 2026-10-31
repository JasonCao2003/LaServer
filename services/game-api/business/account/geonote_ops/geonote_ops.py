# -*- coding: utf-8 -*-
"""任务留言的删除 / 编辑 / 互动(点赞·可见·举报·表情) / bug 报告列表。

对应 0.15.x 的:
  /account/geonote/delete_geonote
  /account/geonote/delete_bug_report_geonote
  /account/geonote/edit_message_geonote
  /account/geonote/social_geonote2
  /account/geonote/request_n_bug_report_geonotes
"""

import json

from flask import Response, request

import business.helpers.geonote_store as geonote_store
from infrastructure.database.models import User


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def _session_user(data):
    user_id = data.get("user") or data.get("user_id")
    session = data.get("session")
    if not user_id or not session:
        return None, _resp({"result": "invalid_params"}, 400)
    user = User.query.filter_by(id=user_id, session=session).first()
    if not user:
        return None, _resp({"result": "invalid_session"}, 401)
    return user_id, None


def _level_of(data):
    level = data.get("level_id")
    if level in (None, "", 0, "0"):
        return None
    return level


def delete_geonote():
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        user_id, err = _session_user(data)
        if err is not None:
            return err
        gid = data.get("geonote_id") or data.get("id") or ""
        geonote_store.delete(gid, user_id)
        return _resp({"result": "ok"})
    except Exception as exc:  # noqa: BLE001
        return _resp({"result": "error", "error": str(exc)})


def delete_bug_report_geonote():
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        user_id, err = _session_user(data)
        if err is not None:
            return err
        gid = data.get("geonote_id") or data.get("id") or ""
        geonote_store.delete(gid, user_id, bug_only=True)
        return _resp({"result": "ok"})
    except Exception as exc:  # noqa: BLE001
        return _resp({"result": "error", "error": str(exc)})


def edit_message_geonote():
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        user_id, err = _session_user(data)
        if err is not None:
            return err
        gid = data.get("geonote_id") or data.get("id") or ""
        message = data.get("message") or data.get("msg") or ""
        note = geonote_store.edit(gid, message, user_id)
        body = geonote_store.payload(
            user_id,
            level_id=(note or {}).get("level_id") or _level_of(data),
            bug=bool((note or {}).get("bug")))
        body.update({"result": "ok", "geonote": note or {}})
        return _resp(body)
    except Exception as exc:  # noqa: BLE001
        return _resp({"result": "error", "geonote": {}, "error": str(exc)})


def social_geonote2():
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        user_id, err = _session_user(data)
        if err is not None:
            return err
        gid = data.get("geonote_id") or data.get("id") or ""
        note = geonote_store.mutate(
            gid,
            liked=data.get("liked"),
            visible=data.get("visible"),
            reported=data.get("reported"),
            emoji_id=data.get("emoji_id"),
            viewer=user_id,
        )
        body = geonote_store.payload(
            user_id,
            level_id=(note or {}).get("level_id") or _level_of(data),
            bug=bool((note or {}).get("bug")))
        body.update({"result": "ok"})
        return _resp(body)
    except Exception as exc:  # noqa: BLE001
        return _resp({"result": "error", "error": str(exc)})


def request_n_bug_report_geonotes():
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        user_id, err = _session_user(data)
        if err is not None:
            return err
        limit = data.get("n") or data.get("limit") or 50
        return _resp(geonote_store.payload(user_id, level_id=_level_of(data),
                                           limit=limit, bug=True))
    except Exception as exc:  # noqa: BLE001
        return _resp({"result": "error", "error": str(exc)})
