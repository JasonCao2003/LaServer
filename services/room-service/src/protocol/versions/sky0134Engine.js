// ─────────────────────────────────────────────────────────────────────────────
// Sky 0.13.4 (build 169938) 专属 UDP 引擎 —— **独立文件, 独立分支**
//
// 只为 0.13.4 家族服务, 不改动 legacyEngine.js / modernEngine.js / sky0155Engine.js
// 的任何一行, 也不改变其它版本发出去的字节。判定与选择都在
// protocol/versions/index.js 里以 "先看 sky0134 标记" 的方式短路, 其余路径原样。
//
// 2026-10-18 追加 0.14.0 (build 172143)
// ------------------------------
// E:\Sky 原包里 0.14.0 只有一份 (sky-children-of-the-light-0140-172143-...apk),
// 它的协议特征与 0.13.4 **完全同族**, 静态证据:
//   1) C->S 包头序列化是同一份代码: 0.13.4 @0xB4B918 与 0.14.0 @0xB5D6BC 都是
//      strb(id → +0) + strb(seq → +1), 即 2 字节 id+seq 包头;
//   2) libBootloader.so 的协议标志位计数与 0.13.4 完全一致
//      (Sec-WebSocket-Protocol / MQTT 序列化 / NetLevelData 有, paho 无);
//   3) .rodata 里的版本常量记录同样是 "06 3f 0e + SERVER_VERSION + CLIENT_VERSION"
//      结构: 0.14.0 = 286/544 (0.13.4 = 284/541, 0.15.5 = 294/4852)。
// 因此它和 0.13.4 一样: 平台按 2 字节包头判成 legacy → 发 EnterGame(5),
// 而它的 pending 分发只认 17 → 名册永远是空的。这里只多认一组指纹,
// 169938/170388 的判定与字节一行不改, 其它版本也走不到这里。
//
// 为什么 0.13.4 需要单独一个引擎
// ------------------------------
// 版本矩阵把它归在 mqtt 族(0.13.4~0.21.5), 但它实际发的 C->S 包头是 2 字节
// (ClientToServerHeader::Serialize @ B4B918 = id:u8 + seq:u8), 于是被握手判成
// legacy, 我们过去按 legacy 给它发 EnterGame id 5 —— 它不认识(见下), 一个名册
// 都收不到, 所以"见不到人"。
//
// 0.13.4 的实际分发(反编译 libBootloader.so @ build 169938):
//   pending 连接: id 17 -> PendingServerMessageHandler::m_HandleEnterGame
//                   负载 = SyncPlayerMsg: [count:u8][netId u8][uuid 16][name 16][levelId u32]×count
//                   处理完再把 (5, payload) 转发给 NetRecvMsg, 由 case 5 初始化
//                   快照读写器 + FriendBarn::NetRecvEnterGame —— 所以**外层 id 必须是 17**,
//                   直接发 5 只会初始化快照, 名册是空的。
//   active  连接: id 17 -> ActiveServerMessageHandler::m_HandlePlayerJoined (@ B494CC)
//                   负载同样 = SyncPlayerMsg: [count:u8][netId u8][uuid 16][name 16][levelId u32]×count
//                   处理逻辑 = 名册同步: 名单里的玩家 AddPlayer/更新 netId 与 levelId,
//                   名单外且不在 Move 组里的玩家 RemovePlayer。
//                 id 7  PlayerLeft, id 8 PlayerChangedLevel, id 9 MoveGame, id 11 CancelMove;
//                 id 5/6 在 active 分发表里是 "Active server Unknown message"。
//
// 所以新人通知与 0.15.5 完全一致: **重发整份名单的 EnterGame(17, [count][records])**。
// (分发表已用 capstone 直接从 .so 的跳转字节表解出: pending 表 @0x1416bd6 -> 只有
//  id 0/1/2/17 有效, 17 跳 0x6767D0; active 表 @0x1416be8 -> id 17 跳 0x68AFB0 即
//  m_HandlePlayerJoined, id 5 跳 "Unknown message" 分支。)
//
// 识别方式(只认 0.13.4 家族: 国际版 169938、pwp 170388 与 0.14.0 172143,
// 见下方指纹表)
// ------------------------------
// JoinGameMsg::Serialize @ B4C3BC 的线上布局(已逐句核对):
//   uuid 16 | 32B | Move(18B + 16×players) | signature 16 | serverVersion u16 |
//   clientVersion u16 | rpcHash u32 | NetVersion 12 | levelChangeCount u8 | levelId u32 | ...
// Move 的 players 计数在正常入房时为 0, 所以
//   payload+82 u16 = SERVER_VERSION = 284, payload+84 u16 = CLIENT_VERSION = 541,
//   payload+86 u32 = 169938(构建号)
// 前两个是 .symtab 里的编译期常量(0x1333A04/0x1333A06 = 0x011C/0x021D, 二进制里
// 没有任何写入点)。已用真机抓包核对(0.13.4 JoinGame, 1933 字节:
//   1c01 1d02 d2970200 -> 284 / 541 / 169938;  +103 levelId = 3526133726;
//   +107 = 00 00 80 3f 关卡摘要首字段), 与 0.15.5(294/4852/179644) 字节层面可区分。
// 仅在宽容解析(2 字节包头)路径上判定, 不消费任何字节, 不影响 levelId 等既有
// 解析结果。
import { PacketIds } from '../PacketIds.js';

export const SKY0134_BUILD = 169938;
export const SKY0134_SERVER_VERSION = 284;   // 0x011C
export const SKY0134_CLIENT_VERSION = 541;   // 0x021D

// ── 0.13.4 底包的另一个构建: pwp 0.13.4 (170388) ────────────────────────────
// 独立于上面的 169938: 只多认一组指纹, 169938 的判定/字节一行不改。
// 已用真机抓包核对(D:\Sky pwp_0.13.4 (170388)_patched.apk, 2 个账号 53 次入房):
//   payload+82 u16 = 284, payload+84 u16 = **542**(169938 是 541),
//   payload+86 u32 = **354648**(169938 是 169938)。
// 该包 libBootloader.so 里 JoinGame 版本常量也正好是 1c01 1e02(284/542),
// 分发函数(m_HandleEnterGame / m_HandlePlayerJoined / m_HandlePlayerChangedLevel /
// GetPlayerIdsInLocalLevel / SyncPlayerMsg)与 169938 同一套, 所以引擎/名册格式完全相同;
// 之前只认 541/169938, 它被当成 legacy → 发 EnterGame(5) → 客户端不认 → 见不到人。
export const SKY0134_PWP_BUILD = 354648;
export const SKY0134_PWP_CLIENT_VERSION = 542;

// ── 0.14.0 (build 172143) ───────────────────────────────────────────────────
// 同一套 2 字节包头 + 新分发的客户端, 只有 JoinGame 里的版本常量不同。
// 来源: E:\Sky\原包\sky-children-of-the-light-0140-172143-arm64-v8a-...apk
//   lib/arm64-v8a/libBootloader.so  .rodata 的版本常量记录 (286 / 544)。
export const SKY0140_SERVER_VERSION = 286;   // 0x011E
export const SKY0140_CLIENT_VERSION = 544;   // 0x0220
export const SKY0140_BUILD = 172143;

const SKY0134_FINGERPRINTS = Object.freeze([
  Object.freeze({ variant: '169938', server: SKY0134_SERVER_VERSION, client: SKY0134_CLIENT_VERSION, build: SKY0134_BUILD }),
  Object.freeze({ variant: '170388', server: SKY0134_SERVER_VERSION, client: SKY0134_PWP_CLIENT_VERSION, build: SKY0134_PWP_BUILD }),
  Object.freeze({ variant: '172143', server: SKY0140_SERVER_VERSION, client: SKY0140_CLIENT_VERSION, build: SKY0140_BUILD })
]);

// ── 0.13.x ~ 0.15.x 通用窗口(同一代 2 字节包头 + 新分发器) ─────────────────
// 实测 .rodata 版本常量(serverVersion/clientVersion):
//   0.11.0 / 0.12.1 / 0.12.2 = 275/520, 275/516, 275/516   (老分发, EnterGame=5)
//   0.13.4 = 284/541, 0.14.0 = 286/544, 0.15.5 = 294/4852  (新分发, EnterGame=17)
//   0.18.0 = 304/4888                                     (已改 6 字节 token 包头)
// 所以在"2 字节包头"的宽容路径上, server ∈ [284, 294] 就是这一代, 与具体构建号无关:
// 没有单独指纹的 0.13.x / 0.14.x / 0.15.x 构建也能自动落到同一个引擎, 而
// 0.11/0.12(≤275) 和 0.18+(≥304) 都不会命中。构建号上下界再收一道, 防止巧合。
// 0.16/0.17 没有 APK 样本, 无法确认分发行为, 这里不猜测(仍走原逻辑)。
//
// 区间两端实测(取 E:\Sky 里该范围最小/最大的两个 APK 直接对比 .so):
//   最小 0.13.4 (169938)  最大 0.15.5 (179644)
//   1) C->S 2 字节包头序列化代码逐字节相同: 0.13.4 @0xB4B918 的 32 字节前缀
//      在 0.15.5 @0x7463C4/0x746400/0x746438 命中 3 次(0.14.0 也是 3 次);
//   2) pending EnterGame(17) 处理函数逐字节相同: 0.15.5 @0x743424 的 48 字节前缀
//      在 0.13.4 @0xB48924 命中;
//   3) active 名册同步(m_HandlePlayerJoined)逐字节相同: 0.15.5 @0x743FC0 的 48 字节
//      前缀在 0.13.4 @0xB494CC 命中;
//   4) 版本常量记录结构相同(284/541、294/4852), JoinGame 里位置同为 +82/+84/+86。
//   对照 0.18.0: 同样搜不到 2 字节包头序列化代码(它已改 6 字节 token 包头),
//   所以这一代的上界就落在 0.15.5 与 0.18.0 之间。
const SKY_ERA_SERVER_MIN = 284;
const SKY_ERA_SERVER_MAX = 294;
const SKY_ERA_CLIENT_MIN = 500;      // 0.13.4=541, 0.14.0=544, 0.15.5=4852
const SKY_ERA_CLIENT_MAX = 9999;
export const SKY_ERA_VARIANT = 'era-0.1x';

// ── 魔改/重打包包(versionCode 被改成 999999 之类) ───────────────────────────
// D:\Sky\Sky：）原包 里的 su-Sky 2.0 / 2.11 / 2.11登录修复 / 千二改 /
// Sky_)0.13.4.3(170388) 的 libBootloader.so 完全相同(24,703,400 B,
// crc 6287343d, .rodata 版本记录 284/542), 即都是 0.13.4 那一代的 .so;
// 但它们的 AndroidManifest versionCode 统一被改成 999999。
// pwp(170388) 的真机抓包已经证明 JoinGame +86 的构建号跟着 APK 的
// versionCode 走(354648), 而不是 .so 里编译进去的那个数(169938/172143 在
// .so 里仍然能找到, 但不上线)。所以这些魔改包发上来的三连是
// 284/542/999999 —— 既不在精确指纹表里, 也不在原来 165000~185000 的构建号
// 窗口里, 结果被当成 legacy 发 EnterGame(5), 客户端不认, 表现为
// **同地图看不见其他玩家**(SkyHub2 里最明显, 因为那张图同时还有 levelId 的问题)。
// 修法跟 sky0122 对 0.12.x 的做法一致: 只要 SERVER/CLIENT 对上这一代的
// 已知组合, 构建号只做"明显有效"校验(非 0、非 0xffffffff), 不再限区间。

// JoinGame 里 levelId 相对 "version 三连" 起点的偏移:
//   +0 serverVersion, +2 clientVersion, +4 build,
//   +8 NetVersion(12), +20 levelChangeCount, +21 levelId
// 对 0.13.4 家族(含 su-Sky 魔改包)这是固定布局, 不依赖关卡摘要的内容。
const LEVEL_ID_FROM_FINGERPRINT = 21;

// 宽容解析(2 字节包头)后 payload 的指纹。命中返回构建标识, 否则 null。
// 正常入房时 Move.players 计数 = 0, 常量固定在 +82/+84; 若客户端正带着 split
// 玩家, Move 的记录会整体后移 16×count, 按 count 校正后再判定。
function fingerprintAt(payload, offset) {
  if (offset + 8 > payload.length) {
    return null;
  }
  const server = payload.readUInt16LE(offset);
  const client = payload.readUInt16LE(offset + 2);
  const build = payload.readUInt32LE(offset + 4);
  for (const fp of SKY0134_FINGERPRINTS) {
    if (server === fp.server && client === fp.client && build === fp.build) {
      return fp.variant;
    }
  }
  // 0.15.5 (294/4852/179644) 有它自己的专属引擎(sky0155Engine.js), 不能被
  // 这扇通用窗口"抢走", 否则监控里 0.15.5 会显示成 0.13.x~0.15.x 一代。
  if (server === 294 && client === 4852 && build === 179644) {
    return null;
  }
  // 精确指纹没命中时, 用上面那一代的版本窗口兜底(只影响 0.13.x~0.15.x)。
  // 构建号不再限区间: 重打包的客户端会把 versionCode 改成 999999 之类,
  // 只排除明显的垃圾值。
  if (server >= SKY_ERA_SERVER_MIN && server <= SKY_ERA_SERVER_MAX
    && client >= SKY_ERA_CLIENT_MIN && client <= SKY_ERA_CLIENT_MAX
    && build !== 0 && build !== 0xffffffff) {
    return SKY_ERA_VARIANT;
  }
  return null;
}

export function sky0134Fingerprint(payload) {
  return sky0134JoinInfo(payload)?.variant ?? null;
}

// 宽容解析时一次性读出这一代的指纹 + 真实 levelId。
// levelId 老代码走 "关卡摘要首字段 00 00 80 3f" 的启发式定位, 自定义图
// (SkyHub2 等)摘要首字段不是 1.0f 时会定位失败 → levelId 解析错 → 服务端
// 把两个玩家放进不同的关卡名册 → 同地图互相看不见。这里按固定布局直读。
export function sky0134JoinInfo(payload) {
  if (!payload || payload.length < 180) {
    return null;
  }
  const playerCount = payload.readUInt32LE(62);
  if (playerCount > 0 && playerCount <= 8) {
    const offset = 82 + 16 * playerCount;
    const variant = fingerprintAt(payload, offset);
    if (variant) {
      return joinInfoAt(payload, variant, playerCount, offset);
    }
  }
  const variant = fingerprintAt(payload, 82);
  if (!variant) {
    return null;
  }
  return joinInfoAt(payload, variant, 0, 82);
}

function joinInfoAt(payload, variant, movePlayers, offset) {
  const levelIdOffset = offset + LEVEL_ID_FROM_FINGERPRINT;
  const server = payload.readUInt16LE(offset);
  const client = payload.readUInt16LE(offset + 2);
  return {
    variant,
    movePlayers,
    server,
    client,
    build: payload.readUInt32LE(offset + 4),
    // 关卡状态/权威/GameMsg 的隔离键: 按客户端协议三元组里的 SERVER/CLIENT 分桶。
    // 0.13.4(284/541)、0.13.4.3(284/542)、0.14.0(286/544)、0.15.5(294/4852) 的
    // RPC 集合不同(实测 0.13.4 的 RPC id 只到 16, 0.15.5 至少到 20), 混在一桶里
    // 就会互发对方解析不了的 level state / RPC, 客户端直接
    // "BinaryStream ... val out of range: 20 in [0,16]" 断言闪退。
    // 同一协议三元组的重打包(versionCode 改成 999999 之类)仍然同桶, 可以互相看见。
    generation: `mqtt-${server}-${client}`,
    levelIdOffset,
    levelId: levelIdOffset + 4 <= payload.length
      ? payload.readUInt32LE(levelIdOffset)
      : undefined
  };
}

export function isSky0134TolerantJoin(payload) {
  return sky0134Fingerprint(payload) !== null;
}

// 0.13.4 的引擎数据(与 LEGACY_ENGINE / MODERN_ENGINE / SKY0155_ENGINE 平级)。
//   enterGamePacketIds: [17]    —— pending 连接只认 17, 5 只是内部转发
//   enterGameLeadingByte: false —— 负载就是 modern 的 [count][records]
//   announceJoinWith: 'EnterGame' + playerJoinedPacketIds: [] —— 有新人重发整份名册
export const SKY0134_ENGINE = Object.freeze({
  id: 'sky0134',
  label: 'Sky 0.13.4 (169938) — isolated engine',
  enterGamePacketIds: Object.freeze([PacketIds.EnterGame]),
  enterGameLeadingByte: false,
  playerRecordBytes: 37,
  nameBytes: 16,
  announceJoinWith: 'EnterGame',
  playerJoinedPacketIds: Object.freeze([]),
  playerLeftPacketIds: Object.freeze([PacketIds.PlayerLeft])
});

// 某玩家/实体是否被标记为 0.13.4(标记只由本文件写入 profile, 见 sky0134Profile)。
export function isSky0134Entity(entity) {
  return entity?.profile?.sky0134 === true;
}

// 把一个(按握手推断出的)profile 包成 0.13.4 专属变体:
//   * 只把 EnterGame 外层 id 改成 17(17 在 legacy 族里是别的含义, 必须只对本版本用)
//   * 打上 sky0134 标记, 供 engineForEntity / 监控展示使用
//   * detectedFamily / effectiveFamily 保持握手结论, 关卡状态与权威的既有隔离
//     规则一行都不改 —— 本次只解决"名册收不到 / 新人看不到"。
// payload 不带 0.13.4 指纹时原样返回, 任何其它版本走不到这里的分支。
export function sky0134Profile(profile, payload) {
  if (!payload?.sky0134) {
    return profile;
  }
  // 构建标识只用于监控/日志展示; 两条指纹走同一引擎、同一份字节。
  // 注意: JoinService 传进来的是**已解包的对象**(PacketCodec.decodeJoinGameTolerant),
  // 不是 Buffer —— 指纹在解包时已经算好并放在 sky0134Variant 上, 这里绝不能再读字节。
  let variant = payload?.sky0134Variant;
  let generation = payload?.sky0134Generation;
  if (Buffer.isBuffer(payload)) {
    const info = sky0134JoinInfo(payload);
    variant = variant ?? info?.variant;
    generation = generation ?? info?.generation;
  }
  if (!variant) {
    variant = '169938';
  }
  const variantLabel = variant === '170388'
    ? { label: 'Sky 0.13.4 (170388) UDP (isolated)', source: 'join:sky0134-pwp-170388' }
    : variant === '172143'
      ? { label: 'Sky 0.14.0 (172143) UDP (isolated)', source: 'join:sky0140-build-172143' }
      : variant === SKY_ERA_VARIANT
        ? { label: 'Sky 0.13.x-0.15.x (2-byte header era) UDP (isolated)', source: 'join:sky-era-2byte' }
        : { label: 'Sky 0.13.4 (169938) UDP (isolated)', source: 'join:sky0134-build-169938' };
  return {
    ...profile,
    id: 'sky0134',
    label: variantLabel.label,
    sky0134: true,
    sky0134Variant: variant,
    // 关卡状态/权威/GameMsg 隔离键(见 sky0134JoinInfo 里的 generation 注释)。
    // 0.13.4 / 0.14.0 / 0.15.5 的 RPC 集合不同, 不能共享 level state。
    effectiveFamily: generation ?? 'mqtt-0134',
    source: variantLabel.source,
    confidence: 'high',
    enterGamePacketIds: SKY0134_ENGINE.enterGamePacketIds
  };
}