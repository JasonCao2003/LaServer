"""Canonical filesystem locations; importing this module does not create files."""
import os

SERVICE_ROOT = os.path.dirname(os.path.abspath(__file__))
BUSINESS_DIR = os.path.join(SERVICE_ROOT, 'business')
INFRASTRUCTURE_DIR = os.path.join(SERVICE_ROOT, 'infrastructure')
CONFIG_DIR = os.path.join(SERVICE_ROOT, 'config')
RESOURCES_DIR = os.path.join(SERVICE_ROOT, 'resources')
STATIC_RESPONSES_DIR = os.path.join(RESOURCES_DIR, 'static_responses')
VAR_DIR = os.path.join(SERVICE_ROOT, 'var')
DATA_DIR = os.path.join(VAR_DIR, 'data')
INSTANCE_DIR = os.path.join(DATA_DIR, 'instance')
DATABASE_PATH = os.path.join(INSTANCE_DIR, 'users.db')
LEGACY_DATABASE_PATH = os.path.join(DATA_DIR, 'legacy', 'users.db')
LOGS_DIR = os.path.join(VAR_DIR, 'logs')
CERTS_DIR = os.path.join(VAR_DIR, 'certs')
SERVER_CONFIG_PATH = os.path.join(CONFIG_DIR, 'server.json')
STATE_DIR = os.path.join(DATA_DIR, 'runtime')
