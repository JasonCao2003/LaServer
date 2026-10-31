# Windows / Linux 服务启动

所有示例从项目根目录执行。Windows 使用 PowerShell；Linux 使用 Bash。脚本也支持从其他工作目录调用，以脚本位置定位项目。

## 前置条件

- Python 服务：安装 uv，使用 `.python-version` 指定的 Python 3.15.1；`uv sync` 在需要时下载解释器。Linux 需要相应平台的可用解释器及依赖产物。
- 联机服务：Node.js >= 20、npm。`sky-enet` 是原生扩展，Linux 必须重新 `npm ci`，不可复制 Windows 的 `node_modules`；缺少预编译产物时需要编译工具链。
- Python 服务需要本机配置、游戏资源和有效的 `var/certs/fullchain.pem`、`var/certs/privkey.pem`。脚本不生成证书或迁移数据库。新实例的数据库初始化由现有业务服务负责；既有数据迁移必须分别保留 `var/data/instance` 与 `var/data/legacy`，并确保目录可写。

## Windows

可双击运行：每个服务目录中的 `setup.bat` 准备环境，`start.bat` 启动服务。批处理通过脚本自身路径定位 PowerShell 入口，无须先切换目录；执行结束后暂停显示结果，并保留退出码。首次部署先运行 `setup.bat`，已有环境直接运行 `start.bat`。窗口运行期间服务保持前台，按 Ctrl+C 停止。

首次准备环境（保留已存在的配置）：

```powershell
.\services\game-api\start.ps1 -Setup
.\services\realtime-server\start.ps1 -Setup
```

分别在两个终端启动：

```powershell
.\services\game-api\start.ps1
```

```powershell
.\services\realtime-server\start.ps1
```

如本机执行策略阻止脚本，可仅对本次进程运行：`powershell -ExecutionPolicy Bypass -File .\services\game-api\start.ps1`（联机服务替换对应路径）。

## Linux

首次准备环境：

```bash
bash services/game-api/start.sh --setup
bash services/realtime-server/start.sh --setup
```

分别在两个终端启动：

```bash
bash services/game-api/start.sh
```

```bash
bash services/realtime-server/start.sh
```

两端均在前台运行，Ctrl+C 停止；Linux 脚本使用 `exec` 将信号直接交给服务进程。脚本不会安装系统服务或配置防火墙。

## 环境配置

准备模式分别复制各服务的 `.env.example` 为 `.env`，不覆盖已有文件。Python 服务另按需复制 `config.example.json` 和 `config/room_config.example.json`。依赖由 `uv sync --locked` / `npm ci` 安装；启动时 Node 不重新安装依赖，Python 由 `uv run --locked` 检查和同步环境。

| 文件 | 主要配置 |
| --- | --- |
| `services/game-api/.env` | `SKY_HOST=0.0.0.0`、`SKY_PORT=8443` |
| `services/realtime-server/.env` | `ROOM_HOST=0.0.0.0`、`ROOM_PORT=25565`、`HTTP_HOST=127.0.0.1`、`HTTP_PORT=8080` |
| `services/game-api/config/room_config.json` | `ip_host` 为客户端可访问的 IPv4，`realtime_port` 必须对应实际 UDP 监听端口或公网映射端口 |

环境文件采用字面量 `KEY=VALUE`，支持空行、整行 `#` 注释和一层单/双引号。不要使用 `export`、行内注释、变量展开或 shell 命令；`MOVE_TARGETS` 可直接填写 JSON。进程中已有的同名环境变量优先。示例不设置 `LOG_PRETTY`，因为当前实现会把非空字符串 `false` 也转换为真。

Node 管理 HTTP 默认仅本机可访问，检查地址为 `http://127.0.0.1:8080/health`。脚本示例默认关闭原始玩家包落盘（`ROOM_PACKET_JOURNAL=0`），需要时自行修改。

Python 端口被占用时，现有实现会向后尝试 30 个端口，以日志中的 `SKY_LISTEN_PORT` 为准。`SKY_HOST=::` 可选择 IPv6/IPv4 双栈，但需要系统支持 IPv6。未通过脚本启动时默认仍为 `::`。

客户端连接业务地址为 `https://<服务器地址>:<实际业务端口>`；防火墙需放行业务 TCP 与房间 UDP。不要把管理 HTTP 端口当作游戏连接端口。

## 验证范围

Windows 上验证脚本语法、环境文件解析和隔离端口的联机服务启动；Bash 脚本通过 Git Bash 检查与模拟启动。Linux 引导分支做隔离测试，尚未在真实 Linux 主机上完成 Python 业务服务、原生 ENet 安装与游戏联机验证。Windows 管理面板仍为独立 GUI，不属于 Linux 服务部署范围。
