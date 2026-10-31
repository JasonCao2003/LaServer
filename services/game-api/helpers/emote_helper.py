# -*- coding: utf-8 -*-
"""
动作(emote)等级辅助模块

机制说明：
- 游戏里的动作通过先祖(spirit shop)解锁，每个动作分多个等级，
  命名格式为 emote_upgrade_<动作名>_<等级>，例如 emote_upgrade_wave_01 ~ _06。
- 需求：新号默认只保留"左下角"的一个动作；其余动作需在地图内通过先祖解锁，
  且解锁后直接开到该动作的最高级。

本模块从 config/spirit_shops.json 动态解析出每个动作族包含的全部等级，
供 get_unlocks / purchase_spirit_shop_item 等接口复用。
"""
import os
import re
import json
import threading

# 如需更换默认动作，把这里改成对应动作名（emote_upgrade_<这里>_NN）即可。
DEFAULT_EMOTE_FAMILY = "wave"

_EMOTE_RE = re.compile(r"^emote_upgrade_(.+)_(\d+)$")

_lock = threading.Lock()
_families = None  # {family: [name, name, ...] 按等级升序}


def _config_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "config", "spirit_shops.json")


def _load_families():
    """解析 spirit_shops.json，构建 {动作族: [各等级name升序]} 映射。"""
    global _families
    if _families is not None:
        return _families
    with _lock:
        if _families is not None:
            return _families
        fam = {}
        try:
            with open(_config_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("spirit_shops", []) if isinstance(data, dict) else []
            tmp = {}  # family -> {level: name}
            for it in items:
                nm = it.get("nm", "")
                if it.get("typ") != "spirit_upgrade":
                    continue
                m = _EMOTE_RE.match(nm)
                if not m:
                    continue
                base, lv = m.group(1), int(m.group(2))
                tmp.setdefault(base, {})[lv] = nm
            for base, lv_map in tmp.items():
                fam[base] = [lv_map[k] for k in sorted(lv_map.keys())]
        except Exception:
            fam = {}
        _families = fam
        return _families


def is_emote_unlock(name):
    """判断某解锁名是否为动作升级项。"""
    return bool(name) and name.startswith("emote_upgrade_")


def emote_family_of(name):
    """从 emote_upgrade_xxx_NN 提取动作族名；非动作返回 None。"""
    if not name:
        return None
    m = _EMOTE_RE.match(name)
    return m.group(1) if m else None


def family_all_levels(family):
    """返回某动作族的全部等级 name（升序）。未知动作返回空列表。"""
    if not family:
        return []
    return list(_load_families().get(family, []))


def family_levels_for(name):
    """给定某个动作(任意等级)的 name，返回其所属动作族的全部等级 name。"""
    return family_all_levels(emote_family_of(name))


def default_emote_names():
    """新号默认应保留的动作 name 列表（默认动作族的全部等级）。"""
    return family_all_levels(DEFAULT_EMOTE_FAMILY)