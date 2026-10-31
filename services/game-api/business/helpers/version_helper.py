# -*- coding: utf-8 -*-
"""
客户端版本解析辅助

用于"版本分类"分发: 不同版本客户端访问同一接口时返回不同内容。
新接口(> 0.26.5 抓包重写)统一以此判定是否命中"高版本"逻辑。

配置阈值:
    HIGH_VERSION_MIN = (0, 26, 6)   # 高于 0.26.5 视为高版本
"""
import re
import threading

from business.helpers.client_version import client_version_from_ua, client_version_from_request

_lock = threading.Lock()

# 高于 0.26.5 视为"高版本"(抓包重写目标)
HIGH_VERSION_MIN = (0, 26, 6)


def parse_client_version(user_agent=None):
    """从 User-Agent 解析客户端版本, 返回 (major, minor, patch) 或 None。

    兼容:
      Sky-Live-com.tgc.sky.android.huawei/0.27.5.xxx
      Sky-Test-com.tgc.sky.android.test.gold.huawei/0.28.0.306383
      Sky-Live-com.tgc.sky.win.test.gold/0.12.1.179644

    实际解析统一交给 client_version(带 config/client_version_overrides.json
    纠偏: 有的魔改老客户端会谎报版本, 如 0.13.4 报成 0.23.6.354648)。
    """
    if user_agent is None:
        return client_version_from_request()
    return client_version_from_ua(user_agent)


def is_high_version(user_agent=None):
    """是否命中"高于 0.26.5"的高版本。解析失败默认按高版本处理(新接口本就是给高版本用的)。"""
    ver = parse_client_version(user_agent)
    if ver is None:
        return True
    return ver >= HIGH_VERSION_MIN
