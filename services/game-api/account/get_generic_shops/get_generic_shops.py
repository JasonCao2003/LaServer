# -*- coding: utf-8 -*-
"""
接口: /account/get_generic_shops
函数: generic_shop_defs()
获取通用商店定义
"""
from flask import jsonify

def generic_shop_defs():
    # 完全不处理任何业务逻辑，直接返回200
    return jsonify({"generic_shop_defs": []}), 200
