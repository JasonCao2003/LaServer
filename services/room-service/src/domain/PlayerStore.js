import { Player } from './Player.js';
import { StateError } from '../utils/errors.js';
import { effectiveFamilyOf } from '../protocol/VersionProfile.js';

export class PlayerStore {
  constructor({ maxPlayers }) {
    this.maxPlayers = maxPlayers;
    this.playersById = new Map();
    this.playersBySession = new Map();
    this.playersByUuid = new Map();
  }

  get size() {
    return this.playersById.size;
  }

  add({ session, uuid, name = '', levelId, netVersion, profile = null,
        clientVersion = null, serverVersion = null, rpcHash = null }) {
    if (this.playersById.size >= this.maxPlayers) {
      throw new StateError('room is full');
    }

    if (this.playersBySession.has(session.id)) {
      throw new StateError('session already has a player', { sessionId: session.id });
    }

    const id = this.allocateId();
    const player = new Player({
      id, uuid, session, name, levelId, netVersion, profile,
      clientVersion, serverVersion, rpcHash
    });

    this.playersById.set(id, player);
    this.playersBySession.set(session.id, player);
    this.playersByUuid.set(uuid.toString(), player);

    return player;
  }

  allocateId() {
    for (let id = 1; id < 255; id += 1) {
      if (!this.playersById.has(id)) {
        return id;
      }
    }

    throw new StateError('no player id available');
  }

  getBySession(session) {
    return this.playersBySession.get(session.id);
  }

  getById(id) {
    return this.playersById.get(id);
  }

  removeBySession(session) {
    const player = this.getBySession(session);
    if (!player) {
      return undefined;
    }

    this.playersById.delete(player.id);
    this.playersBySession.delete(session.id);
    this.playersByUuid.delete(player.uuid.toString());
    return player;
  }

  list() {
    return Array.from(this.playersById.values());
  }

  // family 省略时返回关卡内全部玩家; 指定时只返回该协议族的玩家(levelId 是内容
  // id, 同图不同族会共用同一个 levelId, 见 domain/LevelStateStore.js)。
  listInLevel(levelId, family = undefined) {
    const players = this.list().filter(player => player.levelId === levelId);
    if (family === undefined) {
      return players;
    }
    return players.filter(player => effectiveFamilyOf(player) === family);
  }
}
