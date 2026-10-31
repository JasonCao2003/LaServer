"""Exercise relocated imports, resources and monitoring with disposable state."""
import importlib
import json
import platform
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'services' / 'game-api'
sys.path.insert(0, str(SERVICE))


class GameApiLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Apply the same WMI bypass as the Windows bootstrap before importing dependencies.
        cls.stack = __import__('contextlib').ExitStack()
        cls.addClassCleanup(cls.stack.close)
        if sys.platform == 'win32':
            cls.stack.enter_context(patch.object(platform, 'machine', return_value='AMD64'))
            cls.stack.enter_context(patch.object(platform, 'processor', return_value='AMD64'))
            cls.stack.enter_context(patch.object(platform, 'win32_ver', return_value=('', '', '', '')))
        import paths
        cls.paths = paths
        directory = cls.stack.enter_context(tempfile.TemporaryDirectory())
        cls.data = Path(directory) / 'data'
        instance = cls.data / 'instance'
        instance.mkdir(parents=True)
        cls.stack.enter_context(patch.multiple(
            paths, DATA_DIR=str(cls.data), INSTANCE_DIR=str(instance),
            DATABASE_PATH=str(instance / 'users.db'),
            LEGACY_DATABASE_PATH=str(cls.data / 'legacy' / 'users.db'),
            STATE_DIR=str(cls.data / 'runtime'),
        ))
        from flask import Flask
        from business.account import register_account_routes
        from business.service import register_service_routes
        from business.starwatch import register_starwatch_routes
        cls.app = Flask(__name__)
        cls.app.testing = True
        register_account_routes(cls.app)
        register_service_routes(cls.app)
        register_starwatch_routes(cls.app)

    def test_config_response_after_relocation(self):
        response = self.app.test_client().post('/account/get_map_defs', json={})
        self.assertEqual(response.status_code, 200)
        expected = json.loads((SERVICE / 'config' / 'map.json').read_text(encoding='utf-8'))
        self.assertEqual(response.get_json(), expected)
        response.close()

    def test_resource_dispatch_separated_from_json(self):
        responses = importlib.import_module('infrastructure.responses')
        self.assertEqual(Path(responses._DIR), SERVICE / 'resources' / 'static_responses')
        self.assertTrue(Path(responses._MANIFEST).is_file())
        self.assertTrue(responses._load_manifest())

    def test_monitor_reload_preserves_queue(self):
        bus = importlib.import_module('infrastructure.monitoring.request_monitor_bus')
        middleware = importlib.import_module('infrastructure.monitoring.request_monitor_middleware')
        self.assertEqual(Path(middleware._EXPORT_DIR), self.data / 'monitor_exports')
        queue = bus.request_log_queue
        self.assertIs(middleware.request_log_queue, queue)
        self.assertIs(importlib.reload(middleware).request_log_queue, queue)

    def test_reload_package_names_and_websocket_identity(self):
        hot = importlib.import_module('infrastructure.runtime.hot_reload')
        self.assertEqual({name for name, _ in hot._ROUTE_PACKAGES}, {
            'business.account', 'business.service', 'business.starwatch', 'business.admin',
        })
        sender = importlib.import_module('business.account.send.send')
        websocket = importlib.import_module('infrastructure.transport.websocket_handler')
        self.assertIs(importlib.import_module(websocket.__name__), websocket)
        with patch.object(websocket, 'broadcast_message_to_all_clients') as broadcast:
            sender.broadcast_message_to_websocket_clients('layout-check')
            broadcast.assert_called_once_with('layout-check')


if __name__ == '__main__':
    unittest.main()
