import { PacketIds } from '../PacketIds.js';
import { TgcUuid } from '../types/TgcUuid.js';
import { NetVersion } from '../types/NetVersion.js';
import { NetAddress } from '../types/NetAddress.js';

const MAX_JOIN_LEVEL_DATA_BYTES = 0x2000;

function decodeMove(reader) {
  const address = NetAddress.decode(reader);
  const moveNonce = reader.readUInt64();
  const splitType = reader.readUInt8();
  const playerCount = reader.readUInt32();
  const players = [];

  for (let index = 0; index < playerCount; index += 1) {
    players.push(TgcUuid.decode(reader));
  }

  return {
    address,
    moveNonce,
    splitType,
    playerCount,
    players
  };
}

export class JoinGamePacket {
  static id = PacketIds.JoinGame;
  static name = 'JoinGamePacket';

  static decode(reader) {
    const uuid = TgcUuid.decode(reader);

    reader.skip(16);
    reader.skip(32);

    const move = decodeMove(reader);

    const joinToken = reader.readUInt32();
    const joinState = reader.readUInt8();
    const joinFlags = reader.readUInt32();
    const joinSignature = reader.readBytes(16);
    const serverVersion = reader.readUInt16();
    const clientVersion = reader.readUInt16();
    const rpcHash = reader.readUInt32();

    const netVersion = NetVersion.decode(reader);

    const levelChangeCount = reader.readUInt8();
    const levelId = reader.readUInt32();
    const levelSummary = reader.readBytes(64);
    const localPlayerId = reader.readUInt32();
    const currentPlayerId = reader.readUInt32();
    const levelDataLength = reader.readUInt16();

    // 到这里关键字段(uuid / netVersion / levelChangeCount / levelId)都已解析完毕。
    // levelData 之后的内容会随客户端版本增删(0.23~0.34 各改过), 所以长度字段一旦不可信
    // 就只截尾、不抛错 —— 否则新版本客户端会因为一个尾部字段直接把整个 JoinGame 判失败,
    // 卡在门外(连不上房/看不见对方)。
    let levelData = Buffer.alloc(0);
    let truncated = false;
    if (levelDataLength <= MAX_JOIN_LEVEL_DATA_BYTES && levelDataLength <= reader.remaining) {
      levelData = reader.readBytes(levelDataLength);
    } else {
      truncated = true;
      levelData = reader.readBytes(reader.remaining);
    }

    let moving = false;
    let hasTransferAddress = false;
    let mergeState = 0;
    let transferAddress = null;
    let moveResult = 0;
    if (!truncated) {
      try {
        moving = Boolean(reader.readUInt8());
        hasTransferAddress = Boolean(reader.readUInt8());
        mergeState = reader.readUInt8();
        transferAddress = NetAddress.decode(reader);
        moveResult = reader.readUInt32();
      } catch {
        // 尾部字段错位/缺失: 忽略, 保留已解析的关键字段。
        truncated = true;
      }
    }

    return {
      uuid,
      move,
      joinToken,
      joinState,
      joinFlags,
      joinSignature,
      serverVersion,
      clientVersion,
      rpcHash,
      netVersion,
      levelChangeCount,
      levelId,
      levelSummary,
      levelState: levelSummary,
      localPlayerId,
      currentPlayerId,
      levelData,
      moving,
      hasTransferAddress,
      mergeState,
      mergeFlags: mergeState,
      transferAddress,
      moveResult,
      // true = 尾部 levelData/后续字段因版本差异被截断(关键字段仍有效)。
      truncated
    };
  }
}
