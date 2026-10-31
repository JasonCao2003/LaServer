# -*- coding: utf-8 -*-
"""sky_boot.py —— 启动私服(index.py)的引导脚本(2026-10-05 新增)。

为什么需要它(本机实测): 这台机器上 WMI 查询不返回。而 sqlalchemy 被 import 时会调
platform.machine() -> platform.uname() -> platform.win32_ver() -> _win32_ver()
-> _wmi_query(); _wmi_query 用的是 CPython 自带的 _wmi C 扩展(COM 查询), WMI 不响应时
它**永远不返回**(C 层阻塞, 连超时都用不上)。后果:
    * 手动 `python -u index.py` -> 不打印任何东西、不监听端口, 就那样挂着;
    * 面板点"启动" -> 只看到"启动中", 永远等不到 SKY_LISTEN_PORT;
    * find_python.ps1 / 面板的导入测试也一样挂住(bat 会停在"正在查找可用的 Python 解释器...")。

这里的做法: 在 import sqlalchemy **之前**, 把 platform 的三个 WMI 入口换成不查 WMI 的实现
(值取自 sys.getwindowsversion() 和 PROCESSOR_ARCHITECTURE 环境变量; 私服不依赖精确的
Windows 版本号, 没有任何副作用), 然后原样跑 index.py。

Linux 上跳过 Windows 专用处理。

用法(管理面板已改成用它启动; 手动启动完全一样, 环境变量照旧生效):
    python -u sky_boot.py
(工作目录无所谓, 里面会自己切到本文件所在的源码根目录)
"""
from paths import SERVICE_ROOT
import os
import platform
import sys

if sys.platform == "win32":
    _win = sys.getwindowsversion()
    _ver = "%d.%d.%d" % (_win.major, _win.minor, _win.build)
    platform.machine = lambda: (os.environ.get("PROCESSOR_ARCHITECTURE") or "AMD64")
    platform.processor = lambda: platform.machine()
    platform.win32_ver = lambda *a, **k: (_ver, _ver, "", "Multiprocessor Free")

HERE = SERVICE_ROOT
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.chdir(HERE)
sys.argv = ["index.py"]

import runpy

runpy.run_path(os.path.join(HERE, "index.py"), run_name="__main__")
