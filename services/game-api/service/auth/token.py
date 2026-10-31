# -*- coding: utf-8 -*-
"""
Service接口: /service/auth/api/v1/token
"""

from flask import request, jsonify, Response, send_from_directory
from database.models import db, User
import json
import uuid
import time
import os

def get_auth_token():
    response_data = {
        "host": "sky.resources.cdn.thatgamecompany.com",
        "prefix": "",
        "status": "OK",
        "token": "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJlbnYiOiJsaXZlIiwiaWF0IjoxNzU2NTY2NTIwLCJpcCI6IjExNy4xMzYuNTQuMTc4OjI1NDc0LCAxMTcuMTM2LjU0LjE3OCwgMTAuMTAyLjguMTg2IiwicHVycG9zZSI6WyJyZXNvdXJjZXMiXSwic3ViIjoiMTJjNTE2YTUtYjA3OS00MWQ3LWI0ZDQtNzk5NTFiZTBhYjc5In0.tLG-QJrTCRX3R0Ek09fD-BV04xucHn_FKA8qP6xz6nUpahBTAJT5WQmvl0xgPvtqjYkjwOpmo66yeMTcElsaPQ"
    }
    
    return jsonify(response_data), 200
