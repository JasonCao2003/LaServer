import { PacketIds } from '../PacketIds.js';
import { decodeMusicSyncEntries } from '../types/MusicSync.js';

// GameMsg 子类型。数值与各版本 libBootloader.so 一致(交叉验证自 0.6.5 ~ 0.34.3 的
// 参考实现: 十二融合服务器 src/packets/definitions/gameMsgPacket.js + Colorsky/Rust)。
// 老版本(如 0.6.5)只实现其中一部分类型, 但**数值不变**, 所以服务端可以按同一张表处理。
export const GameMsgType = Object.freeze({
  NetRpc: 2,
  PlayerStateDelta: 3,
  Critters: 4,
  Affinity: 7,
  NetLevelDataElect: 8,
  NetLevelDataRevoke: 9,
  NetLevelDataRevokeAck: 10,
  NetLevelData: 11,
  NetLevelDataHeartbeat: 12,
  SnapshotAck: 14,
  MusicSync: 15,
  Metrics: 16,
  NetLevelElectionNominee: 17,
  AudienceHint: 19,
  AudienceSocialBroadcast: 20,
  AudienceConsensusVote: 21,
  AudienceChat: 22,
  AudienceSpotlightReq: 24
});

// 需要"同关卡原样广播"的 GameMsg 类型。这些的载荷对服务端不透明, 逐字节转发即可
// (客户端按同房语义自行解释)。参考实现十二融合服务器以同样方式中继, 已在其支持的
// 0.6.5 / 0.13.4 / 0.32.2 上验证。
//
// 拆成两组:
//   ALWAYS : 一直转发(本服务原有行为: Critters 4 / MusicSync 15 / Nominee 17)。
//   EVENT  : 新增的关卡内事件/社交类(亲和 7 / 演出·观众 19~24)。老客户端(0.6.5~0.12.x)
//            不发这些类型; 新客户端的"聊天/表情/演出"走这里。默认转发, 可用
//            behaviour.relayEventGameMsg 关掉(若怀疑某版本因收到未知事件包而闪退)。
export const ALWAYS_RELAY_GAME_MSG_TYPES = new Set([
  GameMsgType.Critters,
  GameMsgType.MusicSync,
  GameMsgType.NetLevelElectionNominee
]);

export const EVENT_RELAY_GAME_MSG_TYPES = new Set([
  GameMsgType.Affinity,
  GameMsgType.AudienceHint,
  GameMsgType.AudienceSocialBroadcast,
  GameMsgType.AudienceConsensusVote,
  GameMsgType.AudienceChat,
  GameMsgType.AudienceSpotlightReq
]);

export const RELAY_GAME_MSG_TYPES = new Set([
  ...ALWAYS_RELAY_GAME_MSG_TYPES,
  ...EVENT_RELAY_GAME_MSG_TYPES
]);

// 只接收、记录、不转发(客户端遥测 Metrics 16)。
export const DROP_GAME_MSG_TYPES = new Set([GameMsgType.Metrics]);

function readSnapshotPayload(reader) {
  return {
    snapshot: {
      sequence: reader.readUInt8(),
      base: reader.readUInt8(),
      checksum: reader.readUInt8()
    },
    payload: reader.readBytes(reader.remaining)
  };
}

function writeHeader(writer, packet) {
  writer.writeUInt8(packet.type);
  writer.writeUInt8(packet.levelChangeCount ?? 1);
  writer.writeUInt8(packet.sourcePlayer ?? 0);
}

function writeSnapshotPayload(writer, packet) {
  writer.writeUInt8(packet.snapshot?.sequence ?? 0);
  writer.writeUInt8(packet.snapshot?.base ?? 0);
  writer.writeUInt8(packet.snapshot?.checksum ?? 0);
  writer.writeBytes(packet.payload ?? Buffer.alloc(0));
}

export class GameMsgPacket {
  static id = PacketIds.GameMsg;
  static name = 'GameMsgPacket';

  static decode(reader) {
    const type = reader.readUInt8();
    const levelChangeCount = reader.readUInt8();
    const sourcePlayer = reader.readUInt8();
    const base = { type, levelChangeCount, sourcePlayer };

    if (type === GameMsgType.NetRpc) {
      return {
        ...base,
        playerId: reader.readUInt8(),
        rpcData: reader.readBytes(reader.remaining)
      };
    }

    if (
      type === GameMsgType.PlayerStateDelta ||
      type === GameMsgType.NetLevelData ||
      type === GameMsgType.NetLevelDataElect
    ) {
      return {
        ...base,
        ...readSnapshotPayload(reader)
      };
    }

    if (type === GameMsgType.NetLevelDataRevoke) {
      const raw = reader.readBytes(reader.remaining);
      const parsed = raw.length >= 3
        ? {
            playerId: raw.readUInt8(0),
            reason: raw.readUInt16LE(1)
          }
        : {};

      return {
        ...base,
        ...parsed,
        payloadBytes: raw
      };
    }

    if (type === GameMsgType.NetLevelDataRevokeAck) {
      return {
        ...base,
        ...readSnapshotPayload(reader)
      };
    }

    if (type === GameMsgType.NetLevelDataHeartbeat) {
      return {
        ...base,
        ready: Boolean(reader.readUInt8()),
        netPlayerId: reader.readUInt8(),
        levelId: reader.readUInt32(),
        authorityVersion: reader.readUInt16()
      };
    }

    if (type === GameMsgType.SnapshotAck) {
      return {
        ...base,
        playerAckSeq: reader.readUInt8(),
        levelAckSeq: reader.readUInt8()
      };
    }

    if (type === GameMsgType.MusicSync) {
      const raw = reader.readBytes(reader.remaining);
      const music = decodeMusicSyncEntries(raw);
      return {
        ...base,
        payloadBytes: raw,
        entries: music.entries,
        trailingBytes: music.trailingBytes,
        complete: music.complete
      };
    }

    if (type === GameMsgType.NetLevelElectionNominee) {
      if (reader.remaining >= 6) {
        return {
          ...base,
          playerId: reader.readUInt32(),
          reason: reader.readUInt16()
        };
      }

      return {
        ...base,
        payloadBytes: reader.readBytes(reader.remaining)
      };
    }

    return {
      ...base,
      payloadBytes: reader.readBytes(reader.remaining)
    };
  }

  static encode(writer, packet) {
    writeHeader(writer, packet);

    if (packet.type === GameMsgType.NetRpc) {
      writer.writeUInt8(packet.playerId ?? 0);
      writer.writeBytes(packet.rpcData ?? Buffer.alloc(0));
      return;
    }

    if (
      packet.type === GameMsgType.PlayerStateDelta ||
      packet.type === GameMsgType.NetLevelData ||
      packet.type === GameMsgType.NetLevelDataElect
    ) {
      writeSnapshotPayload(writer, packet);
      return;
    }

    if (packet.type === GameMsgType.NetLevelDataRevoke) {
      if (packet.payloadBytes) {
        writer.writeBytes(packet.payloadBytes);
        return;
      }

      writer.writeUInt8(packet.playerId ?? packet.authorityPlayerId ?? 0);
      writer.writeUInt16(packet.reason ?? 0);
      return;
    }

    if (packet.type === GameMsgType.NetLevelDataRevokeAck) {
      writeSnapshotPayload(writer, packet);
      return;
    }

    if (packet.type === GameMsgType.NetLevelDataHeartbeat) {
      writer.writeUInt8(packet.ready ? 1 : 0);
      writer.writeUInt8(packet.netPlayerId ?? 0);
      writer.writeUInt32(packet.levelId ?? 0);
      writer.writeUInt16(packet.authorityVersion ?? packet.unknown3 ?? 0);
      return;
    }

    if (packet.type === GameMsgType.SnapshotAck) {
      writer.writeUInt8(packet.playerAckSeq ?? 0);
      writer.writeUInt8(packet.levelAckSeq ?? 0);
      return;
    }

    if (packet.type === GameMsgType.NetLevelElectionNominee) {
      if (packet.payloadBytes) {
        writer.writeBytes(packet.payloadBytes);
        return;
      }

      writer.writeUInt32(packet.playerId ?? 0);
      writer.writeUInt16(packet.reason ?? 0);
      return;
    }

    writer.writeBytes(packet.payloadBytes ?? Buffer.alloc(0));
  }
}
