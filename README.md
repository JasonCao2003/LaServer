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
├── tools/patches/              客户端修补工具
├── docs/DEPENDENCIES.md        依赖与部署前置条件
└── requirements.txt            Python 直接依赖清单
```

运行时使用的 `data/`、`logs/`、证书和本地配置不纳入版本控制。

完整依赖、安装方法和当前缺失模块见 [项目依赖文档](docs/DEPENDENCIES.md)。

## 环境要求

- Windows：当前 `sky_boot.py` 使用 Windows 专用启动处理。
- Python：需要预先准备 Flask、SQLAlchemy、eventlet 等项目依赖；直接依赖见根目录 `requirements.txt`，尚未锁定版本或完成全新环境验证。
- Node.js 20 或更新版本及 npm。

## 安装依赖

以下命令在项目根目录执行，使用虚拟环境避免混用 Python 解释器：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
npm --prefix services/realtime-server ci
```

`requirements.txt` 包含 11 个源码直接使用的第三方包，尚未锁定版本；全新环境兼容性待验证。Node.js 依赖通过 `package-lock.json` 锁定，使用 `npm ci` 安装。

Tkinter/Tcl-Tk 和 SQLite 支持由 Python 安装提供，无须安装同名 pip 包。当前管理面板缺少必需的本地模块 `game_control_panel.py`、`api_response_manager.py`，需补齐后才能启动，详见[依赖文档](docs/DEPENDENCIES.md#缺失的本地模块)。

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

以下命令均在项目根目录执行，先完成依赖安装、本地配置及数据准备。

HTTP 业务服务：

```powershell
.\.venv\Scripts\python.exe -u services/game-api/sky_boot.py
```

管理面板：

```powershell
.\.venv\Scripts\python.exe apps/admin/admin_panel_client.py
```

实时联机服务：

```powershell
npm --prefix services/realtime-server start
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
