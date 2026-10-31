from flask import jsonify

def get_questionnaire_groups():
    # 完全不处理任何业务逻辑，直接返回200
    return jsonify({"get_questionnaire_groups": []}), 200