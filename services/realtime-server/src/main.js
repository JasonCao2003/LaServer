import path from 'node:path';
import { loadConfig } from './config/env.js';
import { createLogger } from './observability/logger.js';
import { createHttpServer } from './api/httpServer.js';
import { RoomServer } from './session/RoomServer.js';
import { createMetrics } from './observability/metrics.js';
import { PacketLog } from './observability/PacketLog.js';

const config = loadConfig(process.env);
const logger = createLogger(config.logging);
const metrics = createMetrics();
// UDP 联机监控的真实数据源(收发包环形缓冲)。
// limit: 面板一次最多展示多少条。2000 条在双开联机时只够覆盖 ~6 分钟,
// 排查"聊天/表情发不出去"时经常刚好把出问题的那几秒挤掉(实测: 2000 条里
// 最早的包是 09:22, 而聊天是更早发生的)。这里放宽到 8000, 每条最多 256 字节
// hex, 峰值内存约 10MB 量级, 对 node 完全无压力。
// 可用 ROOM_PACKET_LOG_LIMIT 覆盖。
const packetLog = new PacketLog({
  limit: Number.parseInt(process.env.ROOM_PACKET_LOG_LIMIT ?? '', 10) || 8000,
  // 磁盘缓存: 每个房间端口一个 JSONL(默认 data/packets/_udp_packets_<端口>.jsonl),
  // 每个包都追加落盘, 进程重启不丢; 面板打开监控时用 /packets?all=1 全量拉取。
  // ROOM_PACKET_JOURNAL=0 关闭; ROOM_PACKET_JOURNAL 可指定文件名/路径;
  // ROOM_PACKET_JOURNAL_MAX_MB / ROOM_PACKET_HISTORY_MB 可调上限与回读窗口。
  journalFile: process.env.ROOM_PACKET_JOURNAL === '0'
    ? null
    : path.resolve(process.cwd(), process.env.ROOM_PACKET_JOURNAL
        || `data/packets/_udp_packets_${config.room.port}.jsonl`),
  journalMaxBytes: (Number.parseInt(process.env.ROOM_PACKET_JOURNAL_MAX_MB ?? '', 10) || 0) * 1024 * 1024 || undefined,
  journalHistoryBytes: (Number.parseInt(process.env.ROOM_PACKET_HISTORY_MB ?? '', 10) || 0) * 1024 * 1024 || undefined,
  bootMeta: {
    enetPort: config.room.port,
    httpPort: config.http.port,
    roomId: config.room.id
  }
});

const room = new RoomServer({ config: config.room, logger, metrics, packetLog });
const api = await createHttpServer({ config: config.http, logger, room, metrics, packetLog });

let shuttingDown = false;

async function shutdown(signal) {
  if (shuttingDown) {
    return;
  }

  shuttingDown = true;
  logger.info({ signal }, 'shutdown requested');

  await Promise.allSettled([
    api.close(),
    room.stop()
  ]);
  packetLog.close();
}

process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));

process.on('uncaughtException', error => {
  logger.fatal({ err: error }, 'uncaught exception');
  void shutdown('uncaughtException').finally(() => process.exit(1));
});

process.on('unhandledRejection', reason => {
  logger.fatal({ err: reason }, 'unhandled rejection');
  void shutdown('unhandledRejection').finally(() => process.exit(1));
});

async function bootstrap() {
  logger.info({
    enet: `${config.room.host}:${config.room.port}`,
    http: `${config.http.host}:${config.http.port}`,
    roomId: config.room.id
  }, 'starting server');

  try {
    await room.start();
    logger.info({
      enet: `${config.room.host}:${config.room.port}`
    }, 'enet transport ready');

    await api.listen();
    logger.info({
      http: `${config.http.host}:${config.http.port}`
    }, 'http api ready');

    logger.info({
      enet: `${config.room.host}:${config.room.port}`,
      http: `${config.http.host}:${config.http.port}`
    }, 'server started');
  } catch (error) {
    logger.fatal({ err: error }, 'startup failed');
    await shutdown('startupFailed');
    process.exit(1);
  }
}

await bootstrap();
