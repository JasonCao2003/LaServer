# -*- coding: utf-8 -*-
"""
重建活动(season/event)数据库 - 高低版本分离

规则:
- 高版本 (>= 0.26.0): 以接口库 account_get_event_schedule.jsonc 的 507 个活动为权威,
  使用其正确的 when/duration。接口库里没有、但旧库里存在的 104 个活动 (含 tcandles/季节蜡烛)
  全部加入但默认禁用 (when=[NEVER]), 等待中控启用。
- 低版本 (< 0.26.0): 使用 H:\Sky\Sky初始备份 的 event_schedule.json (28 个 global 活动)。

输出:
- config/event_schedule_high.json  (高版本权威源, 面板编辑此文件)
- config/event_schedule_low.json   (低版本权威源)
- config/event_schedule.json       = 高版本 (向后兼容现有面板/服务端默认读取)
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paths import CONFIG_DIR, SERVICE_ROOT
import json
import os

NEVER = 9999999999  # 远未来时间戳(绝对秒), 表示"禁用/永不触发"

ROOT = SERVICE_ROOT
CFG = CONFIG_DIR
LIB = r"E:\Downloads\That_Sky_Requests-master\That_Sky_Requests-master\account\account_get_event_schedule.jsonc"
BACKUP_LOW = r"H:\Sky\Sky初始备份\Sky\config\event_schedule.json"


def is_tcandle(name):
    return name.startswith("event_") and ("tcandle" in name)


# tcandle 默认持续(分钟): 以接口库 sunset 系列为基准, 三类统一 1440
TCANDLE_DEFAULT_DURATION = "1440"


def load_lib_events():
    txt = open(LIB, "r", encoding="utf-8").read()
    idx = txt.rfind("//Response")
    obj = json.loads(txt[idx + len("//Response"):].strip())
    es = obj["event_schedule"]
    return es["base_time"], es["events"]


def load_current_events():
    with open(os.path.join(CFG, "event_schedule.json"), "r", encoding="utf-8") as f:
        return json.load(f)["event_schedule"]["events"]


def main():
    base_time, lib_evs = load_lib_events()
    lib_names = {e["name"] for e in lib_evs}
    cur_evs = load_current_events()
    cur_names = {e["name"] for e in cur_evs}
    extras = sorted(cur_names - lib_names)

    high_events = []
    for e in lib_evs:
        name = e["name"]
        if is_tcandle(name):
            high_events.append({"name": name, "duration": TCANDLE_DEFAULT_DURATION,
                                "when": [NEVER]})
        else:
            high_events.append({"name": name,
                                "duration": str(e.get("duration", "1440")),
                                "when": list(e.get("when", [base_time]))})
    for name in extras:
        old = next((x for x in cur_evs if x["name"] == name), {})
        if is_tcandle(name):
            high_events.append({"name": name, "duration": TCANDLE_DEFAULT_DURATION,
                                "when": [NEVER]})
            continue
        dur = str(old.get("duration", "1440"))
        high_events.append({"name": name, "duration": dur, "when": [NEVER]})

    high = {"event_schedule": {
        "valid_until": 2147483648,
        "base_time": base_time,
        "events": high_events,
    }}

    with open(BACKUP_LOW, "r", encoding="utf-8") as f:
        low = json.load(f)

    with open(os.path.join(CFG, "event_schedule_high.json"), "w", encoding="utf-8") as f:
        json.dump(high, f, ensure_ascii=False, indent=2)
    with open(os.path.join(CFG, "event_schedule_low.json"), "w", encoding="utf-8") as f:
        json.dump(low, f, ensure_ascii=False, indent=2)
    with open(os.path.join(CFG, "event_schedule.json"), "w", encoding="utf-8") as f:
        json.dump(high, f, ensure_ascii=False, indent=2)

    n_tc = sum(1 for e in high_events if is_tcandle(e["name"]))
    n_disabled = sum(1 for e in high_events if e["when"] == [NEVER])
    print(f"高版本: {len(high_events)} 个活动 (接口库 {len(lib_evs)} + 额外 {len(extras)})")
    print(f"  其中 tcandles: {n_tc}, 默认禁用(含额外): {n_disabled}")
    print(f"低版本: {len(low['event_schedule']['events'])} 个活动 (来自初始备份)")
    print("已写出 event_schedule_high.json / event_schedule_low.json / event_schedule.json")


if __name__ == "__main__":
    main()
