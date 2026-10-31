from flask import request, Response
from database.models import db, User, UserWingBuff
import json

def get_wing_buffs():
    try:
        data = request.get_json(force=True) or {}
        user_id = data.get("user") or data.get("user_id")
        session = data.get("session")
        if not user_id or not session:
            return Response(json.dumps({"error": "invalid request"}), 400, mimetype="application/json")
        user = User.query.filter_by(id=user_id, session=session).first()
        if not user:
            return Response(json.dumps({"error": "invalid session"}), 401, mimetype="application/json")

        # 新建账号全解锁: 开启后, 开启时间点之后新建的账号拉取数据时直接返回完整解锁
        auto_full = False
        try:
            from helpers.full_unlock_helper import should_auto_full_unlock, full_wing_buffs
            auto_full = should_auto_full_unlock(user.created_at)
        except Exception:
            auto_full = False

        merged = {}
        if auto_full:
            # 直接发送准备好的全光翼 JSON(config/all_wing_buffs.json)
            for b in full_wing_buffs():
                nm = b.get("name")
                if nm:
                    merged[nm] = {
                        "name": nm,
                        "collected": b.get("collected", True),
                        "deposited": b.get("deposited", False),
                        "last_conversion": b.get("last_conversion", 0),
                        "deposit_id": b.get("deposit_id", 0),
                    }
        for wb in UserWingBuff.query.filter_by(user_id=user_id).all():
            merged[wb.name] = {
                "name": wb.name,
                "collected": wb.collected,
                "deposited": wb.deposited,
                "last_conversion": wb.last_conversion,
                "deposit_id": wb.deposit_id,
            }
        result = list(merged.values())
        return Response(json.dumps({"result": "ok", "wing_buffs": result}, ensure_ascii=False), 200, mimetype="application/json")
    except Exception as e:
        return Response(json.dumps({"error": str(e)}), 500, mimetype="application/json")
