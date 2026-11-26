"""Optional GUI startup; safe to import without Tkinter or a desktop."""
import logging
import os
import sys
import threading


logger = logging.getLogger(__name__)


def start_monitor_gui(request_queue=None, *, port=None):
    """Start the desktop monitor if available; return its thread or None."""
    # Linux Tk uses X11 (including XWayland), so WAYLAND_DISPLAY alone is
    # insufficient. Windows and macOS do not require DISPLAY.
    if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
        logger.info('未检测到 DISPLAY，跳过请求监控窗口；API 请求监控不受影响')
        return None

    if request_queue is None:
        from infrastructure.monitoring.request_monitor_bus import request_log_queue
        request_queue = request_log_queue

    def _run():
        try:
            import tkinter as tk
        except ImportError as exc:
            logger.warning('Tkinter 不可用，跳过请求监控窗口: %s', exc)
            return

        from infrastructure.monitoring.request_monitor_gui import RequestMonitorApp
        try:
            app = RequestMonitorApp(request_queue, port=port)
        except tk.TclError as exc:
            logger.warning('无法初始化 Tk 窗口，跳过请求监控窗口: %s', exc)
            return
        app.run()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
