// 版本/协议族 -> 发包策略 的注册表。
//
// 用户的要求是"不同版本之间的 UDP 联机不能套用, 要单独分开写分开发包", 所以这里
// 把"同一个包在不同版本时代该怎么写"收敛成两个显式的引擎对象:
//
//   LEGACY_ENGINE  0.6.5  ~ 0.21.5   EnterGame id = 5, 负载带 1 字节前导, 增量名册用 PlayerJoined(6)
//   MODERN_ENGINE  0.22.0 ~ 0.34.3   EnterGame id = 17, 负载无前导,   增量名册靠重发 EnterGame(17)
//
// 选择依据**只有一个**: 该玩家(按 protocol/VersionProfile.js 解析出的 profile)实际会用哪个
// EnterGame 外层 id —— 因为"老引擎/新引擎"这件事本身就是由这个 id 分界的(见 PacketIds.js
// 的长注释: id 5 那一侧的 NetRecvMsg case 5 才是"进游戏 + 初始化快照读写器")。
// 这样 eraEnterGameId 这个 A/B 开关也会自动把引擎一起切过去, 不会出现"id 换成了 17、
// 负载还带老前导字节"这种混合体。
import { PacketIds } from '../PacketIds.js';
import { enterGamePacketIdsFor } from '../VersionProfile.js';
import { LEGACY_ENGINE, legacyPlayerRecord } from './legacyEngine.js';
import { MODERN_ENGINE, modernPlayerRecord } from './modernEngine.js';
import { SKY0155_ENGINE, isSky0155Entity } from './sky0155Engine.js';
import { SKY0134_ENGINE, isSky0134Entity } from './sky0134Engine.js';
import { SKY0122_ENGINE, isSky0122Entity } from './sky0122Engine.js';
import { SKY071_ENGINE, isSky071Entity } from './sky071Engine.js';

export { LEGACY_ENGINE, MODERN_ENGINE, SKY0155_ENGINE, SKY0134_ENGINE, SKY0122_ENGINE, SKY071_ENGINE };

// 按"该玩家会收到的 EnterGame id"选引擎。eraEnterGameId=false 时所有族都用 17,
// 引擎也跟着变成新引擎(便于真机 A/B 回退到改动前的行为)。
//
// 0.15.5 单独短路: 它的 profile 带 sky0155 标记(只由 versions/sky0155Engine.js 的
// sky0155Profile 写入, 判定依据是 JoinGame 里的 179644 构建号指纹), 直接返回它
// 自己的引擎。其余版本完全走下面的原逻辑, 不受影响。
export function engineForEntity(entity, { eraEnterGameId = true } = {}) {
  if (isSky0155Entity(entity)) {
    return SKY0155_ENGINE;
  }
  // 0.13.4 单独短路(只由 versions/sky0134Engine.js 的指纹写入), 其余版本不受影响。
  if (isSky0134Entity(entity)) {
    return SKY0134_ENGINE;
  }
  // 0.12.2 单独短路（只由 versions/sky0122Engine.js 的 JoinGame 指纹写入）。
  if (isSky0122Entity(entity)) {
    return SKY0122_ENGINE;
  }
  // 0.7.1 单独短路（只由 versions/sky071Engine.js 的 252/477 指纹写入）。
  // 名册/加入通知与 legacy 同形, 但 GameMsg type=3 的中继长度字段是 u16,
  // 必须靠独立引擎区分, 不能落回 LEGACY_ENGINE。
  if (isSky071Entity(entity)) {
    return SKY071_ENGINE;
  }
  const ids = enterGamePacketIdsFor(entity, { eraEnterGameId });
  return ids[0] === PacketIds.EnterGameLegacy ? LEGACY_ENGINE : MODERN_ENGINE;
}

// 按"我们要发的 EnterGame 外层 id"反查引擎(S->C 编码侧用, 见 PacketCodec.encodeServer)。
export function engineForEnterGamePacketId(id) {
  if (id === PacketIds.EnterGameLegacy) {
    return LEGACY_ENGINE;
  }
  if (id === PacketIds.EnterGame) {
    return MODERN_ENGINE;
  }
  return null;
}

export function playerRecordFor(engine, player) {
  // 0.15.5 不发 PlayerJoined(6)(它的名册靠重发 EnterGame 17), 这里只是保持与
  // modern 一致的记录形状, 不会被实际调用。
  if (engine === SKY0155_ENGINE) {
    return modernPlayerRecord(player);
  }
  // 0.13.4 的记录形状同样是 modern 的 37 字节(netId/uuid/name/levelId)。
  if (engine === SKY0134_ENGINE) {
    return modernPlayerRecord(player);
  }
  // 0.12.2 的名册记录同样是 modern 的 37 字节（netId/uuid/name/levelId）。
  if (engine === SKY0122_ENGINE) {
    return modernPlayerRecord(player);
  }
  // 0.7.1 的 EnterGame/PlayerJoined 记录是老引擎的 37 字节
  // （netId u8 + uuid16 + name16 + levelId u32），与 legacyPlayerRecord 一致。
  if (engine === SKY071_ENGINE) {
    return legacyPlayerRecord(player);
  }
  return engine === LEGACY_ENGINE ? legacyPlayerRecord(player) : modernPlayerRecord(player);
}
