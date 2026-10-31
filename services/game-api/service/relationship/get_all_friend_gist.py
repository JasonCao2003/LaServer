# -*- coding: utf-8 -*-
"""
Service接口: /service/relationship/api/v1/get_all_friend_gist
"""

from flask import request, jsonify, Response, send_from_directory
from database.models import db, User
import json
import uuid
import time
import os

def get_all_friend_gist():
# 完全不处理任何业务逻辑，直接返回200
    return jsonify({"get_all_friend_gist": []}), 200
