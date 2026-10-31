# -*- coding: utf-8 -*-
"""
接口: /account/get_motd
函数: get_motd()
获取每日消息（Message of the Day）
"""
from paths import CONFIG_DIR, SERVICE_ROOT
from flask import Response
import json
import time
import os

def get_motd():
    try:
        current_dir = SERVICE_ROOT
        config_path = os.path.join(CONFIG_DIR, 'motd_config.json')
        
        print(f"尝试读取配置文件: {config_path}")
        
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            # 邮件时间: 默认实时(显示"刚刚"); 若设为 fixed 则用配置里的固定时间戳
            ts_mode = config_data.get('motd_timestamp_mode', 'now')
            if ts_mode != 'fixed' or not config_data.get('motd_timestamp'):
                config_data['motd_timestamp'] = int(time.time())
            config_data.pop('motd_timestamp_mode', None)

            # 版本兼容: 低版本(如 0.13.x)用 motd_season_start 渲染邮件时间/署名,
            # 高版本(如 0.19.x+)用 motd_season_end。同时下发两者(超集),
            if 'motd_season_start' not in config_data:
                # 没配 start 时, 用 timestamp 兜底(季节开始时间应早于当前)
                config_data['motd_season_start'] = int(config_data.get('motd_timestamp') or time.time())
            if 'motd_season_end' not in config_data:
                config_data['motd_season_end'] = int(config_data['motd_season_start']) + 30 * 86400

            print(f"成功读取配置文件，标题: {config_data.get('motd_title', 'N/A')}")

            return Response(
                response=json.dumps(config_data, sort_keys=False, ensure_ascii=False),
                status=200,
                mimetype='application/json'
            )
        else:
            print(f"配置文件不存在: {config_path}")
            
    except Exception as e:
        print(f"读取配置文件失败: {e}")
    
    current_timestamp = int(time.time())
    response_body = {  
        "motd": "\n光之所向，是最美的遇见\n请相信\n天空王国的温暖万物会\n如约而至\n下次，我们\n不见不散",   
        "motd_title": "欢迎来到光·遇的世界",
        "motd_timestamp": current_timestamp,
        "motd_version": 137688,
        "motd_buttons": [
            {
                "url": "http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=VVgZau4gHBEj9G7TZQl9Pps-nxSHcz0A&authKey=32QRrrHhG4WBAvmogZGKGWP9TxkDiwpDyT%2BOCqX9Hs%2Fz8XWB2QxEN1LOQ1W3%2BwZu&noverify=0&group_code=142395438",
                "img": "Icon_instagram"
            },
            {
                "url": "http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=VVgZau4gHBEj9G7TZQl9Pps-nxSHcz0A&authKey=32QRrrHhG4WBAvmogZGKGWP9TxkDiwpDyT%2BOCqX9Hs%2Fz8XWB2QxEN1LOQ1W3%2BwZu&noverify=0&group_code=142395438",
                "img": "Icon_facebook"
            },
            {
                "url": "http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=VVgZau4gHBEj9G7TZQl9Pps-nxSHcz0A&authKey=32QRrrHhG4WBAvmogZGKGWP9TxkDiwpDyT%2BOCqX9Hs%2Fz8XWB2QxEN1LOQ1W3%2BwZu&noverify=0&group_code=142395438",
                "img": "Icon_twitter"
            },
            {
                "url": "http://qm.qq.com/cgi-bin/qm/qr?_wv=1027&k=VVgZau4gHBEj9G7TZQl9Pps-nxSHcz0A&authKey=32QRrrHhG4WBAvmogZGKGWP9TxkDiwpDyT%2BOCqX9Hs%2Fz8XWB2QxEN1LOQ1W3%2BwZu&noverify=0&group_code=142395438",
                "img": "Icon_discord"
            }
        ],
        "motd_season_end": 2558736000
    }
    
    print(f"返回默认MOTD配置")
    
    return Response(
        response=json.dumps(response_body, sort_keys=False, ensure_ascii=False),
        status=200,
        mimetype='application/json'
    )
