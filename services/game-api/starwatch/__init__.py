# -*- coding: utf-8 -*-
"""
Starwatch接口模块
所有starwatch相关的接口都在这里导入和注册
"""

from .auth.auth import auth


def register_starwatch_routes(app):
    """
    注册所有starwatch路由到Flask应用
    
    参数:
        app: Flask应用实例
    """
    app.add_url_rule('/starwatch/auth/api/v1/auth', 'starwatch_auth', auth, methods=['POST'])


__all__ = ['auth', 'register_starwatch_routes']
