# -*- coding: utf-8 -*-
r"""
Service接口: /service/relationship/api/v1/update_friend_constellation_pages
===========================================================================

客户端在社交界面里"把好友摆到星座页(标星)上 / 挪位置 / 改分页名"时, 会把它整份
布局一次性发上来(实测抓包:
monitor_exports/cache/service__relationship__api__v1__update_friend_constellation_pages.jsonc,
2026-10-04 08:55:55 与 08:59:56, 两个客户端各一次):

  请求: {"user","session",
         "pages":[{"name":"","friends":[{"friend_id":..,"constellation_node_index":..}]}]}
  实测那两次 pages 都是 10 页、每页 friends 为空(客户端把整份 10 页一起发)。

改造前: 这条路径只注册了一个 update_friend_constellation_pages_options()(本来是给
CORS 预检用的空 Response), 却被当成 POST 业务处理器 -> **200 + 空 body**。客户端对
这个接口是有响应对象的(0.22.0 的 libBootloader.so 里
"AccountUpdateFriendConstellationPageRequest | /service/relationship/api/v1/
update_friend_constellation_pages | UpdateFriendConstellationPagesRequest | OK |
, status: " 是连在一起的一串), 空 body 让客户端解析失败 —— 实测"设置玩家为标星
好友闪退"。

读侧(get_friend_statues)里这份数据叫 constellation_friend_pages, 官方形状见
F:\QQ\QQ下载\sky_cn_api_reference(1).md 的 get_friend_constellation 一节:
  "constellation_friend_pages": [{"name": <string>,
                                  "friends": [{"friend_id": <string>,
                                               "constellation_node_index": <integer>}]}]
官方这一层**没有 page_index 字段**, 也就是客户端只能按数组顺序去对齐页号 —— 所以
这里回包按 0..最大页号补满(空页给空 friends), 让数组下标 == 页号。

写入规则: 请求里出现的每一页 = "这一页现在长这样", 先删该页旧行再按请求重建;
请求里没出现的页保持原样(客户端万一只发改动过的页, 也不会把别的页误删)。

没落库的: 分页名字。constellation_friend_page 表只有
(user_id, page_index, friend_id, constellation_node_index), 没有存名字的列, 所以
回包里 name 一律是 ""(客户端给分页改名不会被保存)。要保存得给那张表加一列
(属于改 users.db 的 schema), 需要用户先点头。
"""

from flask import request, Response
from database.models import db, User, ConstellationFriendPage
import json
import logging

logger = logging.getLogger(__name__)

# 客户端自己最多 10 页(整份一起发)。这里留个上限, 免得请求里塞一堆页号把表撑大。
_MAX_PAGES = 64


def _load_pages(user_id, min_pages=0):
    """库里存的星座页 -> 官方形状的数组(按页号补满)。

    page_index 不是官方字段, 是额外给的 —— 客户端按数组下标对齐页号, 多给一个
    同样的信息不影响它解析。
    """
    rows = (
        ConstellationFriendPage.query
        .filter_by(user_id=user_id)
        .order_by(ConstellationFriendPage.page_index,
                  ConstellationFriendPage.constellation_node_index)
        .all()
    )

    by_page = {}
    for row in rows:
        try:
            page_index = int(row.page_index)
        except (TypeError, ValueError):
            continue
        by_page.setdefault(page_index, []).append({
            "friend_id": str(row.friend_id),
            "constellation_node_index": int(row.constellation_node_index or 0),
        })

    total = max([int(min_pages or 0)] + [index + 1 for index in by_page])
    return [
        {"name": "", "page_index": index, "friends": by_page.get(index, [])}
        for index in range(total)
    ]


def _body(pages, result, status):
    return {
        "res": "ok",
        "result": result,
        "status": status,
        "constellation_friend_pages": pages,
        "pages": pages,
        "total_pages": len(pages),
        "total_friends": sum(len(page["friends"]) for page in pages),
    }


def _reply(payload, http_status=200):
    return Response(json.dumps(payload, ensure_ascii=False), http_status,
                    mimetype="application/json")


def _failure(reason, user_id=None):
    """失败也必须 200 + 合法 JSON。

    客户端是把响应当对象解析的(空 body 会崩), 所以失败也回一份结构完整的空结果,
    只把原因写在 status 里。
    """
    logger.warning("update_friend_constellation_pages: %s (user=%s)", reason, user_id)
    return _reply(_body([], False, reason))


def update_friend_constellation_pages():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        raw_pages = data.get("pages")

        if not user_id or not session:
            return _failure("missing_parameters")

        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return _failure("unauthorized", user_id)

        sent_pages = 0
        if isinstance(raw_pages, list):
            sent_pages = min(len(raw_pages), _MAX_PAGES)

            for page_index, page in enumerate(raw_pages[:_MAX_PAGES]):
                if not isinstance(page, dict):
                    continue
                friends = page.get("friends")
                if not isinstance(friends, list):
                    friends = []

                # 这一页 = 请求里说的样子: 先清旧行, 再按请求重建。
                ConstellationFriendPage.query.filter_by(
                    user_id=user_id, page_index=page_index
                ).delete()

                seen = set()
                for item in friends:
                    if not isinstance(item, dict):
                        continue
                    friend_id = item.get("friend_id") or item.get("friend")
                    if not isinstance(friend_id, str):
                        continue
                    # 客户端有时带 ":<hash>" 尾巴(与 give_candle 的 recv_user 同款)
                    friend_id = friend_id.split(":")[0].strip()
                    if not friend_id or friend_id in seen:
                        continue
                    seen.add(friend_id)
                    try:
                        node_index = int(item.get("constellation_node_index") or 0)
                    except (TypeError, ValueError):
                        node_index = 0
                    db.session.add(ConstellationFriendPage(
                        user_id=user_id,
                        page_index=page_index,
                        friend_id=friend_id[:36],
                        constellation_node_index=node_index,
                    ))

            try:
                db.session.commit()
            except Exception as exc:  # noqa: BLE001 - 写库失败也要回 200
                db.session.rollback()
                logger.error("update_friend_constellation_pages: 写库失败(user=%s): %s",
                             user_id, exc, exc_info=True)
                return _failure("server_error", user_id)

        pages = _load_pages(user_id, min_pages=sent_pages)
        logger.debug("update_friend_constellation_pages: user=%s 收到 %s 页, 现存 %s 页",
                     user_id, sent_pages, len(pages))
        return _reply(_body(pages, True, "ok"))

    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.error("update_friend_constellation_pages: 未预期错误: %s", exc, exc_info=True)
        return _failure("server_error")


def update_friend_constellation_pages_options():
    """
    处理CORS预检请求
    """
    response = Response()
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,User-Agent,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'POST,OPTIONS')
    response.headers.add('Access-Control-Max-Age', '86400')
    return response


def update_friend_constellation_pages_entry():
    """url_map 里这条路径真正挂着的视图函数(POST + OPTIONS 一把抓)。

    为什么要分派而不是让 Flask 自己按方法挑: 运行中的服务器是**热更新**的
    (hot_reload.py 的 _patched_add), 对"已经存在的端点"它只替换
    app.view_functions[端点名], 不会重建 url_map。这条路径在运行中的 url_map 里
    挂的端点名就是 update_friend_constellation_pages_options(历史上 POST 被错挂到
    预检处理器上)。所以只能沿用这个名字注册 POST+OPTIONS、把视图函数换成这个
    分派器 —— 换新端点名的话会和 url_map 里那条旧规则撞在同一条路径上, 先注册的
    旧规则(空 body)会继续赢, 客户端继续崩。

    重启服务器后其实两种写法都行, 但保持和热更新一致最省事。
    """
    if request.method == 'OPTIONS':
        return update_friend_constellation_pages_options()
    return update_friend_constellation_pages()