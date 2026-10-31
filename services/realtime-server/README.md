# LaServer 实时联机服务

- `src/`：服务实现。
- `docs/`：协议适配说明。
- `logs/`：运行、错误、调试和测试日志（包括历史轮换文件）。管理面板启动 UDP 时会自动创建此目录。
- `data/packets/`：按房间端口保存的 JSONL 收发包记录，供管理面板回看历史；服务自动创建目录。
- `tests/sky0134-regression.mjs`：协议回归检查脚本。
- `tools/analyze-sky0134-packets.mjs`：读取 `data/packets/_udp_packets_25566.jsonl` 的历史抓包分析脚本。

在本目录执行 `npm start` 启动，`npm run check` 检查入口语法。
直接执行 `npm start` 的控制台输出不会自动写入 `logs/`，需要时可自行重定向。

`ROOM_PACKET_JOURNAL` 可自定义抓包路径（相对于进程工作目录）；设置为 `0` 关闭磁盘记录。默认路径为 `data/packets/_udp_packets_<房间端口>.jsonl`，超过默认 256 MiB 时轮换为 `.1`。

日志与抓包可能包含玩家标识和原始协议内容，请勿公开。已有外部启动脚本若指定旧日志或抓包路径，需同步修改。

手动协议回归：`node tests/sky0134-regression.mjs`（当前有一条构建号限制断言与实现不一致）。历史抓包分析：`node tools/analyze-sky0134-packets.mjs`。
