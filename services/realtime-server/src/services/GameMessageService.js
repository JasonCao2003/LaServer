import { PacketIds } from '../protocol/PacketCodec.js';
import {
  GameMsgType,
  RELAY_GAME_MSG_TYPES,
  ALWAYS_RELAY_GAME_MSG_TYPES,
  EVENT_RELAY_GAME_MSG_TYPES,
  DROP_GAME_MSG_TYPES
} from '../protocol/packets/GameMsgPacket.js';
import { decodeNetLevelDataMsg, encodeNetLevelDataMsg } from '../protocol/types/NetLevelDataMsg.js';
import { behaviour } from '../config/behaviour.js';
import { effectiveFamilyOf, gameMsgTypeLimitOf, rpcIdLimitOf } from '../protocol/VersionProfile.js';
// 0.7.1 专属: 中继帧长度字段 u16(见 versions/sky071Engine.js 文件头第 2 段)。
import { isSky071Entity, sky071PlayerStateFrame } from '../protocol/versions/sky071Engine.js';

export class GameMessageService {
  constructor({ players, levels, broadcaster, logger }) {
    this.players = players;
    this.levels = levels;
    this.broadcaster = broadcaster;
    this.logger = logger;
    this.latestAcks = new Map();
    // type -> 上次记日志的时间戳(未识别 GameMsg 限速用)
    this.unknownGameMsgLog = new Map();
    // 累计统计: 每个 GameMsg type 收到多少包 + 最近一个包的样本(排查"某个类型
    // 到底是啥"用, 例如关卡内聊天/表情)。通过 /health 的 room.gameMsg 暴露。
    this.gameMsgCounts = new Map();
  }

  warn(meta, message) {
    if (typeof this.logger?.warn === 'function') {
      this.logger.warn(meta, message);
    } else {
      this.logger?.debug?.(meta, message);
    }
  }

  // 跨协议族转发闸门(见 config/behaviour.js 的 familyIsolation):
  // levelId 是内容 id, legacy 和 modern 客户端常常在同一张图上, 但两者的
  // level state / PlayerStateDelta 字节布局完全不同, 互相转发会导致对端断言崩溃
  // (2026-10-06 实测: 0.7.1 legacy 客户端收到 0.22.0 modern 的 2331 字节 level state
  //  后闪退; 0.13.4 客户端报 BinaryStream val out of range 20 in [0,16])。
  crossFamilyBlocked(source, target) {
    if (!behaviour.familyIsolation) {
      return false;
    }
    return effectiveFamilyOf(source) !== effectiveFamilyOf(target);
  }

  handle(session, payload) {
    const source = this.players.getBySession(session);
    if (!source) {
      return;
    }

    source.touch();

    this.countGameMsg(source, payload);

    if (payload.type === GameMsgType.NetRpc) {
      source.lastRpc = {
        bytes: payload.rpcData.length,
        updatedAt: Date.now()
      };
      this.forwardToLevel(source, session, {
        ...payload,
        sourcePlayer: source.id
      });
      return;
    }

    // 逐字节原样转发给同关卡其他玩家(不改内容)。覆盖所有版本的"关卡内事件"类型:
    // Critters(4) / Affinity(7) / MusicSync(15) / 选举提名(17) / 演出·观众类(19/20/21/22/24)。
    // 老客户端(0.6.5~0.12.x)只发其中的 4/15 等, 新客户端会发 19~24, 同一张表通吃。
    const relayEvent = ALWAYS_RELAY_GAME_MSG_TYPES.has(payload.type)
      || (EVENT_RELAY_GAME_MSG_TYPES.has(payload.type) && behaviour.relayEventGameMsg);
    if (relayEvent) {
      source.lastOpaqueGameMsg = {
        type: payload.type,
        bytes: payload.payloadBytes?.length ?? 0,
        updatedAt: Date.now()
      };
      this.forwardToLevel(source, session, {
        ...payload,
        sourcePlayer: source.id
      });
      return;
    }

    // 客户端遥测(Metrics 16): 只记录, 不转发。
    if (DROP_GAME_MSG_TYPES.has(payload.type)) {
      source.lastOpaqueGameMsg = {
        type: payload.type,
        bytes: payload.payloadBytes?.length ?? 0,
        updatedAt: Date.now()
      };
      this.logger?.debug?.({
        playerId: source.id,
        type: payload.type,
        bytes: payload.payloadBytes?.length ?? 0
      }, 'dropped client telemetry GameMsg');
      return;
    }

    if (payload.type === GameMsgType.PlayerStateDelta) {
      const decoded = source.playerDelta.read(payload.snapshot, payload.payload);
      if (!decoded.ok) {
        this.logger.debug({ playerId: source.id, reason: decoded.reason }, 'ignored player snapshot delta');
        return;
      }
      this.ackSnapshotRead(source, decoded);

      // 中继帧 = [源玩家 id][长度][PlayerState 原始字节], 但**长度字段按版本分开**:
      //   0.7.1 (及更老) 的 HandleGameMsg case 3 读的是 u16;
      //   0.12.x / 0.13.4 / 0.22+ 读的是 u32(一直如此)。
      // 用 u32 发给 0.7.1 时, 它按 u16 读会少吃 2 字节, 循环再把剩下的
      // "00 00" 当成下一条记录 -> 空流 PlayerState::Serialize -> 断言闪退
      // (现象: 第二个账号加入房间立刻闪退)。所以这里按**目标**逐包构造。
      const playerStateFrame = target => (isSky071Entity(target)
        ? sky071PlayerStateFrame(source.id, decoded.data)
        : this.playerStateFrame(source.id, decoded.data));

      this.forwardSnapshotToLevel(source, session, GameMsgType.PlayerStateDelta, decoded.data, {
        payloadForTarget: playerStateFrame
      });
      return;
    }

    if (payload.type === GameMsgType.NetLevelData) {
      if (!this.levels.isAuthority(source, source.levelId)) {
        this.warn({
          playerId: source.id,
          levelId: source.levelId,
          family: effectiveFamilyOf(source),
          authorityPlayerId: this.levels.getAuthority(source.levelId, effectiveFamilyOf(source))?.id
        }, 'ignored level data from non-authority player');
        return;
      }

      const decoded = source.levelDelta.read(payload.snapshot, payload.payload);
      if (!decoded.ok) {
        this.warn({
          playerId: source.id,
          levelId: source.levelId,
          reason: decoded.reason,
          sequence: payload.snapshot?.sequence,
          base: payload.snapshot?.base,
          payloadBytes: payload.payload?.length ?? 0
        }, 'ignored level snapshot delta');
        return;
      }
      this.ackSnapshotRead(source, decoded);

      this.logger.debug({
        playerId: source.id,
        levelId: source.levelId,
        dataBytes: decoded.data?.length ?? 0,
        dataHex: decoded.data?.toString('hex') ?? 'none'
      }, 'received NetLevelData raw');

      // 不透明模式(默认): 这 2331 字节是客户端自己打包的 level state, 不能按
      // "22 字节头 + 数据" 解析(旧代码解析后 trailingBytes != 0, 把每个包都丢了
      // -> 别人的留影/留言/世界状态同步不过来)。这里原样缓存 + 原样转发。
      // 关掉开关(ROOM_OPAQUE_LEVEL_DATA=0 或 POST /debug/behaviour)可回到旧行为。
      if (behaviour.opaqueLevelData) {
        this.handleOpaqueLevelData(source, session, decoded.data);
        return;
      }

      let levelDataMsg;
      try {
        // 按该玩家所属协议族的候选封装布局逐个尝试(见 VersionProfile.js):
        //   msg22  = 22 字节外层头 + NetLevelData(0.13.x~0.21.x 常见)
        //   raw    = 裸露的 NetLevelData(0.6.5~0.12.x)
        // 取第一个"恰好用完缓冲区"(trailingBytes===0)的解析结果; 若都不完美, 保留第一个
        // 能解析出的结果交给后面的 trailingBytes 诊断分支处理(与改动前行为一致)。
        const layouts = (source.profile?.levelDataLayouts ?? ['msg22'])
          .filter(layout => layout !== 'opaque');
        let fallback = null;
        levelDataMsg = null;
        for (const layout of layouts.length ? layouts : ['msg22']) {
          try {
            const candidate = decodeNetLevelDataMsg(decoded.data, {
              skipHeaderBytes: 0,
              maxDataBytes: 7500,
              layout
            });
            fallback = fallback ?? candidate;
            if (candidate.trailingBytes === 0) {
              levelDataMsg = candidate;
              break;
            }
          } catch {
            // 该布局解不出, 试下一个。
          }
        }
        if (!levelDataMsg) {
          levelDataMsg = fallback;
        }
        if (!levelDataMsg) {
          throw new Error('no level data layout matched');
        }
      } catch (error) {
        this.warn({
          err: error,
          errMessage: error.message,
          playerId: source.id,
          levelId: source.levelId,
          dataBytes: decoded.data?.length ?? 0,
          dataHex: decoded.data?.slice(0, 32).toString('hex') ?? 'none'
        }, 'ignored invalid level data stream');
        return;
      }

      if (levelDataMsg.trailingBytes !== 0) {
        const isAllZero = decoded.data.every(b => b === 0);
        if (isAllZero) {
          this.logger.debug({
            playerId: source.id,
            levelId: source.levelId,
            packetLevelId: levelDataMsg.levelId,
            trailingBytes: levelDataMsg.trailingBytes,
            dataLength: levelDataMsg.dataLength
          }, 'received empty level data snapshot (all zeros)');
          return;
        }
        this.warn({
          playerId: source.id,
          levelId: source.levelId,
          packetLevelId: levelDataMsg.levelId,
          electedPlayer: levelDataMsg.electedPlayer,
          trailingBytes: levelDataMsg.trailingBytes,
          dataLength: levelDataMsg.dataLength,
          levelHash: levelDataMsg.levelHash
        }, 'ignored level data stream with trailing bytes');
        return;
      }

      if (levelDataMsg.levelId === 0) {
        this.warn({
          playerId: source.id,
          levelId: source.levelId,
          packetLevelId: levelDataMsg.levelId,
          dataBytes: decoded.data?.length ?? 0,
          dataHex: decoded.data?.slice(0, 16).toString('hex') ?? 'none'
        }, 'ignored level data with invalid levelId (0)');
        return;
      }

      if (
        source.netVersion?.levelHash !== undefined &&
        levelDataMsg.levelHash !== 0 &&
        levelDataMsg.levelHash !== source.netVersion.levelHash
      ) {
        this.warn({
          playerId: source.id,
          levelId: source.levelId,
          packetLevelId: levelDataMsg.levelId,
          electedPlayer: levelDataMsg.electedPlayer,
          levelHash: levelDataMsg.levelHash,
          expectedLevelHash: source.netVersion.levelHash,
          dataBytes: decoded.data?.length ?? 0,
          dataHex: decoded.data?.slice(0, 32).toString('hex') ?? 'none'
        }, 'ignored level data with mismatched level hash');
        return;
      }

      const levelId = source.levelId;
      this.levels.saveLevelData(levelId, {
        family: effectiveFamilyOf(source),
        mergeState: levelDataMsg.mergeState ?? 0,
        unknown1: levelDataMsg.unknown1 ?? 0,
        unknown2: levelDataMsg.unknown2 ?? 0,
        unknown3: levelDataMsg.unknown3 ?? 0,
        levelHash: levelDataMsg.levelHash ?? 0,
        unknown4: levelDataMsg.unknown4 ?? 0,
        unknown5: levelDataMsg.unknown5 ?? 0,
        unknown6: levelDataMsg.unknown6 ?? 0,
        unknown7: levelDataMsg.unknown7 ?? 0,
        dataLength: levelDataMsg.dataLength ?? levelDataMsg.levelData?.length ?? 0,
        payload: levelDataMsg.levelData,
        updatedAt: Date.now(),
        authorityPlayerId: source.id
      });

      const outgoingData = Buffer.from(decoded.data);
      outgoingData[0] = source.id;
      const forwarded = this.forwardSnapshotToLevel(source, session, GameMsgType.NetLevelData, outgoingData, { levelId });
      this.logger.debug({
        playerId: source.id,
        levelId,
        packetLevelId: levelDataMsg.levelId,
        dataBytes: outgoingData.length,
        levelDataBytes: levelDataMsg.levelData?.length ?? 0,
        forwarded
      }, 'forwarded level data');
      return;
    }

    if (payload.type === GameMsgType.NetLevelDataElect) {
      this.forwardToLevel(source, session, {
        ...payload,
        sourcePlayer: source.id
      });
      return;
    }

    if (payload.type === GameMsgType.NetLevelDataHeartbeat) {
      if (payload.levelId !== source.levelId) {
        this.logger.debug({
          playerId: source.id,
          playerLevelId: source.levelId,
          heartbeatLevelId: payload.levelId
        }, 'ignored level heartbeat for non-current level');
        return;
      }

      // 心跳只补自己族的权威(同图另一族有没有权威与本玩家无关)。
      const family = effectiveFamilyOf(source);
      if (!this.levels.getAuthority(source.levelId, family)) {
        this.levels.setAuthority(source.levelId, source, { family, reason: 'heartbeat_no_authority' });
      }
      return;
    }

    if (payload.type === GameMsgType.NetLevelDataRevoke) {
      this.handleRevoke(source, session, payload);
      return;
    }

    if (payload.type === GameMsgType.NetLevelDataRevokeAck) {
      this.logger.debug({
        playerId: source.id,
        snapshot: payload.snapshot,
        payloadBytes: payload.payloadBytes?.length ?? 0
      }, 'received level data revoke ack');
      return;
    }

    if (payload.type === GameMsgType.SnapshotAck) {
      this.latestAcks.set(source.id, {
        playerAckSeq: payload.playerAckSeq,
        levelAckSeq: payload.levelAckSeq,
        updatedAt: Date.now()
      });
      source.playerDelta.ack(payload.playerAckSeq);
      source.levelDelta.ack(payload.levelAckSeq);
      return;
    }

    // ── 兜底: 上面没列举到的 GameMsg 类型 ──
    // 关卡内的聊天 / 表情 / 动作等走的就是这些类型。旧代码在这里直接"函数末尾
    // 自然返回" -> 既不记日志也不转发 -> 节点侧完全看不到, 消息永远发不出去,
    // 而且排查时日志里一片空白(找不到任何线索)。
    // 现在: 记一条限速日志(带 type / 字节数 / hex 头), 并原样转发给同关卡其他玩家。
    // GameMsgPacket.decode 的默认分支已经把剩余字节原样放进 payloadBytes,
    // encode 的默认分支也原样写回, 所以这里转发是**逐字节不变**的。
    this.handleUnknownGameMsg(source, session, payload);
  }

  countGameMsg(source, payload) {
    const type = payload?.type;
    const bytes = payload?.payloadBytes ?? payload?.rpcData ?? payload?.payload ?? null;
    const entry = this.gameMsgCounts.get(type) ?? {
      type,
      count: 0,
      firstSeenAt: Date.now(),
      lastSeenAt: 0,
      lastPlayerId: null,
      lastBytes: 0,
      lastHex: null
    };

    entry.count += 1;
    entry.lastSeenAt = Date.now();
    entry.lastPlayerId = source?.id ?? null;
    entry.lastBytes = bytes?.length ?? 0;
    if (bytes && bytes.length) {
      entry.lastHex = bytes.subarray(0, 48).toString('hex');
    }
    this.gameMsgCounts.set(type, entry);
  }

  // /health 里暴露的累计统计: 客户端到底发过哪些 GameMsg type(排查聊天/表情等)。
  gameMsgStats() {
    const known = Object.fromEntries(
      Object.entries(GameMsgType).map(([name, id]) => [id, name])
    );
    const types = {};
    for (const [type, entry] of [...this.gameMsgCounts.entries()].sort((a, b) => b[1].count - a[1].count)) {
      types[type] = {
        ...entry,
        name: known[type] ?? null,
        handled: known[type] !== undefined,
        relay: RELAY_GAME_MSG_TYPES.has(type),
        dropped: DROP_GAME_MSG_TYPES.has(type)
      };
    }
    return {
      total: [...this.gameMsgCounts.values()].reduce((sum, e) => sum + e.count, 0),
      types
    };
  }

  handleUnknownGameMsg(source, sourceSession, payload) {
    const type = payload?.type;
    const bytes = payload?.payloadBytes?.length ?? 0;
    const now = Date.now();

    const last = this.unknownGameMsgLog.get(type) ?? 0;
    const interval = behaviour.unknownGameMsgLogIntervalMs ?? 2000;
    if (now - last >= interval) {
      this.unknownGameMsgLog.set(type, now);
      this.logger?.info?.({
        playerId: source.id,
        levelId: source.levelId,
        gameMsgType: type,
        payloadBytes: bytes,
        payloadHex: payload?.payloadBytes?.slice(0, 32).toString('hex') ?? 'none',
        relay: behaviour.relayUnknownGameMsg
      }, 'unhandled GameMsg type');
    }

    if (!behaviour.relayUnknownGameMsg) {
      return 0;
    }

    return this.forwardToLevel(source, sourceSession, {
      ...payload,
      sourcePlayer: source.id
    });
  }

  // level data 当不透明状态包处理: 缓存 + 原样转发给同关卡其他玩家。
  // 客户端(0.13.x / 0.22.x)发的这个块里已经带了它自己的玩家 id / levelId,
  // 所以**不能**再改任何一个字节(旧代码的 outgoingData[0] = source.id 会把
  // 块首字节写坏 -> 收方校验不过, 直接丢掉)。
  handleOpaqueLevelData(source, sourceSession, state) {
    const blob = Buffer.from(state ?? Buffer.alloc(0));
    const levelId = source.levelId;

    const family = effectiveFamilyOf(source);
    this.levels.saveLevelData(levelId, {
      opaque: true,
      family,
      levelHash: source.netVersion?.levelHash ?? 0,
      dataLength: blob.length,
      payload: blob,
      updatedAt: Date.now(),
      authorityPlayerId: source.id
    });

    const forwarded = this.forwardSnapshotToLevel(
      source,
      sourceSession,
      GameMsgType.NetLevelData,
      blob,
      { levelId }
    );

    this.logger.debug({
      playerId: source.id,
      levelId,
      family,
      stateBytes: blob.length,
      forwarded,
      playersInLevel: this.players.listInLevel(levelId).length
    }, 'forwarded opaque level data');

    return forwarded;
  }

  handleRevoke(source, sourceSession, payload) {
    const levelId = payload.levelId ?? source.levelId;
    // Revoke 只影响本玩家所属协议族: 同图另一族的权威/玩家完全不动。
    const family = effectiveFamilyOf(source);
    const authority = this.levels.getAuthority(levelId, family);
    const levelPlayers = this.players.listInLevel(levelId, family);

    if (authority && authority !== source) {
      this.logger.debug({
        sourcePlayerId: source.id,
        levelId,
        family,
        authorityPlayerId: authority.id
      }, 'ignored revoke from non-authority player');
      this.sendRevokeAck(source, authority, levelId);
      return;
    }

    this.levels.revokeAuthority(levelId, { family, reason: 'client_revoke' });
    const next = levelPlayers.find(player => player !== source);

    if (next) {
      this.levels.setAuthority(levelId, next, {
        family,
        previousAuthorityPlayerId: source.id,
        reason: 'client_revoke_migration'
      });
      for (const player of levelPlayers) {
        this.sendElect(player, next, this.levels.loadLevelData(levelId, family));
      }
    } else {
      this.levels.revokeAuthority(levelId, { family, reason: 'client_revoke_level_empty' });
      this.levels.resetFamilyData(levelId, family);
    }

    this.forwardToLevel(source, sourceSession, {
      type: GameMsgType.NetLevelDataRevoke,
      sourcePlayer: source.id,
      levelChangeCount: source.levelChangeCount,
      levelId,
      playerId: source.id,
      reason: payload.reason ?? 0
    });

    this.sendRevokeAck(source, next ?? source, levelId);
  }

  sendRevoke(target, authority, levelId, reason = 0) {
    this.broadcaster.send(target.session, {
      id: PacketIds.GameMsg,
      payload: {
        type: GameMsgType.NetLevelDataRevoke,
        levelChangeCount: target.levelChangeCount,
        sourcePlayer: authority?.id ?? 0,
        playerId: authority?.id ?? 0,
        reason
      }
    });
  }

  sendRevokeAck(target, authority, levelId) {
    const targetFamily = effectiveFamilyOf(target);
    const levelPayload = this.createLevelElectPayload(authority ?? target, this.levels.loadLevelData(levelId, targetFamily));
    const frame = target.levelDelta.write(levelPayload);

    if (!frame) {
      this.logger.debug({
        playerId: target.id,
        levelId
      }, 'skipped level data revoke ack because snapshot frame was too large');
      return;
    }

    if (frame.base === 0 && frame.sequence === 0) {
      this.logger.debug({
        playerId: target.id,
        levelId
      }, 'skipped level data revoke ack because writer stayed in raw recovery state');
      return;
    }

    this.broadcaster.send(target.session, {
      id: PacketIds.GameMsg,
      payload: {
        type: GameMsgType.NetLevelDataRevokeAck,
        levelChangeCount: target.levelChangeCount,
        sourcePlayer: authority?.id ?? 0,
        snapshot: {
          sequence: frame.sequence,
          base: frame.base,
          checksum: frame.checksum
        },
        payload: frame.payload
      }
    });
  }

  sendElect(target, authority, initialData = undefined) {
    // 走到这里就说明这位玩家已经拿到了(或即将拿到)Elect:
    //   - 取消入房时排的那次"延迟补发 Elect"(RoomServer.scheduleJoinElect),
    //     否则会先后收到两个 Elect(0.12.1 这类客户端自己会发 LevelUpdate 触发本函数,
    //     而重复的 Elect 会把客户端 level data 链的起点搞乱 —— 见 Player.changeLevel
    //     那段关于"链起点/关键帧"的实测注释);
    //   - 置 electSent, 让延迟定时器即使先醒来也不会再补一次。
    if (target) {
      if (target.joinElectTimer) {
        clearTimeout(target.joinElectTimer);
        target.joinElectTimer = null;
      }
      target.electSent = true;
    }

    // 绝不给不同协议族的客户端发别族的 Elect/level state(会直接崩):
    // 例如 legacy 客户端拿到 modern 的 level data 头会按 22 字节解析越界。
    if (target && authority && this.crossFamilyBlocked(authority, target)) {
      this.warn({
        targetPlayerId: target.id,
        targetFamily: effectiveFamilyOf(target),
        authorityPlayerId: authority.id,
        authorityFamily: effectiveFamilyOf(authority),
        levelId: authority.levelId
      }, 'skipped NetLevelDataElect: target family differs from authority family');
      return;
    }

    // 运行时开关: 关掉可定位"闪退是不是 Elect 引起的"(面板/HTTP 可随时改)。
    if (!behaviour.elect) {
      this.logger?.warn({
        targetPlayerId: target?.id,
        authorityPlayerId: authority?.id,
        levelId: authority?.levelId
      }, 'sendElect skipped: behaviour.elect = false');
      return;
    }

    const levelPayload = this.createLevelElectPayload(authority, initialData);
    const frame = target.levelDelta.write(levelPayload);

    if (!frame) {
      this.logger.debug({
        targetPlayerId: target.id,
        authorityPlayerId: authority?.id,
        levelId: authority?.levelId,
        levelPayloadLength: levelPayload?.length ?? 0
      }, 'sendElect skipped: frame too large');
      return;
    }

    this.logger.debug({
      targetPlayerId: target.id,
      authorityPlayerId: authority?.id,
      levelId: authority?.levelId,
      electedPlayer: authority?.id,
      hasInitialData: levelPayload?.hasInitialData,
      levelDataLength: levelPayload?.levelData?.length ?? 0,
      frameSeq: frame.sequence,
      frameBase: frame.base
    }, 'sending NetLevelDataElect');

    this.broadcaster.send(target.session, {
      id: PacketIds.GameMsg,
      payload: {
        type: GameMsgType.NetLevelDataElect,
        levelChangeCount: target.levelChangeCount,
        sourcePlayer: 0,
        snapshot: {
          sequence: frame.sequence,
          base: frame.base,
          checksum: frame.checksum
        },
        payload: frame.payload
      }
    });

    // Elect 之后立刻补一帧世界状态(见 behaviour.catchUpLevelData 的长注释):
    // 不透明模式下 Elect 带不了初始 level data, 刚进关的玩家在等权威方下一次
    // NetLevelData 转发之前是没有世界状态的 —— 那段空窗正是"房员先切图房主后切图
    // 会崩溃(断快照)"的地方。
    this.sendCachedLevelDataCatchUp(target, authority);
  }

  // 把缓存里最新的一份 level data 原样补发给 target(刚收到 Elect 的玩家)。
  //
  // 为什么是"原样转发 + 帧头重打", 而不是塞进 Elect:
  //   createLevelElectPayload 里 opaque 状态的块不能再用 22 字节头封装(客户端会按
  //   level data 结构解, 历史上报过 "Read past end of bit stream (size: 22)"), 所以
  //   不透明模式下 Elect 的 hasInitialData=false —— 世界状态只能另外发一帧。
  //
  // 帧头怎么打(照抄 forwardSnapshotToLevel 的做法): 用 **target 自己的** levelDelta
  // writer 打包, sourcePlayer 填缓存里的权威方。这里刻意**不** resetWriter:
  //   刚做完换图时 target.levelDelta 的 writer 是新的(resyncing=true, frameCount=0),
  //   Elect 那一帧已经占掉了 frameCount=0 这个关键帧格; 紧接着 write 得到的是
  //   SnapshotWriter.write 里 resyncing 分支的 (sequence=0, base=0) 裸块 —— 客户端
  //   reader 的 base===0 分支会收下它(见 SnapshotReader.read: base 0 走 save/raw),
  //   最差情况也只是被丢, 不会污染链(sequence=0 不进 history, 客户端 ack 0 会被
  //   服务端 ackSnapshotRead 的 !decoded.ack 挡掉)。
  //   反过来若在这里再 resetWriter() 逼一个关键帧, 新 writer 的 allocateSequence()
  //   会**重复分配**同一个序号(新 writer nextSequence 从 2 开始, 和 Elect 那帧撞号),
  //   客户端 find(base) 命中的是旧的那帧 -> delta 套错基准 -> 世界状态直接烂掉。
  //   宁可这帧可能白发, 也不能撞号。
  sendCachedLevelDataCatchUp(target, authority = undefined) {
    if (!behaviour.catchUpLevelData) {
      return false;
    }
    if (!target || !target.session) {
      return false;
    }

    const levelId = authority?.levelId ?? target.levelId;
    // 按 target 自己的协议族取缓存: 绝不把别族的 level state 补发给它。
    const targetFamily = effectiveFamilyOf(target);
    const cached = this.levels.loadLevelData(levelId, targetFamily);
    const blob = cached?.payload;
    if (!blob || blob.length === 0) {
      return false;
    }
    if (authority && this.crossFamilyBlocked(authority, target)) {
      return false;
    }
    // 这份状态本来就是这位玩家自己发上来的(它就是权威方) -> 不用补。
    if (cached.authorityPlayerId !== undefined && cached.authorityPlayerId === target.id) {
      return false;
    }

    const frame = target.levelDelta.write(Buffer.from(blob));
    if (!frame) {
      this.logger.debug({
        targetPlayerId: target.id,
        levelId,
        dataBytes: blob.length
      }, 'skipped level data catch-up because snapshot frame was too large');
      return false;
    }

    const sourcePlayer = cached.authorityPlayerId ?? authority?.id ?? 0;
    this.broadcaster.send(target.session, {
      id: PacketIds.GameMsg,
      payload: {
        type: GameMsgType.NetLevelData,
        levelChangeCount: target.levelChangeCount,
        sourcePlayer,
        snapshot: {
          sequence: frame.sequence,
          base: frame.base,
          checksum: frame.checksum
        },
        payload: frame.payload
      }
    });

    this.logger.info({
      targetPlayerId: target.id,
      sourcePlayer,
      levelId,
      family: targetFamily,
      dataBytes: blob.length,
      frameSeq: frame.sequence,
      frameBase: frame.base,
      opaque: Boolean(cached.opaque)
    }, 'sent cached level data catch-up after elect');

    return true;
  }

  forwardToLevel(source, sourceSession, payload) {
    let forwarded = 0;
    let skippedCrossFamily = 0;
    let skippedUnsupported = 0;
    const rpcId = payload.type === GameMsgType.NetRpc && payload.rpcData?.length
      ? payload.rpcData[0]
      : null;

    for (const target of this.players.listInLevel(source.levelId)) {
      if (target === source || target.session === sourceSession) {
        continue;
      }

      if (this.crossFamilyBlocked(source, target)) {
        skippedCrossFamily += 1;
        continue;
      }

      // 目标客户端能力兜底(见 VersionProfile.gameMsgTypeLimitOf):
      // 0.13.4 的 GameMsg 类型枚举只到 16、内嵌 RPC 枚举也只到 16, 收到 19~24
      // 或 RPC 17+ 会直接断言闪退("20 in [0, 16]")。宁可这一条不转发。
      if (typeof payload.type === 'number' && payload.type > gameMsgTypeLimitOf(target)) {
        skippedUnsupported += 1;
        continue;
      }
      if (rpcId !== null && rpcId > rpcIdLimitOf(target)) {
        skippedUnsupported += 1;
        continue;
      }

      const outgoing = {
        ...payload,
        levelChangeCount: target.levelChangeCount
      };

      this.broadcaster.send(target.session, {
        id: PacketIds.GameMsg,
        payload: outgoing
      });
      forwarded += 1;
    }

    if (skippedUnsupported > 0) {
      this.logger?.debug?.({
        sourcePlayerId: source.id,
        levelId: source.levelId,
        type: payload.type,
        rpcId,
        forwarded,
        skippedCrossFamily,
        skippedUnsupported
      }, 'skipped GameMsg relay: target version does not support this type/rpc id');
    }

    return forwarded;
  }

  // payloadForTarget(target) 可选: 按目标版本重新封装转发负载(0.7.1 的 u16 长度)。
  // 不传时用 data 原样(所有既有调用点行为不变)。
  forwardSnapshotToLevel(source, sourceSession, type, data, { levelId = source.levelId, payloadForTarget = null } = {}) {
    let forwarded = 0;
    let dropped = 0;
    let skippedCrossFamily = 0;

    for (const target of this.players.listInLevel(levelId)) {
      if (target === source || target.session === sourceSession) {
        continue;
      }

      // 快照字节布局按族而异: 别族客户端解不了(playerDelta/levelDelta 都是各族自己的
      // writer 封装的, 但内容字段/量化范围不同), 直接不发。
      if (this.crossFamilyBlocked(source, target)) {
        skippedCrossFamily += 1;
        continue;
      }

      const state = type === GameMsgType.PlayerStateDelta
        ? target.playerDelta
        : target.levelDelta;
      const outbound = payloadForTarget ? payloadForTarget(target) : data;
      const frame = state.write(outbound);

      if (!frame) {
        dropped += 1;
        this.warn({
          sourcePlayerId: source.id,
          targetPlayerId: target.id,
          levelId,
          type,
          dataBytes: data?.length ?? 0
        }, 'dropped snapshot forward because frame was too large');
        continue;
      }

      this.broadcaster.send(target.session, {
        id: PacketIds.GameMsg,
        payload: {
          type,
          levelChangeCount: target.levelChangeCount,
          sourcePlayer: source.id,
          snapshot: {
            sequence: frame.sequence,
            base: frame.base,
            checksum: frame.checksum
          },
          payload: frame.payload
        }
      });
      forwarded += 1;
    }

    if (type === GameMsgType.NetLevelData && forwarded === 0) {
      this.logger.debug({
        sourcePlayerId: source.id,
        levelId,
        family: effectiveFamilyOf(source),
        playersInLevel: this.players.listInLevel(levelId).map(player => player.id),
        dropped,
        skippedCrossFamily
      }, 'level data had no receiver in level');
    }

    return forwarded;
  }

  uint32Buffer(value) {
    const buffer = Buffer.allocUnsafe(4);
    buffer.writeUInt32LE(value >>> 0, 0);
    return buffer;
  }

  // 0.12.x / 0.13.4 / 0.22+ 的 PlayerStateDelta 中继帧:
  //   [sourcePlayerId u8][length u32 LE][PlayerState 原始字节]
  playerStateFrame(playerId, data) {
    return Buffer.concat([
      Buffer.from([playerId]),
      this.uint32Buffer(data.length),
      data
    ]);
  }

  ackSnapshotRead(target, decoded) {
    if (!decoded.ack) {
      return;
    }

    this.broadcaster.send(target.session, {
      id: PacketIds.GameMsg,
      payload: {
        type: GameMsgType.SnapshotAck,
        levelChangeCount: target.levelChangeCount,
        sourcePlayer: 0,
        playerAckSeq: target.playerDelta.latestSequence(),
        levelAckSeq: target.levelDelta.latestSequence()
      }
    });
  }

  createLevelElectPayload(authority, initialData) {
    const cached = this.levels.loadLevelData(authority.levelId, effectiveFamilyOf(authority));
    // 注意: RoomServer 调 sendElect 时会把 loadLevelData() 的整条记录当 initialData 传进来,
    // 所以 initialData.payload 也可能就是这个不透明块 —— 必须一起挡掉。
    const data = Buffer.from(initialData?.payload ?? initialData?.levelData ?? Buffer.alloc(0));
    // 不透明 level data(客户端打包后的 level state)不能再用 22 字节头重新封装 ——
    // 那样客户端按 22 字节头解出来是错的(历史上表现为 "Read past end of bit stream (size: 22)")。
    // Elect 仍照常发(hasInitialData=false, 空数据), 真正的世界状态由后续
    // NetLevelData 原样转发补上。
    const payload = cached?.opaque ? Buffer.alloc(0) : data;
    const encoded = encodeNetLevelDataMsg({
      electedPlayer: authority.id,
      levelId: authority.levelId,
      hasInitialData: payload.length > 0,
      levelData: payload,
      ...(cached ?? {})
    });

    // 末尾补 N 个 0x00(见 config/behaviour.js 的说明)。
    // 客户端解析 level data 头时, 在它自己的 length 字段位置读到的仍然是 0,
    // 于是"读到的数据长度 = 0", 不再越过缓冲区末尾。
    const pad = behaviour.padLevelData;
    if (pad > 0) {
      return Buffer.concat([encoded, Buffer.alloc(pad)]);
    }
    return encoded;
  }
}
