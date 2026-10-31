# -*- coding: utf-8 -*-
"""
Service接口: /service/social-feed/api/v1/edit  ("替换"已经存在的留言/纸船/留影)

客户端字符串表里**有**这条路径(反编译 _strs.txt: /service/social-feed/api/v1/edit),
而服务端原来没有注册它 -> 请求落到 JSON 兜底 -> 返回 200 {}。玩家站在"已经放过
留言/纸船/留影"的地方再放一次/点替换时, 客户端走的就是 edit, 拿不到 result /
social_feed_id 就继续不了(实测几次闪退会话的最后一条请求都是 recording/create,
后面再没有任何请求 —— 客户端在"替换"分支里拿不到合法 id)。

响应形状按 create 同族(官方 create 抓包 static_responses/
service__social-feed__api__v1__create.json):
    {"result": "success", "social_feed_id": "<uuid>:<8hex>"}

行为(尽量宽容, 绝不回 4xx —— 客户端把任何非 2xx 显示成"非法请求。\n请重试。"):
  1. 按 social_feed_id / feed_id / id / social_feed_ids[0] 取 id(允许 "<uuid>:<8hex>"),
     查到库里那条 feed 就**原地更新**(message / pool_* / extra_data);
  2. 查不到(客户端发的是全 0 占位 id, 或这条 feed 是"离线时摆的"没入库):
     用客户端给的 uuid 新建一条(保持它本地物件认的 id 不变);uuid 不合法就新生成;
  3. extra_data 走旁路文件(extra_store, 不动数据库表结构): 新值和旧值**合并**,
     这样"只更新机器人坐标/只换留影的 resource_id"都不会把其它字段抹掉 ——
     留影蜡烛替换时客户端只发新的 resource_id, 旧实现(没有这个接口)会让它一直
     指向旧留影。
"""
from flask import request, Response
from database.models import db, User, SocialFeedPost
import hashlib
import json
import logging
import time
import uuid

from . import extra_store

logger = logging.getLogger(__name__)

# 与 social_feed_create 一致的过期时长(官方留言/纸船默认 14 天)
TTL_SECONDS = 1209600

# 客户端"还没有 id"的哨兵值(见 get_by_ids.NIL_ID)
NIL_ID = "00000000-0000-0000-0000-000000000000"


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False,
                                        sort_keys=False),
                    status=status, mimetype="application/json")


def _public_id(post):
    """客户端认的 "<uuid>:<8hex>"(与 social_feed_create 的算法一致)。"""
    fid = post.social_feed_id or ""
    if ":" in fid:
        return fid
    suffix = hashlib.md5((fid + (post.message or "")).encode("utf-8")).hexdigest()[:8]
    return "%s:%s" % (fid, suffix)


def _pick_id(data):
    """从请求体里取要替换的 feed id(各版本键名不一)。"""
    for key in ("social_feed_id", "feed_id", "id", "social_feed_ids", "feed_ids", "ids"):
        if key not in data:
            continue
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x.strip():
                    return x.strip()
    return ""


def _looks_like_uuid(s):
    s = str(s or "")
    return len(s) == 36 and s.count("-") == 4 and s != NIL_ID


def social_feed_edit():
    """替换一条已有的 feed(纸船/留言/留影)。永不回非 2xx。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        # 宽容: 会话不对也只记日志, 不 4xx(否则客户端弹"非法请求", 反而更像卡死)。
        user = (User.query.filter_by(id=user_id, session=session).first()
                if (user_id and session) else None)
        if user is None:
            logger.warning("social-feed edit: 会话无效(user=%r), 仍按请求处理", user_id)

        raw_id = _pick_id(data)
        fid = raw_id.split(":")[0]
        post = (SocialFeedPost.query.filter_by(social_feed_id=fid).first()
                if fid else None)

        message = data.get("message")
        extra_data = data.get("extra_data")
        if not isinstance(extra_data, dict):
            extra_data = {}
        # 客户端有的版本把 resource_id 放在顶层(留影蜡烛替换时用它指新留影)
        if isinstance(data.get("resource_id"), str):
            extra_data.setdefault("resource_id", data["resource_id"])

        if post is None:
            # 没查到 -> 当成"以这个 id 新建"(保持客户端本地物件认的 id 不变)。
            new_uuid = fid if _looks_like_uuid(fid) else str(uuid.uuid4())
            now = int(time.time())
            post = SocialFeedPost(
                user_id=(user.id if user is not None else (user_id or "")),
                social_feed_id=new_uuid,
                pool_type=str(data.get("pool_type") or ""),
                pool_name=str(data.get("pool_name") or ""),
                message=("" if message is None else str(message)),
                created_at=now,
                expire_at=now + TTL_SECONDS,
                likes_count=0, comments_count=0, report_count=0,
                hide_count=0, attempt_count=0, succeed_count=0, is_poisoned=False,
            )
            db.session.add(post)
            logger.info("social-feed edit: 未查到 %r -> 以该 id 新建 %s", raw_id, new_uuid)
        else:
            if message is not None:
                post.message = str(message)
            if data.get("pool_type"):
                post.pool_type = str(data["pool_type"])
            if data.get("pool_name"):
                post.pool_name = str(data["pool_name"])
            # 替换 = 重新放一次: 续上过期时间(列表接口只下发未过期的 feed, 不续就"放完了看不到")
            post.expire_at = int(time.time()) + TTL_SECONDS
            logger.info("social-feed edit: 更新 %s (pool=%s/%s)",
                        post.social_feed_id, post.pool_type, post.pool_name)

        db.session.commit()

        # 回给客户端的 id: **优先把客户端发来的合法 id 原样回填**(它本地物件就是按
        # 这个 id 存的, 换了 id 它就对不上了); 客户端发的是全 0/没带后缀时才用本条的
        # 公开 id。注意 _public_id 的后缀是按 message 推的, 改过 message 就变了 ——
        # 所以能原样回就原样回。
        public_id = raw_id if (":" in raw_id and raw_id.split(":")[0] == post.social_feed_id) \
            else _public_id(post)
        # extra_data 合并后落旁路文件(数据库没有这一列)
        if extra_data:
            old = extra_store.load_record(public_id).get("extra_data")
            merged = dict(old) if isinstance(old, dict) else {}
            merged.update(extra_data)
            extra_store.save_extra(public_id, merged,
                                   comments_enabled=data.get("comments_enabled"),
                                   tags=data.get("tags"),
                                   is_private=data.get("private"))
        return _resp({"result": "success", "social_feed_id": public_id})
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.error("social-feed edit 异常: %s", e, exc_info=True)
        # 形状正确的失败响应, 客户端不会弹"非法请求"
        return _resp({"result": "error"})
