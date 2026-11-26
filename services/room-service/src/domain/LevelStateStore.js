import { effectiveFamilyOf } from '../protocol/VersionProfile.js';

// 关卡 id 是**内容 id**(同一张图所有版本都一样), 所以不同协议族的客户端本来就会
// 落在同一个 levelId 上。同一个 levelId 下, **每个协议族各有一份权威 + 一份 level data
// 缓存** —— 这是本类所有键都带 family 的原因(见 config/behaviour.js 的 familyIsolation,
// 以及 2026-10-06 "legacy 客户端收到 modern 的 2331 字节 level state 后闪退" 的实测)。
export class LevelStateStore {
  constructor({ roomId, logger }) {
    this.roomId = roomId;
    this.logger = logger;
    this.authorities = new Map();
    this.levelData = new Map();
  }

  // 存储键: family + levelId(family 为 null 时按 "unknown" 单独一族)。
  keyFor(levelId, family) {
    return `${family ?? 'unknown'}\u0000${levelId}`;
  }

  // 从玩家身上取有效协议族(见 protocol/VersionProfile.js)。
  familyOf(player) {
    return effectiveFamilyOf(player);
  }

  getAuthority(levelId, family = undefined) {
    if (family !== undefined) {
      return this.authorities.get(this.keyFor(levelId, family));
    }
    // 不指定族: 返回该关卡任意一族的权威(仅用于日志/管理面板这类"看个大概"的地方)。
    for (const [key, player] of this.authorities) {
      if (key.endsWith(`\u0000${levelId}`)) {
        return player;
      }
    }
    return undefined;
  }

  // 该关卡当前有权威记录的协议族集合('unknown' 归一成 null)。
  familiesForLevel(levelId) {
    const families = [];
    for (const key of this.authorities.keys()) {
      const [family, keyLevelId] = key.split('\u0000');
      if (keyLevelId === String(levelId)) {
        families.push(family === 'unknown' ? null : family);
      }
    }
    return families;
  }

  setAuthority(levelId, player, context = {}) {
    const family = context.family !== undefined ? context.family : this.familyOf(player);
    const key = this.keyFor(levelId, family);
    const previous = this.authorities.get(key);
    this.authorities.set(key, player);

    if (previous !== player) {
      this.logger?.info({
        roomId: this.roomId,
        levelId,
        family,
        authorityPlayerId: player.id,
        previousAuthorityPlayerId: context.previousAuthorityPlayerId ?? previous?.id,
        peerId: player.session?.peerId?.toString(),
        uuid: player.uuid?.toString(),
        reason: context.reason
      }, 'level authority elected');
    }
  }

  revokeAuthority(levelId, context = {}) {
    if (context.family !== undefined) {
      const key = this.keyFor(levelId, context.family);
      const previous = this.authorities.get(key);
      if (this.authorities.delete(key)) {
        this.logger?.info({
          roomId: this.roomId,
          levelId,
          family: context.family,
          authorityPlayerId: previous?.id,
          peerId: previous?.session?.peerId?.toString(),
          uuid: previous?.uuid?.toString(),
          reason: context.reason
        }, 'level authority revoked');
      }
      return;
    }
    // 不指定族: 撤掉该关卡所有族(管理面板/整关清空用)。
    for (const key of [...this.authorities.keys()]) {
      if (key.endsWith(`\u0000${levelId}`)) {
        const previous = this.authorities.get(key);
        this.authorities.delete(key);
        this.logger?.info({
          roomId: this.roomId,
          levelId,
          family: key.split('\u0000')[0],
          authorityPlayerId: previous?.id,
          reason: context.reason
        }, 'level authority revoked');
      }
    }
  }

  isAuthority(player, levelId = player.levelId) {
    return this.getAuthority(levelId, this.familyOf(player)) === player;
  }

  saveLevelData(levelId, data) {
    // family 由权威方决定(缓存的就是这一族客户端打包出来的状态)。
    const family = data.family !== undefined ? data.family : data.authorityFamily;
    this.levelData.set(this.keyFor(levelId, family), {
      // opaque=true 表示 payload 是客户端自己打包好的 level state(不透明块),
      // 不能再用 22 字节头重新封装(见 GameMessageService.createLevelElectPayload)。
      opaque: Boolean(data.opaque),
      family: family ?? null,
      mergeState: data.mergeState ?? 0,
      unknown1: data.unknown1 ?? 0,
      unknown2: data.unknown2 ?? 0,
      unknown3: data.unknown3 ?? 0,
      levelHash: data.levelHash ?? data.unknown4 ?? 0,
      unknown4: data.unknown4 ?? 0,
      unknown5: data.unknown5 ?? 0,
      unknown6: data.unknown6 ?? 0,
      unknown7: data.unknown7 ?? 0,
      dataLength: data.dataLength ?? (data.payload?.length ?? 0),
      payload: Buffer.from(data.payload ?? Buffer.alloc(0)),
      updatedAt: data.updatedAt ?? Date.now(),
      authorityPlayerId: data.authorityPlayerId,
      authorityFamily: family ?? null
    });
  }

  loadLevelData(levelId, family = undefined) {
    if (family !== undefined) {
      return this.levelData.get(this.keyFor(levelId, family));
    }
    for (const [key, record] of this.levelData) {
      if (key.endsWith(`\u0000${levelId}`)) {
        return record;
      }
    }
    return undefined;
  }

  // 只清某一族的缓存 level data(权威由 revokeAuthority 单独处理)。
  resetFamilyData(levelId, family) {
    this.levelData.delete(this.keyFor(levelId, family));
  }

  reset(levelId, context = {}) {
    if (levelId === undefined) {
      this.authorities.clear();
      this.levelData.clear();
      return;
    }

    this.revokeAuthority(levelId, context);
    for (const key of [...this.levelData.keys()]) {
      if (key.endsWith(`\u0000${levelId}`)) {
        this.levelData.delete(key);
      }
    }
  }

  toApi() {
    const entries = [];
    const seen = new Set();
    const push = (key, kind) => {
      if (seen.has(key)) {
        for (const entry of entries) {
          if (entry._key === key) {
            entry[kind] = true;
          }
        }
        return;
      }
      seen.add(key);
      const [family, levelId] = key.split('\u0000');
      const authority = this.authorities.get(key);
      const data = this.levelData.get(key);
      entries.push({
        _key: key,
        roomId: this.roomId,
        levelId: Number(levelId),
        family: family === 'unknown' ? null : family,
        authorityPlayerId: authority?.id,
        hasLevelData: Boolean(data),
        levelDataOpaque: data?.opaque ?? false,
        levelDataBytes: data?.payload?.length ?? 0,
        levelHash: data?.levelHash ?? 0,
        mergeState: data?.mergeState ?? 0,
        updatedAt: data?.updatedAt
      });
    };
    for (const key of this.authorities.keys()) {
      push(key, 'hasAuthority');
    }
    for (const key of this.levelData.keys()) {
      push(key, 'hasLevelData');
    }
    return entries.map(({ _key, ...rest }) => rest);
  }
}
