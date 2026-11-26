import { PlayerSnapshotState } from './PlayerSnapshotState.js';

export class Player {
  constructor({ id, uuid, session, name = '', levelId, netVersion, profile = null,
                clientVersion = null, serverVersion = null, rpcHash = null }) {
    this.id = id;
    this.uuid = uuid;
    this.session = session;
    // 客户端自报的名字(老引擎 JoinGame / NetPlayer 里有定长 16 字节名字)。
    // EnterGame / PlayerJoined 的下发记录里要带上它, 否则对端名册是空名。
    this.name = name ?? '';
    this.levelId = levelId;
    this.netVersion = netVersion;
    // 版本适配上下文(见 protocol/VersionProfile.js)。由 JoinService 依据 UDP 握手推断,
    // 或在 ROOM_FORCE_PROFILE 下强制。GameMessageService 用它选 level data 封装布局等。
    this.profile = profile;
    // JoinGame 严格布局里客户端自报的版本字段(老客户端宽容解析时全为 null)。
    // 只用于 UDP 监控缓存/展示"这条包是哪个客户端版本发的", 不参与任何协议判断。
    //   rpcHash       = 构建号(实测 401861 就是 0.34.0 的构建号)
    //   clientVersion = 客户端版本整数(原样保留, 未做映射)
    //   serverVersion = 客户端认为的服务端版本整数
    this.clientVersion = clientVersion;
    this.serverVersion = serverVersion;
    this.rpcHash = rpcHash;
    // 入房"延迟补发 Elect"用(见 session/RoomServer.js 的 scheduleJoinElect):
    //   electSent      = 这位玩家是否已经收到过 Elect(sendElect 里置位)
    //   joinElectTimer = 入房时排的那次延迟 Elect(客户端自己触发了就会被清掉)
    this.electSent = false;
    this.joinElectTimer = null;
    this.levelChangeCount = 1;
    this.joinedAt = Date.now();
    this.lastSeenAt = Date.now();
    this.playerDelta = new PlayerSnapshotState();
    this.levelDelta = new PlayerSnapshotState();
  }

  touch(now = Date.now()) {
    this.lastSeenAt = now;
  }

  // UDP 监控用的版本标签: 优先构建号(能和安装包 "Sky_v0.34.0 (401861).apk" 对上),
  // 其次客户端版本号; 老客户端两者都没有时退回协议族。只读, 不改变任何行为。
  get versionLabel() {
    const parts = [];
    if (this.rpcHash !== null && this.rpcHash !== undefined) {
      parts.push('build ' + this.rpcHash);
    }
    if (this.clientVersion !== null && this.clientVersion !== undefined) {
      parts.push('cv ' + this.clientVersion);
    }
    if (parts.length) {
      return parts.join(' / ');
    }
    return this.profile?.detectedFamily ?? null;
  }

  changeLevel(levelId, levelChangeCount, netVersion = this.netVersion) {
    const oldLevelId = this.levelId;
    const oldLevelChangeCount = this.levelChangeCount;
    this.levelId = levelId;
    this.levelChangeCount = levelChangeCount;
    this.netVersion = netVersion;
    // 2026-10-05 23:59 修: 判断条件从 "levelId 变了" 加宽成 "levelId 或 levelChangeCount 变了"。
    // 客户端每次(重新)加载关卡都会把 levelChangeCount 往前推 —— **包括切到同一个 levelId**
    // (今天 23:45/23:48/23:52/23:53/23:55 五次 LevelUpdate 全是 oldLevelId === levelId,
    //  所以这里的 reset 一次都没跑)。客户端那边 reader 是刚建的新链(rfind/base 从 0 起),
    //  收到的却是我们**换图前那条链**的 delta -> find(base) 永远失败 -> 每帧被当 nokeyframe 丢掉
    //  -> 它的发送窗口 16 格填满后停发 -> 客户端卡在读图/切图时闪退("切图崩溃")。
    // levelChangeCount 就是客户端自己的"关卡代次", 它一变就说明对方重开了快照链, 必须一起重置。
    if (oldLevelId !== levelId || oldLevelChangeCount !== levelChangeCount) {
      // 换图时**只重置发送侧**(给这位玩家下发的快照链重新从关键帧开始),
      // 接收侧(reader)必须保留 —— 见下面 2026-10-01 的实测:
      //
      // 客户端的 level 快照链是**跨换图连续**的: 它换图后发的包 base=75, 而 75 是它
      // 换图前自己分配的关键帧(换图前最后一个 ack 到我们的 levelAckSeq 就是 73)。
      // 我们一 reset, history 只剩种子 [seq 1], find(75) 永远找不到 ->
      // 之后**每一个** level data 包都被判 nokeyframe 丢掉(实测 08:48:27~08:48:38
      // 连丢 ~100 个), 而且因为从来没成功读到, 我们回给客户端的 SnapshotAck 里
      // levelAckSeq 卡在 1(种子值) -> 客户端那边我们的 ack 等于没作用, 它自己的
      // 发送窗口/缓冲永远不回收, 约 12 秒后(08:48:38)停止发包并闪退。
      // 换图前的对照: levelAckSeq 正常推进到 73, 一切正常。
      this.levelDelta.resetWriter();

      // 同理,**玩家状态(playerDelta)的发送侧也必须一起重置**。
      //
      // 2026-10-05 实测("房主牵着房员进入其他地图, 房员崩溃"抓包 22:11:24~22:11:43):
      //   客户端那边的"同房间玩家状态"链和 level 链一样是**按关卡(房间)**建的:
      //   它一切图, 收别人玩家状态的那条 reader 链就换了新的一条, 旧 base 找不到了。
      //   而这里只重置了 levelDelta, playerDelta 还在用换图前的 history[0] 当 base
      //   (房员那次是 seq=96/base=95 一路发到 108) -> 客户端 find(95) 永远失败 ->
      //   之后每个玩家状态包都被丢, 两边收对方的 ack 就冻在换图那一刻(房员 playerAck
      //   卡 95, 房主卡 122), 客户端发送窗口 16 格填满后(~16s)停止发包 -> 闪退。
      //   level 链因为这里 resetWriter 过(第一帧就是 base=0 的关键帧), 所以一直是好的
      //   (房员 levelAck 6->14 正常推进) —— 正好反证: 缺的就是这条 player 链。
      // 只重置 writer(发送侧), reader 保留(见上面 2026-10-01 的实测, 重置 reader 会
      // 让 ack 卡住并闪退); 重置后发给该玩家的第一帧就是完整状态的关键帧(base=0),
      // 客户端换图后新建的那条链正好从它开始同步。
      this.playerDelta.resetWriter();
    }
    this.touch();
  }

  toApi() {
    return {
      id: this.id,
      roomId: this.session.roomId,
      uuid: this.uuid.toString(),
      name: this.name,
      peerId: this.session.peerId.toString(),
      levelId: this.levelId,
      levelChangeCount: this.levelChangeCount,
      netVersion: this.netVersion?.values,
      levelHash: this.netVersion?.levelHash,
      clientVersion: this.clientVersion,
      serverVersion: this.serverVersion,
      rpcHash: this.rpcHash,
      versionLabel: this.versionLabel,
      protocolFamily: this.profile?.detectedFamily ?? null,
      protocolProfile: this.profile?.id ?? null,
      protocolLabel: this.profile?.label ?? null,
      protocolSource: this.profile?.source ?? null,
      joinedAt: this.joinedAt,
      lastSeenAt: this.lastSeenAt,
      lastRpc: this.lastRpc,
      lastOpaqueGameMsg: this.lastOpaqueGameMsg,
      snapshots: {
        player: this.playerDelta.toApi(),
        level: this.levelDelta.toApi()
      }
    };
  }
}
