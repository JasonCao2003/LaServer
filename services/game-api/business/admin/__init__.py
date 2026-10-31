# -*- coding: utf-8 -*-
"""管理接口包(主控/中控面板专用): /admin/api/*

为什么单独一个包:
  * /account/* 是给游戏客户端的, /admin/* 是给主控(admin_panel.py)的;
  * hot_reload.py 的 _ROUTE_PACKAGES 里本来就留了 ("admin", "register_admin_routes")
    一项 —— 以前源码里没有这个包, 所以每次热更新都会带一条
    "admin: No module named 'admin'" 的警告。本包一存在, 热更新就自动带上它,
    改完源码调一次 /admin/api/hot_reload 即可生效, 不用重启私服。
  * 访问控制: request_monitor_middleware._is_admin_only_path() 把整段 "/admin/"
    从公网隧道挡掉(带 Cloudflare 转发头的一律 403), 只有本机主控直连能用。

接口一览(请求体都是 JSON):
  POST /admin/api/friends_list  {user, keyword?}
        某账号的好友行(含方向/状态/昵称/备注/设备名/心火), 给主控"好友控制"页显示。
  POST /admin/api/friends_set   {user, friend, action: add|remove|restore}
        双向加/删/恢复好友; 改完给双方推 friends_updated —— 在线客户端立刻重拉好友树,
        这就是需求里的"账号二要同步删除 / 同步加上"。删除是 status='removed' 的墓碑行,
        不是 DELETE(理由见 friend_helper.set_friendship_state)。
  POST /admin/api/friends_clear {user}
        清空该账号的全部好友(双向), 每个被删的账号各推一条 friends_updated。
"""
import json
import logging

from flask import jsonify, request

logger = logging.getLogger(__name__)


def _payload():
    """容错取请求体(JSON; 主控那边历史上也发过 data=b"{}" 的裸 POST)。"""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    try:
        data = json.loads(request.get_data(as_text=True) or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _friend_rows(user_id, keyword=""):
    """该账号的好友行(正向 + 只有反向的单边数据, 去重)。"""
    from infrastructure.database.models import Friendship, FriendStatus, User

    rows = []
    seen = set()

    def _one(other_id):
        if not other_id or other_id in seen:
            return
        seen.add(other_id)
        forward = Friendship.query.filter_by(user_id=user_id, friend_id=other_id).first()
        reverse = Friendship.query.filter_by(user_id=other_id, friend_id=user_id).first()
        status = FriendStatus.query.filter_by(user_id=user_id, friend_id=other_id).first()
        user = User.query.filter_by(id=other_id).first()
        rows.append({
            "friend_id": other_id,
            "status": (forward.status if forward is not None else ""),
            "reverse_status": (reverse.status if reverse is not None else ""),
            "two_way": bool(forward is not None and reverse is not None),
            "nickname": (status.nickname if status is not None else "") or "",
            "given": _int(status.given) if status is not None else 0,
            "recvd": _int(status.recvd) if status is not None else 0,
            "remark": (user.remark if user is not None else "") or "",
            "device_name": (user.device_name if user is not None else "") or "",
        })

    for row in Friendship.query.filter_by(user_id=user_id).all():
        _one(row.friend_id)
    for row in Friendship.query.filter_by(friend_id=user_id).all():
        _one(row.user_id)

    kw = (keyword or "").strip().lower()
    if kw:
        rows = [r for r in rows
                if kw in (r["friend_id"] or "").lower()
                or kw in r["nickname"].lower()
                or kw in r["remark"].lower()
                or kw in r["device_name"].lower()]
    # 正常好友排前面, 其余(拉黑/墓碑/单边)按名字排
    rows.sort(key=lambda r: (r["status"] != "active", r["nickname"] or r["friend_id"]))
    return rows


def register_admin_routes(app):
    """注册 /admin/api/* 管理接口(由 hot_reload 调用)。"""

    def _endpoint_friends_list():
        data = _payload()
        user_id = str(data.get("user") or "").strip()
        if not user_id:
            return jsonify({"ok": False, "error": "缺少 user"})
        try:
            rows = _friend_rows(user_id, data.get("keyword") or "")
        except Exception as exc:  # noqa: BLE001
            logger.error("admin friends_list 失败: %s", exc, exc_info=True)
            return jsonify({"ok": False, "error": "读取失败: %s" % exc})
        return jsonify({"ok": True, "user": user_id, "count": len(rows), "friends": rows})

    def _endpoint_friends_set():
        data = _payload()
        user_id = str(data.get("user") or "").strip()
        friend_id = str(data.get("friend") or data.get("friend_id") or "").strip()
        action = str(data.get("action") or data.get("state") or "add").strip().lower()
        if action in ("del", "delete"):
            action = "remove"
        if action in ("add", "create", "new"):
            action = "add"
        try:
            import business.helpers.friend_helper as friend_helper
            ok, detail = friend_helper.set_friendship_state(user_id, friend_id, action)
        except Exception as exc:  # noqa: BLE001
            logger.error("admin friends_set 失败: %s", exc, exc_info=True)
            return jsonify({"ok": False, "error": "执行失败: %s" % exc})

        pushed = False
        if ok:
            try:
                pushed = bool(friend_helper.push_friends_updated(user_id, friend_id))
            except Exception as exc:  # noqa: BLE001 - 推送失败不影响关系本身
                logger.warning("admin friends_set: 推送 friends_updated 失败: %s", exc)
        logger.info("admin friends_set: %s <-> %s action=%s ok=%s pushed=%s (%s)",
                    user_id, friend_id, action, ok, pushed, detail)
        return jsonify({"ok": bool(ok), "detail": detail, "action": action,
                        "user": user_id, "friend": friend_id, "pushed": pushed})

    def _endpoint_friends_clear():
        data = _payload()
        user_id = str(data.get("user") or "").strip()
        if not user_id:
            return jsonify({"ok": False, "error": "缺少 user"})
        try:
            import business.helpers.friend_helper as friend_helper
            from infrastructure.database.models import Friendship
            ids = {row.friend_id for row in Friendship.query.filter_by(user_id=user_id).all()}
            ids |= {row.user_id for row in Friendship.query.filter_by(friend_id=user_id).all()}
            ids = sorted(i for i in ids if i and i != user_id)
            removed, failed = [], []
            for friend_id in ids:
                ok, _detail = friend_helper.set_friendship_state(
                    user_id, friend_id, friend_helper.FRIEND_STATE_REMOVE)
                (removed if ok else failed).append(friend_id)
            if removed:
                try:
                    friend_helper.push_friends_updated(user_id, *removed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("admin friends_clear: 推送失败: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("admin friends_clear 失败: %s", exc, exc_info=True)
            return jsonify({"ok": False, "error": "执行失败: %s" % exc})
        logger.info("admin friends_clear: %s 清空好友 %d 个(失败 %d)",
                    user_id, len(removed), len(failed))
        return jsonify({"ok": True, "user": user_id, "removed": removed,
                        "removed_count": len(removed), "failed": failed})

    app.add_url_rule("/admin/api/friends_list", "admin_friends_list",
                     _endpoint_friends_list, methods=["POST", "GET"])
    app.add_url_rule("/admin/api/friends_set", "admin_friends_set",
                     _endpoint_friends_set, methods=["POST", "GET"])
    app.add_url_rule("/admin/api/friends_clear", "admin_friends_clear",
                     _endpoint_friends_clear, methods=["POST", "GET"])