import { PacketCodec, PacketIds } from '../protocol/PacketCodec.js';
import { ConnectionManager } from './ConnectionManager.js';
import { PlayerStore } from '../domain/PlayerStore.js';
import { LevelStateStore } from '../domain/LevelStateStore.js';
import { RoomDirectory } from '../domain/RoomDirectory.js';
import { EnetServer } from '../transport/EnetServer.js';
import { BroadcastService } from '../services/BroadcastService.js';
import { JoinService } from '../services/JoinService.js';
import { LevelService } from '../services/LevelService.js';
import { GameMessageService } from '../services/GameMessageService.js';
import { MoveService } from '../services/MoveService.js';
import { PacketDispatcher } from './PacketDispatcher.js';
import { TickLoop } from './TickLoop.js';
import { behaviour } from '../config/behaviour.js';
import { describeProfiles, describeMatrix, effectiveFamilyOf } from '../protocol/VersionProfile.js';
import { engineForEntity, LEGACY_ENGINE, MODERN_ENGINE } from '../protocol/versions/index.js';

export class RoomServer {
  constructor({ config, logger, metrics, packetLog = null }) {
    this.config = config;
    this.logger = logger;
    this.metrics = metrics;
    // UDP 联机监控: 真实收发的 ENet 包都记到这里, 由 GET /packets 暴露给面板。
    this.packetLog = packetLog;
    // 逐包 INFO 日志默认关闭(正常运行时一局下来几万行, 会把运行日志写爆)。
    // 排查"看不见对方 / 入房闪退"这类联机问题时设 ROOM_LOG_PACKETS=1 打开。
    this.logPackets = process.env.ROOM_LOG_PACKETS === '1';
    this.codec = new PacketCodec();
    this.sessions = new ConnectionManager({ roomId: config.id });
    this.players = new PlayerStore({ maxPlayers: config.maxPlayers });
    this.levels = new LevelStateStore({ roomId: config.id, logger });
    this.rooms = new RoomDirectory();
    this.transport = new EnetServer({
      host: config.host,
      port: config.port,
      maxPeers: config.maxPeers,
      channels: config.channels,
      logger
    });

    this.broadcaster = new BroadcastService({
      codec: this.codec,
      transport: this.transport,
      sessions: this.sessions,
      metrics,
      packetLog
    });

    // 入房瞬间是否主动下发 NetLevelDataElect。
    // 已核实参考实现(that-sky-colorsky-udp)只在 JoinGame 时广播 EnterGame,
    // Elect 仅由 server/packet/level_update.rs 在客户端"换关(LevelUpdate)"时下发。
    // 实测 0.12.1 客户端: 07:58:38.944 连上 -> 07:58:38.951 发 JoinGame -> 之后
    // 8 秒内再没发出任何包 -> 07:58:47 掉线(闪退)。也就是说它是因为入房后收到的
    // 东西而死的; 入房时服务端只发了 EnterGame + Elect, 而 Elect 是"多出来的那一个"。
    // 故默认关闭入房即 Elect, 需要回退旧行为设 ROOM_ELECT_ON_JOIN=1。
    // 这两个开关改成运行时读取(config/behaviour.js), 面板/HTTP 可随时改,
    // 不用重启进程就能在真机上对比"发 / 不发"两种策略。
    this.joinService = new JoinService({
      players: this.players,
      levels: this.levels,
      broadcaster: this.broadcaster,
      logger,
      metrics,
      behaviour,
      // 入房时缓存客户端版本号(见 services/JoinService.js)
      packetLog,
      onAuthorityChanged: (target, authority) => {
        if (behaviour.electOnJoin) {
          this.gameMessageService?.sendElect(target, authority, this.levels.loadLevelData(authority.levelId, effectiveFamilyOf(authority)));
          return;
        }
        // 默认路径: 不立刻 Elect(参考实现只在客户端 LevelUpdate 时 Elect), 但给
        // "自己不发 LevelUpdate 的老客户端"排一次延迟 Elect —— 见 scheduleJoinElect。
        this.scheduleJoinElect(target, authority);
      }
    });

    this.levelService = new LevelService({
      players: this.players,
      levels: this.levels,
      broadcaster: this.broadcaster,
      logger,
      onAuthorityChanged: (target, authority) => this.gameMessageService?.sendElect(target, authority, this.levels.loadLevelData(authority.levelId, effectiveFamilyOf(authority)))
    });

    this.gameMessageService = new GameMessageService({
      players: this.players,
      levels: this.levels,
      broadcaster: this.broadcaster,
      logger
    });

    this.moveService = new MoveService({
      players: this.players,
      rooms: this.rooms,
      broadcaster: this.broadcaster,
      transport: this.transport,
      logger
    });

    this.dispatcher = new PacketDispatcher({
      joinService: this.joinService,
      levelService: this.levelService,
      gameMessageService: this.gameMessageService,
      moveService: this.moveService,
      broadcaster: this.broadcaster,
      logger
    });

    for (const target of config.moveTargets) {
      this.rooms.register(target);
    }

    this.tickLoop = new TickLoop({
      tickRate: config.tickRate,
      logger,
      onTick: () => this.onTick()
    });
  }

  async start() {
    this.transport.on('peer:connect', peerId => this.onPeerConnect(peerId));
    this.transport.on('peer:message', (peerId, data) => this.onPeerMessage(peerId, data));
    this.transport.on('peer:disconnect', (peerId, data) => this.onPeerDisconnect(peerId, data));

    await this.transport.start();
    this.tickLoop.start();

    this.logger.info({ ...behaviour }, 'join behaviour configured');
  }

  async stop() {
    this.tickLoop.stop();
    await this.transport.stop();
  }

  onPeerConnect(peerId) {
    if (this.sessions.size >= this.config.maxPeers) {
      this.transport.disconnect(peerId, 'now');
      return;
    }

    const session = this.sessions.add(peerId);
    this.scheduleConnectNudge(session);
    session.joinTimer = setTimeout(() => {
      if (!session.player && !session.closed) {
        this.metrics.disconnects.inc({ reason: 'join_timeout' });
        // 连上了但 joinTimeoutMs 内没收到可解析的 JoinGame 就会被踢 —— 老客户端
        // 如果包头/负载解不出来, 表现就是"连上了又立刻掉线, 永远看不见别人"。
        this.logger.warn({
          peerId: peerId.toString(),
          sessionId: session.id,
          joinTimeoutMs: this.config.joinTimeoutMs
        }, 'peer dropped: no valid JoinGame within join timeout');
        this.transport.disconnect(peerId, 'later');
      }
    }, this.config.joinTimeoutMs);

    this.metrics.connectedPeers.set(this.sessions.size);
    // 提到 info: 客户端到底有没有连上 UDP 房间, 是"看不见对方"排查的第一现场。
    this.logger.info({ peerId: peerId.toString(), sessionId: session.id }, 'peer connected');
  }

  // 连上后 N 毫秒还没说话(既没发过任何包, 也还不是玩家)就主动补发一个 EnterGame。
  //
  // 为什么(实测 2026-10-07 15:18:20~15:18:59, _udp_runtime_8443.log):
  //   某个老客户端反复 "peer connected -> 恰好 1000ms(joinTimeoutMs) -> peer dropped:
  //   no valid JoinGame within join timeout -> peer disconnected", 期间服务端**一个
  //   客户端包都没收到**(日志里没有 client packet, 也没有 failed to process packet)。
  //   它在等服务端先说话; 服务端不说, 它就永远不发 JoinGame —— 用户看到的就是
  //   "0.7.1 能连上 UDP 但见不到人, UDP 包比别的版本少"。
  //   补发的 EnterGame 正是老引擎 NetRecvMsg case 5 那条(初始化快照读写器 +
  //   FriendBarn::NetRecvEnterGame), 也是它等的那个包; 之后客户端就会自己发
  //   JoinGame / LevelUpdate, 流程接上。见 PacketIds.js 的实证注释。
  // 只对"还没成为玩家且没发过包"的会话生效, 所以正常客户端(通常在 ~15ms 内就发
  // JoinGame)永远不会收到这个多余的包。
  scheduleConnectNudge(session) {
    const delay = behaviour.nudgeAfterConnectMs;
    if (!delay || delay <= 0) {
      return;
    }

    session.connectNudgeTimer = setTimeout(() => {
      session.connectNudgeTimer = null;
      // 这个回调绝不能抛: 它跑在裸 setTimeout 里, 抛出去就是 uncaughtException ->
      // 整个房间进程关停(之前这里引用了一个不存在的 peerId, 每次静默客户端连上
      // 350ms 后都会把服务端打死)。任何异常都只记一条日志。
      try {
        if (session.closed || session.player || session.packetsReceived > 0) {
          return;
        }

        // 只发给这一个还没说话的会话(它还没有 player, 不能用广播)。
        // 老引擎的"进游戏"是 id 5(见 PacketIds.js 的实证注释): 这个包会初始化它的
        // 快照读写器并把流程踢起来, 之后它就会自己发 JoinGame / LevelUpdate。
        // 前导字节由 PacketCodec 按引擎补上(legacyEnterGameLeadingByte)。
        //
        // 引擎选择: 静默 peer 还没发过任何包, 握手期推断不出族(它正是"等我们说话"
        // 的那种老客户端), 所以默认老引擎 id 5; 只有当它此前已经用新布局(token+id+seq)
        // 说过话、或人为强制了新族(ROOM_FORCE_PROFILE=modern)时才用新引擎 id 17,
        // 免得把 17 塞给老客户端(老引擎里 17 不是 EnterGame, 是另一件事)。
        const modernLayout = behaviour.versionProfile === 'modern'
          || session.layout === 'token+id+seq';
        const enterGameId = modernLayout ? PacketIds.EnterGame : PacketIds.EnterGameLegacy;
        const nudgeEngine = modernLayout ? MODERN_ENGINE : LEGACY_ENGINE;
        this.broadcaster.send(session, {
          id: enterGameId,
          payload: {
            // 名册同样只放同引擎的玩家(与 JoinService / BroadcastService.announceJoin 一致),
            // 免得给老客户端塞进新引擎玩家的记录变成看不见的幽灵。
            players: this.players.list()
              .filter(current => engineForEntity(current, { eraEnterGameId: behaviour.eraEnterGameId }) === nudgeEngine)
              .map(current => ({
                netId: current.id,
                uuid: current.uuid,
                name: current.name ?? '',
                levelId: current.levelId
              }))
          }
        });

        this.logger.info({
          peerId: session.peerId.toString(),
          sessionId: session.id,
          delayMs: delay,
          enterGamePacketId: enterGameId,
          inferredEngine: modernLayout ? 'modern' : 'legacy'
        }, 'nudged silent peer with EnterGame');
      } catch (error) {
        this.logger.error({
          sessionId: session.id,
          err: error?.stack ?? String(error)
        }, 'connect nudge failed (ignored)');
      }
    }, delay);

    // 纯补发用, 不该拖着事件循环
    session.connectNudgeTimer.unref?.();
  }

  onPeerMessage(peerId, data) {
    const session = this.sessions.get(peerId);
    if (!session || session.closed) {
      return;
    }

    const now = Date.now();
    session.packetsReceived = (session.packetsReceived ?? 0) + 1;
    session.markPacket(now);
    this.metrics.packetBytes.inc({ direction: 'client' }, data.length);

    if (data.length > this.config.maxPacketBytes) {
      this.rejectSessionPacket(session, 'packet_too_large');
      return;
    }

    try {
      const packet = this.codec.decodeClient(data);

      // 老客户端版本不支持的包(例如 0.12.1 的 MoveGame/GameMsg): 直接跳过,
      // 既不广播也不计入坏包 —— 否则累计 8 个包后客户端会被踢, 表现为"看不见对方"。
      if (packet.unsupported) {
        this.metrics.packetErrors.inc({ reason: 'unsupported_packet' });
        this.packetLog?.record({
          dir: 'in',
          peerId,
          playerId: session.player?.id ?? null,
          buffer: data,
          packetId: data.length ? data[0] : null,
          name: 'unsupported',
          note: `该客户端版本没有这个包(id=${data.length ? data[0] : '?'}), 已跳过`,
          ver: session.player?.versionLabel ?? null
        });
        this.logger.warn({
          peerId: peerId.toString(),
          seq: packet.sequence,
          rawLen: data.length,
          rawHex: data.subarray(0, 64).toString('hex')
        }, 'client packet skipped (unsupported)');
        return;
      }

      // 记下最近一次的 C->S 包头布局 / 是否宽容解析, 供入房时判定协议族
      // (见 protocol/VersionProfile.js + JoinService)。
      session.layout = packet.layout;
      if (packet.tolerant) {
        session.joinTolerant = true;
      }

      this.metrics.packets.inc({ direction: 'client', type: packet.name });
      // 真实入站包: 记进 UDP 联机监控(包头布局/是否宽容解析/原始字节一并带上)。
      this.packetLog?.record({
        dir: 'in',
        peerId,
        playerId: session.player?.id ?? null,
        buffer: data,
        packetId: packet.id,
        name: packet.name,
        note: packet.tolerant
          ? `宽容解析 (包头 ${packet.layout})`
          : `包头 ${packet.layout}`,
        ver: session.player?.versionLabel ?? null
      });
      // 记录每个"有语义"的客户端包(心跳 ping 不记, 否则刷屏)。这样下次实测时
      // 就能直接看到客户端发了什么、包头布局是哪一种、JoinGame 有没有被认出来。
      if (this.logPackets && packet.name !== 'NetTimePingPacket') {
        this.logger.info({
          peerId: peerId.toString(),
          id: packet.id,
          name: packet.name,
          layout: packet.layout,
          tolerant: packet.tolerant ?? false,
          len: data.length,
          head: Array.from(data.subarray(0, 8))
        }, 'client packet');
      }
      const endTimer = this.metrics.packetDuration.startTimer({ type: packet.name });
      try {
        this.dispatcher.dispatch(session, packet);
      } finally {
        endTimer();
      }
    } catch (error) {
      this.packetLog?.record({
        dir: 'in',
        peerId,
        playerId: session.player?.id ?? null,
        buffer: data,
        packetId: data.length ? data[0] : null,
        name: 'decode_error',
        note: `解包失败: ${error?.message ?? error}`,
        ver: session.player?.versionLabel ?? null
      });
      this.rejectSessionPacket(session, error.name ?? 'decode_error');
      this.logger.warn({
        err: error,
        peerId: peerId.toString(),
        // [诊断] 老版客户端(如 0.12.1)发来的原始字节, 用于确认真实包 id 与包头布局
        // (本服务假设 C->S 包头为 token:u32 + id:u8 + seq:u8)。仅记录, 不改变行为。
        rawLen: data.length,
        rawHex: data.subarray(0, 2100).toString('hex'),
        rawHead: Array.from(data.subarray(0, 8))
      }, 'failed to process packet');
    }
  }

  rejectSessionPacket(session, reason) {
    const count = session.registerBadPacket();
    this.metrics.packetErrors.inc({ reason });

    if (count >= this.config.badPacketLimit) {
      this.metrics.disconnects.inc({ reason: 'bad_packet_limit' });
      this.transport.disconnect(session.peerId, 'later');
    }
  }

  // 入房后延迟补发 Elect(开关见 config/behaviour.js 的 electAfterJoinMs)。
  //
  // 为什么需要: 有的老客户端**自己从来不发 LevelUpdate**, 而本服务(和参考实现)只在
  // 收到 LevelUpdate 时才 Elect(见下面 levelService 的 onAuthorityChanged), 于是这类
  // 客户端永远收不到 Elect。实测 0.7.1(读书郎 C30, 09:04:43 那一轮):
  //   09:04:43.891 连上 -> 09:04:43.892 JoinGame -> **之后一个包都不发** ->
  //   09:04:48.926 自己断开(ENet disconnect data=12); 09:05:17 起每 ~5.0s 一轮。
  // 对照收到过 Elect 的客户端(0.12.1 09:00:34 / 0.22.0 09:02:44 / 0.23.6 09:09:07):
  // 它们拿到 Elect 后 ~15ms 内就开始发 level data / 快照, 也就是说 Elect 正是它们在等
  // 的那个包。所以: 入房成为(或并入)某关的权威方后, 若 electAfterJoinMs 内客户端自己
  // 没触发过 Elect, 就补发一次。
  //
  // 只对 legacy 族(0.6.5~0.12.3)生效:
  //   - mqtt / modern 族客户端(0.13.4+)实测都会自己发 LevelUpdate, 行为保持原样;
  //   - legacy 族里会自己发 LevelUpdate 的(如 0.12.1)也没影响 —— sendElect 一执行就
  //     会把这个定时器清掉, 它们收到的 Elect 跟改动前是同一个(same seq/base)。
  scheduleJoinElect(target, authority) {
    const delay = behaviour.electAfterJoinMs;
    if (!delay || delay <= 0 || !target) {
      return;
    }
    if ((target.profile?.detectedFamily ?? null) !== 'legacy') {
      return;
    }
    if (target.joinElectTimer) {
      clearTimeout(target.joinElectTimer);
    }
    target.joinElectTimer = setTimeout(() => {
      target.joinElectTimer = null;
      if (target.electSent || target.session?.closed) {
        return;
      }
      // 用**当前**的权威方(可能已经不是入房时的那个), 与 LevelService 的语义一致。
      // 按 target 自己的协议族查(同图可能同时有另一族, 不能拿错族的权威)。
      const targetFamily = effectiveFamilyOf(target);
      const current = this.levels.getAuthority(target.levelId, targetFamily);
      if (!current) {
        return;
      }
      this.logger.info({
        playerId: target.id,
        levelId: target.levelId,
        family: targetFamily,
        delayMs: delay,
        protocolLabel: target.profile?.label ?? null,
        authorityPlayerId: current.id
      }, 'deferred elect after join (client did not send LevelUpdate)');
      this.gameMessageService?.sendElect(target, current, this.levels.loadLevelData(target.levelId, targetFamily));
    }, delay);
    // 纯补发用, 不该拖着事件循环
    target.joinElectTimer.unref?.();
  }

  onPeerDisconnect(peerId, data) {
    const session = this.sessions.remove(peerId);
    if (!session) {
      return;
    }

    const player = this.players.removeBySession(session);
    if (player) {
      if (player.joinElectTimer) {
        clearTimeout(player.joinElectTimer);
        player.joinElectTimer = null;
      }
      this.levelService.rebalanceAuthority(player.levelId, effectiveFamilyOf(player));
      this.broadcaster.broadcast({
        id: PacketIds.PlayerLeft,
        payload: {
          playerId: player.id,
          reason: 0
        }
      }, { playerOnly: true });
    }

    this.metrics.connectedPeers.set(this.sessions.size);
    this.metrics.onlinePlayers.set(this.players.size);
    this.metrics.disconnects.inc({ reason: String(data ?? 'normal') });

    this.logger.info({
      peerId: peerId.toString(),
      playerId: player?.id,
      uuid: player?.uuid?.toString(),
      levelId: player?.levelId,
      reason: String(data ?? 'normal')
    }, 'peer disconnected');
  }

  kickPlayer(playerId, reason = 0) {
    const player = this.players.getById(Number(playerId));
    if (!player) {
      return false;
    }

    this.broadcaster.send(player.session, {
      id: PacketIds.Disconnect,
      payload: {}
    });
    this.transport.disconnect(player.session.peerId, 'later', reason);
    return true;
  }

  broadcastRpc({ levelId, playerId = 0, rpcData }) {
    const data = Buffer.isBuffer(rpcData)
      ? rpcData
      : Buffer.from(String(rpcData ?? ''), 'utf8');

    for (const player of this.players.list()) {
      if (levelId !== undefined && player.levelId !== Number(levelId)) {
        continue;
      }

      this.broadcaster.send(player.session, {
        id: PacketIds.GameMsg,
        payload: {
          type: 2,
          levelChangeCount: player.levelChangeCount,
          sourcePlayer: 0,
          playerId,
          rpcData: data
        }
      });
    }
  }

  // 手动撤权威(管理接口用)。family 省略时对该关卡每一族各撤一次。
  revokeLevelAuthority(levelId, reason = 0, family = undefined) {
    const id = Number(levelId);
    const families = family !== undefined
      ? [family]
      : this.levelService.activeFamiliesInLevel(id)
        .filter(fam => this.levels.getAuthority(id, fam));

    if (!families.length) {
      return false;
    }

    let revoked = false;
    for (const fam of families) {
      const authority = this.levels.getAuthority(id, fam);
      if (!authority) {
        continue;
      }
      this.levels.revokeAuthority(id, { family: fam, reason: `server_revoke:${reason}` });
      for (const player of this.players.listInLevel(id, fam)) {
        this.gameMessageService.sendRevoke(player, authority, id, reason);
      }
      this.levelService.rebalanceAuthority(id, fam);
      revoked = true;
    }
    return revoked;
  }

  registerMoveTarget(room) {
    return this.rooms.register(room);
  }

  unregisterMoveTarget(roomId) {
    return this.rooms.unregister(roomId);
  }

  movePlayerToRoom(playerId, roomId = undefined) {
    const player = this.players.getById(Number(playerId));
    if (!player) {
      return { ok: false, reason: 'player_not_found' };
    }

    const target = roomId
      ? this.rooms.rooms.get(String(roomId))
      : this.rooms.findTarget({ levelId: player.levelId });

    if (!target) {
      this.moveService.cancelPlayerMove(player, 1);
      return { ok: false, reason: 'target_not_found' };
    }

    return {
      ok: this.moveService.movePlayer(player, target),
      targetRoomId: target.id
    };
  }

  moveLevelToRoom(levelId, roomId = undefined) {
    const target = roomId
      ? this.rooms.rooms.get(String(roomId))
      : this.rooms.findTarget({ levelId });

    if (!target) {
      return { ok: false, reason: 'target_not_found' };
    }

    return {
      ok: true,
      moved: this.moveService.moveLevel(levelId, target),
      targetRoomId: target.id
    };
  }

  onTick() {
    this.metrics.connectedPeers.set(this.sessions.size);
    this.metrics.onlinePlayers.set(this.players.size);
  }

  getStatus() {
    return {
      host: this.config.host,
      roomId: this.config.id,
      port: this.config.port,
      peers: this.sessions.size,
      players: this.players.size,
      tickRate: this.config.tickRate,
      limits: {
        maxPacketBytes: this.config.maxPacketBytes,
        badPacketLimit: this.config.badPacketLimit
      },
      moveTargets: this.rooms.list(),
      pendingMoves: this.moveService.listPending(),
      moveHistory: this.moveService.listHistory(),
      // 客户端发来的 GameMsg 累计统计(每个 type 收到多少包 + 样本 hex)。
      // 排查"关卡内聊天/表情到底走哪个 GameMsg type"用: GET /health
      gameMsg: this.gameMessageService?.gameMsgStats?.() ?? null,
      // 版本适配: 已知协议族 + 离线扫描(E:\Sky 74 个版本)覆盖情况 + 在线玩家的族分布。
      versionProfiles: describeProfiles(),
      versionMatrix: describeMatrix(),
      playerFamilies: this.playerFamilyCounts()
    };
  }

  // 当前在线玩家按协议族的分布(排查"某版本客户端有没有被认出来")。
  playerFamilyCounts() {
    const counts = {};
    for (const player of this.players.list()) {
      const family = player.profile?.detectedFamily ?? 'unknown';
      counts[family] = (counts[family] ?? 0) + 1;
    }
    return counts;
  }
}
