# LaServer 游戏业务服务

## 功能目录

- `sky_boot.py`、`index.py`：Windows 启动引导及 Flask 应用初始化。
- `account/`、`service/`、`admin/`、`starwatch/`：各类接口。
- `helpers/`：业务辅助模块、版本适配、好友/货币/魔法、响应覆盖等共享逻辑。
- `database/`：数据库模型 `models.py` 与迁移代码。
- `data/instance/`：Flask 运行数据库 `users.db`、魔法配置、留影、场景及社交状态。
- `data/legacy/users.db`：原根目录数据库，供原独立 SQLAlchemy 引擎使用；与 Flask 数据库分别保留，未合并。
- `data/monitor_exports/`：HTTP 监控导出、接口缓存与保存标记。
- `certs/`：TLS 证书与私钥。
- `resources/static_responses/`：静态响应包、响应数据与分流索引。
- `resources/images/`：原 PNG 图片资源。
- `config/`、根目录 `config.json`：游戏与服务配置。
- `tools/`：配置生成与维护脚本，仍依赖脚本配置的外部数据源。
- `logs/`：面板、服务与隧道日志。
- 根目录监控、热重载、WebSocket 等模块：服务基础设施；隧道状态文件保留原位。

## 启动与维护

项目根目录执行：`python -u services/game-api/sky_boot.py`。
管理面板：`python apps/admin/admin_panel_client.py`。

业务辅助模块使用 `from helpers.xxx import ...`；模型使用 `from database.models import ...`。静态响应包为 `resources.static_responses`，热重载引用已同步。

Flask 的实例目录显式设置为绝对路径 `data/instance`；独立引擎使用 `data/legacy/users.db` 的绝对路径。管理面板、响应覆盖、监控与存档代码均指向同一 Flask 数据库位置。

此次调整需在下次启动进程时生效，不应在旧进程内仅热重载。外部启动脚本若硬编码旧数据库、证书或资源路径，需要同步修改。不要公开数据库、私钥或监控记录。

原始代码副本：`bak/sky-module-layout-before-20261031/`；移动清单：`bak/sky-module-layout-moves-20261031.csv`（均相对于项目根目录）。
