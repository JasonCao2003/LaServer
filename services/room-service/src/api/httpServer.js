import fastify from 'fastify';
import { behaviour } from '../config/behaviour.js';

export async function createHttpServer({ config, logger, room, metrics, packetLog }) {
  const app = fastify({
    loggerInstance: logger
  });

  app.get('/health', async () => ({
    ok: true,
    room: room.getStatus()
  }));

  app.get('/rooms', async () => room.getStatus());

  app.get('/move/targets', async () => ({
    targets: room.rooms.list()
  }));

  app.get('/move/pending', async () => ({
    moves: room.moveService.listPending()
  }));

  app.get('/players', async () => ({
    count: room.players.size,
    players: room.players.list().map(player => player.toApi())
  }));

  app.get('/players/:id', async (request, reply) => {
    const player = room.players.getById(Number(request.params.id));
    if (!player) {
      reply.code(404);
      return { ok: false, error: 'player_not_found' };
    }

    return player.toApi();
  });

  app.get('/levels', async () => ({
    levels: room.levels.toApi()
  }));

  // ── UDP 联机监控: 真实的 ENet 收发包 ──
  // GET /packets?since=<seq>   增量拉取(面板秒级轮询用, 只取新包)
  // GET /packets?all=1&since=0 拉取磁盘缓存里的全部包(面板打开监控时用, 含
  //                            打开窗口之前发生的包; 跨 node 重启的也在一起)
  // GET /packets/stats         计数器汇总
  // POST /packets/clear        清空当前窗口
  app.get('/packets', async (request) => {
    const query = request.query ?? {};
    const since = Number(query.since ?? 0) || 0;
    const versions = { ...packetLog.versions };
    if (query.all === '1' || query.all === 'true') {
      const limit = Number(query.limit ?? 0) || 0;
      // fullHistory() 会先把磁盘缓存回读进内存, 里面也包含上次运行留下的 join
      // 事件(客户端版本号), 所以 joins 要在它之后再取, 否则第一次请求是空的。
      const history = packetLog.fullHistory({ since, limit });
      return {
        since,
        seq: packetLog.seq,
        count: history.packets.length,
        total: history.total,
        dropped: history.dropped,
        fromDisk: history.fromDisk,
        all: true,
        versions,
        joins: packetLog.recentJoins(30),
        stats: packetLog.stats(),
        packets: history.packets
      };
    }
    return {
      since,
      seq: packetLog.seq,
      count: packetLog.entries.length,
      versions,
      joins: packetLog.recentJoins(30),
      packets: packetLog.listSince(since)
    };
  });

  app.get('/packets/stats', async () => packetLog.stats());

  app.post('/packets/clear', async () => ({ cleared: packetLog.clear() }));

  // ── 运行时行为开关(排查入房闪退用, 改完立刻生效, 不用重启) ──
  // GET  /debug/behaviour               看看当前值
  // POST /debug/behaviour  {"elect":false,"enterGameOnJoin":false,"padLevelData":0,...}
  app.get('/debug/behaviour', async () => ({ behaviour }));

  app.post('/debug/behaviour', async (request, reply) => {
    const body = request.body;
    if (!body || typeof body !== 'object') {
      reply.code(400);
      return { ok: false, error: 'body_must_be_json_object' };
    }

    for (const key of Object.keys(behaviour)) {
      if (!(key in body)) {
        continue;
      }
      if (typeof behaviour[key] === 'boolean') {
        behaviour[key] = Boolean(body[key]);
      } else if (typeof behaviour[key] === 'number') {
        const n = Number(body[key]);
        behaviour[key] = Number.isFinite(n) ? n : behaviour[key];
      }
    }

    logger?.info({ behaviour: { ...behaviour } }, 'behaviour updated via HTTP');
    return { ok: true, behaviour: { ...behaviour } };
  });

  app.get('/metrics', async (_request, reply) => {
    reply.header('content-type', metrics.registry.contentType);
    return metrics.registry.metrics();
  });

  return {
    app,
    listen: () => app.listen({ host: config.host, port: config.port }),
    close: () => app.close()
  };
}
