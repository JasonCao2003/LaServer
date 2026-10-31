# LaServer

SkyLightAwaits 的游戏业务与实时联机服务端，包含 Python HTTP 服务、Node.js UDP 联机服务及桌面管理面板。

## 项目结构

```text
LaServer/
├── apps/admin/                 桌面管理面板
├── services/
│   ├── game-api/               Flask 游戏业务服务
│   │   ├── account/            账号与玩家接口
│   │   ├── admin/              管理接口
│   │   ├── service/            游戏业务接口
│   │   ├── starwatch/          统计与认证接口
│   │   ├── helpers/            共享业务模块与版本适配
│   │   ├── database/           数据库模型及迁移
│   │   ├── config/             游戏配置
│   │   ├── resources/          静态响应和图片
│   │   └── tools/              配置维护脚本
│   └── realtime-server/        ENet / UDP 实时联机服务
│       ├── src/                协议、房间、传输与监控
│       ├── tests/              手动协议回归脚本
│       ├── tools/              历史抓包分析
│       └── docs/               协议适配说明
└── tools/patches/              客户端修补工具
```

运行时使用的 `data/`、`logs/`、证书和本地配置不纳入版本控制。

## 环境要求

- Windows：当前 `sky_boot.py` 使用 Windows 专用启动处理。
- Python：需要预先准备 Flask、SQLAlchemy、eventlet 等项目依赖；目前尚未提供完整的 Python 依赖清单。
- Node.js 20 或更新版本及 npm。

## 本地配置

在项目根目录执行：

```powershell
Copy-Item services/game-api/config.example.json services/game-api/config.json
Copy-Item services/game-api/config/room_config.example.json services/game-api/config/room_config.json
```

按实际部署地址修改上述文件。管理面板默认定位本项目的两个服务目录。

在 `services/game-api/certs/` 准备 `fullchain.pem` 和 `privkey.pem`，或使用管理面板的证书配置功能。

既有实例的主数据库及状态位于 `services/game-api/data/instance/`；原独立数据库位于 `data/legacy/users.db`，两者应分别迁移。仓库不包含账号数据、运行状态或证书；仅克隆源码并不代表已完成运行环境部署。

## 启动

HTTP 业务服务：

```powershell
python -u services/game-api/sky_boot.py
```

管理面板：

```powershell
python apps/admin/admin_panel_client.py
```

实时联机服务：

```powershell
cd services/realtime-server
npm ci
npm start
```

联机服务可通过 `ROOM_PORT`、`HTTP_PORT` 等环境变量配置，详见 `src/config/env.js`。与既有实例并行运行时使用独立端口。

## 检查与测试

在 `services/realtime-server/` 执行：

```powershell
npm run check
node tests/sky0134-regression.mjs
```

`npm run check` 仅检查入口语法。手动协议回归脚本当前有一条构建号限制断言与实现不一致，尚未修复。目录整理后已完成 Python 语法与路径静态检查；完整 HTTP 运行验证仍需安装依赖并使用隔离数据执行。

## 数据与安全

`.gitignore` 排除了备份、依赖目录、数据库、证书私钥、日志、抓包、本机配置，以及两份含敏感字段的认证响应样本。请勿提交生产数据或访问凭据。

HTTP 监控历史保存在 `services/game-api/data/monitor_exports/`；UDP 收发包历史保存在 `services/realtime-server/data/packets/`；各服务日志保存在各自的 `logs/`。

更多模块说明参见各服务目录的 README。
