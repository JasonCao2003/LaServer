"""Game controls backed by the admin panel's existing account operations."""
import tkinter as tk
from tkinter import ttk, messagebox

from api_response_manager import ApiResponseManager


def build_game_control_tab(app, parent):
    frame = ttk.Frame(parent, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="游戏控制", font=("微软雅黑", 14)).pack(anchor="w")
    ttk.Label(frame, text="先在账号管理中选择账号，再切换到此页。操作使用同一主数据库。",
              wraplength=800).pack(anchor="w", pady=(8, 16))
    target = tk.StringVar(parent, value="未选择账号")
    ttk.Label(frame, textvariable=target).pack(anchor="w", pady=(0, 12))

    def user_id():
        uid = getattr(app, "selected_uid", None)
        if not uid:
            messagebox.showwarning("选择账号", "请先在账号管理中选择一个账号。", parent=parent)
            return None
        try:
            if not app.db.get_user(uid):
                raise ValueError("所选账号不存在，请刷新账号列表。")
        except Exception as exc:
            messagebox.showerror("读取账号失败", str(exc), parent=parent)
            return None
        return uid

    def invoke(method):
        if user_id():
            getattr(app, method)()

    def unlock(scope, label):
        uid = user_id()
        if not uid or not messagebox.askyesno(
                label, f"给账号 {uid} {label}？\n已有内容会跳过，玩家重新登录或同步后生效。",
                parent=parent):
            return
        try:
            result = app.db.push_full_unlocks(uid, scope=scope)
            errors = result.get("errors", [])
            summary = "，".join(f"{key}: {result.get(key, 0)}" for key in
                                ("emotes", "maps", "unlocks", "wings"))
            app.log(f"{label} {uid}: {summary}", "warn" if errors else "success")
            if errors:
                messagebox.showwarning("部分完成", summary + "\n" + "\n".join(errors), parent=parent)
            else:
                messagebox.showinfo("操作完成", summary, parent=parent)
        except Exception as exc:
            messagebox.showerror("操作失败", str(exc), parent=parent)

    actions = ttk.LabelFrame(frame, text="账号解锁", padding=12)
    actions.pack(fill="x", pady=8)
    for column, (label, scope) in enumerate([
            ("解锁全部动作", "emotes"), ("解锁全部地图", "maps"),
            ("解锁全部能力", "unlocks"), ("解锁全部光翼", "wings")]):
        ttk.Button(actions, text=label, command=lambda s=scope, t=label: unlock(s, t)).grid(
            row=0, column=column, padx=6, pady=8, sticky="ew")
        actions.columnconfigure(column, weight=1)
    ttk.Button(actions, text="推送全解锁", command=lambda: invoke("_push_full_unlocks")).grid(
        row=1, column=0, columnspan=2, padx=6, pady=8, sticky="ew")
    ttk.Button(actions, text="解锁全部服饰道具", command=lambda: invoke("_push_all_items")).grid(
        row=1, column=2, columnspan=2, padx=6, pady=8, sticky="ew")
    data = ttk.LabelFrame(frame, text="数据与响应", padding=12)
    data.pack(fill="x", pady=8)
    ttk.Button(data, text="账号数据编辑（货币、背包等）",
               command=lambda: invoke("_show_data_browser")).pack(side="left", padx=6, pady=8)

    def open_responses():
        uid = user_id()
        if uid:
            ApiResponseManager(app, app.root_dir, uid)

    ttk.Button(data, text="当前账号接口响应", command=open_responses).pack(side="left", padx=6)

    def refresh_target(event=None):
        uid = getattr(app, "selected_uid", None)
        target.set("当前账号：" + (str(uid) if uid else "未选择"))

    app.nb.bind("<<NotebookTabChanged>>", refresh_target, add="+")
    refresh_target()
