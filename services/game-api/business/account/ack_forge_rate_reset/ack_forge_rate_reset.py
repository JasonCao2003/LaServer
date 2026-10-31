from flask import request, Response, jsonify
from infrastructure.database.models import db, User, ServerAssignment, GameServer
import json
import time

def ack_forge_rate_reset():
    """处理熔铸率重置确认接口"""
    response = {
        "result": "ok",
        "update_forge_rates": [
            {
                "source_type": "wax",
                "current_tier": 26,
                "current_level": 4,
                "tier_completion": 0.0,
                "level_completion": 0.0,
                "level_tier_index": 0,
                "level_tier_count": 5,
                "reset_acknowledged": True,
                "enabled": True
            }
        ]
    }
    return jsonify(response), 200