# -*- coding: utf-8 -*-
"""任务留言/神龛留言(geonote)持久化。

客户端在神龛、任务点、共享空间写下的留言都走这里:
  /account/geonote/create_geonote2、create_bug_report_geonote、social_geonote2、
  delete_geonote、delete_bug_report_geonote、edit_message_geonote、
  get_user_geonotes、request_geonotes_for_n_friends、request_n_bug_report_geonotes

旧实现全部返回空数组, 客户端写完就消失。这里落一份 JSON 旁路文件
(instance/geonotes.json), 不动数据库表结构; 返回形状同时带新版
(geonote_id/created_at/pos_x...)与旧版(id/created_time/pos)键, 兼容各版本客户端。
"""
from paths import DATA_DIR, SERVICE_ROOT

import io
import json
import logging
import os
import random
import threading
import time

logger = logging.getLogger(__name__)

_ROOT = SERVICE_ROOT
NOTE_DIR = os.path.join(DATA_DIR, "instance")
NOTE_PATH = os.path.join(NOTE_DIR, "geonotes.json")

_lock = threading.Lock()

MAX_NOTES = 1000
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _gen_id():
    return "gn_%s_%s" % (
        format(int(time.time() * 1000), "x"),
        "".join(random.choice(_ALPHABET) for _ in range(6)))


def _load():
    try:
        if not os.path.isfile(NOTE_PATH):
            return []
        with io.open(NOTE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        notes = data.get("geonotes") if isinstance(data, dict) else data
        return notes if isinstance(notes, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("geonote 读取失败: %s", exc)
        return []


def _save(notes):
    try:
        os.makedirs(NOTE_DIR, exist_ok=True)
        tmp = NOTE_PATH + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump({"geonotes": notes[-MAX_NOTES:]}, f,
                      ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, NOTE_PATH)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("geonote 保存失败: %s", exc)
        return False


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _vec3(v, default=None):
    """接受 "[x,y,z]" / "(x,y,z)" 字符串, [x,y,z] 列表, "x,y,z"。"""
    base = list(default or (0.0, 0.0, 0.0))
    if isinstance(v, (list, tuple)):
        for i in range(min(3, len(v))):
            try:
                base[i] = float(v[i])
            except (TypeError, ValueError):
                pass
        return base
    if isinstance(v, str):
        nums = []
        for part in v.replace("(", " ").replace(")", " ").replace(",", " ").split():
            try:
                nums.append(float(part))
            except ValueError:
                continue
            if len(nums) == 3:
                break
        for i, n in enumerate(nums[:3]):
            base[i] = n
    return base


def _client(g, viewer=""):
    """一篇留言的客户端形状: 新版与旧版字段并存。"""
    if not isinstance(g, dict):
        return None
    gid = g.get("geonote_id") or g.get("id") or ""
    author = g.get("author_id") or g.get("creator") or ""
    name = g.get("author_name") or g.get("creator_nickname") or (author[:8] if author else "")
    created = int(g.get("created_at") or g.get("created_time") or 0)
    pos = _vec3(g.get("pos"), (g.get("pos_x") or 0, g.get("pos_y") or 0, g.get("pos_z") or 0))
    return {
        # 新版(0.30+)键
        "geonote_id": gid,
        "pool_type": g.get("pool_type") or "SeasonMeditationShrineNormal",
        "pool_name": g.get("pool_name") or "",
        "message": g.get("message") or "",
        "author_id": author,
        "author_name": name,
        "created_at": created,
        "level_id": g.get("level_id") or 0,
        "pos_x": pos[0],
        "pos_y": pos[1],
        "pos_z": pos[2],
        # 旧版(0.15.x)键
        "id": gid,
        "creator": author,
        "creator_nickname": name,
        "created_time": created,
        "pos": pos,
        "level_hash": int(g.get("level_hash") or 0),
        "parent_guid": int(g.get("parent_guid") or 0),
        "parent_rel_pos": _vec3(g.get("parent_rel_pos")),
        "cam_dir": _vec3(g.get("cam_dir")),
        # 互动状态
        "likes": int(g.get("likes") or 0),
        "liked": bool(g.get("liked")),
        "visible": bool(g.get("visible", True)),
        "reported": bool(g.get("reported")),
        "emoji_id": int(g.get("emoji_id") or 0),
        "bug": bool(g.get("bug")),
        "is_mine": bool(viewer) and author == viewer,
    }


def to_client(g, viewer=""):
    return _client(g, viewer)


def _sorted(notes):
    return sorted(notes, key=lambda g: int(g.get("created_at") or g.get("created_time") or 0),
                  reverse=True)


def _level_match(g, level_id):
    try:
        return str(g.get("level_id") or 0) == str(int(level_id))
    except (TypeError, ValueError):
        return str(g.get("level_id") or 0) == str(level_id)


def list_notes(author="", level_id=None, limit=100, bug=False, viewer=""):
    notes = [g for g in _load() if bool(g.get("bug")) == bool(bug)]
    if author:
        notes = [g for g in notes
                 if (g.get("author_id") or g.get("creator")) == author]
    if level_id not in (None, "", 0, "0"):
        notes = [g for g in notes if _level_match(g, level_id)]
    notes = _sorted(notes)
    limit = max(1, min(int(limit or 100), 200))
    return [_client(g, viewer) for g in notes[:limit]]


def recent(limit=50, viewer=""):
    notes = _sorted([g for g in _load() if not g.get("bug")])
    limit = max(1, min(int(limit or 50), 200))
    return [_client(g, viewer) for g in notes[:limit]]


def payload(user_id="", level_id=None, limit=0, bug=False):
    """官方形状: geonotes + likes + social + emojis。

    新版客户端读 geonotes[*].geonote_id, 旧版读 .id/.pos/.created_time,
    因此每篇留言同时带两套键。
    """
    notes = [g for g in _load() if bool(g.get("bug")) == bool(bug)]
    if level_id not in (None, "", 0, "0"):
        notes = [g for g in notes if _level_match(g, level_id)]
    notes = _sorted(notes)
    if limit:
        try:
            notes = notes[:max(1, min(int(limit), 200))]
        except (TypeError, ValueError):
            pass
    items = [_client(g, user_id) for g in notes]
    items = [it for it in items if it]
    likes = [{"geonote_id": it["geonote_id"], "id": it["id"], "likes": it["likes"]}
             for it in items]
    social = [{
        "geonote_id": it["geonote_id"],
        "id": it["id"],
        "liked": 1 if it["liked"] else 0,
        "visible": 1 if it["visible"] else 0,
        "reported": 1 if it["reported"] else 0,
        "emoji_id": it["emoji_id"],
    } for it in items]
    return {"geonotes": items, "likes": likes, "emojis": [], "social": social}


def create(author="", author_name="", message="", pool_type="", pool_name="",
           level_id=0, pos=(0, 0, 0), extra=None, bug=False, level_hash=0,
           parent_guid=0, parent_rel_pos=None, cam_dir=None):
    message = str(message or "").strip()
    if not author or not message:
        return None
    pos3 = _vec3(pos)
    note = {
        "geonote_id": _gen_id(),
        "id": "",
        "pool_type": pool_type or "SeasonMeditationShrineNormal",
        "pool_name": pool_name or "",
        "message": message[:500],
        "author_id": author,
        "creator": author,
        "author_name": author_name or author[:8],
        "created_at": int(time.time()),
        "level_id": level_id or 0,
        "level_hash": int(level_hash or 0),
        "pos": pos3,
        "pos_x": pos3[0],
        "pos_y": pos3[1],
        "pos_z": pos3[2],
        "parent_guid": int(parent_guid or 0),
        "parent_rel_pos": _vec3(parent_rel_pos),
        "cam_dir": _vec3(cam_dir),
        "likes": 0,
        "liked": False,
        "visible": True,
        "reported": False,
        "emoji_id": 0,
        "bug": bool(bug),
    }
    note["id"] = note["geonote_id"]
    if isinstance(extra, dict):
        for k, v in extra.items():
            note.setdefault(k, v)
    with _lock:
        notes = _load()
        notes.append(note)
        _save(notes)
    logger.info("geonote create: %s -> %s (bug=%s)", author[:8], message[:30], bug)
    return _client(note, author)


def _find(notes, geonote_id):
    for g in notes:
        if str(g.get("geonote_id") or g.get("id")) == str(geonote_id):
            return g
    return None


def get(geonote_id, viewer=""):
    return _client(_find(_load(), geonote_id), viewer)


def delete(geonote_id, author="", bug_only=False):
    if not geonote_id:
        return False
    with _lock:
        notes = _load()
        target = _find(notes, geonote_id)
        if target is None:
            return False
        if author and (target.get("author_id") or target.get("creator")) != author:
            return False
        if bug_only and not target.get("bug"):
            return False
        notes.remove(target)
        _save(notes)
    return True


def edit(geonote_id, message, author=""):
    message = str(message or "").strip()
    if not geonote_id or not message:
        return None
    with _lock:
        notes = _load()
        target = _find(notes, geonote_id)
        if target is None:
            return None
        owner = target.get("author_id") or target.get("creator")
        if author and owner != author:
            return None
        target["message"] = message[:500]
        target["edited_at"] = int(time.time())
        _save(notes)
    return _client(target, author)


def _flag(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().lower()
        if v in ("1", "true", "yes"):
            return True
        if v in ("0", "false", "no", ""):
            return False
    try:
        return bool(int(v))
    except (TypeError, ValueError):
        return bool(v)


def mutate(geonote_id, liked=None, visible=None, reported=None, emoji_id=None, viewer=""):
    """点赞/可见/举报/表情: 同一篇留言的互动状态(对应 social_geonote2)。"""
    with _lock:
        notes = _load()
        target = _find(notes, geonote_id)
        if target is None:
            return None
        liked_f, vis_f, rep_f = _flag(liked), _flag(visible), _flag(reported)
        if liked_f is not None:
            if liked_f and not target.get("liked"):
                target["likes"] = int(target.get("likes") or 0) + 1
            elif not liked_f and target.get("liked"):
                target["likes"] = max(0, int(target.get("likes") or 0) - 1)
            target["liked"] = liked_f
        if vis_f is not None:
            target["visible"] = vis_f
        if rep_f is not None:
            target["reported"] = rep_f
        if emoji_id not in (None, ""):
            try:
                target["emoji_id"] = int(emoji_id)
            except (TypeError, ValueError):
                pass
        target["edited_at"] = int(time.time())
        _save(notes)
    return _client(target, viewer)
