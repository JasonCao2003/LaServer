import { PacketIds } from '../PacketIds.js';
import { NetVersion } from '../types/NetVersion.js';

export class LevelUpdatePacket {
  static id = PacketIds.LevelUpdate;
  static name = 'LevelUpdatePacket';

  static decode(reader) {
    const levelChangeCount = reader.readUInt8();
    const netVersion = NetVersion.decode(reader);
    const levelStatus = reader.readUInt8();
    const levelId = reader.readUInt32();
    const levelSummary = reader.readBytes(64);
    // 老客户端(< 0.20.0)的 LevelUpdate 负载正好 82 字节: 1 + NetVersion(12) + 1 + 4 + 64,
    // 没有新客户端末尾那 1 个字节。之前这里强制 readUInt8() 会越界抛错, 整个包被判
    // "unsupported" 丢掉 -> 服务端不给客户端回 NetLevelDataElect -> 客户端等关卡数据
    // 等到闪退(实测: 入房后只发了这一个包, 7.4 秒后掉线)。
    // 参考实现 that-sky-colorsky-udp 的 level_update.rs 只取 payload[14..18] 当 levelId,
    // 也正是这个偏移量, 所以尾字节按"有就读、没有就算了"处理。
    const trailingState = reader.remaining >= 1 ? reader.readUInt8() : 0;
    return {
      levelChangeCount,
      netVersion,
      levelStatus,
      levelId,
      levelSummary,
      trailingState
    };
  }

  static encode(writer, packet) {
    writer.writeUInt8(packet.levelChangeCount ?? 0);
    const netVersion = packet.netVersion instanceof NetVersion
      ? packet.netVersion
      : new NetVersion(packet.netVersion?.values ?? packet.netVersion ?? []);
    netVersion.encode(writer);
    writer.writeUInt8(packet.levelStatus ?? packet.unknown2 ?? 0);
    writer.writeUInt32(packet.levelId);
    writer.writeBytes(packet.levelSummary ?? packet.unknown3 ?? Buffer.alloc(64));
    writer.writeUInt8(packet.trailingState ?? packet.unknown4 ?? 0);
  }
}
