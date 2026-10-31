# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/free_gifts/send
"""

from flask import request, jsonify, Response
from database.models import db, User
import json
import uuid
import time

def send_free_gift_options():
    """
    处理CORS预检请求
    """
    response = Response()
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'POST,OPTIONS')
    response.headers.add('Access-Control-Max-Age', '3600')
    return response

