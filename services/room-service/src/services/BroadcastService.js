import { effectiveFamilyOf, enterGamePacketIdsFor } from '../protocol/VersionProfile.js';
import { engineForEntity, playerRecordFor, LEGACY_ENGINE, SKY0122_ENGINE, SKY071_ENGINE } from '../protocol/versions/index.js';
import { behaviour } from '../config/behaviour.js';

export class BroadcastService {
  constructor({ codec, transport, sessions, metrics, packetLog = null }) {
    this.codec = codec;
    this.transport = transport;
    this.sessions = sessions;
    this.metrics = metrics;
    // UDP 联机监控的数据源: 每个真正发出去的包都记一条。
    this.packetLog = packetLog;
  }

  _recordOut(session, buffer, packetId, note) {
    this.packetLog?.record({
      dir: 'out',
      peerId: session?.peerId,
      playerId: session?.player?.id ?? null,
      buffer,
      packetId,
      note,
      // 客户端版本号(入房时缓存, 见 domain/Player.js 的 versionLabel)
      ver: session?.player?.versionLabel ?? null
    });
  }

  send(session, packet, options) {
    const buffer = this.codec.encodeServer(packet);
    this.metrics?.packetBytes?.inc({ direction: 'server' }, buffer.length);
    this._recordOut(session, buffer, packet.id, 'unicast');
    this.transport.send(session.peerId, buffer, options);
  }

  broadcast(packet, { exceptSession, playerOnly = true, sameFamilyAs = undefined } = {}) {
    const buffer = this.codec.encodeServer(packet);
    this.broadcastEncoded(buffer, { exceptSession, playerOnly, sameFamilyAs, packetId: packet.id });
  }

  // "进游戏"(EnterGame / 玩家列表)必须**按目标自己的协议族选包 id**:
  //   老族(0.6.5~0.21.x) -> id 5, 新族(0.22.0+) -> id 17。
  // 这不是可选优化: 老引擎的 NetRecvMsg 里 case 5 才初始化快照读写器
  // (SnapshotWriter/SnapshotReader + FriendBarn::NetRecvEnterGame), 而 case 17
  // 只是设标志位 —— 老客户端收到 17 会去解一个从未填充过的快照缓冲区(症状:
  // "连上了但一个包都不发 / 见不到人", 甚至断言闪退)。见 PacketIds.js 的长注释。
  //
  // 负载完全相同, 只有外层 id 不同, 所以这里对每个目标分别编码(不能用 broadcast
  // 的"编码一次发所有人")。packetIdsFor 可覆盖(默认按玩家的 profile 取)。
  // 把"进游戏/玩家名册"发给**一个**会话(按该会话自己引擎的 id 编码)。
  // 新玩家入房时用它: 老引擎只要自己在房, 就必须收到 EnterGame(5) 把流程踢起来
  // (老客户端的 pending 连接就在等这个包), 但它不该收到别人的 EnterGame。
  sendEnterGame(session, payload, { packetIdsFor = undefined } = {}) {
    if (!session || session.closed) {
      return { targets: 0, packets: 0 };
    }

    const ids = packetIdsFor
      ? packetIdsFor(session.player)
      : enterGamePacketIdsFor(session.player, { eraEnterGameId: behaviour.eraEnterGameId });
    if (!ids || !ids.length) {
      return { targets: 0, packets: 0 };
    }

    let packets = 0;
    for (const id of ids) {
      const buffer = this.codec.encodeServer({ id, payload });
      this.metrics?.packetBytes?.inc({ direction: 'server' }, buffer.length);
      this._recordOut(session, buffer, id, 'unicast-enter-game');
      this.transport.send(session.peerId, buffer);
      packets += 1;
    }

    return { targets: 1, packets };
  }

  // 有新玩家加入时, 通知**已经在房里的其他玩家**。
  //
  // 按引擎分开(用户要求: 不同版本不能套用, 要分开发包):
  //   老引擎(EnterGame id=5): 只发 PlayerJoined(6) —— 老客户端再收一次 EnterGame(5)
  //     会关掉它的 pending 连接、主连接遇到 case 5 更是直接断言 abort
  //     (见 protocol/versions/legacyEngine.js 的实证注释)。
  //   新引擎(id=17): 沿用一直可用的做法, 重发一份完整名册的 EnterGame。
  //
  // behaviour.announceJoinWithPlayerJoined=false 可强制回退成"所有族都重发 EnterGame"
  // (改动前的行为), 便于真机 A/B 对比。
  announceJoin(newPlayer, payload, { packetIdsFor = undefined } = {}) {
    if (!newPlayer || !newPlayer.session) {
      return { targets: 0, packets: 0, mode: 'none' };
    }

    const engine = engineForEntity(newPlayer, { eraEnterGameId: behaviour.eraEnterGameId });
    // 0.7.1 与 legacy 一样只认 PlayerJoined(6): 它的主连接 NetGameClient::Update
    // 遇到 case 5 会直接断言 abort, 所以不能重发 EnterGame(见 versions/sky071Engine.js)。
    const usePlayerJoined = (engine === LEGACY_ENGINE || engine === SKY071_ENGINE)
      && behaviour.announceJoinWithPlayerJoined !== false;

    let targets = 0;
    let packets = 0;

    for (const session of this.sessions.list()) {
      if (session.closed || session === newPlayer.session) {
        continue;
      }
      if (!session.player) {
        continue;
      }
      // 只通知同一个引擎的玩家: id 6 在老/新引擎里的含义与布局是分开的,
      // 而且跨族玩家的 uuid/名册本来就不该混在一起(见 behaviour.familyIsolation)。
      if (engineForEntity(session.player, { eraEnterGameId: behaviour.eraEnterGameId }) !== engine) {
        continue;
      }
      // 同一引擎内部还按协议版本隔离(0.13.4 与 0.15.5 共用引擎但 RPC 集合不同):
      // 名册混发会让对端看到"幽灵"玩家, 关卡状态混发则直接断言闪退。
      if (effectiveFamilyOf(session.player) !== effectiveFamilyOf(newPlayer)) {
        continue;
      }
      // 0.12.2/0.12.1: 名册按关卡编(见 JoinService), 只通知同一关卡的玩家,
      // 否则别图玩家会收到本图名册并把幽灵条目塞进 barn。其它引擎不变。
      if (engine === SKY0122_ENGINE && session.player.levelId !== newPlayer.levelId) {
        continue;
      }

      targets += 1;

      if (usePlayerJoined) {
        for (const id of engine.playerJoinedPacketIds) {
          const buffer = this.codec.encodeServer({ id, payload: playerRecordFor(engine, newPlayer) });
          this.metrics?.packetBytes?.inc({ direction: 'server' }, buffer.length);
          this._recordOut(session, buffer, id, 'broadcast-player-joined');
          this.transport.send(session.peerId, buffer);
          packets += 1;
        }
        continue;
      }

      const ids = packetIdsFor
        ? packetIdsFor(session.player)
        : enterGamePacketIdsFor(session.player, { eraEnterGameId: behaviour.eraEnterGameId });
      for (const id of ids ?? []) {
        const buffer = this.codec.encodeServer({ id, payload });
        this.metrics?.packetBytes?.inc({ direction: 'server' }, buffer.length);
        this._recordOut(session, buffer, id, 'broadcast-enter-game');
        this.transport.send(session.peerId, buffer);
        packets += 1;
      }
    }

    return {
      targets,
      packets,
      mode: usePlayerJoined ? 'PlayerJoined' : 'EnterGame'
    };
  }

  // 0.13.4 单独修复(切图后会见不到人): 切图成功后, 只给"同一引擎且已经在新图里"的玩家
  // 重发一份完整名录 EnterGame(17)。0.13.4 客户端 active 分发表里 id 17 就是
  // m_HandlePlayerJoined(名册同步: 名录里的玩家 AddPlayer/更新 levelId, 名录外且不在
  // Move 组里的玩家 RemovePlayer), 而 PlayerChangedLevel(8) 只改已存在条目的 levelId,
  // 不会把新图里的玩家补进 barn —— 所以切图后本地 barn levelId 变了却没有任何新条目,
  // 结果"见不到人"。只有 LevelService 的 sky0134 分支调用本方法, 其它引擎/版本不受影响。
  resyncRosterEnterGame(payload, { levelId, engine, exceptSession = undefined, sameFamilyAs = undefined } = {}) {
    const family = sameFamilyAs === undefined
      ? undefined
      : (typeof sameFamilyAs === 'string' ? sameFamilyAs : effectiveFamilyOf(sameFamilyAs));
    let targets = 0;
    let packets = 0;

    for (const session of this.sessions.list()) {
      if (session.closed || session === exceptSession || !session.player) {
        continue;
      }
      const player = session.player;
      if (player.levelId !== levelId) {
        continue;
      }
      if (engineForEntity(player, { eraEnterGameId: behaviour.eraEnterGameId }) !== engine) {
        continue;
      }
      if (family !== undefined && effectiveFamilyOf(player) !== family) {
        continue;
      }

      const ids = enterGamePacketIdsFor(player, { eraEnterGameId: behaviour.eraEnterGameId });
      if (!ids || !ids.length) {
        continue;
      }

      targets += 1;
      for (const id of ids) {
        const buffer = this.codec.encodeServer({ id, payload });
        this.metrics?.packetBytes?.inc({ direction: 'server' }, buffer.length);
        this._recordOut(session, buffer, id, 'level-resync-enter-game');
        this.transport.send(session.peerId, buffer);
        packets += 1;
      }
    }

    return { targets, packets };
  }

  broadcastEnterGame(payload, { exceptSession, playerOnly = true, packetIdsFor = undefined } = {}) {
    let packets = 0;
    let targets = 0;

    for (const session of this.sessions.list()) {
      if (session.closed || session === exceptSession) {
        continue;
      }
      if (playerOnly && !session.player) {
        continue;
      }

      const ids = packetIdsFor
        ? packetIdsFor(session.player)
        : enterGamePacketIdsFor(session.player, { eraEnterGameId: behaviour.eraEnterGameId });
      if (!ids || !ids.length) {
        continue;
      }

      targets += 1;
      for (const id of ids) {
        const buffer = this.codec.encodeServer({ id, payload });
        this.metrics?.packetBytes?.inc({ direction: 'server' }, buffer.length);
        this._recordOut(session, buffer, id, 'broadcast-enter-game');
        this.transport.send(session.peerId, buffer);
        packets += 1;
      }
    }

    return { targets, packets };
  }

  // sameFamilyAs: 只发给与它同协议族的会话(可以是 player 对象或 family 字符串)。
  // 关卡/玩家状态在跨族之间不可互通(字节布局不同), 广播前必须按族过滤。
  broadcastEncoded(buffer, { exceptSession, playerOnly = true, packetId = null, note = 'broadcast', sameFamilyAs = undefined } = {}) {
    const family = sameFamilyAs === undefined
      ? undefined
      : (typeof sameFamilyAs === 'string' ? sameFamilyAs : effectiveFamilyOf(sameFamilyAs));
    this.metrics?.packetBytes?.inc({ direction: 'server' }, buffer.length);

    for (const session of this.sessions.list()) {
      if (session.closed || session === exceptSession) {
        continue;
      }

      if (playerOnly && !session.player) {
        continue;
      }

      if (family !== undefined && effectiveFamilyOf(session.player) !== family) {
        continue;
      }

      this._recordOut(session, buffer, packetId, note);
      this.transport.send(session.peerId, buffer);
    }
  }
}
