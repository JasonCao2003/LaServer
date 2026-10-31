# -*- coding: utf-8 -*-
"""
接口: /account/get_remote_outfit  (高版本 > 0.26.5 新增)
获取目标玩家的外观装扮(使用重塑查看他人外观时调用)。
请求: {"user","user_id","session","target"}
响应(按抓包"使用重塑的接口"):
  {"result":"ok","get_remote_outfit":{...装扮...},"serendipity_types":[]}
"""
from flask import request, Response
from infrastructure.database.models import db, User, UserOutfit
import json
import logging

logger = logging.getLogger(__name__)


def _serialize(o):
    """UserOutfit 行 -> 客户端期望的装扮结构(字段名与 set_outfit/get_outfit 一致)。"""
    return {
        "wing": {"tex": o.wing_tex, "pat": o.wing_pat, "mask": o.wing_mask, "id": o.wing_id},
        "voice": o.voice,
        "seed": o.seed,
        "scale": o.scale,
        "refreshversion": o.refreshversion,
        "prop": {"tex": o.prop_tex, "pat": o.prop_pat, "mask": o.prop_mask, "id": o.prop_id},
        "neck": {"tex": o.neck_tex, "pat": o.neck_pat, "mask": o.neck_mask, "id": o.neck_id},
        "mask": {"tex": o.mask_tex, "pat": o.mask_pat, "mask": o.mask_mask, "id": o.mask_id},
        "horn": {"tex": o.horn_tex, "pat": o.horn_pat, "mask": o.horn_mask, "id": o.horn_id},
        "height": o.height,
        "hat": {"tex": o.hat_tex, "pat": o.hat_pat, "mask": o.hat_mask, "id": o.hat_id},
        "hair": {"tex": o.hair_tex, "pat": o.hair_pat, "mask": o.hair_mask, "id": o.hair_id},
        "feet": {"tex": o.feet_tex, "pat": o.feet_pat, "mask": o.feet_mask, "id": o.feet_id},
        "face": {"tex": o.face_tex, "pat": o.face_pat, "mask": o.face_mask, "id": o.face_id},
        "body": {"tex": o.body_tex, "pat": o.body_pat, "mask": o.body_mask, "id": o.body_id},
        "attitude": o.attitude or "0",
        "arms": {"id": o.arms_id},
    }


def _default_outfit(user_id):
    """目标无装扮记录时, 返回初始装扮(与 get_outfit 的初始值一致)。"""
    o = UserOutfit(
        user_id=user_id,
        wing_tex=0, wing_pat=0, wing_mask=0, wing_id=2245279351,
        voice=9, seed=16515, scale=0.22549559, refreshversion=0,
        prop_tex=0, prop_pat=0, prop_mask=0, prop_id=2035109393,
        neck_tex=0, neck_pat=0, neck_mask=0, neck_id=3800884691,
        mask_tex=0, mask_pat=0, mask_mask=0, mask_id=3663390882,
        horn_tex=0, horn_pat=0, horn_mask=0, horn_id=3680499229,
        height=1.93536222,
        hat_tex=0, hat_pat=0, hat_mask=0, hat_id=1470462579,
        hair_tex=0, hair_pat=0, hair_mask=0, hair_id=1229053584,
        feet_tex=0, feet_pat=0, feet_mask=0, feet_id=1532093990,
        face_tex=0, face_pat=0, face_mask=0, face_id=4267756678,
        body_tex=0, body_pat=0, body_mask=0, body_id=3691343236,
        attitude="0", arms_id=0,
    )
    db.session.add(o)
    return o


def get_remote_outfit():
    try:
        data = request.get_json(force=True, silent=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        target = data.get("target") or data.get("target_user") or data.get("remote_user")
        if not user_id or not session:
            return Response(json.dumps({"error": "Invalid request body"}),
                            400, mimetype="application/json")
        if not target:
            return Response(json.dumps({"error": "Missing target"}),
                            400, mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"error": "invalid session"}),
                            401, mimetype="application/json")

        # 目标玩家可能不存在 -> 返回初始装扮, 不 500
        target_user = User.query.filter_by(id=target).first()
        if not target_user:
            target_user = User.query.filter_by(id=target.split(":")[0]).first()
        outfit = None
        if target_user:
            outfit = UserOutfit.query.filter_by(user_id=target_user.id).first()
        if not outfit:
            if target_user:
                outfit = _default_outfit(target_user.id)
                db.session.commit()
            else:
                # 目标不存在: 返回一份初始装扮(不写库, 不 500)
                o = UserOutfit(
                    user_id=target[:36] or "000000000000000000000000",
                    wing_tex=0, wing_pat=0, wing_mask=0, wing_id=2245279351,
                    voice=9, seed=16515, scale=0.22549559, refreshversion=0,
                    prop_tex=0, prop_pat=0, prop_mask=0, prop_id=2035109393,
                    neck_tex=0, neck_pat=0, neck_mask=0, neck_id=3800884691,
                    mask_tex=0, mask_pat=0, mask_mask=0, mask_id=3663390882,
                    horn_tex=0, horn_pat=0, horn_mask=0, horn_id=3680499229,
                    height=1.93536222,
                    hat_tex=0, hat_pat=0, hat_mask=0, hat_id=1470462579,
                    hair_tex=0, hair_pat=0, hair_mask=0, hair_id=1229053584,
                    feet_tex=0, feet_pat=0, feet_mask=0, feet_id=1532093990,
                    face_tex=0, face_pat=0, face_mask=0, face_id=4267756678,
                    body_tex=0, body_pat=0, body_mask=0, body_id=3691343236,
                    attitude="0", arms_id=0,
                )
                return Response(json.dumps({
                    "result": "ok",
                    "get_remote_outfit": _serialize(o),
                    "serendipity_types": [],
                }, ensure_ascii=False), 200, mimetype="application/json")

        body = {
            "result": "ok",
            "get_remote_outfit": _serialize(outfit),
            "serendipity_types": [],
        }
        # 该接口按"目标玩家"返回其真实装扮。曾经被 static_responses 的静态库
        # 短路成同一套固定装扮(所有人看到的一样), 已由 _dynamic_exclude.json 改回动态。
        logger.info("get_remote_outfit viewer=%s target=%s source=%s",
                    user_id, target, "db" if outfit else "default")
        return Response(json.dumps(body, ensure_ascii=False), 200,
                        mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), 500,
                        mimetype="application/json")
