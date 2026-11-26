// ─────────────────────────────────────────────────────────────────────────────
// Sky 0.15.5 (build 179644) 专属 UDP 引擎 —— **独立文件, 独立分支**
//
// 这个文件只为 0.15.5 服务, 不改动 legacyEngine.js / modernEngine.js 的任何一行,
// 也不改变 0.22.0 ~ 0.34.x 的行为与发出去的字节。判定与选择都在
// protocol/versions/index.js 里以 "先看 sky0155 标记" 的方式短路, 其余路径原样。
//
// 为什么 0.15.5 需要单独一个引擎
// ------------------------------
// 版本矩阵把它归在 mqtt 族(0.13.4~0.21.5), 平台按 2 字节包头(id+seq) 会把它判成
// legacy —— 但它的 NetGameClient 实际是"新引擎的分发器 + 老时代的包头":
//
//   pending 连接 sub_743424: 收到 id 17 -> "Recvd EnterGame" -> 触发 enter_the_game
//   active  连接 sub_743FC0: 收到 id 17 -> 名册同步(增删玩家, 日志 "Recvd PlayerJoined: ")
//   active  连接 case 5     : 明确的 "Active server Unknown message" —— 直接忽略
//
// 也就是说: 我们过去按 legacy 族给它发 EnterGame id 5(+1 字节前导), 它一个名册都
// 没收到, 所以能进房、能移动, 却"见不到人"; 它则每隔 ~6.7s 重发一次 JoinGame。
//
// 它要的 EnterGame 负载与 modern 完全一致(见 sub_743424/sub_743FC0 的解析):
//   [count compressed(0,8) = 首字节即 u8 计数][netId u8][uuid 16][name 16][levelId u32]
// 不含 legacy 的 1 字节前导, 也无 PlayerJoined(6); 有新人时靠**重发 EnterGame(17)**
// 整份名册来通知(与 modern 的做法相同)。
//
// 识别方式(只认 0.15.5, 不认别的版本)
// ------------------------------------
// 真机抓包 0.15.5 JoinGame(2 字节包头, id 3)的负载里有三个编译期常量:
//   payload+82 u16 = 294, payload+84 u16 = 4852, payload+86 u32 = 179644(构建号)
// 三个同时命中才认, 并额外要求真实 payload 长度 >= 90。0.22.0 用的是 6 字节包头,
// 其它老版本没有这组常量, 因此不会被误判(SKY0155 判定仅在宽容解析路径上生效)。
import { PacketIds } from '../PacketIds.js';

// 0.15.5 (179644) JoinGame 里的构建号常量。
export const SKY0155_BUILD = 179644;

// 宽容解析(2 字节包头)后 payload 的指纹。命中才返回 true。
// 仅做只读检查, 不消费任何字节, 不影响 levelId 等既有解析结果。
export function isSky0155TolerantJoin(payload) {
  if (!payload || payload.length < 90) {
    return false;
  }
  return payload.readUInt16LE(82) === 294
    && payload.readUInt16LE(84) === 4852
    && payload.readUInt32LE(86) === SKY0155_BUILD;
}

// 0.15.5 的引擎数据(与 LEGACY_ENGINE / MODERN_ENGINE 平级, 互不引用)。
//   enterGamePacketIds: [17]   —— 它只认 17; 5 是 "Unknown message"
//   enterGameLeadingByte: false —— 负载就是 modern 的 EnterGame 布局
//   announceJoinWith: 'EnterGame' + playerJoinedPacketIds: [] —— 有新人重发整份名册
export const SKY0155_ENGINE = Object.freeze({
  id: 'sky0155',
  label: 'Sky 0.15.5 (179644) — isolated engine',
  enterGamePacketIds: Object.freeze([PacketIds.EnterGame]),
  enterGameLeadingByte: false,
  playerRecordBytes: 37,
  nameBytes: 16,
  announceJoinWith: 'EnterGame',
  playerJoinedPacketIds: Object.freeze([]),
  playerLeftPacketIds: Object.freeze([PacketIds.PlayerLeft])
});

// 某玩家/实体是否被标记为 0.15.5(标记只由本文件写入 profile, 见 sky0155Profile)。
export function isSky0155Entity(entity) {
  return entity?.profile?.sky0155 === true;
}

// 把一个(按握手推断出的)profile 包成 0.15.5 专属变体:
//   * 只把 EnterGame 外层 id 改成 17(17 在 legacy 族里是别的含义, 所以必须只对本版本用)
//   * 打上 sky0155 标记, 供 engineForEntity / 监控展示使用
//   * detectedFamily / effectiveFamily 保持握手结论(legacy), 关卡状态与权威的
//     既有隔离规则一行都不改 —— 本次只解决"名册收不到"。
// payload 不带 0.15.5 指纹时原样返回, 任何其它版本走不到这里的分支。
export function sky0155Profile(profile, payload) {
  if (!payload?.sky0155) {
    return profile;
  }
  return {
    ...profile,
    id: 'sky0155',
    label: 'Sky 0.15.5 (179644) UDP (isolated)',
    sky0155: true,
    // 与 sky0134Engine 的 generation 键一致: 0.15.5(294/4852) 的 RPC 集合比
    // 0.13.4 多(0.13.4 只到 16, 0.15.5 至少到 20), 关卡状态必须隔离, 否则
    // 0.13.4 收到 0.15.5 的 RPC 20 会 BinaryStream 断言闪退。
    // 重打包的 0.15.5(versionCode 999999)走 era 通道, 生成的也是同一个键。
    effectiveFamily: 'mqtt-294-4852',
    source: 'join:sky0155-build-179644',
    confidence: 'high',
    enterGamePacketIds: SKY0155_ENGINE.enterGamePacketIds
  };
}