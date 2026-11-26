// 注意: **16 进制 id 在不同版本区间不是同一张表**(2026-10-07 由反编译实证)。
// 服务端 -> 客户端的 "EnterGame"(玩家列表/初始化快照机)在
//   0.6.5 ~ 0.21.x  -> id 5
//   0.22.0 ~ 0.34.x -> id 17
// 证据(两个带符号的反编译 dump 里 S->C 分发函数 NetRecvMsg 的 switch 完全一致):
//   D:\工具\SO\libBootloader0123.so.c L1335836  NetRecvMsg: case 1/5/7/8/13/14/17
//   D:\工具\SO\libBootloader0134..so.c L1415545 NetRecvMsg: case 1/5/7/8/13/14/17
//   case 5 的函数体才是"进游戏": SnapshotWriter::Initialize(game+72, 10) +
//     SnapshotReader::Initialize(game+52468) + SnapshotWriter::Initialize(game+104856) +
//     SnapshotReader::Initialize(game+157252) + FriendBarn::NetRecvEnterGame(game) ——
//     也就是初始化快照读写器(不初始化它连 level data 都发不出去, 表现就是
//     "连上了但一个包都不发 / 见不到人"), 而且 case 5 **不解析负载**。
//   case 17 只是设两个标志位(`*(game+158272)=1; *(game+158273)=1`, 与 case 1/7/8 同一段
//     代码), 在老引擎里根本不是 EnterGame —— 把它当 EnterGame 发过去, 客户端会以为
//     "有新的世界状态待读取" 而去解一个**从未被填充过的快照缓冲区**(见
//     NetLevelRecv.cpp 的 level data 链), 解出来的垃圾 level state 再序列化时就会
//     撞 BinaryStream 断言(0.13.4 报的 `val out of range: 20 in [0, 16]` 正是
//     LevelState::Serialize 里三处 (0,0x10) 数组计数之一: "move along rail" +304 /
//     "make floaty" +440 / "quests" +9264, 上限 16 而垃圾计数是 20)。
export const PacketIds = Object.freeze({
  ClientConnect: 0,
  Disconnect: 1,
  Kick: 2,
  JoinGame: 3,
  LevelUpdate: 4,
  // 老版本(0.6.5~0.21.x)的 "EnterGame / 进游戏" id(新版本是 17, 见 EnterGame)。
  EnterGameLegacy: 5,
  PlayerJoined: 6,
  PlayerLeft: 7,
  PlayerChangedLevel: 8,
  MoveGame: 9,
  MoveResult: 10,
  CancelMove: 11,
  NetTimePing: 12,
  NetTimePong: 13,
  GameMsg: 14,
  EnterGame: 17
});
