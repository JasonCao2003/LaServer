from flask import jsonify

def set_device_token():
    # 原代码直接返回空的JSON对象和200状态码
    return jsonify({}), 200