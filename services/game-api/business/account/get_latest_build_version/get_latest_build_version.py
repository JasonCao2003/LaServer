# -*- coding: utf-8 -*-
"""
接口: /account/get_latest_build_version
函数: get_latest_build_version()
获取最新构建版本
"""
from flask import request, Response
from infrastructure.database.models import db, Version
import json
import re

def get_latest_build_version():
    # 从客户端 User-Agent 提取 build version，确保服务端返回值 >= 客户端版本
    # User-Agent 格式: Sky-Live-com.tgc.sky.android/0.12.2.160278 (...)
    client_build = 0
    ua = request.headers.get("User-Agent", "")
    m = re.search(r'/[\d.]*\.(\d+)', ua)
    if m:
        try:
            client_build = int(m.group(1))
        except ValueError:
            pass

    latest_version = Version.query.order_by(Version.id.desc()).first()
    if not latest_version:
        latest_version = Version(version=client_build)
        db.session.add(latest_version)
        db.session.commit()

    # 确保 latest_build_version >= 客户端版本，否则游戏报连接错误
    version_to_return = max(latest_version.version, client_build)

    # 更新数据库中的版本（如果客户端更新了）
    if client_build > latest_version.version:
        latest_version.version = client_build
        db.session.commit()

    response_body = {
        "latest_build_version": version_to_return,
        "min_gpu_rating": 540,
        "maintenance_mode": False,
        "maintenance_msg": "",
        "app_store_uri": "",
        "tos": {
            "version": 4,
            "tos_url": "",
            "pp_url": ""
        },
        "cs_enabled": True,
        "cs_db_enabled": False,
        "country_code": "CN"
    }

    return Response(
        response=json.dumps(response_body, sort_keys=False),
        status=200,
        mimetype='application/json'
    )
