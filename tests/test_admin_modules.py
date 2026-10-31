"""Admin module regression checks; all database writes use disposable data."""
import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "admin"))
sys.path.insert(0, str(ROOT / "services" / "game-api"))
from api_response_manager import ResponseStore, ApiResponseManager
from game_control_panel import build_game_control_tab


class AdminModulesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        database = self.root / "var" / "data" / "instance" / "users.db"
        database.parent.mkdir(parents=True)
        sqlite3.connect(database).close()
        self.store = ResponseStore(self.root)

    def test_server_reads_scoped_overrides_and_fallback(self):
        spec = importlib.util.spec_from_file_location(
            "server_override", ROOT / "services/game-api/business/helpers/response_override.py")
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        with patch.object(backend, "_db_path", return_value=str(self.store.path)):
            self.store.save("", "/example", '{"scope":"global"}', True)
            self.store.save("alice", "/example", '{"scope":"alice"}', True)
            self.assertEqual(backend.get_override("alice", "/example"), '{"scope":"alice"}')
            self.assertEqual(backend.get_override("bob", "/example"), '{"scope":"global"}')
            self.store.save("alice", "/example", "disabled", False)
            self.assertEqual(backend.get_override("alice", "/example"), '{"scope":"global"}')
            self.store.delete("alice", "/example")
            self.assertEqual(self.store.list_entries("alice"), [])
            self.assertEqual(len(self.store.list_entries("")), 1)

    def test_recent_response_and_upsert(self):
        with closing(sqlite3.connect(self.store.path)) as conn, conn:
            conn.execute("CREATE TABLE request_log(user_id TEXT,path TEXT,body_resp TEXT)")
            conn.execute("INSERT INTO request_log VALUES('alice','/example','original')")
            conn.execute("INSERT INTO request_log VALUES('bob','/private','private')")
        self.assertEqual(self.store.list_entries("alice")[0]["source"], "最近响应")
        self.store.save("alice", "/example", "first", True)
        self.store.save("alice", "/example", "second", False)
        entries = self.store.list_entries("alice")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["body"], "second")
        self.store.delete("alice", "/example")
        self.assertEqual(self.store.list_entries("alice")[0]["body"], "original")

    def test_invalid_paths_and_missing_database(self):
        for path in ("", "https://example.org/a", "//host/a", "/a?b=1", "/a\n"):
            with self.assertRaises(ValueError):
                self.store.save("alice", path, "{}", True)
        with self.assertRaises(sqlite3.OperationalError):
            ResponseStore(self.root / "missing").list_entries("")

    def test_tk_widgets_load_and_dirty_changes_are_guarded(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        notebook = ttk.Notebook(root)
        parent = ttk.Frame(notebook)
        notebook.add(parent, text="游戏控制")
        app = SimpleNamespace(nb=notebook, selected_uid=None, root_dir=str(self.root))
        build_game_control_tab(app, parent)
        window = ApiResponseManager(root, self.root, "alice")
        window.withdraw()
        window.path_var.set("/example")
        window.body.insert("1.0", "{}")
        with patch("api_response_manager.messagebox.askyesno", return_value=False):
            window.new()
            self.assertEqual(window.path_var.get(), "/example")
        window.save()
        self.assertEqual(self.store.list_entries("alice")[0]["body"], "{}")
        window.destroy()


if __name__ == "__main__":
    unittest.main()
