from flask import send_from_directory
import os

def iap_list():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    config_dir = os.path.join(project_root, 'config')
    
    return send_from_directory(
        directory=config_dir,
        path='iaplist.json',
        mimetype='application/json'
    )