# 从桌面面板启动

在 Windows 的项目根目录执行：

```powershell
uv run --locked python services/panel-service/admin_panel_client.py
```

面板打开后，在服务控制界面启动游戏 API，房间进程随 API 启动。可以在面板中查看监听端口和日志。

## 首次准备

- 安装 uv 和 Node.js 20 或以上。Python 版本由根目录 `.python-version` 指定。
- 在项目根目录运行 `npm --prefix services/room-service ci` 安装房间服务依赖。Python 依赖由启动命令中的 uv 按锁文件准备。
- 按 [项目本地配置说明](../README.md#本地配置) 准备游戏 API 配置和证书。迁移已有实例时，分别保留 `var/data/instance/` 与 `var/data/legacy/`。
- 确认 `services/api-service/config/room_config.json` 的 `ip_host` 和 `realtime_port` 是客户端实际可达的地址和 UDP 端口。

## 端口与环境

API 的实际 HTTPS 端口以面板日志中的 `SKY_LISTEN_PORT` 为准。房间的管理 HTTP 默认从本机端口 8080 开始，游戏客户端连接的是配置中下发的 UDP 地址和端口。

额外环境变量在打开面板前设置到启动终端，由子进程继承。面板会另外设置 API 端口和房间实例相关变量。项目中的 `.env.example` 可用来查阅配置项，当前启动流程无需环境文件加载脚本。
