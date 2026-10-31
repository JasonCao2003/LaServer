from flask import jsonify

def get_trust_tier_defs():
    # 完全不处理任何业务逻辑，直接返回200
    return jsonify({"get_trust_tier_defs": []}), 200