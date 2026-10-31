import { PacketIds } from '../protocol/PacketCodec.js';
import { resolveProfile, effectiveFamilyOf, sameEffectiveFamily } from '../protocol/VersionProfile.js';
import { engineForEntity, SKY0122_ENGINE } from '../protocol/versions/index.js';
// 0.15.5 单独修复: 只在 JoinGame 带 179644 构建号指纹时替换 profile(见该文件注释),
// 其它版本在这里原样返回, 不会碰到 LEGACY/MODERN 的任何判定。
import { sky0155Profile } from '../protocol/versions/sky0155Engine.js';
// 0.13.4 单独修复: 只在 JoinGame 带 284/541(169938) 或 284/542(170388) 版本常量指纹时替换 profile
// (见该文件注释), 其它版本在这里原样返回。
import { sky0134Profile } from '../protocol/versions/sky0134Engine.js';
// 0.12.2 单独修复: 只在 JoinGame 带 275/516(160278) 版本常量指纹时替换 profile,
// 其它版本在这里原样返回。独立文件, 独立族键, 不碰 legacy/modern/0134/0155。
import { sky0122Profile } from '../protocol/versions/sky0122Engine.js';
// 0.7.1 单独修复: 只在 JoinGame 带 252/477(143371) 版本指纹时替换 profile,
// 其它版本在这里原样返回。独立文件、独立族键, 不碰 legacy/modern/0122/0134/0155。
import { sky071Profile } from '../protocol/versions/sky071Engine.js';

// 名册负载: 每个玩家一条记录(netId / uuid / 名字 / 关卡)。
// 老引擎的 37 字节定长记录和 EnterGamePacket.encode 需要的字段一致,
// 所以这里统一构造, 由各引擎的编码器决定外层 id 与是否带前导字节。
function rosterOf(players) {
  return players.map(current => ({
    netId: current.id,
    uuid: current.uuid,
    name: current.name ?? '',
    levelId: current.levelId
  }));
}

export class JoinService {
  constructor({ players, levels, broadcaster, logger, metrics, onAuthorityChanged, behaviour,
                packetLog = null }) {
    this.players = players;
    this.levels = levels;
    this.broadcaster = broadcaster;
    this.logger = logger;
    this.metrics = metrics;
    this.onAuthorityChanged = onAuthorityChanged;
    // UDP 联机监控: 入房时把客户端自报的版本号(构建号/版本整数)缓存一条 join 事件,
    // 落盘后下次打开监控仍能看到"这个版本连过"。只记录, 不参与任何协议判断。
    this.packetLog = packetLog;
    // 运行时开关(config/behaviour.js): enterGameOnJoin 默认 true(不开互相看不见),
    // 只在定位闪退时临时关掉, 看客户端是否还活。
    this.behaviour = behaviour ?? { enterGameOnJoin: true };
  }

  join(session, payload) {
    // 版本适配: 依据 C->S 包头布局(握手时记到 session 上)推断协议族; 或在
    // ROOM_FORCE_PROFILE 下强制。见 protocol/VersionProfile.js。
    const profile = sky071Profile(sky0122Profile(sky0134Profile(sky0155Profile(resolveProfile({
      forcedFamily: this.behaviour.versionProfile || undefined,
      layout: session.layout,
      tolerant: session.joinTolerant
    }), payload), payload), payload), payload);

    const player = this.players.add({
      session,
      uuid: payload.uuid,
      name: payload.name,
      levelId: payload.levelId,
      netVersion: payload.netVersion,
      profile,
      // JoinGame 严格布局里的版本字段(老客户端宽容解析时是 undefined -> null)
      clientVersion: payload.clientVersion ?? null,
      serverVersion: payload.serverVersion ?? null,
      rpcHash: payload.rpcHash ?? null
    });

    session.attachPlayer(player);

    // 版本号缓存(供 UDP 监控/排查用): 每个包记录会带 player.versionLabel,
    // 这里再把入房时的原始版本字段单独落一条事件, 重启/重开监控后仍可查。
    this.packetLog?.recordEvent?.({
      peerId: session.peerId.toString(),
      playerId: player.id,
      uuid: player.uuid?.toString?.() ?? null,
      name: player.name,
      levelId: player.levelId,
      ver: player.versionLabel,
      clientVersion: player.clientVersion,
      serverVersion: player.serverVersion,
      rpcHash: player.rpcHash,
      netVersion: player.netVersion?.values ?? null,
      protocolFamily: profile.detectedFamily,
      protocolProfile: profile.id,
      protocolLabel: profile.label,
      headerLayout: session.layout ?? null,
      tolerantJoin: Boolean(session.joinTolerant),
      truncatedJoin: Boolean(payload.truncated)
    });

    this.metrics.onlinePlayers.set(this.players.size);

    // 进游戏流程按引擎分开(用户要求: 不同版本不能套用, 要分开发包)——
    //   * 给**新玩家自己**: 一份完整的 EnterGame 名册, 由它自己引擎的 id 编码
    //     (老引擎 5 带前导字节 / 新引擎 17), 让它的 NetRecvEnterGame / 快照读写器起来。
    //   * 给**已经在房的老玩家**: 只发 PlayerJoined(6)(老引擎), 绝不重发 EnterGame(5)
    //     —— 老客户端主连接遇 case 5 会断言 abort, pending 连接也会被关掉(见
    //     protocol/versions/legacyEngine.js 的实证注释)。新引擎继续沿用重发 EnterGame。
    const engine = engineForEntity(player, { eraEnterGameId: this.behaviour.eraEnterGameId });
    // 名册只放**同引擎**的玩家: 老/新引擎的 37 字节记录虽然同名同布局, 但跨引擎的玩家
    // 拿不到对方的关卡状态(familyIsolation), 列进名册只会变成看不见也动不了的"幽灵"
    // 化身。按引擎分开编名册, 与用户要求的"不同版本不能套用"一致。
    let rosterPlayers = this.players.list().filter(current => {
      if (engineForEntity(current, { eraEnterGameId: this.behaviour.eraEnterGameId }) !== engine) {
        return false;
      }
      // 同一引擎里还有不同协议版本(RPC 集合不同, 例如 0.13.4 与 0.15.5 共用
      // sky0134 引擎): 拿不到对方关卡状态的玩家列进名册只会变成"幽灵"化身,
      // 而且 RPC/level state 一旦混发就会断言闪退。名册按隔离键收窄。
      if (!sameEffectiveFamily(current, player)) {
        return false;
      }
      // 0.12.2 单独规则: 它的 NetPlayerBarn::AddPlayer 在 m_count >= 8 时直接断言,
      // 名册只放**同一关卡**的同引擎玩家(与 barn 的 GetPlayerIdsInLocalLevel 语义一致),
      // 不会把别的图的玩家塞进去占满槽位。其它引擎保持原来的“全房间同引擎”名册。
      if (engine === SKY0122_ENGINE) {
        return current.levelId === player.levelId;
      }
      return true;
    });
    if (engine === SKY0122_ENGINE && rosterPlayers.length > 8) {
      // barn 只有 8 个槽位; 截断时务必先保自己, 否则新玩家可能连自己都不在名册里。
      rosterPlayers = [
        player,
        ...rosterPlayers.filter(current => current !== player && current.id !== player.id)
      ].slice(0, 8);
    }
    const roster = { players: rosterOf(rosterPlayers) };

    if (this.behaviour.enterGameOnJoin) {
      const self = this.broadcaster.sendEnterGame(player.session, roster);
      const others = this.broadcaster.announceJoin(player, roster);

      this.logger?.info?.({
        playerId: player.id,
        family: effectiveFamilyOf(player),
        engine: engine.id,
        announceJoinWith: engine.announceJoinWith,
        selfEnterGame: {
          ids: player.profile?.enterGamePacketIds ?? [PacketIds.EnterGame],
          packets: self?.packets ?? 0
        },
        others,
        rosterSize: roster.players.length
      }, 'enter game delivered (engine-split)');
    }

    // 权威按协议族分开查: 同一张图(levelId)里 legacy 玩家和 modern 玩家互不干扰,
    // 各自拿自己族的权威快照(跨族 level state 会导致对端直接崩)。
    const family = effectiveFamilyOf(player);
    const authority = this.levels.getAuthority(player.levelId, family);
    if (!authority) {
      this.levels.setAuthority(player.levelId, player, { family, reason: 'join_first_player' });
      this.onAuthorityChanged?.(player, player);
    } else {
      this.onAuthorityChanged?.(player, authority);
    }

    this.logger.info({
      peerId: session.peerId.toString(),
      playerId: player.id,
      uuid: player.uuid.toString(),
      levelId: player.levelId,
      family,
      // 关卡号是从 JoinGame 负载哪儿解出来的: 严格布局 / 末 4 字节(trailing) /
      // 关卡摘要块(summary@偏移)。老客户端解析出 0 时会有 summary@ 兜底(见 PacketCodec)。
      levelIdSource: payload.levelIdSource ?? (payload.tolerant ? 'tolerant' : 'strict'),
      levelIdTrailing: payload.levelIdTrailing ?? null,
      // 客户端自报的版本字段(严格布局里才有; 宽容解析没有)。客户端崩溃日志里那句
      // "please check CLIENT_VERSION if RPCs have changed" 指的就是 clientVersion ——
      // 记下来, 以后对崩溃报告时不用再靠 UA 猜。
      clientServerVersion: payload.serverVersion ?? null,
      clientVersion: payload.clientVersion ?? null,
      clientRpcHash: payload.rpcHash ?? null,
      protocolFamily: profile.detectedFamily,
      protocolProfile: profile.id,
      protocolLabel: profile.label,
      protocolSource: profile.source,
      protocolConfidence: profile.confidence,
      headerLayout: session.layout ?? null,
      tolerantJoin: Boolean(session.joinTolerant),
      joinTruncated: Boolean(payload.truncated)
    }, 'player joined');

    return player;
  }
}
