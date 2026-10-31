import { PacketIds } from '../protocol/PacketCodec.js';
import { effectiveFamilyOf, sameEffectiveFamily } from '../protocol/VersionProfile.js';
// 0.13.4 单独修复(切图后会见不到人)用到的引擎标识; 其它版本走不到下面的分支。
import { engineForEntity, SKY0134_ENGINE, SKY0122_ENGINE } from '../protocol/versions/index.js';

export class LevelService {
  constructor({ players, levels, broadcaster, logger, onAuthorityChanged }) {
    this.players = players;
    this.levels = levels;
    this.broadcaster = broadcaster;
    this.logger = logger;
    this.onAuthorityChanged = onAuthorityChanged;
  }

  changeLevel(session, payload) {
    const player = this.players.getBySession(session);
    if (!player) {
      return;
    }

    const oldLevelId = player.levelId;
    player.changeLevel(payload.levelId, payload.levelChangeCount, payload.netVersion);

    // 旧关卡: 只重算本玩家的协议族(同图不同族各有各的权威)。
    this.rebalanceAuthority(oldLevelId, effectiveFamilyOf(player));

    // 参考实现 (server/packet/level_update.rs) 对每一个 LevelUpdate 都回一次
    // send_elect_to(该玩家), 客户端靠这个 NetLevelDataElect 拿到关卡快照才开始同步。
    // 我们原来只在"权威换成了别人"时才回, 于是玩家自己是权威时永远收不到 Elect
    // (实测表现: 客户端发完 LevelUpdate 就再无动作, 数秒后闪退)。
    // 这里保证"每个 LevelUpdate 恰好回一次 Elect":
    //   - 该关卡权威发生变化时 rebalanceAuthority 已经通知过全体(含本玩家), 不再重复;
    //   - 权威没变时(首次进关除外)由这里补发。
    const family = effectiveFamilyOf(player);
    const authorityBefore = this.levels.getAuthority(player.levelId, family);
    const authority = this.rebalanceAuthority(player.levelId, family);
    if (authority && authorityBefore === authority) {
      this.onAuthorityChanged?.(player, authority);
    }

    this.broadcaster.broadcast({
      id: PacketIds.PlayerChangedLevel,
      payload: {
        playerId: player.id,
        levelId: player.levelId
      }
    }, { exceptSession: session, playerOnly: true, sameFamilyAs: player });

    // 0.13.4 单独修复: "切图后会见不到人"。
    // 客户端切图时 NetGameClient::OnLevelLoadLate -> m_SendChangedLevel 发 LevelUpdate,
    // 本地 barn 里本地玩家的 levelId 由 NetModule::OnLevelLoad 直接写成新关卡 id
    // (NetPlayerBarn::GetPlayerIdsInLocalLevel 只显示 barn levelId 与本地相同的玩家)。
    // 但服务端此前只回 Elect + PlayerChangedLevel(8), 后者只更新**已存在**条目的 levelId,
    // 不会补发名册 -> 新图里的玩家永远进不了 barn, 于是"切图后见不到人"。
    // 这里按 JoinService 入房时的做法补发一次完整名录(EnterGame id=17 双投: 给切图者本人
    // + 给已经在新图里的同引擎玩家), 引擎/版本严格隔离, 其它版本完全不执行本分支。
    // 只在关卡真的变了才补发(入房后客户端也会发一次同 levelId 的 LevelUpdate, 不重复发,
    // 保证入房流程一个字节都不多)。
    if (oldLevelId !== player.levelId && engineForEntity(player) === SKY0134_ENGINE) {
      // 名录只放**这张新图里**的同引擎玩家(和客户端 barn 的语义一致, 也避免名册超过
      // barn 容量 8; NetPlayerBarn::AddPlayer 在 m_count >= 8 时直接断言崩溃)。
      const roster = {
        players: this.players.listInLevel(player.levelId)
          .filter(current => engineForEntity(current) === SKY0134_ENGINE)
          // 同一引擎里 0.13.4 / 0.14.0 / 0.15.5 的 RPC 集合不同, 名册按隔离键收窄。
          .filter(current => sameEffectiveFamily(current, player))
          .map(current => ({
            netId: current.id,
            uuid: current.uuid,
            name: current.name ?? '',
            levelId: current.levelId
          }))
      };
      const self = this.broadcaster.sendEnterGame(player.session, roster);
      const peers = this.broadcaster.resyncRosterEnterGame?.(roster, {
        levelId: player.levelId,
        engine: SKY0134_ENGINE,
        exceptSession: session,
        sameFamilyAs: player
      });
      this.logger.info({
        playerId: player.id,
        uuid: player.uuid?.toString(),
        levelId: player.levelId,
        rosterSize: roster.players.length,
        selfPackets: self?.packets ?? 0,
        peers: peers ?? null
      }, 'sky0134 level change roster resync sent');
    }

    // 0.12.2 单独分支: 与上面 0.13.4 的目的相同(切图后双方 barn 里都没有新图
    // 的名册), 但引擎/名册完全独立。只有 sky0122 引擎会进来, 其它版本不受影响。
    if (oldLevelId !== player.levelId && engineForEntity(player) === SKY0122_ENGINE) {
      // barn 只有 8 槽; 截断时先保自己(见 JoinService 的同款处理)。
      let rosterPlayers = this.players.listInLevel(player.levelId)
        .filter(current => engineForEntity(current) === SKY0122_ENGINE);
      if (rosterPlayers.length > 8) {
        rosterPlayers = [
          player,
          ...rosterPlayers.filter(current => current !== player && current.id !== player.id)
        ].slice(0, 8);
      }
      const roster = {
        players: rosterPlayers.map(current => ({
          netId: current.id,
          uuid: current.uuid,
          name: current.name ?? '',
          levelId: current.levelId
        }))
      };
      const self = this.broadcaster.sendEnterGame(player.session, roster);
      const peers = this.broadcaster.resyncRosterEnterGame?.(roster, {
        levelId: player.levelId,
        engine: SKY0122_ENGINE,
        exceptSession: session
      });
      this.logger.info({
        playerId: player.id,
        uuid: player.uuid?.toString(),
        levelId: player.levelId,
        rosterSize: roster.players.length,
        selfPackets: self?.packets ?? 0,
        peers: peers ?? null
      }, 'sky0122 level change roster resync sent');
    }

    this.logger.info({
      playerId: player.id,
      uuid: player.uuid?.toString(),
      oldLevelId,
      levelId: player.levelId,
      family,
      authorityPlayerId: authority?.id,
      electedSent: Boolean(authority)
    }, 'player changed level & elect sent');
  }

  // 重算某个关卡**某个协议族**的权威。
  //
  // levelId 是内容 id, 不同协议族的客户端会共用同一个 levelId; 权威必须按族分开
  // (legacy 权威发的 22 字节头 level state 给 modern 客户端会直接崩, 反之亦然)。
  // family 省略时: 若该关卡只活跃一族, 就按那一族重算(兼容旧调用); 多族时对每一族
  // 都重算一遍, 返回 callersFamily(由调用方传 family 决定)。
  rebalanceAuthority(levelId, family = undefined) {
    if (family === undefined) {
      const families = this.activeFamiliesInLevel(levelId);
      let result;
      for (const fam of families) {
        result = this.rebalanceAuthority(levelId, fam);
      }
      return result;
    }

    const current = this.levels.getAuthority(levelId, family);
    const players = this.players.listInLevel(levelId)
      .filter(player => effectiveFamilyOf(player) === family);

    if (current && players.includes(current)) {
      return current;
    }

    const previousAuthorityPlayerId = current?.id;
    const next = players[0];
    if (next) {
      this.levels.setAuthority(levelId, next, {
        family,
        previousAuthorityPlayerId,
        reason: current ? 'authority_left_level' : 'level_rebalance'
      });
      for (const player of players) {
        this.onAuthorityChanged?.(player, next);
      }
      return next;
    }

    // 本族已经没人了: 只清本族的权威 + 缓存数据, 不动其他族。
    this.levels.revokeAuthority(levelId, { family, reason: 'level_empty' });
    this.levels.resetFamilyData?.(levelId, family);
    return undefined;
  }

  // 该关卡当前实际有玩家的协议族(去重)。
  activeFamiliesInLevel(levelId) {
    const families = new Set();
    for (const player of this.players.listInLevel(levelId)) {
      families.add(effectiveFamilyOf(player));
    }
    return [...families];
  }
}
