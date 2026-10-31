from paths import CONFIG_DIR, SERVICE_ROOT
# -*- coding: utf-8 -*-
# mailbox_helper.py - shared mailbox logic
import os, json, time, re

# 版本门槛: 高版本邮件系统。默认门槛 0.16.0 —— 邮件可在 mailbox.json 中用
# "min_version"/"max_version" (形如 "0.16.0") 限定可见版本; 未标注的邮件对所有
# 版本可见。客户端版本从 User-Agent 解析 (Sky-xxx/0.26.1.xxx)。
_VER_RE = re.compile(r"Sky-[^/\s]+/(\d+(?:\.\d+)*)")
DEFAULT_GATE = (0, 16, 0)


def parse_version(ua):
    """从 UA 解析版本三元组, 失败返回 None。"""
    if not ua:
        return None
    m = _VER_RE.search(ua)
    if not m:
        return None
    nums = []
    for p in m.group(1).split(".")[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _parse_ver_str(s):
    try:
        nums = [int(x) for x in str(s).split(".")[:3]]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums)
    except Exception:
        return None


def _visible_for(mail, ver):
    """根据邮件的 min_version/max_version 与客户端版本判断是否可见。"""
    if ver is None:
        # 无法解析版本时, 仅显示无版本限制的邮件 (保守)
        return not mail.get("min_version") and not mail.get("max_version")
    mn = _parse_ver_str(mail.get("min_version")) if mail.get("min_version") else None
    mx = _parse_ver_str(mail.get("max_version")) if mail.get("max_version") else None
    if mn is not None and ver < mn:
        return False
    if mx is not None and ver > mx:
        return False
    return True


def _filter_mails(mails, ua):
    ver = parse_version(ua)
    return [m for m in mails if _visible_for(m, ver)]


def _config_path():
    root = SERVICE_ROOT
    return os.path.join(CONFIG_DIR, "mailbox.json")

def load_mailbox():
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"from_id": "44e9f7e2-c9fb-4328-87d4-3544221856ce", "mails": []}

def _header(mail, uid, now):
    days = mail.get("expires_days", 30)
    has_att = bool(mail.get("attachments"))
    h = {
        "id": mail.get("id", ""),
        "type": mail.get("type", "misc"),
        "from": load_mailbox().get("from_id", "44e9f7e2-c9fb-4328-87d4-3544221856ce"),
        "to": uid,
        "sent_at": now,
        "received_at": now,
        "expires_at": now + days * 86400,
    }
    if has_att:
        h["has_attachments"] = True
    return h

def build_list(uid, max_count=32, ua=None):
    """v2/get_all : 邮件列表(按版本过滤; 含 headers+subject+properties)"""
    mb = load_mailbox()
    now = int(time.time())
    msgs = []
    mails = _filter_mails(mb.get("mails", []), ua)
    for mail in mails[:max_count]:
        att = mail.get("attachments", [])
        msgs.append({
            "payload": {
                "headers": _header(mail, uid, now),
                "subject": mail.get("subject", ""),
                "properties": {
                    "HasCurrencies": any(a.get("type") == "currency" for a in att),
                    "HasLinks": False,
                    "HasUnlocks": any(a.get("type") == "unlock" for a in att),
                }
            }
        })
    return {"status": "OK", "messages": msgs}

def build_detail(uid, ids, ua=None):
    """v1/get : 指定邮件的详情(按版本过滤; 含 body+attachments)"""
    mb = load_mailbox()
    now = int(time.time())
    visible = _filter_mails(mb.get("mails", []), ua)
    by_id = {m.get("id"): m for m in visible}
    update_messages = []
    for item in ids:
        mid = item.get("id") if isinstance(item, dict) else item
        mail = by_id.get(mid)
        if not mail:
            continue
        att = mail.get("attachments", [])
        payload = {
            "headers": _header(mail, uid, now),
            "subject": mail.get("subject", ""),
            "body": mail.get("body", ""),
            "properties": {
                "HasCurrencies": any(a.get("type") == "currency" for a in att),
                "HasLinks": False,
                "HasUnlocks": any(a.get("type") == "unlock" for a in att),
            }
        }
        if att:
            payload["attachments"] = att
        update_messages.append({"payload": payload})
    return {"status": "OK", "update_messages": update_messages}
