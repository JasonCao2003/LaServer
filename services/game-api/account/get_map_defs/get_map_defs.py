from flask import send_from_directory
import os

def get_map_defs():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    config_dir = os.path.join(project_root, 'config')
    
    return send_from_directory(
        directory=config_dir,
        path='map.json',
        mimetype='application/json'
    )