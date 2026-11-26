# LaServer 项目依赖

Python 依赖由根目录 `pyproject.toml` 声明、`uv.lock` 锁定，使用 `uv sync --locked` 安装到 `.venv`。`requirements.txt` 为 uv 导出的 pip 兼容清单。Node.js 依赖由 `package.json` 和 `package-lock.json` 管理。当前 Python 3.15.1 已通过依赖导入及 Flask-SocketIO/eventlet 初始化检查，尚未完成端到端业务验证。

## 运行环境

| 部分 | 环境要求 | 说明 |
| --- | --- | --- |
| 游戏业务服务 | Python、Windows / Linux | Windows 引导绕过 WMI；Linux 跳过此补丁，完整运行待验证，见 `STARTUP.md` |
| 管理面板 | Python、Tk/Tcl、Windows | 使用 Tkinter GUI、防火墙、进程管理等 Windows 功能 |
| 实时联机服务 | Node.js >= 20、npm | 版本下限取自 `package.json` 的 `engines` |
| 客户端修补工具 | Python | 当前源码仅直接导入标准库 |
| 数据存储 | Python 内置 SQLite 支持 | 不需要独立 SQLite 服务进程；数据库文件由部署环境提供或初始化 |

当前项目 Python 范围为 `>=3.15,<3.16`，`.python-version` 固定本机验证使用的 3.15.1。其他 Python 版本尚未验证。

## Python 游戏业务服务

依赖声明见根目录 `pyproject.toml`，完整操作流程见 [README](../README.md#安装依赖)。下表列出 11 项直接依赖，uv 同时安装锁定的传递依赖。

| 导入名 | pip 包名 | 用途 |
| --- | --- | --- |
| `flask` | `Flask` | HTTP 路由、请求与响应 |
| `werkzeug` | `Werkzeug` | HTTP 工具；源码直接导入，亦为 Flask 的依赖 |
| `flask_sqlalchemy` | `Flask-SQLAlchemy` | Flask ORM 集成 |
| `sqlalchemy` | `SQLAlchemy` | 模型、查询与数据库迁移 |
| `flask_sock` | `flask-sock` | WebSocket 接入 |
| `flask_socketio` | `Flask-SocketIO` | Socket.IO 通信；安装时解析其传递依赖 |
| `eventlet` | `eventlet` | 当前服务指定的异步运行模式及 TLS 服务 |
| `cryptography` | `cryptography` | 密钥、加密与证书处理 |
| `jwt` | `PyJWT` | JWT 处理；不要用同名导入推断成另一个 `jwt` 包 |
| `bcrypt` | `bcrypt` | 密码哈希 |
| `requests` | `requests` | HTTP 客户端 |

在项目根目录执行，无须手动激活虚拟环境：

```powershell
uv sync --locked
uv pip check
uv run --locked python -u services/api-service/sky_boot.py
```

添加依赖用 `uv add <包名>`；更新锁文件和环境后，用以下命令重新导出 pip 兼容清单：

```powershell
uv export --format requirements-txt --no-hashes --no-emit-project --output-file requirements.txt
```

依赖检查不代表完整业务服务可用，业务验证仍应使用隔离数据。

`tkinter`、`sqlite3`、`ssl`、`json`、`socket`、`threading` 等属于 Python 标准库或解释器配套组件，不通过同名 pip 包安装。Tkinter 需要解释器安装时包含 Tcl/Tk；Windows 图形监控同样需要它。

## 管理面板依赖

面板主要使用 Python 标准库和 Tkinter；生成自签名证书的功能还需要 `cryptography`。面板启动的游戏服务需要上表中的完整服务依赖。

### 本地管理模块

`services/panel-service/api_response_manager.py` 随项目提供。其 `ApiResponseManager` 用于查看最近响应并管理账号与全局响应覆盖。

响应管理只访问 `services/api-service/var/data/instance/users.db`，不会自动创建缺失的数据库。全局覆盖使用空用户 ID，账号专属覆盖优先；删除或禁用专属覆盖后可能回退到全局覆盖。


其余按功能加载、存在异常回退的本地模块包括：

| 模块 | 关联功能 |
| --- | --- |
| `ddns_updater` | 动态域名更新 |
| `public_net` | 公网地址探测与端口映射 |
| `map_unlock_helper` | 部分地图解锁管理逻辑 |

面板中另有按需导入的旧名称 `outfit_version_db`；对应代码现位于 `services/api-service/business/helpers/outfit_version_db.py`。相关跨目录导入仍需运行验证，安装 pip 包不会修复本地模块路径问题。

### 外部程序

- `node`：面板启动实时联机服务时需要可找到 Node.js。
- `cloudflared.exe`：可选，仅公网隧道功能使用；优先查找 `services/panel-service/cloudflared.exe`，再查找 `%LOCALAPPDATA%/cloudflared/cloudflared.exe`。
- Windows 自带的网络、防火墙、进程管理工具：面板相关功能会调用；部分操作可能要求管理员权限。

## Node.js 实时联机服务

以下为项目直接依赖；精确安装结果以提交的锁文件为准。

| 包 | package.json 范围 | 当前锁定版本 | 用途 |
| --- | --- | --- | --- |
| `fastify` | `^5.3.3` | `5.8.5` | HTTP 管理与监控 API |
| `pino` | `^9.7.0` | `9.14.0` | 结构化日志 |
| `pino-pretty` | `^13.0.0` | `13.1.3` | 控制台日志格式化 |
| `prom-client` | `^15.1.3` | `15.1.3` | 监控指标 |
| `sky-enet` | `^0.1.5` | `0.1.5` | ENet UDP 原生绑定 |
| `zod` | `^3.25.76` | `3.25.76` | 环境变量与配置校验 |

在项目根目录安装与检查：

```powershell
npm --prefix services/room-service ci
npm --prefix services/room-service run check
```

`npm ci` 按锁文件安装，不要提交 `node_modules/`。当前没有独立的 `devDependencies`，测试使用 Node 内置测试能力。

`sky-enet` 是原生扩展，安装脚本使用 `node-gyp-build` 加载匹配的原生产物。若当前系统、CPU 和 Node 版本没有可用产物，需要进一步处理原生构建；包内提供 `node-gyp rebuild` 构建脚本。这类错误与普通 JavaScript 语法错误不同，应先查看安装日志确认具体原因。

`npm run check` 只检查 `src/main.js` 语法；不能证明 ENet 原生模块可加载、端口可监听或实际联机成功。

## 配置与数据前置条件

依赖安装不等于部署完成，还需要：

- 从 `services/api-service/config/server.example.json` 生成本机 `config/server.json`。
- 从 `services/api-service/config/room_config.example.json` 生成本机 `services/api-service/config/room_config.json`。
- 在 `services/api-service/var/certs/` 提供 `fullchain.pem` 和 `privkey.pem`。
- 准备或初始化 `services/api-service/var/data/instance/` 中的主库及状态；原独立引擎使用 `services/api-service/var/data/legacy/users.db`，两份数据库不合并。
- 根据部署环境配置 HTTP、UDP 监听端口及防火墙。

维护脚本还使用外部输入：`services/api-service/tools/build_daily_quest_catalog.py` 读取其 `DB_QUEST` 路径，`services/api-service/tools/rebuild_event_schedules.py` 读取 `LIB` 和 `BACKUP_LOW` 路径。源码中保留了原机器绝对路径，执行前须改为自己的数据源；这些输入不由 pip 或 npm 提供。

## 验证状态与常见问题

| 现象 | 检查方向 |
| --- | --- |
| `No module named flask_sqlalchemy` | 确认运行解释器与安装依赖的解释器相同，并安装 `Flask-SQLAlchemy` |
| `No module named api_response_manager` | 确认 `api_response_manager.py` 与面板启动脚本位于同一目录 |
| Tkinter 不可导入或无 GUI | 检查 Python 的 Tcl/Tk 配套组件及桌面会话 |
| ENet 模块加载失败 | 检查 `npm ci` 日志、原生产物及系统/CPU/Node 匹配情况 |
| 证书或配置找不到 | 按前置条件准备本机文件，它们被 `.gitignore` 排除 |
| Python 直接启动挂在 Windows 信息查询 | 使用项目 `sky_boot.py` 引导入口 |

此前已通过 Python 语法与本地路径静态检查，以及实时服务入口语法检查。当前 uv 环境已安装全部依赖并通过导入检查；尚未完成完整业务服务启动、管理面板启动或双客户端联机测试。

## 更新依赖清单

新增 Python 第三方依赖时使用 `uv add`，提交 `pyproject.toml`、`uv.lock`，重新导出 `requirements.txt`，并同步本文用途表。依赖锁定不等于业务兼容性已经验证。

新增或更新 Node.js 依赖时，在 `services/room-service/` 使用 npm 修改 `package.json`，连同生成的 `package-lock.json` 一起提交，并同步本文版本表。文档更新本身不安装、升级或验证依赖。
