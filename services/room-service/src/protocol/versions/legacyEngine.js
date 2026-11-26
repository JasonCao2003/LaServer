// 老引擎(0.6.5 ~ 0.21.5, 也就是 "EnterGame 外层 id = 5" 的那整个时代)的 **发包策略**。
//
// 为什么要按版本分开发包(2026-10-10, 用户实测结论 + 反编译实证)
// --------------------------------------------------------------
// "能连上 UDP、能走动、但一个玩家都看不见" 的根因就在 EnterGame 的负载上:
// 我们一直按 **新引擎** 的布局发 `[count:u8][每人 37 字节]`, 而老引擎的
// `EnterGameMsg::Serialize` 在 count 前面还有 1 个字节:
//
//   BinaryStream::Serialize(a2, this);                        // this[0]  : u8
//   BinaryStream::SerializeCompressed(a2, this + 1, 0, 8u);   // this[1]  : 玩家数
//   for (i = 0; i < this[1]; ++i) {
//     BinaryStream::Serialize(a2, netId);                     //   u8
//     TgcUuid::Serialize(a2, uuid);                           //   16 字节 uuid
//     BinaryStream::SerializeBytes(a2, name, 0x10);           //   16 字节定长名字
//     BinaryStream::Serialize(a2, levelId);                   //   u32
//   }
//
// 证据(四处 dump 完全一致, 逐行可核对):
//   D:\工具\SO\libBootloader065.so.c              L1090453-1090497
//   D:\工具\其他\ai\so_adapt\0.7.1\libBootloader.so.c  L33877-33903
//   D:\工具\SO\libBootloader0122.so.c             L1305677 附近
//   D:\工具\SO\libBootloader0134..so.c            L1404472 附近
// 其中 0.7.1 是最清楚的一份:
//     TgcUuid::Serialize((TgcUuid *)((char *)v7 - 35), a2);   // uuid 紧跟在 netId 后
//     BinaryStream::SerializeBytes(a2, (unsigned __int8 *)v7 - 19, 0x10u);
//  `TgcUuid::Serialize` = `BinaryStream::SerializeBytes(a2, this, 0x10)`(16 字节裸 uuid,
//  L46086), 所以每条玩家记录 = 1 + 16 + 16 + 4 = **37 字节**, 与我们的 playerRecordBytes 一致。
//
// 少了那个前导字节会怎样(这就是"看不见人"的机制, 不是猜的):
//   老客户端读: this[0](未用) = payload[0] = 玩家数; 然后 count = payload[1];
//   我们发的 payload[1] 恰好是第 1 个玩家的 netId(= 1), 于是它认为"名册里只有 1 个
//   玩家", 并从 payload[2] 开始按 37 字节切 —— 拿到的 uuid 被整体错位 1 字节,
//   levelId 更是读到缓冲区末尾之外。结果: 名册里的 uuid 一个都对不上 -> 对方玩家的
//   头像根本不会被创建 -> **一个玩家都看不见**, 而且因为 count 只有 1~2, 也不会越界
//   到崩溃(与"能玩、不闪退、就是看不见人"的现象完全吻合)。
//
// 注意 EnterGame 的 `case 5` 只存在于"老引擎"这一侧: 0.22.0+(新引擎)的 id 是 17,
// 布局实测是 `[count][每人 37 字节]`(0.22.0/0.23.6/0.34.0 真机联机正常就是这么发的),
// 所以这个前导字节 **必须按族分开**, 不能新老通吃 —— 见 modernEngine.js。
import { PacketIds } from '../PacketIds.js';

// EnterGame 里一条玩家记录的字节数(netId 1 + uuid 16 + name 16 + levelId 4)。
export const LEGACY_PLAYER_RECORD_BYTES = 37;

// 名字字段是**定长 16 字节**(0.7.1 `BinaryStream::SerializeBytes(a2, name, 0x10)`,
// `NetPlayer::Serialize` 用的是 `SerializeCString(name, 0x10)`; EnterGame 里记录步长
// 恒为 40 也说明它是定长而不是变长)。
export const LEGACY_NAME_BYTES = 16;

export const LEGACY_ENGINE = Object.freeze({
  id: 'legacy-engine',
  label: '老引擎 ENet (0.6.5–0.21.5, EnterGame id=5)',
  detectedFamilies: Object.freeze(['legacy', 'mqtt']),

  // 发给该引擎客户端的 "进游戏/玩家名册" 外层 id。
  enterGamePacketIds: Object.freeze([PacketIds.EnterGameLegacy]),
  // EnterGame 负载开头有没有那 1 个前导字节(见文件头注释)。
  enterGameLeadingByte: true,
  playerRecordBytes: LEGACY_PLAYER_RECORD_BYTES,
  nameBytes: LEGACY_NAME_BYTES,

  // 有新玩家加入时, **已经在房里的**老引擎客户端收什么:
  //   'PlayerJoined' -> 只给它发 PlayerJoined(id 6)
  // 为什么不能再给它发一次 EnterGame(5): 0.7.1 的 NetGameClient::Update 里, 收到
  // id 5 的那条连接("pending game" 那条)会立刻回一个 MoveResult(10)、然后
  // `enet_peer_disconnect_later()` 关掉自己; 而主连接的 switch 遇到 case 0/case 5
  // 直接 `Private::AssertMsgImpl("false", "NetGameClient.cpp", ..., "Received unexpected
  // message: %u")` + `abort()`(0.7.1 dump L32127-32138)。也就是说"再喂一次 EnterGame"
  // 在老引擎上不是"刷新名册", 而是关连接 / 断言闪退。新增玩家只在 PlayerJoined 里出现。
  announceJoinWith: 'PlayerJoined',
  playerJoinedPacketIds: Object.freeze([PacketIds.PlayerJoined]),
  playerLeftPacketIds: Object.freeze([PacketIds.PlayerLeft])
});

// 该引擎的一条玩家记录(netId/uuid/name/levelId), 供 EnterGame 与 PlayerJoined 复用。
export function legacyPlayerRecord(player) {
  return {
    netId: player.id,
    uuid: player.uuid,
    name: player.name ?? '',
    levelId: player.levelId
  };
}
