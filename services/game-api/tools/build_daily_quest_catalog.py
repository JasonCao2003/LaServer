# -*- coding: utf-8 -*-
"""
生成每日任务目录 config/daily_quest_catalog.json
来源: 数据库 quest_defs.json 中所有带 completion_stats_types 的任务 (可计数完成 = 适合做每日任务)。
每项: {daily_quest_def_id, stat_type, cn} —— cn 为按关键词推导的中文名(中英对照用)。
面板"添加任务"弹窗读取本目录; 服务端每日随机也从本目录取池。
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paths import CONFIG_DIR, SERVICE_ROOT
import json
import os
import re

ROOT = SERVICE_ROOT
DB_QUEST = r"E:\Downloads\server\quest_defs.json"
OUT = os.path.join(CONFIG_DIR, "daily_quest_catalog.json")

# 关键词 -> 中文 (用于把 quest id / stat_type 翻成可读中文)
KW = [
    ("fetchlight", "寻光"), ("lightseeker", "寻光者"), ("save_a_spirit", "解救先祖"),
    ("save_spirit", "解救先祖"), ("hug", "拥抱"), ("send_a_gift", "赠送礼物"),
    ("send_gift", "赠送礼物"), ("gift", "礼物"), ("pickup", "拾取"), ("wax", "蜡"),
    ("candle", "蜡烛"), ("visit", "拜访"), ("rainbow", "彩虹"), ("prairie", "云野"),
    ("forest", "雨林"), ("valley", "霞谷"), ("wasteland", "暮土"), ("vault", "禁阁"),
    ("dusk", "暮土"), ("night", "夜晚"), ("cozycave", "舒适洞穴"), ("thumbsup", "点赞"),
    ("crab", "螃蟹"), ("shout", "呼喊"), ("emote", "动作"), ("meditate", "冥想"),
    ("bird", "鸟"), ("bluebird", "青鸟"), ("feather", "羽毛"), ("chase", "追逐"),
    ("bloom", "花憩"), ("harvest", "收获"), ("water", "浇水"), ("music", "音乐"),
    ("message", "留言"), ("companion", "同伴"), ("daily", "每日"), ("generic", "通用"),
    ("fly", "飞行"), ("race", "竞速"), ("collect", "收集"), ("collectible", "回忆"),
    ("burn", "点亮"), ("light", "点亮"), ("relive", "重温"), ("memory", "回忆"),
    ("spirit", "先祖"), ("world_quest", "世界任务"), ("quest", "任务"),
    ("ancestor", "先祖"), ("krill", "磷虾"), ("manta", "蝠鲼"), ("jelly", "水母"),
    ("boat", "船"), ("fish", "鱼"), ("seed", "种子"), ("plant", "种植"),
    ("dark", "暗"), ("stone", "石"), ("crystal", "水晶"), ("totem", "图腾"),
]


def to_cn(name):
    s = name.lower()
    parts = []
    for kw, cn in KW:
        if kw in s:
            parts.append(cn)
    m = re.search(r"_(\d+)$", name)
    num = m.group(1) if m else ""
    base = "·".join(dict.fromkeys(parts)) if parts else name
    return (base + (f" {num}" if num else "")).strip()


def main():
    with open(DB_QUEST, "r", encoding="utf-8") as f:
        qd = json.load(f)["get_quest_defs"]
    catalog = []
    seen = set()
    for q in qd:
        qid = q.get("id")
        stats = q.get("completion_stats_types") or []
        if not qid or not stats:
            continue
        stat = stats[0]
        if qid in seen:
            continue
        seen.add(qid)
        catalog.append({
            "daily_quest_def_id": qid,
            "stat_type": stat,
            "cn": to_cn(qid),
        })
    catalog.sort(key=lambda x: x["daily_quest_def_id"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"daily_quest_catalog": catalog}, f, ensure_ascii=False, indent=2)
    print(f"生成 {len(catalog)} 个每日任务条目 -> {OUT}")
    for c in catalog[:8]:
        print(" ", c)


if __name__ == "__main__":
    main()
