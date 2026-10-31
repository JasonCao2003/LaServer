import { PacketIds } from '../PacketIds.js';

// "进游戏 / 玩家名册"。
//
// 同一个包, **老引擎和新引擎的负载不一样**(详见 versions/legacyEngine.js 的长注释):
//   老引擎(0.6.5~0.21.5, id=5):  payload = [前导 u8][count:u8][每人 37 字节]
//   新引擎(0.22.0~0.34.3, id=17): payload = [count:u8][每人 37 字节]
// 前导字节是 `EnterGameMsg::Serialize` 的第一句 `BinaryStream::Serialize(a2, this)`
// (this[0] 从来没有被赋值, 所以恒为 0), 新引擎的 dump 被 strip 了没法核对, 但真机
// 实证: 现在这个"无前导"格式在 0.22.0 上联机正常 —— 所以两边分开, 谁也别迁就谁。
//
// 每人 37 字节 = netId u8 + uuid 16 + 定长名字 16(我们发空串, 即 16 个 0) + levelId u32。
export class EnterGamePacket {
  static id = PacketIds.EnterGame;
  static name = 'EnterGamePacket';

  static encode(writer, packet, options = {}) {
    if (options.leadingByte === true) {
      // 老引擎的前导字节(内容恒 0; 客户端读进 this[0] 后从不使用)。
      writer.writeUInt8(0);
    }

    const players = packet.players ?? [];
    writer.writeUInt8(players.length & 0xff);

    for (const player of players) {
      writer.writeUInt8(player.netId & 0xff);
      player.uuid.encode(writer);
      // 定长 16 字节名字(空串 = 全 0)。
      writer.writeFixedString(player.name ?? '', 16);
      writer.writeUInt32(player.levelId);
    }
  }
}
