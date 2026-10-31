"""Edit the response_override table consumed by helpers.response_override."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import json
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText


class ResponseStore:
    def __init__(self, root_dir):
        self.path = Path(root_dir).resolve() / "var" / "data" / "instance" / "users.db"

    def connect(self):
        # mode=rw refuses to silently create a database at the wrong location.
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def list_entries(self, uid):
        with closing(self.connect()) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            entries = {}
            if "request_log" in tables:
                for row in conn.execute("SELECT path, body_resp FROM request_log WHERE user_id=? ORDER BY path", (uid,)):
                    entries[row["path"]] = dict(path=row["path"], body=row["body_resp"] or "",
                                                enabled=False, source="最近响应")
            if "response_override" in tables:
                for row in conn.execute("SELECT path, body, enabled FROM response_override WHERE user_id=? ORDER BY path", (uid,)):
                    entries[row["path"]] = dict(row, source="覆盖")
            return [entries[path] for path in sorted(entries)]

    def save(self, uid, path, body, enabled):
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in "?#\r\n"):
            raise ValueError("接口路径必须以 / 开头，且不含域名、查询参数或换行。")
        with closing(self.connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS response_override (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT '',
                path TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT, UNIQUE(user_id, path))""")
            conn.execute("""INSERT INTO response_override(user_id,path,body,enabled,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(user_id,path) DO UPDATE SET
                body=excluded.body, enabled=excluded.enabled, updated_at=excluded.updated_at""",
                         (uid, path, body, int(enabled), datetime.now().isoformat(timespec="seconds")))

    def delete(self, uid, path):
        with closing(self.connect()) as conn, conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='response_override' AND type='table'").fetchone():
                conn.execute("DELETE FROM response_override WHERE user_id=? AND path=?", (uid, path))


class ApiResponseManager(tk.Toplevel):
    def __init__(self, master, root_dir, uid=""):
        super().__init__(master)
        self.store = ResponseStore(root_dir)
        self.uid = str(uid or "")
        self.title("接口响应管理 — " + (self.uid or "全局"))
        self.geometry("960x640")
        self.minsize(720, 480)
        self.path_var = tk.StringVar(self)
        self.enabled = tk.BooleanVar(self, value=True)
        self.status = tk.StringVar(self)
        self._snapshot = None
        self._selected = None
        self.entries = []
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="作用范围：" + ("账号 " + self.uid if self.uid else "所有账号（全局覆盖）")).pack(anchor="w")
        ttk.Label(frame, text="账号专属覆盖优先于全局覆盖。保存后由服务端读取；禁用或删除专属覆盖后仍可能命中全局覆盖。",
                  wraplength=900).pack(anchor="w", pady=(4, 10))
        panes = ttk.Panedwindow(frame, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=3)
        self.tree = ttk.Treeview(left, columns=("state",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="接口路径")
        self.tree.heading("state", text="状态")
        self.tree.column("#0", width=220)
        self.tree.column("state", width=85)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        ttk.Label(right, text="接口路径（精确匹配）").pack(anchor="w")
        ttk.Entry(right, textvariable=self.path_var).pack(fill="x", pady=6)
        ttk.Checkbutton(right, text="启用覆盖", variable=self.enabled).pack(anchor="w")
        ttk.Label(right, text="响应体（JSON 或原始文本）").pack(anchor="w", pady=(8, 4))
        self.body = ScrolledText(right, wrap="none", undo=True)
        self.body.pack(fill="both", expand=True)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(10, 0))
        for label, command in [("刷新", self.refresh), ("新增", self.new),
                               ("导入文本", self.import_body), ("格式化 JSON", self.format_json),
                               ("保存覆盖", self.save), ("删除覆盖", self.delete)]:
            ttk.Button(bar, text=label, command=command).pack(side="left", padx=(0, 6))
        ttk.Label(frame, textvariable=self.status, wraplength=900).pack(anchor="w", pady=(8, 0))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def editor_state(self):
        return (self.path_var.get(), self.body.get("1.0", "end-1c"), self.enabled.get())

    def discard(self):
        return self._snapshot is None or self.editor_state() == self._snapshot or messagebox.askyesno(
            "未保存的修改", "放弃未保存的修改？", parent=self)

    def load(self, entry=None):
        self.path_var.set(entry["path"] if entry else "")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", entry["body"] if entry else "")
        self.enabled.set(bool(entry["enabled"]) if entry and entry["source"] == "覆盖" else True)
        self.body.edit_reset()
        self._snapshot = self.editor_state()

    def refresh(self):
        if not self.discard():
            return
        try:
            self.entries = self.store.list_entries(self.uid)
            self.tree.delete(*self.tree.get_children())
            self._selected = None
            for index, entry in enumerate(self.entries):
                state = ("启用" if entry["enabled"] else "禁用") if entry["source"] == "覆盖" else "最近响应"
                self.tree.insert("", "end", iid=str(index), text=entry["path"], values=(state,))
            self.load()
            self.status.set(f"已加载 {len(self.entries)} 个接口。最近响应可能被服务端截断，保存前请检查。")
        except Exception as exc:
            self.status.set(f"读取失败：{exc}")

    def _select(self, event=None):
        selected = self.tree.selection()
        if not selected or selected[0] == self._selected:
            return
        if not self.discard():
            if self._selected is not None:
                self.tree.selection_set(self._selected)
            else:
                self.tree.selection_remove(*selected)
            return
        self._selected = selected[0]
        self.load(self.entries[int(self._selected)])

    def new(self):
        if self.discard():
            self.tree.selection_remove(*self.tree.selection())
            self._selected = None
            self.load()

    def import_body(self):
        filename = filedialog.askopenfilename(parent=self, filetypes=[("文本 / JSON", "*.json *.txt"), ("所有文件", "*.*")])
        if filename:
            try:
                content = Path(filename).read_text(encoding="utf-8-sig")
                if self.discard():
                    self.body.delete("1.0", "end")
                    self.body.insert("1.0", content)
            except Exception as exc:
                messagebox.showerror("导入失败", str(exc), parent=self)

    def format_json(self):
        try:
            content = json.dumps(json.loads(self.body.get("1.0", "end-1c")), ensure_ascii=False, indent=2)
            self.body.delete("1.0", "end")
            self.body.insert("1.0", content)
        except ValueError as exc:
            messagebox.showerror("JSON 格式错误", str(exc), parent=self)

    def save(self):
        path, body, enabled = self.editor_state()
        if not self.uid and not messagebox.askyesno("全局覆盖", "此次保存会影响所有未设置专属覆盖的账号，继续？", parent=self):
            return
        try:
            self.store.save(self.uid, path, body, enabled)
            self._snapshot = self.editor_state()
            self.refresh()
            self.status.set(f"已保存 {path}，状态：{'启用' if enabled else '禁用'}。")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)

    def delete(self):
        path = self.path_var.get()
        if not path or not messagebox.askyesno("删除覆盖", f"删除 {path} 的覆盖？最近响应记录会保留。", parent=self):
            return
        try:
            self.store.delete(self.uid, path)
            self._snapshot = self.editor_state()
            self.refresh()
            self.status.set(f"已删除 {path} 的覆盖。")
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)

    def close(self):
        if self.discard():
            self.destroy()
