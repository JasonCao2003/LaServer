# -*- coding: utf-8 -*-
"""
Sky 私服 - 请求监控面板 (tkinter GUI)
实时显示所有 HTTP 请求/响应，支持 JSON / HEX / Raw 多种查看模式。

使用方式：
    在 index.py 中导入并启动：
        from request_monitor_gui import start_monitor_gui
        start_monitor_gui()
    或者独立运行本文件（需要服务端通过 socket 推送数据）。
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from paths import DATA_DIR, SERVICE_ROOT, STATE_DIR

import os
import sys
import json
import time
import queue
import threading
import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox
from datetime import datetime

# ============================================================
# ============================================================
MAX_LIST_ITEMS = 100    # 内存/界面只留最近 100 条(其余已落盘)
POLL_INTERVAL_MS = 100  # 轮询队列间隔

# 接口缓存(磁盘转储)目录与"保存"标记文件 (任务3)
# 默认: 接口记录只在内存, 关闭服务器时连同缓存一起删除;
# 点击"保存接口缓存"后写入标记文件, 此后持续落盘, 关闭时保留缓存。
# 缓存落盘到 monitor_exports/cache/ (与"保存到文件"导出同一父目录, 互不干扰)。
_EXPORT_DIR = os.path.join(DATA_DIR, "monitor_exports")
_DUMP_DIR = os.path.join(_EXPORT_DIR, "cache")
_KEEP_MARKER = os.path.join(_EXPORT_DIR, ".keep_cache")

BG_DARK = "#1e1e1e"
BG_PANEL = "#252526"
BG_LIST = "#1e1e1e"
FG_DEFAULT = "#d4d4d4"
FG_DIM = "#808080"
FG_PATH = "#dcdcaa"
FG_METHOD_GET = "#4ec9b0"
FG_METHOD_POST = "#569cd6"
FG_METHOD_PUT = "#ce9178"
FG_METHOD_DELETE = "#f44747"
FG_STATUS_OK = "#6a9955"
FG_STATUS_ERR = "#f44747"
FG_STATUS_WARN = "#d7ba7d"
HIGHLIGHT_BG = "#094771"
SEPARATOR_COLOR = "#3c3c3c"


def method_color(method):
    return {
        'GET': FG_METHOD_GET,
        'POST': FG_METHOD_POST,
        'PUT': FG_METHOD_PUT,
        'DELETE': FG_METHOD_DELETE,
    }.get(method.upper(), FG_DEFAULT)


def status_color(code):
    if 200 <= code < 300:
        return FG_STATUS_OK
    elif 300 <= code < 400:
        return FG_STATUS_WARN
    else:
        return FG_STATUS_ERR


# ============================================================
# ============================================================
class RequestMonitorApp:
    def __init__(self, request_queue=None):
        """
        request_queue: queue.Queue 实例，从中间件获取记录。
        如果为 None，则创建内部队列（独立模式）。
        """
        self.queue = request_queue or queue.Queue()
        self.records = []  # 所有捕获的记录
        self.filtered_records = []  # 筛选后的记录
        self.selected_index = -1
        self.filter_text = ""
        self.filter_method = "ALL"
        self.auto_scroll = True

        # ---- 采集线程 / 看门狗 (2026-10-05: 用户要求"持续监控一直到私服关闭") ----
        # records 现在由采集线程(_collector_loop)追加、界面线程读取, 用锁保护;
        # 界面线程只看 _pending_added/_pending_trimmed 两个计数器做增量刷新, 不再自己读队列。
        self._records_lock = threading.Lock()
        self._pending_added = 0
        self._pending_trimmed = 0
        self._last_poll_at = time.time()      # 轮询链最后一次跑的时间(看门狗用)
        self._last_collect_at = time.time()   # 采集线程最后一次活动时间
        self._collector = None

        self.root = tk.Tk()
        # 多开支持: 标题带端口区分(SKY_PORT 由管理面板传入), 避免多个监控窗口同名,
        _port = os.environ.get("SKY_PORT", "")
        _suffix = (" :%s" % _port) if _port else ""
        self.root.title("Sky 私服 - 请求监控面板" + _suffix)
        self.root.geometry("1400x800")
        self.root.configure(bg=BG_DARK)
        self.root.minsize(900, 500)

        # 关闭按钮(X)改为"隐藏窗口"而非销毁: 这样关闭后所有已捕获的接口数据
        # 仍保留在内存里, 管理面板的"接口请求响应查看"按钮(FindWindow+ShowWindow)
        # 能再次唤起, 并显示上次关闭前的全部数据。彻底退出随服务器进程结束。
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)

        self._build_ui()
        # 先起采集线程(持续收记录), 再起界面轮询
        self._start_collector()
        self._start_watchdog()
        self._poll_queue()

    def _hide_window(self):
        """关闭窗口时只隐藏, 保留数据, 便于面板按钮再次唤起。"""
        try:
            self.root.withdraw()
        except Exception:
            pass


    def _build_ui(self):
        self.mono_font = tkfont.Font(family="Consolas", size=10)
        self.list_font = tkfont.Font(family="Consolas", size=9)
        self.title_font = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")

        toolbar = tk.Frame(self.root, bg=BG_PANEL, height=36)
        toolbar.pack(fill=tk.X, side=tk.TOP)
        toolbar.pack_propagate(False)

        tk.Label(toolbar, text=" 🔍 筛选:", bg=BG_PANEL, fg=FG_DEFAULT,
                 font=self.title_font).pack(side=tk.LEFT, padx=(8, 4))

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", self._on_filter_change)
        filter_entry = tk.Entry(toolbar, textvariable=self.filter_var,
                                bg=BG_DARK, fg=FG_DEFAULT, insertbackground=FG_DEFAULT,
                                font=self.mono_font, width=30, relief=tk.FLAT,
                                highlightthickness=1, highlightcolor="#007acc")
        filter_entry.pack(side=tk.LEFT, padx=4, pady=4)

        tk.Label(toolbar, text=" 方法:", bg=BG_PANEL, fg=FG_DEFAULT,
                 font=self.title_font).pack(side=tk.LEFT, padx=(12, 4))

        self.method_var = tk.StringVar(value="ALL")
        method_combo = ttk.Combobox(toolbar, textvariable=self.method_var,
                                    values=["ALL", "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
                                    width=8, state="readonly")
        method_combo.pack(side=tk.LEFT, padx=4, pady=4)
        method_combo.bind("<<ComboboxSelected>>", self._on_filter_change)

        self.autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text="自动滚动", variable=self.autoscroll_var,
                       bg=BG_PANEL, fg=FG_DEFAULT, selectcolor=BG_DARK,
                       activebackground=BG_PANEL, activeforeground=FG_DEFAULT,
                       font=self.list_font).pack(side=tk.LEFT, padx=12)

        tk.Button(toolbar, text="🗑 清除全部", command=self._clear_all,
                  bg="#3c3c3c", fg=FG_DEFAULT, relief=tk.FLAT,
                  font=self.list_font, padx=8).pack(side=tk.RIGHT, padx=8, pady=4)

        # 保存接口缓存按钮 (任务3): 默认关服时删除磁盘缓存, 点此保存则保留
        self.save_cache_btn = tk.Button(
            toolbar, text="💾 保存接口缓存", command=self._toggle_save_cache,
            bg="#3c3c3c", fg=FG_DEFAULT, relief=tk.FLAT,
            font=self.list_font, padx=8)
        self.save_cache_btn.pack(side=tk.RIGHT, padx=4, pady=4)
        self._refresh_save_cache_btn()

        # 保存到文件按钮: 把当前所有捕获记录导出到 源码下独立新建文件夹内的新文件,
        # 文件名为当前时间(精确到秒)。每次点击新建一个文件, 互不覆盖。
        tk.Button(
            toolbar, text="📄 保存到文件", command=self._save_to_file,
            bg="#3c3c3c", fg=FG_DEFAULT, relief=tk.FLAT,
            font=self.list_font, padx=8).pack(side=tk.RIGHT, padx=4, pady=4)

        self.count_label = tk.Label(toolbar, text="0 条请求", bg=BG_PANEL,
                                    fg=FG_DIM, font=self.list_font)
        self.count_label.pack(side=tk.RIGHT, padx=8)

        # 主体区域 PanedWindow
        paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL,
                               bg=SEPARATOR_COLOR, sashwidth=3)
        paned.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(paned, bg=BG_DARK)
        paned.add(left_frame, minsize=350, width=500)

        header = tk.Frame(left_frame, bg=BG_PANEL, height=24)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="  时间        方法    状态  耗时    路径",
                 bg=BG_PANEL, fg=FG_DIM, font=self.list_font,
                 anchor=tk.W).pack(fill=tk.X, padx=4)

        # 列表 (用 Canvas + Frame 自绘实现彩色)
        list_container = tk.Frame(left_frame, bg=BG_DARK)
        list_container.pack(fill=tk.BOTH, expand=True)

        self.list_canvas = tk.Canvas(list_container, bg=BG_LIST,
                                     highlightthickness=0)
        self.list_scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL,
                                            command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=self.list_scrollbar.set)

        self.list_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.list_inner = tk.Frame(self.list_canvas, bg=BG_LIST)
        self.list_canvas_window = self.list_canvas.create_window(
            (0, 0), window=self.list_inner, anchor=tk.NW)

        self.list_inner.bind("<Configure>",
                             lambda e: self.list_canvas.configure(
                                 scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", self._on_canvas_resize)
        self.list_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        right_frame = tk.Frame(paned, bg=BG_DARK)
        paned.add(right_frame, minsize=400)

        detail_toolbar = tk.Frame(right_frame, bg=BG_PANEL, height=36)
        detail_toolbar.pack(fill=tk.X)
        detail_toolbar.pack_propagate(False)

        tk.Label(detail_toolbar, text=" 📋 详情", bg=BG_PANEL, fg=FG_DEFAULT,
                 font=self.title_font).pack(side=tk.LEFT, padx=8)

        self.view_mode = tk.StringVar(value="smart")
        modes = [("智能", "smart"), ("JSON", "json"), ("HEX", "hex"), ("Raw", "raw")]
        for label, val in modes:
            tk.Radiobutton(detail_toolbar, text=label, variable=self.view_mode,
                           value=val, command=self._refresh_detail,
                           bg=BG_PANEL, fg=FG_DEFAULT, selectcolor=BG_DARK,
                           activebackground=BG_PANEL, activeforeground=FG_DEFAULT,
                           font=self.list_font, indicatoron=False,
                           padx=10, pady=2, relief=tk.FLAT,
                           offrelief=tk.FLAT, overrelief=tk.RAISED
                           ).pack(side=tk.LEFT, padx=2, pady=4)

        detail_paned = tk.PanedWindow(right_frame, orient=tk.VERTICAL,
                                      bg=SEPARATOR_COLOR, sashwidth=3)
        detail_paned.pack(fill=tk.BOTH, expand=True)

        req_frame = tk.Frame(detail_paned, bg=BG_DARK)
        detail_paned.add(req_frame, minsize=100)

        req_header = tk.Frame(req_frame, bg="#2d2d30", height=22)
        req_header.pack(fill=tk.X)
        req_header.pack_propagate(False)
        tk.Label(req_header, text="  ▶ 请求 (Request)", bg="#2d2d30",
                 fg="#569cd6", font=self.list_font).pack(side=tk.LEFT)

        self.req_text = tk.Text(req_frame, bg=BG_DARK, fg=FG_DEFAULT,
                                font=self.mono_font, wrap=tk.NONE,
                                relief=tk.FLAT, highlightthickness=0,
                                insertbackground=FG_DEFAULT)
        req_scroll_y = ttk.Scrollbar(req_frame, command=self.req_text.yview)
        req_scroll_x = ttk.Scrollbar(req_frame, orient=tk.HORIZONTAL,
                                      command=self.req_text.xview)
        self.req_text.configure(yscrollcommand=req_scroll_y.set,
                                xscrollcommand=req_scroll_x.set)
        req_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        req_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.req_text.pack(fill=tk.BOTH, expand=True)

        resp_frame = tk.Frame(detail_paned, bg=BG_DARK)
        detail_paned.add(resp_frame, minsize=100)

        resp_header = tk.Frame(resp_frame, bg="#2d2d30", height=22)
        resp_header.pack(fill=tk.X)
        resp_header.pack_propagate(False)
        tk.Label(resp_header, text="  ◀ 响应 (Response)", bg="#2d2d30",
                 fg="#6a9955", font=self.list_font).pack(side=tk.LEFT)

        self.resp_text = tk.Text(resp_frame, bg=BG_DARK, fg=FG_DEFAULT,
                                 font=self.mono_font, wrap=tk.NONE,
                                 relief=tk.FLAT, highlightthickness=0,
                                 insertbackground=FG_DEFAULT)
        resp_scroll_y = ttk.Scrollbar(resp_frame, command=self.resp_text.yview)
        resp_scroll_x = ttk.Scrollbar(resp_frame, orient=tk.HORIZONTAL,
                                       command=self.resp_text.xview)
        self.resp_text.configure(yscrollcommand=resp_scroll_y.set,
                                 xscrollcommand=resp_scroll_x.set)
        resp_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        resp_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.resp_text.pack(fill=tk.BOTH, expand=True)

        # Text 标签颜色
        for t in (self.req_text, self.resp_text):
            t.tag_configure("header_key", foreground="#569cd6")
            t.tag_configure("header_val", foreground="#ce9178")
            t.tag_configure("json_key", foreground="#9cdcfe")
            t.tag_configure("json_str", foreground="#ce9178")
            t.tag_configure("json_num", foreground="#b5cea8")
            t.tag_configure("json_bool", foreground="#569cd6")
            t.tag_configure("hex_offset", foreground="#808080")
            t.tag_configure("hex_byte", foreground="#d4d4d4")
            t.tag_configure("hex_ascii", foreground="#6a9955")
            t.tag_configure("info", foreground="#569cd6")
            t.tag_configure("dim", foreground="#808080")

        for t in (self.req_text, self.resp_text):
            ctx = tk.Menu(t, tearoff=0)
            ctx.add_command(label="复制", command=lambda w=t: self._copy_text(w))
            ctx.add_command(label="全选", command=lambda w=t: self._select_all(w))
            ctx.add_command(label="复制全部", command=lambda w=t: self._copy_all(w))
            t.bind("<Button-3>", lambda e, m=ctx: m.tk_popup(e.x_root, e.y_root))
            t.bind("<Control-c>", lambda e, w=t: self._copy_text(w))
            t.bind("<Control-a>", lambda e, w=t: (self._select_all(w), "break")[1])

        # 列表行 widgets
        self.row_widgets = []

    # ============================================================
    # ============================================================
    def _copy_text(self, widget):
        """复制选中文本，无选中则复制全部"""
        try:
            sel = widget.get("sel.first", "sel.last")
        except tk.TclError:
            sel = widget.get("1.0", tk.END).strip()
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append(sel)

    def _select_all(self, widget):
        """全选"""
        widget.tag_add("sel", "1.0", "end")

    def _copy_all(self, widget):
        """复制全部内容"""
        text = widget.get("1.0", tk.END).strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    # ============================================================
    # ============================================================
    def _on_canvas_resize(self, event):
        self.list_canvas.itemconfig(self.list_canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        self.list_canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _add_row_widget(self, idx):
        """在列表中添加一行"""
        rec = self.filtered_records[idx]
        ts = datetime.fromtimestamp(rec['timestamp']).strftime("%H:%M:%S")
        method = rec['method']
        status = rec['status_code']
        elapsed = rec['elapsed_ms']
        path = rec['path']

        row = tk.Frame(self.list_inner, bg=BG_LIST, cursor="hand2")
        row.pack(fill=tk.X, padx=2, pady=0)

        tk.Label(row, text=ts, bg=BG_LIST, fg=FG_DIM,
                 font=self.list_font, width=9, anchor=tk.W
                 ).pack(side=tk.LEFT)

        tk.Label(row, text=f"{method:<7}", bg=BG_LIST,
                 fg=method_color(method), font=self.list_font,
                 width=7, anchor=tk.W).pack(side=tk.LEFT)

        tk.Label(row, text=f"{status}", bg=BG_LIST,
                 fg=status_color(status), font=self.list_font,
                 width=5, anchor=tk.W).pack(side=tk.LEFT)

        elapsed_str = f"{elapsed:.0f}ms" if elapsed < 1000 else f"{elapsed/1000:.1f}s"
        tk.Label(row, text=f"{elapsed_str:<7}", bg=BG_LIST,
                 fg=FG_DIM, font=self.list_font,
                 width=7, anchor=tk.W).pack(side=tk.LEFT)

        tk.Label(row, text=path, bg=BG_LIST, fg=FG_PATH,
                 font=self.list_font, anchor=tk.W
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row._row_index = idx
        for child in row.winfo_children():
            child._row_index = idx
            child.bind("<Button-1>", self._on_row_click)
        row.bind("<Button-1>", self._on_row_click)

        self.row_widgets.append(row)

    def _on_row_click(self, event):
        widget = event.widget
        idx = getattr(widget, '_row_index', -1)
        if idx < 0:
            return

        if 0 <= self.selected_index < len(self.row_widgets):
            old_row = self.row_widgets[self.selected_index]
            old_row.configure(bg=BG_LIST)
            for child in old_row.winfo_children():
                child.configure(bg=BG_LIST)

        self.selected_index = idx
        row = self.row_widgets[idx]
        row.configure(bg=HIGHLIGHT_BG)
        for child in row.winfo_children():
            child.configure(bg=HIGHLIGHT_BG)

        self._refresh_detail()

    # ============================================================
    # ============================================================
    def _refresh_detail(self, *_):
        if self.selected_index < 0 or self.selected_index >= len(self.filtered_records):
            return
        rec = self.filtered_records[self.selected_index]
        mode = self.view_mode.get()

        self.req_text.configure(state=tk.NORMAL)
        self.req_text.delete("1.0", tk.END)
        self._render_request(rec, mode)
        self.req_text.configure(state=tk.DISABLED)

        self.resp_text.configure(state=tk.NORMAL)
        self.resp_text.delete("1.0", tk.END)
        self._render_response(rec, mode)
        self.resp_text.configure(state=tk.DISABLED)

    def _render_request(self, rec, mode):
        t = self.req_text
        t.insert(tk.END, f"{rec['method']} ", "info")
        t.insert(tk.END, f"{rec['full_url']}\n\n", "header_val")

        t.insert(tk.END, "── Headers ──\n", "dim")
        for k, v in rec.get('headers_req', {}).items():
            t.insert(tk.END, f"{k}: ", "header_key")
            t.insert(tk.END, f"{v}\n", "header_val")

        body = rec.get('body_req', b'')
        if body:
            t.insert(tk.END, "\n── Body ──\n", "dim")
            self._render_body(t, body, mode)

    def _render_response(self, rec, mode):
        t = self.resp_text
        status = rec['status_code']
        t.insert(tk.END, f"HTTP {status} ", "info")
        t.insert(tk.END, f"({rec['elapsed_ms']}ms)\n\n")

        t.insert(tk.END, "── Headers ──\n", "dim")
        for k, v in rec.get('headers_resp', {}).items():
            t.insert(tk.END, f"{k}: ", "header_key")
            t.insert(tk.END, f"{v}\n", "header_val")

        body = rec.get('body_resp', b'')
        if body:
            t.insert(tk.END, "\n── Body ──\n", "dim")
            self._render_body(t, body, mode)

    def _render_body(self, text_widget, body_bytes, mode):
        """根据模式渲染 body"""
        if isinstance(body_bytes, str):
            body_bytes = body_bytes.encode('utf-8', errors='replace')

        if mode == "hex":
            self._render_hex(text_widget, body_bytes)
        elif mode == "json":
            self._render_json(text_widget, body_bytes)
        elif mode == "raw":
            try:
                raw = body_bytes.decode('utf-8', errors='replace')
            except Exception:
                raw = repr(body_bytes)
            text_widget.insert(tk.END, raw)
        else:  # smart
            # 尝试 JSON，失败则显示 raw/hex
            try:
                text_str = body_bytes.decode('utf-8')
                obj = json.loads(text_str)
                self._render_json(text_widget, body_bytes)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # 如果全是可打印字符则 raw，否则 hex
                try:
                    decoded = body_bytes.decode('utf-8')
                    if all(c.isprintable() or c in '\n\r\t' for c in decoded):
                        text_widget.insert(tk.END, decoded)
                    else:
                        self._render_hex(text_widget, body_bytes)
                except UnicodeDecodeError:
                    self._render_hex(text_widget, body_bytes)

    def _render_json(self, text_widget, body_bytes):
        """渲染 JSON 并着色"""
        try:
            text_str = body_bytes.decode('utf-8')
            obj = json.loads(text_str)
            formatted = json.dumps(obj, indent=2, ensure_ascii=False)
            for line in formatted.split('\n'):
                stripped = line.lstrip()
                if stripped.startswith('"') and '": ' in stripped:
                    # key: value
                    colon_pos = line.index('": ')
                    key_part = line[:colon_pos + 1]
                    val_part = line[colon_pos + 1:]
                    text_widget.insert(tk.END, key_part, "json_key")
                    val_stripped = val_part.strip().rstrip(',')
                    if val_stripped.startswith('"'):
                        text_widget.insert(tk.END, val_part + "\n", "json_str")
                    elif val_stripped in ('true', 'false', 'null'):
                        text_widget.insert(tk.END, val_part + "\n", "json_bool")
                    elif val_stripped.replace('.', '').replace('-', '').isdigit():
                        text_widget.insert(tk.END, val_part + "\n", "json_num")
                    else:
                        text_widget.insert(tk.END, val_part + "\n")
                else:
                    text_widget.insert(tk.END, line + "\n")
        except Exception:
            text_widget.insert(tk.END, body_bytes.decode('utf-8', errors='replace'))

    def _render_hex(self, text_widget, body_bytes):
        """渲染 hex dump (16字节一行)"""
        for i in range(0, len(body_bytes), 16):
            chunk = body_bytes[i:i+16]
            text_widget.insert(tk.END, f"{i:08X}  ", "hex_offset")
            # hex 字节
            hex_part = ' '.join(f"{b:02X}" for b in chunk)
            hex_part = hex_part.ljust(47)
            text_widget.insert(tk.END, hex_part + "  ", "hex_byte")
            # ASCII
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            text_widget.insert(tk.END, ascii_part + "\n", "hex_ascii")

    # ============================================================
    # ============================================================
    def _on_filter_change(self, *_):
        self.filter_text = self.filter_var.get().strip().lower()
        self.filter_method = self.method_var.get()
        self._rebuild_list()

    def _apply_filter(self):
        """对所有记录应用筛选条件"""
        result = []
        for rec in self.records:
            if self.filter_method != "ALL" and rec['method'] != self.filter_method:
                continue
            if self.filter_text:
                searchable = (rec['path'] + rec.get('query_string', '') +
                              rec.get('remote_addr', '')).lower()
                if self.filter_text not in searchable:
                    continue
            result.append(rec)
        return result

    def _rebuild_list(self):
        """完全重建列表"""
        for w in self.row_widgets:
            w.destroy()
        self.row_widgets.clear()
        self.selected_index = -1

        self.filtered_records = self._apply_filter()
        for i in range(len(self.filtered_records)):
            self._add_row_widget(i)

        self.count_label.configure(
            text=f"{len(self.filtered_records)}/{len(self.records)} 条请求")

    # ============================================================
    # ============================================================
    def _poll_queue(self):
        """轮询入口(见 _poll_queue_once 的实现)。**任何异常都不许打断轮询链。**

        2026-10-01 玩家反馈"接口监控只显示 200 条, 新请求不显示": 老实现把
        `self.root.after(...)` 排在 _poll_queue 的最后一行, 中间
        任何一步(建行/筛选/读显示标记)抛异常, tkinter 只会打印错误、**不会再排下一次**,
        面板就永远停在最后那批记录上。现在把"排下一次"放进 finally, 保证不断链。
        """
        try:
            self._poll_queue_once()
        except Exception as e:
            try:
                print("[监控面板] 刷新异常(已忽略, 轮询继续):", e)
            except Exception:
                pass
        finally:
            self._schedule_poll()

    # ============================================================
    # 持续采集 / 看门狗 —— "一直监控到私服关闭"
    # ============================================================
    def _take_pending(self):
        """取走并清零"新增/裁剪"计数(采集线程写, 界面线程读)。"""
        with self._records_lock:
            added, trimmed = self._pending_added, self._pending_trimmed
            self._pending_added = 0
            self._pending_trimmed = 0
        return added, trimmed

    def _resync_queue(self):
        """总队列万一被换掉(例如热更新把中间件模块重载了, 老代码就会换一个新队列),
        就把自己切到新的那个 —— 切之前先把旧队列里剩下的记录取干净, 一条都不丢。

        正常情况下 request_monitor_bus.request_log_queue 永远是同一个对象(那是
        2026-10-05 为"热更新之后监控停摆"专门做的总线模块), 这个函数只是兜底。
        """
        try:
            bus = sys.modules.get("infrastructure.monitoring.request_monitor_bus")
            if bus is None:
                import infrastructure.monitoring.request_monitor_bus as bus  # noqa: WPS433 - 运行时兜底
            new_q = getattr(bus, "request_log_queue", None)
            if new_q is None or new_q is self.queue:
                return
        except Exception:
            return
        leftovers = []
        try:
            while True:
                leftovers.append(self.queue.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            pass
        self.queue = new_q
        for rec in leftovers:
            with self._records_lock:
                self.records.append(rec)
                self._pending_added += 1
        try:
            print("[监控面板] 总队列已更新, 采集线程跟着切换(补取 %d 条)" % len(leftovers))
        except Exception:
            pass

    def _collector_loop(self):
        """**持续采集**: 阻塞式从总队列取记录, 只做"入列表 + 记计数", 不碰任何 tkinter 控件
        —— 就算界面卡住、窗口被藏起来、刷新链断了, 记录也照样一条不漏地收进来。

        这是个死循环, 唯一的退出方式是服务器进程结束(线程是 daemon, 随进程消失),
        符合用户要求的"持续监控一直到私服关闭"。
        """
        while True:
            try:
                self._resync_queue()
                try:
                    rec = self.queue.get(timeout=0.5)
                except queue.Empty:
                    self._last_collect_at = time.time()
                    continue
                with self._records_lock:
                    self.records.append(rec)
                    self._pending_added += 1
                    while len(self.records) > MAX_LIST_ITEMS:
                        del self.records[0]
                        self._pending_trimmed += 1
                self._last_collect_at = time.time()
            except Exception as e:  # noqa: BLE001 - 采集线程绝不允许退出
                try:
                    print("[监控面板] 采集线程异常(已忽略, 继续采集):", e)
                except Exception:
                    pass
                time.sleep(0.5)

    def _start_collector(self):
        if self._collector is not None and self._collector.is_alive():
            return
        self._collector = threading.Thread(target=self._collector_loop, daemon=True,
                                           name="req-monitor-collector")
        self._collector.start()

    def _watchdog_loop(self):
        """看门狗(每 2 秒): 采集线程死了就重新拉起; 界面刷新链停了就重新点火。
        两者都断过也不丢数据 —— 采集线程一直在收, 只是暂时没显示。"""
        while True:
            time.sleep(2)
            try:
                if self._collector is None or not self._collector.is_alive():
                    print("[监控面板] 采集线程不在了, 重新拉起")
                    self._start_collector()
            except Exception:
                pass
            try:
                stall = time.time() - self._last_poll_at
                now = time.time()
                if stall > 5 and now - getattr(self, "_last_stall_warn", 0.0) > 30:
                    # 只告警, **不再**从看门狗线程调 root.after: 那同样是跨线程
                    # Tk 调用, 每次泄漏 1 个句柄 + 约 15KB 内存。轮询链由
                    # _poll_queue 的 finally 保证, 真断了只能重启服务器。
                    self._last_stall_warn = now
                    print("[监控面板] 界面刷新停了 %.1fs(仅告警)" % stall)
            except Exception:
                pass

    def _start_watchdog(self):
        threading.Thread(target=self._watchdog_loop, daemon=True,
                         name="req-monitor-watchdog").start()

    def _schedule_poll(self):
        """把下一次轮询排到 Tk 主线程。

        本函数只会在 Tk 线程里被调用(_poll_queue 本身就是 after 回调), 所以
        root.after 是安全的。**绝不要**再像 2026-10-05 那样"新开线程 sleep 后
        再调 root.after": Tkinter 的跨线程调用每次都泄漏 1 个 Windows 句柄 +
        约 15KB 内存(本机 Python 3.13 + Tk 8.6 实测, 随调用次数线性增长),
        跑几天就足以把私服进程撑到 MemoryError。
        """
        try:
            self.root.after(POLL_INTERVAL_MS, self._poll_queue)
        except Exception:
            pass

    def _poll_queue_once(self):
        """把采集线程攒下的"新增/裁剪"量刷新到界面上。

        **不要在这里读队列**(队列由 _collector_loop 持续读, 读队列不再是界面的职责,
        界面卡住也不影响采集), 也**不要在这里排下一次轮询**(见 _poll_queue)。
        """
        self._last_poll_at = time.time()   # 看门狗靠它判断"刷新链还活着吗"
        added, trimmed = self._take_pending()

        if added > 0:
            has_filter = bool(self.filter_text) or self.filter_method != "ALL"
            if trimmed > 0 and has_filter:
                # 有筛选时, 裁剪会打乱索引映射, 直接整表重建最稳妥
                self._rebuild_list()
            else:
                # 无筛选: 先从顶部移除被裁掉的旧行并修正剩余行索引,
                # 再把新记录追加到底部 (增量更新, 避免整表重建)
                if trimmed > 0:
                    for w in self.row_widgets[:trimmed]:
                        w.destroy()
                    del self.row_widgets[:trimmed]
                    del self.filtered_records[:trimmed]
                    for new_idx, w in enumerate(self.row_widgets):
                        w._row_index = new_idx
                        for child in w.winfo_children():
                            child._row_index = new_idx
                    if self.selected_index >= 0:
                        self.selected_index -= trimmed
                        if self.selected_index < 0:
                            self.selected_index = -1

                new_filtered = self._apply_filter()
                new_count = len(new_filtered) - len(self.filtered_records)
                if new_count > 0:
                    self.filtered_records = new_filtered
                    start = len(self.filtered_records) - new_count
                    for i in range(start, len(self.filtered_records)):
                        self._add_row_widget(i)

                    if self.autoscroll_var.get():
                        self.list_canvas.update_idletasks()
                        self.list_canvas.yview_moveto(1.0)

            self.count_label.configure(
                text=f"{len(self.filtered_records)}/{len(self.records)} 条请求")

        # 检查"显示"标记: 面板按钮请求唤起窗口时写此标记, 这里检测到就重新显示
        self._check_show_marker()

    def _check_show_marker(self):
        """面板的'接口请求响应查看'按钮会写一个 show 标记文件; 检测到则把(被隐藏的)
        窗口重新显示并置顶, 然后删除标记。由 GUI 自身 deiconify, tkinter 状态一致。"""
        try:
            marker = os.path.join(STATE_DIR, ".show_monitor")
            if os.path.exists(marker):
                os.remove(marker)
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", True)
                self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass


    # ============================================================
    # 保存接口缓存 (任务3)
    # ============================================================
    def _cache_saved(self):
        return os.path.exists(_KEEP_MARKER)

    def _refresh_save_cache_btn(self):
        if not hasattr(self, "save_cache_btn"):
            return
        if self._cache_saved():
            self.save_cache_btn.configure(
                text="✅ 已保存(关服保留)", bg="#2d6a2d", fg="#d4ffd4")
        else:
            self.save_cache_btn.configure(
                text="💾 保存接口缓存", bg="#3c3c3c", fg=FG_DEFAULT)

    def _toggle_save_cache(self):
        """切换接口缓存保存状态。
        已保存 -> 取消(恢复为只留内存、关服时删除磁盘缓存);
        未保存 -> 写入标记(此后持续落盘, 并把当前内存里的记录立即补写到磁盘, 关服保留)。
        """
        try:
            if self._cache_saved():
                os.remove(_KEEP_MARKER)
            else:
                os.makedirs(_EXPORT_DIR, exist_ok=True)
                with open(_KEEP_MARKER, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
                # 开启保存的瞬间: 把之前只在内存里的历史记录补写到磁盘,
                # 之后的新请求由中间件 _dump_record 持续追加。
                try:
                    from infrastructure.monitoring.request_monitor_middleware import flush_history_to_disk
                    flush_history_to_disk()
                except Exception as e:
                    print(f"[接口缓存] 历史补写失败: {e}")
        except Exception as e:
            print(f"[接口缓存] 切换保存状态失败: {e}")
        self._refresh_save_cache_btn()

    # ============================================================
    # 保存到文件: 导出当前所有捕获记录到 源码下独立文件夹内、以时间命名的新文件
    # ============================================================
    @staticmethod
    def _jsonable(v):
        """把记录里的 bytes 等不可直接 JSON 序列化的值转成字符串。"""
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8")
            except Exception:
                return v.decode("utf-8", "replace")
        return v

    def _record_to_dict(self, rec):
        out = {}
        for k, v in rec.items():
            if isinstance(v, dict):
                out[k] = {kk: self._jsonable(vv) for kk, vv in v.items()}
            else:
                out[k] = self._jsonable(v)
        return out

    def _save_to_file(self):
        """把当前所有捕获记录写到 monitor_exports/ 下以当前时间命名的新文件。"""
        with self._records_lock:
            records = list(self.records)
        if not records:
            messagebox.showinfo("保存到文件", "当前没有可保存的接口记录。",
                                parent=self.root)
            return
        try:
            os.makedirs(_EXPORT_DIR, exist_ok=True)
            # 文件名为当前时间(精确到秒); 同秒重复点击则追加毫秒避免覆盖
            fname = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".json"
            fpath = os.path.join(_EXPORT_DIR, fname)
            if os.path.exists(fpath):
                fname = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f") + ".json"
                fpath = os.path.join(_EXPORT_DIR, fname)
            data = {
                "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(records),
                "records": [self._record_to_dict(r) for r in records],
            }
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self.root)
            return
        messagebox.showinfo("保存到文件",
                            f"已保存 {len(records)} 条记录到:\n{fpath}",
                            parent=self.root)

    # ============================================================
    # ============================================================
    def _clear_all(self):
        with self._records_lock:          # 采集线程可能正在往里加, 加锁清
            self.records.clear()
            self._pending_added = 0
            self._pending_trimmed = 0
        self.filtered_records.clear()
        for w in self.row_widgets:
            w.destroy()
        self.row_widgets.clear()
        self.selected_index = -1
        self.count_label.configure(text="0 条请求")
        self.req_text.configure(state=tk.NORMAL)
        self.req_text.delete("1.0", tk.END)
        self.req_text.configure(state=tk.DISABLED)
        self.resp_text.configure(state=tk.NORMAL)
        self.resp_text.delete("1.0", tk.END)
        self.resp_text.configure(state=tk.DISABLED)

    # ============================================================
    # ============================================================
    def run(self):
        self.root.mainloop()


# ============================================================
# 供 index.py 直接调用的启动函数
# ============================================================
def start_monitor_gui(request_queue=None):
    """在新线程中启动监控面板 (不阻塞服务器)"""
    if request_queue is None:
        # 走"总线"模块取队列: 热更新会 reload request_monitor_middleware, 但不会动
        # request_monitor_bus, 所以面板与中间件永远是同一个队列对象(见 bus 模块头注释)。
        try:
            from infrastructure.monitoring.request_monitor_bus import request_log_queue
            request_queue = request_log_queue
        except Exception:
            from infrastructure.monitoring.request_monitor_middleware import request_log_queue
            request_queue = request_log_queue

    def _run():
        app = RequestMonitorApp(request_queue)
        app.run()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ============================================================
# ============================================================
if __name__ == "__main__":
    sys.path.insert(0, SERVICE_ROOT)
    try:
        from infrastructure.monitoring.request_monitor_middleware import request_log_queue
        print("已连接中间件队列，等待请求...")
        app = RequestMonitorApp(request_log_queue)
        app.run()
    except ImportError:
        print("独立测试模式 - 生成模拟数据")
        test_queue = queue.Queue()
        for i in range(5):
            test_queue.put({
                'timestamp': time.time() - (5 - i),
                'method': ['GET', 'POST', 'PUT'][i % 3],
                'path': f'/account/test_endpoint_{i}',
                'full_url': f'https://localhost:8443/account/test_endpoint_{i}',
                'query_string': f'user_id=test_{i}',
                'headers_req': {'Content-Type': 'application/json', 'User-Agent': 'Sky/1.0'},
                'body_req': json.dumps({"test": True, "index": i}).encode(),
                'status_code': [200, 200, 404, 200, 500][i],
                'headers_resp': {'Content-Type': 'application/json'},
                'body_resp': json.dumps({"code": 200, "msg": "ok", "data": {"id": i}}).encode(),
                'elapsed_ms': [12.5, 45.2, 3.1, 120.8, 8.7][i],
                'remote_addr': '192.168.1.100',
            })
        app = RequestMonitorApp(test_queue)
        app.run()
