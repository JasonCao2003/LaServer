// ────────────────────────────────────────────────────────────────────────────
// Sky 0.7.1 (build 143371) 专属 UDP 引擎 —— 独立文件, 独立分支
//
// 只服务 0.7.1 (原包 0.7.1(143371).apk, libBootloader.so 里的
// SERVER_VERSION=252 / CLIENT_VERSION=477)。
// 不修改 legacyEngine.js / modernEngine.js / sky0134Engine.js / sky0155Engine.js
// / sky0122Engine.js 的任何一行, 也不改变它们发出去的字节。
//
// 反编译实证 (D:\工具\其他\ai\so_adapt\0.7.1\libBootloader.so.c):
//
// 1) JoinGameMsg::Serialize (L33866 附近, 0x9de434):
//      uuid16 | token32 | Move::SerializeWithoutSignature |
//      signature16 | serverVersion u16 | clientVersion u16 |
//      NetVersion(12) | levelChangeCount u8 | levelId u32 | ...
//    无 split 玩家时 serverVersion 落在 payload+82、clientVersion +84,
//    levelId 在 payload+99 (相对指纹起点 +17; 0.12.2 是 +21, 因为那边
//    中间多了一个 build u32)。
//
// 2) HandleGameMsg case 3 (L38424 附近, 0x9eba18), PlayerStateDelta 的
//    线上布局是:
//      PlayerId (u8, 见 PlayerId::Serialize 的 mode 1/2/3) |
//      length **u16** | PlayerState 原始字节
//    注意是 **u16** 长度, 而 0.12.x/0.13.x/0.22+ 的同一段是 u32。
//    实测(0.7.1 第二人入房后的第一条 type=3 包):
//      0e 69 01 03 01 01 02 00 62 | 01 | 5e 01 00 00 | <350B PlayerState>
//    客户端读 PlayerId=01, 长度 u16=0x015e=350, 数据 350B, 正好把
//    1+2+350=353 字节读完, **剩下 2 个 00 00** —— 于是循环再读一条
//    "玩家 0 的短包", 长度字段取自缓冲区外/垃圾, 随后 PlayerState::Serialize
//    在空流上断言 -> abort()。真机现象就是"第二个账号加入房间闪退"。
//    所以给 0.7.1 目标转发时必须用 u16 长度(见 playerStateRelayLengthBytes)。
//
// 3) EnterGame 走老引擎 id 5 + 1 字节前导 + 37 字节记录
//    (EnterGameMsg::Serialize L33877), PlayerJoined id 6 的负载就是一条
//    37 字节记录 (NetGameClient::Update 主连接 switch 里有 case 6)。
//    名册/加入通知与 LEGACY_ENGINE 完全同形, 但这里仍然单独写一份,
//    由 engineForEntity 的短路独立选中, 不与 legacy 互相影响。
// ────────────────────────────────────────────────────────────────────────────
import { PacketIds } from '../PacketIds.js';

export const SKY071_SERVER_VERSION = 252;   // 0x00fc
export const SKY071_CLIENT_VERSION = 477;   // 0x01dd
export const SKY071_BUILD = 143371;         // 0x2300b (APK versionCode)

// levelId 相对 "serverVersion u16" 起点的偏移:
//   +0 serverVersion, +2 clientVersion, +4 NetVersion(12),
//   +16 levelChangeCount, +17 levelId u32
const LEVEL_ID_FROM_FINGERPRINT = 17;

function fingerprintAt(payload, offset) {
  if (offset + 4 > payload.length) {
    return null;
  }
  if (payload.readUInt16LE(offset) !== SKY071_SERVER_VERSION
    || payload.readUInt16LE(offset + 2) !== SKY071_CLIENT_VERSION) {
    return null;
  }
  if (offset + LEVEL_ID_FROM_FINGERPRINT + 4 > payload.length) {
    return null;
  }
  return {
    offset,
    levelId: payload.readUInt32LE(offset + LEVEL_ID_FROM_FINGERPRINT)
  };
}

// 宽容解析 (2 字节包头) 时读取 0.7.1 JoinGame 的指纹 + 真实 levelId。
// 正常入房 Move.players = 0, 常量固定在 +82; 若客户端带着 split 玩家,
// Move 的记录会整体后移, 这里在头部窗口内找精确的 252/477 指纹
// (0.12.2/0.13.4/0.15.5 的指纹都是别的数值, 不会命中)。
export function sky071JoinInfo(payload) {
  if (!payload || payload.length < 103) {
    return null;
  }

  const direct = fingerprintAt(payload, 82);
  if (direct) {
    return { variant: String(SKY071_BUILD), levelIdOffset: 82 + LEVEL_ID_FROM_FINGERPRINT, levelId: direct.levelId };
  }

  // 带 split 玩家时的兜底: 只在 Move 区(80..400)里找指纹, 不碰包尾的
  // 0x800 全零块, 避免把随机字节误判成版本号。
  const limit = Math.min(payload.length - (LEVEL_ID_FROM_FINGERPRINT + 4), 400);
  for (let offset = 80; offset <= limit; offset += 1) {
    const hit = fingerprintAt(payload, offset);
    if (hit) {
      return {
        variant: String(SKY071_BUILD),
        levelIdOffset: offset + LEVEL_ID_FROM_FINGERPRINT,
        levelId: hit.levelId
      };
    }
  }
  return null;
}

export function isSky071TolerantJoin(payload) {
  return sky071JoinInfo(payload) !== null;
}

// 0.7.1 的引擎数据 (与 LEGACY_ENGINE / MODERN_ENGINE / SKY0134_ENGINE /
// SKY0122_ENGINE / SKY0155_ENGINE 平级, 互不引用):
//   enterGamePacketIds: [5]            —— pending 连接只认 case 5
//   enterGameLeadingByte: true         —— EnterGameMsg 前导字节
//   announceJoinWith: 'PlayerJoined'   —— 主连接 case 6, 不能再喂 id 5 (断言 abort)
//   playerStateRelayLengthBytes: 2     —— HandleGameMsg case 3 的 u16 长度(见文件头 2)
export const SKY071_ENGINE = Object.freeze({
  id: 'sky071',
  label: 'Sky 0.7.1 (252/477) — isolated engine',
  enterGamePacketIds: Object.freeze([PacketIds.EnterGameLegacy]),
  enterGameLeadingByte: true,
  playerRecordBytes: 37,
  nameBytes: 16,
  announceJoinWith: 'PlayerJoined',
  playerJoinedPacketIds: Object.freeze([PacketIds.PlayerJoined]),
  playerLeftPacketIds: Object.freeze([PacketIds.PlayerLeft]),
  // GameMsg type=3 中继帧里长度字段的字节数(u16), 详见文件头第 2 段。
  playerStateRelayLengthBytes: 2
});

// 某玩家实体是否被标记为 0.7.1 (标记只由本文件的 sky071Profile 写入)。
export function isSky071Entity(entity) {
  return entity?.profile?.sky071 === true;
}

// 0.7.1 目标的中继帧: [sourcePlayerId u8][length u16 LE][PlayerState]
// 只在本文件里构造, 其它版本走 GameMessageService 原来的 u32 分支。
export function sky071PlayerStateFrame(playerId, data) {
  const header = Buffer.allocUnsafe(3);
  header.writeUInt8(playerId & 0xff, 0);
  header.writeUInt16LE(data.length & 0xffff, 1);
  return Buffer.concat([header, data]);
}

// 把一个"按握手推断出的 profile"包成 0.7.1 专属变体:
//   * 打上 sky071 标记, 供 engineForEntity / 中继帧选择使用
//   * effectiveFamily 改成独立键 'legacy-071'
//     —— 0.7.1 的 PlayerState / LevelData 序列化与 0.12.x/0.13.4+/0.22+ 都不同,
//        关卡权威、level data 缓存、GameMsg 快照转发必须与其它版本隔离,
//        否则同图不同版本会互发对方解不了的字节。
// payload 不带 0.7.1 指纹时原样返回, 其它版本走不到这里的分支。
export function sky071Profile(profile, payload) {
  if (!payload?.sky071) {
    return profile;
  }

  let variant = payload?.sky071Variant;
  if (!variant) {
    variant = String(SKY071_BUILD);
  }

  return {
    ...profile,
    id: 'sky071',
    label: `Sky 0.7.1 (${variant}) UDP (isolated)`,
    sky071: true,
    sky071Variant: variant,
    source: `join:sky071-${variant}`,
    confidence: 'high',
    effectiveFamily: 'legacy-071',
    enterGamePacketIds: SKY071_ENGINE.enterGamePacketIds
  };
}
