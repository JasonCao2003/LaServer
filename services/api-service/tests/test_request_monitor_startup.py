import importlib
import os
from pathlib import Path
import queue
import sys
import threading
import types
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RequestMonitorStartupTests(unittest.TestCase):
    def setUp(self):
        self.startup = importlib.import_module(
            'infrastructure.monitoring.request_monitor_startup')

    def test_headless_linux_skips_without_importing_tk(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(sys, 'platform', 'linux'), \
                patch.dict(sys.modules, {'tkinter': None}), \
                self.assertLogs(self.startup.__name__, level='INFO'):
            self.assertIsNone(self.startup.start_monitor_gui(port=8444))

    def run_with_gui(self, tkinter_module, app_class, *, platform='linux',
                     environ=None):
        errors = []
        modules = {
            'tkinter': tkinter_module,
            'infrastructure.monitoring.request_monitor_gui':
                types.SimpleNamespace(RequestMonitorApp=app_class),
        }
        if environ is None:
            environ = {'DISPLAY': ':test'}
        with patch.dict(os.environ, environ, clear=True), \
                patch.object(sys, 'platform', platform), \
                patch.dict(sys.modules, modules), \
                patch.object(threading, 'excepthook', errors.append):
            thread = self.startup.start_monitor_gui(self.request_queue, port=8444)
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [], 'GUI startup leaked an exception')

    def test_missing_tkinter_is_nonfatal(self):
        self.request_queue = queue.Queue()
        with self.assertLogs(self.startup.__name__, level='WARNING'):
            self.run_with_gui(None, None)

    def test_unavailable_display_is_nonfatal(self):
        class TclError(Exception):
            pass

        class UnavailableApp:
            def __init__(self, request_queue, *, port):
                raise TclError('could not connect to display')

        self.request_queue = queue.Queue()
        with self.assertLogs(self.startup.__name__, level='WARNING'):
            self.run_with_gui(types.SimpleNamespace(TclError=TclError), UnavailableApp)

    def test_desktop_keeps_queue_and_actual_port(self):
        received = []

        class App:
            def __init__(self, request_queue, *, port):
                received.append((request_queue, port))

            def run(self):
                received.append('running')

        self.request_queue = queue.Queue()
        self.run_with_gui(types.SimpleNamespace(TclError=RuntimeError), App)
        self.assertEqual(received, [(self.request_queue, 8444), 'running'])

    def test_windows_without_display_uses_shared_queue(self):
        from infrastructure.monitoring.request_monitor_bus import request_log_queue
        received = []

        class App:
            def __init__(self, request_queue, *, port):
                received.append((request_queue, port))

            def run(self):
                received.append('running')

        self.request_queue = None
        self.run_with_gui(types.SimpleNamespace(TclError=RuntimeError), App,
                          platform='win32', environ={})
        self.assertEqual(received, [(request_log_queue, 8444), 'running'])


if __name__ == '__main__':
    unittest.main()
