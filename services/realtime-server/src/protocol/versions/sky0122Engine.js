// ────────────────────────────────────────────────────────────────────────────
// Sky 0.12.2 (build 160278) 专属 UDP 引擎 —— 独立文件, 独立分支
//
// 只服务 0.12.1/0.12.2 (原包 sky-children-of-the-light-0-12-2-160278.apk,
// libBootloader.so 里的 SERVER_VERSION=275 / CLIENT_VERSION=516)。
// 不修改 legacyEngine.js / modernEngine.js / sky0134Engine.js / sky0155Engine.js
// 的任何一行, 也不改变它们发出去的字节。
//
// 反编译实证 (D:\工具\SO\libBootloader0122.so.c):
//   PendingServerMessageHandler 分发 sub_FBF3D8 (L2539564):
//     case 17 -> m_HandleEnterGame (L1301932, 0xA90FD0)
//     case 5  -> "Pending server Unknown message" (直接丢弃)
//   ActiveServerMessageHandler 分发 sub_FBF524 (L2539620):
//     case 17 -> m_HandlePlayerJoined (L1302596)
//     case 5  -> "Active server Unknown message"
//   SyncPlayerMsg::Serialize (L1305615, 0xA94D34):
//     BinaryStream::SerializeCompressed(count, 0, 8)   // 只占 1 字节低 4 bit
//     [netId u8][uuid 16][name 16][levelId u32] × count
//     —— 没有 legacy EnterGameMsg 的 1 字节前导
//   m_HandleEnterGame 里 SyncPlayerMsg 反序列化后按记录
//     netId=+4, uuid=+5, name=+21, levelId=+37 (步长 40 的内存对齐结构)
//     重建 NetPlayerBarn, 所以线上仍是 37 字节紧凑记录。
//   JoinGameMsg::Serialize (L1305290, 0xA94A68):
//     uuid16 | token32 | Move(18+16n) | signature16 |
//     serverVersion u16(+82) | clientVersion u16(+84) | build u32(+86) |
//     NetVersion12 | levelChangeCount u8(+102) | levelId u32(+103) | ...
//
// 因此 0.12.2 的“进游戏/玩家名册”必须是 **id 17 + [count][37 字节记录]**,
// 不是 legacy 的 id 5; 新人通知也只能重发 id 17 名册 (active 侧的 id 6
// PlayerJoined 不在分发表里, 不会被处理)。
//
// 识别方式: 只在 2 字节包头 (id+seq) 的宽容 JoinGame 路径上, 命中
//   +82 = 275, +84 = 516 才认; +86 的构建号优先认 160278 (本 APK),
// 同时兼容 SERVER/CLIENT 相同、构建号不同的 0.12.x 包 (例如 0.12.1)。
// ────────────────────────────────────────────────────────────────────────────
import { PacketIds } from '../PacketIds.js';

export const SKY0122_SERVER_VERSION = 275;   // 0x0113
export const SKY0122_CLIENT_VERSION = 516;   // 0x0204
export const SKY0122_BUILD = 160278;         // 0x27216 (APK versionCode)

// JoinGame 里 levelId 相对 “version 三连” 起点的偏移:
//   +0 serverVersion, +2 clientVersion, +4 build,
//   +8 NetVersion(12), +20 levelChangeCount, +21 levelId
const LEVEL_ID_FROM_FINGERPRINT = 21;

function fingerprintAt(payload, offset) {
  if (offset + 8 > payload.length) {
    return null;
  }
  const server = payload.readUInt16LE(offset);
  const client = payload.readUInt16LE(offset + 2);
  const build = payload.readUInt32LE(offset + 4);
  if (server !== SKY0122_SERVER_VERSION || client !== SKY0122_CLIENT_VERSION) {
    return null;
  }
  if (build === SKY0122_BUILD) {
    return '160278';
  }
  // 同 SERVER/CLIENT 的 0.12.x 构建。只接受明显有效的非 0 构建号,
  // 避免把全 0 的垃圾负载误判成 0.12.2。
  if (build !== 0 && build !== 0xffffffff) {
    return String(build);
  }
  return null;
}

// 宽容解析 (2 字节包头) 时读取 0.12.2 JoinGame 的指纹 + 真实 levelId。
// 正常入房 Move.players = 0, 常量固定在 +82/+84; 若客户端正带着 split
// 玩家, Move 的记录会整体后移 16×count, 按 count 校正后再判定。
export function sky0122JoinInfo(payload) {
  if (!payload || payload.length < 104) {
    return null;
  }

  const playerCount = payload.readUInt32LE(62);
  if (playerCount > 0 && playerCount <= 8) {
    const offset = 82 + 16 * playerCount;
    const variant = fingerprintAt(payload, offset);
    if (variant && offset + LEVEL_ID_FROM_FINGERPRINT + 4 <= payload.length) {
      return {
        variant,
        movePlayers: playerCount,
        levelIdOffset: offset + LEVEL_ID_FROM_FINGERPRINT,
        levelId: payload.readUInt32LE(offset + LEVEL_ID_FROM_FINGERPRINT)
      };
    }
  }

  const variant = fingerprintAt(payload, 82);
  if (!variant || 82 + LEVEL_ID_FROM_FINGERPRINT + 4 > payload.length) {
    return null;
  }
  return {
    variant,
    movePlayers: 0,
    levelIdOffset: 82 + LEVEL_ID_FROM_FINGERPRINT,
    levelId: payload.readUInt32LE(82 + LEVEL_ID_FROM_FINGERPRINT)
  };
}

export function isSky0122TolerantJoin(payload) {
  return sky0122JoinInfo(payload) !== null;
}

// 0.12.2 的引擎数据 (与 LEGACY_ENGINE / MODERN_ENGINE / SKY0134_ENGINE 平级,
// 互不引用):
//   enterGamePacketIds: [17]     —— pending 连接只认 17; 5 是 Unknown message
//   enterGameLeadingByte: false  —— 负载就是 SyncPlayerMsg 的 [count][records]
//   announceJoinWith: 'EnterGame' + playerJoinedPacketIds: [] —— 重发整份名册
export const SKY0122_ENGINE = Object.freeze({
  id: 'sky0122',
  label: 'Sky 0.12.x (275/516) — isolated engine',
  enterGamePacketIds: Object.freeze([PacketIds.EnterGame]),
  enterGameLeadingByte: false,
  playerRecordBytes: 37,
  nameBytes: 16,
  announceJoinWith: 'EnterGame',
  playerJoinedPacketIds: Object.freeze([]),
  playerLeftPacketIds: Object.freeze([PacketIds.PlayerLeft])
});

// 某玩家实体是否被标记为 0.12.2 (标记只由本文件的 sky0122Profile 写入)。
export function isSky0122Entity(entity) {
  return entity?.profile?.sky0122 === true;
}

// 把一个“按握手推断出的 profile”包成 0.12.2 专属变体:
//   * 只把 EnterGame 外层 id 改成 17 (17 在其它 legacy 客户端里不是 EnterGame)
//   * 打上 sky0122 标记, 供 engineForEntity / 监控展示使用
//   * effectiveFamily 改成独立键 'legacy-0122'
//     —— 0.12.2 的 PlayerState / LevelData 序列化与 0.13.4+、0.7.x 都不同,
//        关卡权威、level data 缓存、GameMsg 快照转发必须与其它版本隔离,
//        否则同图不同版本会互发对方解不了的字节。
// payload 不带 0.12.2 指纹时原样返回, 其它版本走不到这里的分支。
export function sky0122Profile(profile, payload) {
  if (!payload?.sky0122) {
    return profile;
  }

  // JoinService 传进来的是 PacketCodec 已解包的对象 (不是 Buffer),
  // 指纹在解包时已经算好并放在 sky0122Variant; 这里不要再读字节。
  let variant = payload?.sky0122Variant;
  if (!variant && Buffer.isBuffer(payload)) {
    variant = sky0122JoinInfo(payload)?.variant ?? null;
  }
  if (!variant) {
    variant = String(SKY0122_BUILD);
  }

  return {
    ...profile,
    id: 'sky0122',
    label: `Sky 0.12.x (${variant}) UDP (isolated)`,
    sky0122: true,
    sky0122Variant: variant,
    source: `join:sky0122-${variant}`,
    confidence: 'high',
    effectiveFamily: 'legacy-0122',
    enterGamePacketIds: SKY0122_ENGINE.enterGamePacketIds
  };
}