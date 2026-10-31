# -*- coding: utf-8 -*-
"""
接口: /account/star/get
函数: get_star_tags()
获取星标标签定义和用户链接
"""
from flask import request, Response
from database.models import db, User, StarTagDef, StarTagLink
import json

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_star_tags():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session = request_data.get('session')
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        tag_defs = StarTagDef.query.all()
        
        tag_def_list = []
        for tag_def in tag_defs:
            # 解析batches字段（JSON格式）
            try:
                batches = json.loads(tag_def.batches) if tag_def.batches else []
            except:
                batches = []
            
            tag_def_list.append({
                "name": tag_def.name,
                "sku": tag_def.sku,
                "part": tag_def.part,
                "consumable_name": tag_def.consumable_name,
                "group_name": tag_def.group_name,
                "batches": batches,
                "can_link_with": tag_def.can_link_with
            })
        
        user_tag_links = StarTagLink.query.filter_by(user_id=user_id).all()
        
        tag_link_list = []
        for link in user_tag_links:
            tag_link_list.append({
                "tag_name": link.tag_name,
                "linked_item": link.linked_item,
                "linked_at": link.linked_at
            })
        
        response_body = {
            "get_star_tag_defs": tag_def_list,
            "star_tag_links": tag_link_list
        }
        
        return Response(
            response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
            status=200,
            mimetype='application/json'
        )
        
    except Exception as e:
        return Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype='application/json'
        )
