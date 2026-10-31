from paths import CONFIG_DIR, SERVICE_ROOT
from flask import send_from_directory
import os

def get_quest_defs():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = SERVICE_ROOT
    config_dir = CONFIG_DIR
    
    return send_from_directory(
        directory=config_dir,
        path='quest.json',
        mimetype='application/json'
    )