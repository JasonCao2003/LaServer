import { BinaryReader } from './binary/BinaryReader.js';
import { BinaryWriter } from './binary/BinaryWriter.js';
import { PacketIds } from './PacketIds.js';
import { TgcUuid } from './types/TgcUuid.js';
import { DisconnectPacket } from './packets/DisconnectPacket.js';
import { EnterGamePacket } from './packets/EnterGamePacket.js';
import { JoinGamePacket } from './packets/JoinGamePacket.js';
import { LevelUpdatePacket } from './packets/LevelUpdatePacket.js';
import { MoveGamePacket } from './packets/MoveGamePacket.js';
import { MoveResultPacket } from './packets/MoveResultPacket.js';
import { CancelMovePacket } from './packets/CancelMovePacket.js';
import { GameMsgPacket } from './packets/GameMsgPacket.js';
import { NetTimePingPacket } from './packets/NetTimePingPacket.js';
import { NetTimePongPacket } from './packets/NetTimePongPacket.js';
import { PlayerChangeLevelPacket } from './packets/PlayerChangeLevelPacket.js';
import { PlayerJoinedPacket } from './packets/PlayerJoinedPacket.js';
import { PlayerLeftPacket } from './packets/PlayerLeftPacket.js';
import { engineForEnterGamePacketId } from './versions/index.js';
import { isSky0155TolerantJoin } from './versions/sky0155Engine.js';
import { sky0134JoinInfo } from './versions/sky0134Engine.js';
import { sky0122JoinInfo } from './versions/sky0122Engine.js';
import { sky071JoinInfo } from './versions/sky071Engine.js';
import { behaviour } from '../config/behaviour.js';
import { ProtocolError } from '../utils/errors.js';

const packetTypes = new Map();

// 已知 id 的上界(用于把"老客户端发的、本服务未实现的合法 id"和真正的乱码区分开)。
const KNOWN_PACKET_ID_MAX = Math.max(...Object.values(PacketIds));

export function registerPacket(packetType) {
  packetTypes.set(packetType.id, packetType);
}

[
  DisconnectPacket,
  EnterGamePacket,
  JoinGamePacket,
  LevelUpdatePacket,
  MoveGamePacket,
  MoveResultPacket,
  CancelMovePacket,
  GameMsgPacket,
  NetTimePingPacket,
  NetTimePongPacket,
  PlayerChangeLevelPacket,
  PlayerJoinedPacket,
  PlayerLeftPacket
].forEach(registerPacket);

// C->S 包头候选布局。
// 标准布局(参考实现 readAsServer / 新客户端)为 token:u32 + id:u8 + seq:u8。
// 但老版本客户端(实测: 0.12.1 + 读书郎 C30)的包头是 id:u8 + seq:u8(无 token),
// 按标准布局解析出来的 id 是负载里的随机字节(实测 15/188), 于是每个包都被判
// "unknown client packet" 丢弃, 客户端永远无法成为房间 player -> 双开看不见对方。
// 这里依次尝试两种布局, 顺序上标准布局优先, 保证新客户端行为完全不变。
const CLIENT_HEADER_LAYOUTS = [
  {
    name: 'token+id+seq',
    headerLen: 6,
    read: reader => ({ token: reader.readUInt32(), id: reader.readUInt8(), sequence: reader.readUInt8() })
  },
  {
    name: 'id+seq',
    headerLen: 2,
    read: reader => ({ token: 0, id: reader.readUInt8(), sequence: reader.readUInt8() })
  }
];

// 老客户端(0.12.1)的 JoinGame 负载布局与新客户端不同, 严格解析必然失败。
// 宽容解析(与 that-sky-colorsky-udp 的做法一致):
//   uuid    = 负载前 16 字节
//   levelId = 负载最后 4 字节(小端)
// 已用真实抓包验证: uuid 与 users.db 中的账号 id 完全一致, levelId 与 7/3 正常的
// 联机日志中的 levelId(3526133726)一致。
//
// 2026-10-06 补: "负载最后 4 字节"**并不总是**关卡号。同一台 0.7.1(C30)客户端
// 不同次连接里, 前 256 字节逐字节相同, 末 4 字节却时而等于关卡号(3526133726)、
// 时而为 0 —— 为 0 的那几次, 服务端把玩家放进"关卡 0", 玩家 5 秒后自己断开
// (reason=12)并无限重连(见 session/RoomServer.js 的 scheduleJoinElect)。
// 而负载里的**关卡摘要块**是稳定的: <levelId:u32 LE><levelSummary 首字段 1.0f>。
// 所以末 4 字节解出 0 / 0xFFFFFFFF 时, 改成去负载里找第一个 00 00 80 3f, 取它
// 前面 4 字节当关卡号(0.7.1 与 0.23.6 两个老客户端的抓包都能这样解出真正关卡)。
const LEVEL_SUMMARY_SCALE_1_0 = Buffer.from([0x00, 0x00, 0x80, 0x3f]);

function recoverLevelIdFromSummary(payload) {
  const limit = Math.min(payload.length - 4, 512);
  for (let offset = 16; offset <= limit; offset += 1) {
    if (payload.compare(LEVEL_SUMMARY_SCALE_1_0, 0, 4, offset, offset + 4) !== 0) {
      continue;
    }
    const levelId = payload.readUInt32LE(offset - 4);
    if (levelId !== 0 && levelId !== 0xffffffff) {
      return { levelId, at: offset - 4 };
    }
  }
  return null;
}

function decodeJoinGameTolerant(buffer, headerLen) {
  const payload = buffer.subarray(headerLen);
  if (payload.length < 20) {
    return null;
  }
  const trailingLevelId = payload.readUInt32LE(payload.length - 4);
  let levelId = trailingLevelId;
  let levelIdSource = 'trailing';
  if (levelId === 0 || levelId === 0xffffffff) {
    const recovered = recoverLevelIdFromSummary(payload);
    if (recovered) {
      levelId = recovered.levelId;
      levelIdSource = `summary@${recovered.at}`;
    }
  }
  // 0.12.2 (SERVER_VERSION 275 / CLIENT_VERSION 516): 它的 JoinGame 末尾是
  // moveResult，而不是 levelId，不能沿用 trailing 猜测。命中指纹时直接按
  // JoinGameMsg::Serialize 的固定布局读 +103（有 split 玩家时 +16×count）。
  // 只有 0.12.2 会命中，其它版本的 levelId 解析一个字节都不变。
  const sky0122 = sky0122JoinInfo(payload);
  if (sky0122) {
    levelId = sky0122.levelId;
    levelIdSource = `sky0122@${sky0122.levelIdOffset}`;
  }
  // 0.13.4 家族(含 0.14.0 与重打包的 su-Sky 系): 指纹 + 真实 levelId。
  // 这一代的 JoinGame 末尾不是 levelId, 而 summary 启发式在自定义图
  // (SkyHub2 等, 摘要首字段不是 00 00 80 3f)上会失败, 所以命中指纹时按
  // 固定布局直读 +103(有 split 玩家时 +16×count)。其它版本这里恒为 null。
  const sky0134 = sky0134JoinInfo(payload);
  if (sky0134 && sky0134.levelId !== undefined
    && sky0134.levelId !== 0 && sky0134.levelId !== 0xffffffff) {
    levelId = sky0134.levelId;
    levelIdSource = `sky0134@${sky0134.levelIdOffset}`;
  }
  const sky0134Variant = sky0134?.variant ?? null;
  // 0.7.1 (SERVER_VERSION 252 / CLIENT_VERSION 477): levelId 固定落在 +99
  // (指纹起点 +82 之后的第 17 字节), 与 0.12.2 的 +103 不同。命中时直接按
  // JoinGameMsg::Serialize 的布局读, 不再依赖 trailing/summary 兜底。
  // 只有 0.7.1 会命中, 其它版本这一行恒为 null。
  const sky071 = sky071JoinInfo(payload);
  if (sky071) {
    levelId = sky071.levelId;
    levelIdSource = `sky071@${sky071.levelIdOffset}`;
  }
  return {
    uuid: new TgcUuid(payload.subarray(0, 16)),
    levelId,
    levelIdSource,
    levelIdTrailing: trailingLevelId,
    netVersion: null,
    tolerant: true,
    // 0.15.5 (179644) 专属标记(见 versions/sky0155Engine.js)。
    // 只读指纹, 不改变上面任何既有字段; 其它版本这里恒为 false。
    sky0155: isSky0155TolerantJoin(payload),
    // 0.13.4 家族专属标记(见 versions/sky0134Engine.js)。与上面互斥。
    sky0134: sky0134Variant !== null,
    sky0134Variant,
    // 关卡状态隔离键(见 versions/sky0134Engine.js 的 generation 注释)。
    sky0134Generation: sky0134?.generation ?? null,
    // 0.12.2 专属标记（见 versions/sky0122Engine.js）。与上面几个指纹互斥。
    sky0122: sky0122 !== null,
    sky0122Variant: sky0122?.variant ?? null,
    // 0.7.1 专属标记（见 versions/sky071Engine.js）。与上面几个指纹互斥。
    sky071: sky071 !== null,
    sky071Variant: sky071?.variant ?? null
  };
}

export class PacketCodec {
  decodeClient(buffer, { layout } = {}) {
    const [standard, legacy] = CLIENT_HEADER_LAYOUTS;
    let standardError = null;

    // A known two-byte JoinGame must win before trying the token header:
    // UUID bytes at offset 4 can otherwise look like a valid packet ID (even
    // Disconnect, whose decoder consumes no payload). MuMu 0.13.4 reproduces
    // this with a 1929-byte join incorrectly accepted as Disconnect.
    const legacyPayload = buffer.subarray(legacy.headerLen);
    if (layout !== standard.name && buffer[0] === PacketIds.JoinGame
      && (sky0122JoinInfo(legacyPayload)
        || sky071JoinInfo(legacyPayload)
        || sky0134JoinInfo(legacyPayload))) {
      return {
        id: PacketIds.JoinGame,
        name: packetTypes.get(PacketIds.JoinGame)?.name ?? 'JoinGamePacket',
        token: 0,
        sequence: buffer[1],
        layout: 'tolerant-join',
        payload: decodeJoinGameTolerant(buffer, legacy.headerLen),
        tolerant: true
      };
    }

    // ---- 1) 标准布局(token:u32 + id:u8 + seq:u8): 新客户端, 行为与改动前一致 ----
    let standardHead = null;
    if (layout !== 'id+seq' && layout !== 'tolerant-join') {
      try {
        const reader = new BinaryReader(buffer);
        standardHead = standard.read(reader);
        const packetType = packetTypes.get(standardHead.id);
        if (packetType?.decode) {
          const payload = packetType.decode(reader, { fromServer: false });
          return {
            id: standardHead.id,
            name: packetType.name,
            token: standardHead.token,
            sequence: standardHead.sequence,
            layout: standard.name,
            payload
          };
        }
      } catch (error) {
        standardError = error;   // 记下, 最后仍解不出来时原样抛出(保持原有严格性)
      }
    }

    // ---- 2) 老客户端布局(id:u8 + seq:u8): 实测 0.12.1 客户端就是这种包头 ----
    let legacyHead = null;
    try {
      const reader = new BinaryReader(buffer);
      legacyHead = legacy.read(reader);

      // 老客户端可能会发本服务没实现(但 id 在合法范围内)的包, 例如
      //   id 0 = ClientConnect, id 5 = 老引擎的 EnterGame 回执, id 16 = 未用。
      // 这类包不能当"坏包"累计(badPacketLimit=8 一满客户端就被踢, 表现就是
      // "能连上但见不到人"), 也不能抛异常 —— 直接当 unsupported 跳过。
      // 只有 id 落在已知范围之外才继续往下抛(那是真正的乱码/半包)。
      if (!packetTypes.has(legacyHead.id) && legacyHead.id <= KNOWN_PACKET_ID_MAX) {
        return {
          id: -1,
          name: 'unsupported',
          token: 0,
          sequence: legacyHead.sequence,
          layout: 'unsupported',
          payload: null,
          unsupported: true,
          unsupportedId: legacyHead.id
        };
      }

      const packetType = packetTypes.get(legacyHead.id);
      if (packetType?.decode) {
        try {
          const payload = packetType.decode(reader, { fromServer: false });
          // 额外要求"负载正好用完整个包"(允许少量尾随字节), 避免在错误偏移上
          // 碰巧撞到已知 id 而解出垃圾数据(会凭空造出假玩家)。
          if (reader.remaining <= 8) {
            return {
              id: legacyHead.id,
              name: packetType.name,
              token: 0,
              sequence: legacyHead.sequence,
              layout: legacy.name,
              payload
            };
          }
        } catch (error) {
          standardError = standardError ?? error;
        }

        // 老客户端的 JoinGame 负载布局与新客户端不同: 用宽容解析把它接进房间,
        // 否则客户端永远进不来(表现就是"看不见对方")。
        if (legacyHead.id === PacketIds.JoinGame) {
          const tolerant = decodeJoinGameTolerant(buffer, legacy.headerLen);
          if (tolerant) {
            return {
              id: PacketIds.JoinGame,
              name: packetType.name,
              token: 0,
              sequence: legacyHead.sequence,
              layout: 'tolerant-join',
              payload: tolerant,
              tolerant: true
            };
          }
        }

        // 认出了包 id 但负载解不开: 属于"该客户端版本不支持的包", 交给上层跳过,
        // 不能当坏包累计(否则 8 个包后客户端被踢 -> 表现就是"看不见对方")。
        return {
          id: -1,
          name: 'unsupported',
          token: 0,
          sequence: legacyHead.sequence,
          layout: 'unsupported',
          payload: null,
          unsupported: true
        };
      }
    } catch (error) {
      standardError = standardError ?? error;
    }

    throw standardError ?? new ProtocolError('unknown client packet', {
      id: buffer.length ? buffer[0] : -1
    });
  }

  encodeServer(packet) {
    // 老族(legacy/mqtt, 0.6.5~0.21.5)的"进游戏/玩家列表"外层 id 是 5(新族 0.22.0+ 是 17),
    // 两者负载完全一样(见 PacketIds.js 与 VersionProfile.enterGamePacketIdsFor)。
    // packetTypes 只按 EnterGamePacket.id(=17) 注册过编码器; 若不把 5 映射过去, 给老客户端
    // 发 EnterGame 会在这里抛 ProtocolError("unknown server packet", {id:5}), 把
    // JoinService.join 直接打断 -> 老客户端永远收不到玩家列表 -> 等不到包, 5~6 秒后被踢/
    // 自断 -> 无限重连。表现就是"0.7.1 / 0.12.1 能连上却见不到人"。
    const packetType = packetTypes.get(packet.id)
      ?? (packet.id === PacketIds.EnterGameLegacy
        ? packetTypes.get(PacketIds.EnterGame)
        : undefined);
    if (!packetType?.encode) {
      throw new ProtocolError('unknown server packet', { id: packet.id });
    }

    const payloadWriter = new BinaryWriter();
    // EnterGame 的负载布局按"引擎"分开(见 protocol/versions/*.js):
    //   老引擎(id 5, 0.6.5~0.21.5) -> 开头多 1 个前导字节;
    //   新引擎(id 17, 0.22.0+)   -> 没有前导字节(真机实证可用, 不动)。
    // 放在这里(而不是放在各个调用点上)是为了让**任何**用 id 5 发 EnterGame 的地方
    // 都不可能忘记这个字节。behaviour.legacyEnterGameLeadingByte=false 可回退。
    const engine = engineForEnterGamePacketId(packet.id);
    const leadingByte = engine?.enterGameLeadingByte === true
      && behaviour.legacyEnterGameLeadingByte !== false;
    packetType.encode(payloadWriter, packet.payload ?? {}, { fromServer: true, leadingByte });

    const payload = payloadWriter.toBuffer();
    const writer = new BinaryWriter(payload.length + 3);
    writer.writeUInt8(packet.id);
    writer.writeUInt16(payload.length);
    writer.writeBytes(payload);
    return writer.toBuffer();
  }
}

export { PacketIds };
