// 新引擎(0.22.0 ~ 0.34.3, EnterGame 外层 id = 17)的发包策略。
//
// 与老引擎唯一的差别是 EnterGame 的负载/外 id(见 legacyEngine.js 的长注释):
//   - 外层 id 是 17(0.22.0/0.23.6/0.34.0 真机联机正常, 一直用的就是 17);
//   - 负载 **没有** 前导字节, 直接 `[count:u8][每人 37 字节]`。
//     同一版本的 EnterGameMsg::Serialize 我们没法用符号名核对(0.19.5/0.20.0/0.33.7
//     的 dump 都被 strip 了), 但真机实证是硬证据: 现在这个格式下 0.22.0 两台机器
//     同图、互相可见、还能一起切图(2026-10-10 21:37~21:47 的日志)。
//     所以这里 **保持不动**, 只把老引擎那边补上前导字节。
//
// 已经在房的新引擎客户端在新玩家加入时, 沿用"再发一份完整名册的 EnterGame" ——
// 这是它一直以来的行为, 实证可用(与老引擎不同, 新引擎的 EnterGame 是可以重复收的)。
import { PacketIds } from '../PacketIds.js';

export const MODERN_PLAYER_RECORD_BYTES = 37;
export const MODERN_NAME_BYTES = 16;

export const MODERN_ENGINE = Object.freeze({
  id: 'modern-engine',
  label: '新引擎 ENet (0.22.0–0.34.3, EnterGame id=17)',
  detectedFamilies: Object.freeze(['modern']),

  enterGamePacketIds: Object.freeze([PacketIds.EnterGame]),
  enterGameLeadingByte: false,
  playerRecordBytes: MODERN_PLAYER_RECORD_BYTES,
  nameBytes: MODERN_NAME_BYTES,

  // 新引擎不使用 PlayerJoined(6) 做增量名册: 名册靠重发 EnterGame 刷新。
  announceJoinWith: 'EnterGame',
  playerJoinedPacketIds: Object.freeze([]),
  playerLeftPacketIds: Object.freeze([PacketIds.PlayerLeft])
});

export function modernPlayerRecord(player) {
  return {
    netId: player.id,
    uuid: player.uuid,
    name: player.name ?? '',
    levelId: player.levelId
  };
}
