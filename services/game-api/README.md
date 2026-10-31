# LaServer 游戏业务服务

## 功能目录

| 目录 | 功能 |
| --- | --- |
| `business/account/` | 账号、玩家、好友、背包及商店接口 |
| `business/service/` | 场景、留影、社交、消息与任务接口 |
| `business/admin/` | 管理接口 |
| `business/starwatch/` | 统计与认证接口 |
| `business/helpers/` | 共享业务逻辑及客户端版本适配 |
| `infrastructure/database/` | ORM 模型与数据库迁移 |
| `infrastructure/monitoring/` | 请求采集、中间件、共享队列及监控界面 |
| `infrastructure/transport/` | WebSocket 连接与消息广播 |
| `infrastructure/runtime/` | 热重载与统一 JSON 错误处理 |
| `infrastructure/responses/` | 静态响应分流代码 |
| `config/` | 服务与游戏规则配置 |
| `resources/images/` | 图片资源 |
| `resources/static_responses/` | 静态响应 JSON、清单与排除规则 |
| `var/data/instance/` | 主数据库、魔法配置及游戏状态 |
| `var/data/legacy/` | 原独立数据库，不与主库合并 |
| `var/data/monitor_exports/` | 监控导出与缓存 |
| `var/data/runtime/` | 面板 PID、隧道状态、端口及显示标记 |
| `var/logs/` | 服务、面板与隧道日志 |
| `var/certs/` | 证书与私钥 |
| `tools/` | 配置生成与维护脚本 |

根目录保留 `index.py`、`sky_boot.py`、`paths.py`、各平台启动脚本、环境变量文件和本说明。

## 路径与导入约定

`paths.py` 集中定义服务根目录、配置、资源、数据库、日志和证书位置；模块不应通过固定数量的父目录推测服务根目录。路径模块导入时不会创建目录或打开数据库。

- 业务导入：`business.account`、`business.helpers.*` 等。
- 数据模型：`infrastructure.database.models`。
- 基础设施：`infrastructure.monitoring.*`、`infrastructure.transport.*`、`infrastructure.runtime.*`。
- 静态响应：代码在 `infrastructure.responses`，数据在 `resources/static_responses/`。

## 启动与配置

从项目根目录按 [启动文档](../../docs/STARTUP.md) 执行；原 `sky_boot.py` 启动入口保持不变。

服务配置示例为 `config/server.example.json`，本机文件为 `config/server.json`。房间配置仍为 `config/room_config.json`。证书文件为 `var/certs/fullchain.pem` 和 `var/certs/privkey.pem`。

独立打开监控界面：在本目录执行 `python -m infrastructure.monitoring.request_monitor_gui`。从项目根目录也可执行 `python services/game-api/infrastructure/monitoring/request_monitor_gui.py`。

本次目录迁移需要重启进程，不能仅在旧进程中热重载。外部脚本若硬编码原来的数据库、证书或源码路径，需同步更新。管理面板已使用新目录扫描源码并访问运行文件。

`var/` 整体被 Git 忽略。不要提交数据库、私钥、监控历史或隧道状态。维护脚本仍依赖外部数据源，执行前检查输入路径。

## 验证

从项目根目录使用准备好的 Python 环境执行：

```powershell
python -m unittest discover -s tests -v
```

目录迁移回归覆盖配置响应读取、静态响应资源定位、监控热重载队列、WebSocket 模块一致性和管理面板临时数据库。测试不启动监听服务，不修改生产数据库；完整客户端联机仍需另外验证。

迁移前代码副本及文件移动清单位于项目根目录 `bak/functional-layout-*/`。
