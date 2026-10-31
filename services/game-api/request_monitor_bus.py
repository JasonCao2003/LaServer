# -*- coding: utf-8 -*-
"""请求监控的"总线" —— 队列只在这里创建一次。

为什么要单独一个模块(2026-10-05 玩家反馈"接口请求响应查看有时候不监控"):

  request_monitor_middleware 在 hot_reload 的重载名单里(hot_reload.py 的
  ("request_monitor_middleware", ...)), 每次点"热更新"都会 importlib.reload 它。
  老代码把队列建在那个模块里(request_log_queue = queue.Queue(maxsize=10000)),
  reload 会把模块字典里的这个名字**换成一个新队列** —— 而监控面板窗口是服务器启动时
  用**旧队列**起的(_poll_queue 一直在 get 那个旧对象), 于是:
      * 中间件继续往**新**队列里塞记录(注册的钩子还在, 取的是模块字典里的新名字);
      * 面板死盯着**旧**队列, 永远 get 不到东西 —— 窗口就停在热更新前那批记录上,
        看起来就是"有时候不监控"(实测: 每次热更新必定复现)。
  记录其实没丢, 只是面板看错了地方; 但用户看到的就是监控停了。

  现在队列放在这个模块。hot_reload 不重载它, 所以不管热更新多少次、中间件被 reload
  多少次, 大家拿到的永远是同一个 Queue 对象: 中间件塞、面板取, 一直到服务器进程结束
  (队列随进程消失, 不需要任何清理)。

另外 request_monitor_gui 的采集线程每轮都会核对一次
`request_monitor_bus.request_log_queue is self.queue`, 万一将来有人改了重载名单把它
换掉了, 采集线程会自动切到新队列(先把旧队列里剩下的记录取干净, 一条都不丢)。
"""
import queue

# 中间件塞记录 / 监控面板取记录, 都用这一个。maxsize 到顶时中间件丢最旧的(见
# request_monitor_middleware._capture_after), 保证在忙的时候也是"最新的看得到"。
# 2026-10-23 内存治理: 10000 -> 500。内存里只留最近 100 条, 队列只做短时缓冲;
# 积压时中间件丢最旧的, 而每条记录在捕获时已同步落盘, 不会丢数据。
request_log_queue = queue.Queue(maxsize=500)


def get_request_log_queue():
    """取总队列。中间件与面板都走这里, 保证是同一个对象。"""
    return request_log_queue