# LaServer 项目依赖

本文根据仓库 Python 导入、`services/realtime-server/package.json` 和 `package-lock.json` 整理。Python 列表是源码直接依赖清单，尚未在全新环境完成安装和端到端验证；不是已验证的版本锁定文件。

## 运行环境

| 部分 | 环境要求 | 说明 |
| --- | --- | --- |
| 游戏业务服务 | Python、Windows | `sky_boot.py` 调用 `sys.getwindowsversion()` 并绕过本机 WMI 查询问题，当前启动引导是 Windows 专用实现 |
| 管理面板 | Python、Tk/Tcl、Windows | 使用 Tkinter GUI、防火墙、进程管理等 Windows 功能 |
| 实时联机服务 | Node.js >= 20、npm | 版本下限取自 `package.json` 的 `engines` |
| 客户端修补工具 | Python | 当前源码仅直接导入标准库 |
| 数据存储 | Python 内置 SQLite 支持 | 不需要独立 SQLite 服务进程；数据库文件由部署环境提供或初始化 |

Python 最低兼容版本尚未确认，不承诺某一版本可完整运行。应使用隔离虚拟环境验证，成功部署后记录 Python 版本与完整依赖锁定结果。

## Python 游戏业务服务

安装入口为根目录 [`requirements.txt`](../requirements.txt)，完整操作流程见 [README](../README.md#安装依赖)。该文件目前包含下表 11 个直接依赖；pip 同时会安装它们的传递依赖。

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

根目录 `requirements.txt` 提供未锁版本的直接依赖清单；项目尚无 Python 依赖锁文件。以下命令只用于建立待验证环境，不能保证最新解析出的版本组合兼容：

在项目根目录执行，无须先激活虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

`pip check` 检查已安装包声明的依赖关系，不会验证业务代码或服务可用性。后续安装、检查和运行均使用同一虚拟环境的 Python。不要将其他项目环境的 `pip freeze` 当作本项目已验证的依赖清单。

`tkinter`、`sqlite3`、`ssl`、`json`、`socket`、`threading` 等属于 Python 标准库或解释器配套组件，不通过同名 pip 包安装。Tkinter 需要解释器安装时包含 Tcl/Tk；Windows 图形监控同样需要它。

## 管理面板依赖

面板主要使用 Python 标准库和 Tkinter；生成自签名证书的功能还需要 `cryptography`。面板启动的游戏服务需要上表中的完整服务依赖。

### 缺失的本地模块

下面两个模块被面板在顶层直接导入，当前 Git 仓库未包含；缺失时会在启动阶段报错：

- `game_control_panel.py`：`build_game_control_tab`。
- `api_response_manager.py`：`ApiResponseManager`。

需要从原项目补齐并放入可导入路径，例如 `apps/admin/`；它们不是应从 PyPI 按名称安装的第三方包。补齐前不能将管理面板视为可直接运行。

其余按功能加载、存在异常回退的本地模块包括：

| 模块 | 关联功能 |
| --- | --- |
| `other_control_panel` | 其他控制页 |
| `magic_control_panel` | 魔法控制页 |
| `ugc_control_panel` | 社交内容控制页 |
| `friend_control_panel` | 好友控制页 |
| `ddns_updater` | 动态域名更新 |
| `public_net` | 公网地址探测与端口映射 |
| `map_unlock_helper` | 部分地图解锁管理逻辑 |

面板中另有按需导入的旧名称 `outfit_version_db`；对应代码现位于 `services/game-api/helpers/outfit_version_db.py`。相关跨目录导入仍需运行验证，安装 pip 包不会修复本地模块路径问题。

### 外部程序

- `node`：面板启动实时联机服务时需要可找到 Node.js。
- `cloudflared.exe`：可选，仅公网隧道功能使用；优先查找 `apps/admin/cloudflared.exe`，再查找 `%LOCALAPPDATA%/cloudflared/cloudflared.exe`。
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
npm --prefix services/realtime-server ci
npm --prefix services/realtime-server run check
```

`npm ci` 按锁文件安装，不要提交 `node_modules/`。当前没有独立的 `devDependencies`，测试使用 Node 内置测试能力。

`sky-enet` 是原生扩展，安装脚本使用 `node-gyp-build` 加载匹配的原生产物。若当前系统、CPU 和 Node 版本没有可用产物，需要进一步处理原生构建；包内提供 `node-gyp rebuild` 构建脚本。这类错误与普通 JavaScript 语法错误不同，应先查看安装日志确认具体原因。

`npm run check` 只检查 `src/main.js` 语法；不能证明 ENet 原生模块可加载、端口可监听或实际联机成功。手动回归脚本 `node tests/sky0134-regression.mjs` 当前有一条已知失败的构建号限制断言。

## 配置与数据前置条件

依赖安装不等于部署完成，还需要：

- 从 `services/game-api/config.example.json` 生成本机 `config.json`。
- 从 `services/game-api/config/room_config.example.json` 生成本机 `services/game-api/config/room_config.json`。
- 在 `services/game-api/certs/` 提供 `fullchain.pem` 和 `privkey.pem`。
- 准备或初始化 `services/game-api/data/instance/` 中的主库及状态；原独立引擎使用 `services/game-api/data/legacy/users.db`，两份数据库不合并。
- 根据部署环境配置 HTTP、UDP 监听端口及防火墙。

维护脚本还使用外部输入：`services/game-api/tools/build_daily_quest_catalog.py` 读取其 `DB_QUEST` 路径，`services/game-api/tools/rebuild_event_schedules.py` 读取 `LIB` 和 `BACKUP_LOW` 路径。源码中保留了原机器绝对路径，执行前须改为自己的数据源；这些输入不由 pip 或 npm 提供。

## 验证状态与常见问题

| 现象 | 检查方向 |
| --- | --- |
| `No module named flask_sqlalchemy` | 确认运行解释器与安装依赖的解释器相同，并安装 `Flask-SQLAlchemy` |
| `No module named game_control_panel` | 缺失本地源码模块，须补齐；不能靠第三方依赖安装解决 |
| Tkinter 不可导入或无 GUI | 检查 Python 的 Tcl/Tk 配套组件及桌面会话 |
| ENet 模块加载失败 | 检查 `npm ci` 日志、原生产物及系统/CPU/Node 匹配情况 |
| 证书或配置找不到 | 按前置条件准备本机文件，它们被 `.gitignore` 排除 |
| Python 直接启动挂在 Windows 信息查询 | 使用项目 `sky_boot.py` 引导入口 |

此前已通过 Python 语法与本地路径静态检查，以及实时服务入口语法检查。当前终端的 Python 曾因缺少 `flask_sqlalchemy` 无法完成服务导入验证；本次依赖文档不代表已完成全新安装、管理面板启动或双客户端联机测试。

## 更新依赖清单

新增 Python 第三方导入时，同时更新 `requirements.txt` 与本文的用途表。兼容性测试通过后，再决定版本约束；不要把未验证的环境冻结结果当作发布锁文件。

新增或更新 Node.js 依赖时，在 `services/realtime-server/` 使用 npm 修改 `package.json`，连同生成的 `package-lock.json` 一起提交，并同步本文版本表。文档更新本身不安装、升级或验证依赖。
