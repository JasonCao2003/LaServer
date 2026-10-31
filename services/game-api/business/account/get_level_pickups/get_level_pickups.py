from flask import request, Response
from infrastructure.database.models import db, User, LevelPickup
import json
import os
import time

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_level_pickups():
    try:
        request_data = request.get_json(force=True)
        
        if not request_data or 'user' not in request_data or 'session' not in request_data or 'level' not in request_data:
            return Response(
                response=json.dumps({"error": "Invalid request body"}),
                status=400,
                mimetype='application/json'
            )
        
        user_id = request_data.get('user')
        session = request_data.get('session')
        level = request_data.get('level')
        
        user = get_cached_user(user_id, session)
        if not user:
            return Response(
                response=json.dumps({"error": "Invalid session or user not found"}),
                status=401,
                mimetype='application/json'
            )
        
        pickups = LevelPickup.query.filter_by(user_id=user_id, level=level).all()
        
        if not pickups:
            default_emitters = [
                {
                    "emitter_name": "CandlespaceBlizz",
                    "events": [
                        {
                            "preactive_duration": 0,
                            "pool_name": "social_blizzard",
                            "pool_amount": 1000,
                            "last_chunk_seq": -1,
                            "event_name": "social_blizz_sm",
                            "emit_interval": 6,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 1,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 1439
                        },
                        {
                            "preactive_duration": 0,
                            "pool_name": "social_blizzard",
                            "pool_amount": 1000,
                            "last_chunk_seq": -1,
                            "event_name": "social_blizz_lg",
                            "emit_interval": 4,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 1,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 1439
                        }
                    ]
                },
                {
                    "emitter_name": "CandlespaceSnowHutFire",
                    "events": [
                        {
                            "preactive_duration": 0,
                            "pool_name": "social_blizzard",
                            "pool_amount": 1000,
                            "last_chunk_seq": -1,
                            "event_name": "social_snowhutfire_sm",
                            "emit_interval": 6,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 1,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 1439
                        },
                        {
                            "preactive_duration": 0,
                            "pool_name": "social_blizzard",
                            "pool_amount": 1000,
                            "last_chunk_seq": -1,
                            "event_name": "social_snowhutfire_lg",
                            "emit_interval": 4,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 1,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 1439
                        }
                    ]
                },
                {
                    "emitter_name": "RainbowEvent",
                    "events": [
                        {
                            "preactive_duration": 0,
                            "pool_name": "event_rainbow",
                            "pool_amount": 500,
                            "last_chunk_seq": -1,
                            "event_name": "social_rainbow_on",
                            "emit_interval": 6,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 2,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 20160
                        }
                    ]
                },
                {
                    "emitter_name": "SakuraEmitterBlooming",
                    "events": [
                        {
                            "preactive_duration": 0,
                            "pool_name": "event_bloom_2022",
                            "pool_amount": 1000,
                            "last_chunk_seq": -1,
                            "event_name": "global_bloom_sakura_bloom_wax02",
                            "emit_interval": 12,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 1,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 4320
                        },
                        {
                            "preactive_duration": 0,
                            "pool_name": "event_bloom_2022",
                            "pool_amount": 1000,
                            "last_chunk_seq": -1,
                            "event_name": "global_bloom_sakura_bloom_wax01",
                            "emit_interval": 36,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 1,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 4320
                        }
                    ]
                },
                {
                    "emitter_name": "SakuraEmitterFalling",
                    "events": [
                        {
                            "preactive_duration": 0,
                            "pool_name": "event_bloom_2022",
                            "pool_amount": 1000,
                            "last_chunk_seq": -1,
                            "event_name": "global_bloom_sakura_fall_wax02",
                            "emit_interval": 6,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 1,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 8640
                        },
                        {
                            "preactive_duration": 0,
                            "pool_name": "event_bloom_2022",
                            "pool_amount": 1000,
                            "last_chunk_seq": -1,
                            "event_name": "global_bloom_sakura_fall_wax01",
                            "emit_interval": 9,
                            "currency_type": "wax",
                            "collected": 0,
                            "chunks_per_emit": 1,
                            "chunk_size_likelihood": [1.0, 0.0, 0.0],
                            "chunk_size_amount": [1, 0, 0],
                            "active_duration": 4320
                        }
                    ]
                }
            ]
            
            for emitter in default_emitters:
                for event in emitter["events"]:
                    pickup = LevelPickup(
                        user_id=user_id,
                        level=level,
                        emitter_name=emitter["emitter_name"],
                        event_name=event["event_name"],
                        preactive_duration=event["preactive_duration"],
                        pool_name=event["pool_name"],
                        pool_amount=event["pool_amount"],
                        last_chunk_seq=event["last_chunk_seq"],
                        emit_interval=event["emit_interval"],
                        currency_type=event["currency_type"],
                        collected=event["collected"],
                        chunks_per_emit=event["chunks_per_emit"],
                        chunk_size_likelihood=json.dumps(event["chunk_size_likelihood"]),
                        chunk_size_amount=json.dumps(event["chunk_size_amount"]),
                        active_duration=event["active_duration"]
                    )
                    db.session.add(pickup)
            
            db.session.commit()
            
            pickups = LevelPickup.query.filter_by(user_id=user_id, level=level).all()
        
        emitters_dict = {}
        for pickup in pickups:
            emitter_name = pickup.emitter_name
            if emitter_name not in emitters_dict:
                emitters_dict[emitter_name] = {
                    "emitter_name": emitter_name,
                    "events": []
                }
            
            # JSON
            chunk_size_likelihood = json.loads(pickup.chunk_size_likelihood) if pickup.chunk_size_likelihood else [1.0, 0.0, 0.0]
            chunk_size_amount = json.loads(pickup.chunk_size_amount) if pickup.chunk_size_amount else [1, 0, 0]
            
            emitters_dict[emitter_name]["events"].append({
                "preactive_duration": pickup.preactive_duration,
                "pool_name": pickup.pool_name,
                "pool_amount": pickup.pool_amount,
                "last_chunk_seq": pickup.last_chunk_seq,
                "event_name": pickup.event_name,
                "emit_interval": pickup.emit_interval,
                "currency_type": pickup.currency_type,
                "collected": pickup.collected,
                "chunks_per_emit": pickup.chunks_per_emit,
                "chunk_size_likelihood": chunk_size_likelihood,
                "chunk_size_amount": chunk_size_amount,
                "active_duration": pickup.active_duration
            })
        
        pickup_emitters = list(emitters_dict.values())
        
        response_body = {
            "level": level,
            "pickups": [],
            "global_pickups": [],
            "pickup_emitters": pickup_emitters
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
