// 运行时行为开关: 可以在不重启进程的情况下, 用 HTTP 改动。
//
// 为什么需要它: "客户端一进房就闪退" 这类问题必须在真机上反复试不同的下发策略
// (发不发 EnterGame / 发不发 Elect / level data 末尾补几个 0), 每试一次都重启
// 面板 + 重新登录代价太大, 所以把这些开关做成运行时可改。
//
//   GET  /debug/behaviour              查看当前值
//   POST /debug/behaviour  {json}      只改传进来的字段
export const behaviour = {
  // 入房时是否广播 EnterGame(玩家列表)。关掉用于定位"闪退是不是 EnterGame 引起的"。
  enterGameOnJoin: process.env.ROOM_ENTER_GAME_ON_JOIN !== '0',

  // 是否按**协议族**选 EnterGame 的包 id(老族 5 / 新族 17)。默认开。
  //
  // 2026-10-07 反编译实证(两个带符号 dump 的 S->C 分发函数 NetRecvMsg):
  //   libBootloader0123.so.c L1335836 与 libBootloader0134..so.c L1415545 的 switch
  //   都是 case 1/5/7/8/13/14/17, 其中 **case 5** 才做"进游戏"该做的事:
  //     SnapshotWriter::Initialize(game+72, 10) / SnapshotReader::Initialize(game+52468) /
  //     SnapshotWriter::Initialize(game+104856) / SnapshotReader::Initialize(game+157252) /
  //     FriendBarn::NetRecvEnterGame(game)
  //   而 **case 17** 只写两个标志位。老引擎里 17 不是 EnterGame, 发过去会让客户端
  //   去读未填充的快照缓冲区(0.13.4 崩溃 `val out of range: 20 in [0, 16]` 就是
  //   LevelState 的 move-along-rail/floaty/quests 计数[上限 16]被垃圾值 20 撑到);
  //   而快照机没被初始化时老客户端连 level data 都不发 -> "连上了却见不到人"。
  // 曲线救国等价物: 关掉(= 所有族都用 17)只用于真机 A/B 对比定位。
  //   POST http://127.0.0.1:8080/debug/behaviour  {"eraEnterGameId":false}
  eraEnterGameId: process.env.ROOM_ERA_ENTER_GAME !== '0',

  // 连接后 N 毫秒内既没发任何包、也还没成为玩家时, 主动补发一个 EnterGame 催它说话
  // (默认 350ms, 0 = 关闭)。
  //
  // 为什么需要(2026-10-07 实测 _udp_runtime_8443.log 15:18:20~15:18:59 一整分钟):
  //   某个老客户端反复 "peer connected -> 恰好 1000ms(joinTimeoutMs) -> peer dropped:
  //   no valid JoinGame within join timeout -> peer disconnected", 期间**一个包都没收到**
  //   (日志里没有任何 client packet / failed to process packet)。也就是它连上 ENet 后
  //   在等**服务端先说话**, 服务端不说话它就永远不发 JoinGame —— 用户看到的就是
  //   "0.7.1 能连上 UDP 但见不到人、UDP 包比别的版本少"。补一个 EnterGame(它就是
  //   老引擎 case 5 那个"进游戏", 见上)把流程踢起来。
  nudgeAfterConnectMs: clampInt(process.env.ROOM_NUDGE_AFTER_CONNECT_MS, 350, 0, 30000),

  // 是否允许下发 NetLevelDataElect。关掉用于定位"闪退是不是 Elect 引起的"。
  elect: process.env.ROOM_ELECT !== '0',

  // 入房瞬间是否主动 Elect(参考实现只在客户端发 LevelUpdate 时才 Elect)。
  electOnJoin: process.env.ROOM_ELECT_ON_JOIN === '1',

  // 入房后"延迟补发 Elect"的毫秒数(仅 legacy 族生效, 0 = 关闭)。
  //
  // 为什么需要: 有的老客户端**自己从来不发 LevelUpdate**, 而本服务(和参考实现)只在
  // 收到 LevelUpdate 时才 Elect, 于是它永远收不到 Elect —— 实测 0.7.1(读书郎 C30):
  // 09:04:43.891 连上 -> 09:04:43.892 发 JoinGame -> 之后一个包都不发 ->
  // 09:04:48.926 自己断开(ENet disconnect data=12), 然后无限重连(每轮 ~5.0s)。
  // 而收到过 Elect 的客户端(0.12.1 / 0.22.0 / 0.23.6 抓包)都在 ~15ms 内开始发包,
  // 说明 Elect 就是它们等的那个包。
  // 默认 1200ms: 会自己触发 Elect 的客户端(入房后 ~15ms 就发 LevelUpdate)早已把
  // 这个定时器清掉(见 GameMessageService.sendElect), 所以它们行为一点不变;
  // 1200ms 又远小于老客户端 ~5s 的等待上限。
  electAfterJoinMs: clampInt(process.env.ROOM_ELECT_AFTER_JOIN_MS, 1200, 0, 30000),

  // Elect 里 level data 末尾补多少个 0x00。
  // 实测: 客户端解析时 BinaryStream 抛 "Read past end of bit stream"(size: 22),
  // 说明这一版客户端的 level data 头比我们写的 22 字节更长; 末尾补 0 后, 客户端在
  // 它自己的 length 字段位置读到的仍是 0(length=0), 就不会越界。
  padLevelData: clampInt(process.env.ROOM_ELECT_PAD, 16, 0, 64),

  // level data 当成"不透明状态包"原样转发(默认开)。
  //
  // 2026-10-01 实测(0.22.0 客户端 + 抓包 + 反编译 libBootloader0200.so.c 的
  // LevelState.cpp / NetLevelClient.cpp):
  //   客户端发来的 NetLevelData 里, GameMsg 之后的 3 字节快照头(seq/base/checksum)
  //   用 FNV-1a-8(0xC5/0x93) 校验是**对得上的**, 解出来的状态恒为 **2331 字节**,
  //   内容恒定(base=上一个关键帧, XOR 全 0 = 状态没变)。
  //   这 2331 字节不是参考实现 that-sky-colorsky-udp 里的 "22 字节头 + 数据",
  //   而是客户端自己打包后的 level state(客户端内存里的 LevelData 结构体是
  //   0xA14=2580 字节: magic 14256 / 字段 / 2560 字节位流负载 / 末尾 u16 长度,
  //   快照层再把它压成 2331 字节的不透明块)。
  //   旧代码按 22 字节头解析 -> dataLength 读到负载中间的随机值(1024)、
  //   trailingBytes != 0 -> **每个 level data 包都被丢弃** ->
  //   别人的留影 / 留言 / 世界物体永远同步不过来(只有创建者自己本地可见)。
  // 打开后: 不做结构解析, 原样缓存 + 原样转发给同关卡的其他玩家。
  opaqueLevelData: process.env.ROOM_OPAQUE_LEVEL_DATA !== '0',

  // Elect 之后是否立刻把缓存里最新的一份 level data 补发给刚进关的玩家(默认开)。
  //
  // 为什么需要(2026-10-05 真机日志 + 反编译):
  //   不透明模式下 createLevelElectPayload 是**发不出**初始 level data 的 —— Elect 里
  //   那 22 字节头 + 位流不是客户端打包的那种结构, 硬塞进去客户端按自己的格式解析会
  //   抛 "Read past end of bit stream"。所以刚进关的玩家只能等"权威方下一次 NetLevelData
  //   被转发过来"才拿得到世界状态, 中间有一段空窗期; 用户反馈
  //   "房员先切图房主后切图会崩溃 可能是断快照了" 就发生在这段空窗里。
  //   这里在 sendElect 发完 Elect 之后, 立刻把 levels.loadLevelData() 缓存的那份原样
  //   补一帧给该玩家: 走它自己的快照链(刚 resetWriter 过 -> 一定是关键帧 base=0),
  //   和后续转发帧同一条链, 不会互相冲突; 帧太大(>SNAPSHOT_MAX_DATA_BYTES)时丢弃,
  //   行为与改动前一致。
  // 关掉(定位用): POST http://127.0.0.1:8080/debug/behaviour {"catchUpLevelData":false}
  catchUpLevelData: process.env.ROOM_CATCH_UP_LEVEL_DATA !== '0',

  // 未识别的 GameMsg 类型: 是否**转发**给同关卡其他玩家。
  //
  // 2026-10-01 实测(0.12.1 读书郎 C30 客户端, node /health 的 room.gameMsg):
  // 客户端只会发 type 2(NetRpc, 一次) / 3(PlayerStateDelta) / 7(未知, 每 ~4 秒
  // 一个 1 字节载荷 0x00) / 11 / 14。旧代码遇到没列举的类型直接静默丢弃(不记日志
  // 也不转发), 所以排查时日志里一片空白。
  //
  // 现在**日志永远记**(见 GameMessageService.handleUnknownGameMsg, 限速), 但转发
  // 默认**关**: 把一个对方客户端可能按别的语义解释的未知包塞过去, 有触发客户端
  // 断言/闪退的风险(这个私服已经被"闪退"折腾很久了), 在没有确认 type 7 的语义
  // 之前不值得冒这个险。
  // 需要时打开: ROOM_RELAY_UNKNOWN_GAME_MSG=1 或
  //   POST http://127.0.0.1:8080/debug/behaviour  {"relayUnknownGameMsg":true}
  relayUnknownGameMsg: process.env.ROOM_RELAY_UNKNOWN_GAME_MSG === '1',

  // 未识别 GameMsg 日志限速: 每种 type 每 N 毫秒最多记一条(默认 2000ms)。
  unknownGameMsgLogIntervalMs: clampInt(process.env.ROOM_UNKNOWN_GAME_MSG_LOG_MS, 2000, 0, 600000),

  // 关卡内"事件类"GameMsg(亲和 7 / 演出·观众 19~24)是否同关卡转发给其他玩家。
  // 默认开(= 各版本聊天/演出尽量互通); 若怀疑某老版本客户端收到这类包会闪退, 关掉:
  //   ROOM_RELAY_EVENT_GAME_MSG=0
  // (注意: Critters 4 / MusicSync 15 / 选举提名 17 属老行为, 不受此开关影响。)
  relayEventGameMsg: process.env.ROOM_RELAY_EVENT_GAME_MSG !== '0',

  // 强制指定所有玩家的协议族(协议适配用, 便于在真机上对比不同族的策略)。
  // 取值: 'legacy' | 'mqtt' | 'modern' | '' (=自动, 由 UDP 握手推断)。
  // 见 protocol/VersionProfile.js。例: ROOM_FORCE_PROFILE=legacy
  versionProfile: normalizeProfile(process.env.ROOM_FORCE_PROFILE),

  // 协议族隔离(默认开): **不同协议族的玩家之间不转发任何关卡状态**(level data /
  // 玩家快照 / GameMsg / RPC), 而且关卡权威(和那份 level data 缓存)按族分开记。
  //
  // 为什么(2026-10-06 实测, _udp_runtime_8443.log):
  //   0.7.1(legacy b966265d) 和 0.22.0(modern 9b165378) 同时被解析到同一个
  //   levelId 3526133726(关卡 id 是**内容 id**, 不是房间号, 不同版本本来就会撞),
  //   于是 legacy 客户端入房后收到了 modern 客户端上传的 **2331 字节 level state**:
  //       "sent cached level data catch-up after elect {sourcePlayer:1, dataBytes:2331}"
  //   这份块是 modern 客户端按**它自己**的结构体打包的(0.22.0 的 LevelData 结构、
  //   0.22.0 的 RPC 集合), 老客户端按自己的结构解 -> "val out of range" /
  //   "please check CLIENT_VERSION if RPCs have changed" 这类断言闪退, 日志里
  //   反复出现 入房 -> 补发 -> 3~4 秒后掉线(reason 12) 的循环。
  //   官方服务器不会有这个问题: 它按版本分 shard, 不同版本根本不在同一局。
  //
  // 关掉(回到"所有族互相转发"的旧行为, 仅用于对比定位):
  //   POST http://127.0.0.1:8080/debug/behaviour  {"familyIsolation":false}
  familyIsolation: process.env.ROOM_FAMILY_ISOLATION !== '0',

  // 老引擎(EnterGame id=5)的负载前是否补 1 个前导字节(默认开)。
  //
  // 反编译实证: 0.7.1/0.6.5 的 EnterGameMsg::Serialize 先 BinaryStream::Serialize(this)
  // (1 字节, 恒 0), 之后才是 SerializeCompressed(this+1, 0, 8) 的人数; 每个玩家记录
  // 37 字节(netId u8 + TgcUuid 16 + 定长名字 16 + levelId u32)。
  // 之前抓包看到服务端发的是 [count][players](没有前导字节) —— 老客户端会把 count
  // 当那个未用字段、把第 1 个玩家记录的 netId 当人数, 于是名册被整体错位解析,
  // **永远建不出别人的化身** -> 用户看到的"能进游戏能走路, 但一个玩家都看不见"。
  // 只对老引擎(id=5)生效; 新引擎(id=17)本来就是 [count][players]。
  //   POST /debug/behaviour  {"legacyEnterGameLeadingByte":false}
  legacyEnterGameLeadingByte: process.env.ROOM_LEGACY_ENTER_GAME_LEADING_BYTE !== '0',

  // 新玩家入房时, 用哪种方式通知**已经在房的老玩家**: true=发 PlayerJoined(6),
  // false=重发一份完整 EnterGame(旧行为, 仅用于真机 A/B 对比)。
  //
  // 为什么需要(反编译实证, 0.7.1 NetGameClient 的两个 switch):
  //   主连接的分发 switch 里 **case 0 和 case 5 直接**
  //   Private::AssertMsgImpl("false", ... "Received unexpected message: %u") -> abort();
  //   也就是老客户端一旦进了房, 再收到 EnterGame(5) 就是致命错误(不是静默忽略)。
  //   增量通知必须用 PlayerJoined(6)(同一 switch 的 case 6, 37 字节记录,
  //   和 EnterGame 里每个玩家记录同布局)。
  //   ROOM_ANNOUNCE_JOIN_WITH_PLAYER_JOINED=0 关闭。
  announceJoinWithPlayerJoined: process.env.ROOM_ANNOUNCE_JOIN_WITH_PLAYER_JOINED !== '0'
};

function normalizeProfile(value) {
  const v = String(value ?? '').trim().toLowerCase();
  return v === 'legacy' || v === 'mqtt' || v === 'modern' ? v : null;
}

function clampInt(value, fallback, min, max) {
  const n = Number.parseInt(value ?? '', 10);
  const v = Number.isFinite(n) ? n : fallback;
  return Math.min(max, Math.max(min, v));
}
