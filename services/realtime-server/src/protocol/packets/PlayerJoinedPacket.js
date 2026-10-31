import { PacketIds } from '../PacketIds.js';

// PlayerJoined(id 6): 老引擎里"又有一个玩家进房了"的增量通知。
//
// 负载就是 EnterGame 里的一条玩家记录 —— 两者在客户端是同一个结构体
// (`NetPlayer` / `NetPlayerInfo`), 序列化函数逐句对得上, 0.7.1 与 0.13.4 完全一致:
//
//   0.7.1 : EnterGameMsg::Serialize 每条记录 (dump L33877)
//             BinaryStream::Serialize(a2, netId);                  // u8
//             TgcUuid::Serialize(uuid, a2);                        // 16 字节裸 uuid
//             BinaryStream::SerializeBytes(a2, name, 0x10u);       // 16 字节定长名字
//             BinaryStream::Serialize(a2, levelId);                // u32
//   0.7.1 : NetGameClient::Update 主连接的 ReceiveMessage switch 里有 `case 6:`(L32183),
//           也就是 **id 6 是它主动处理的合法 S->C 包**(同一 switch 里 case 0/case 5 会
//           直接断言 abort —— 见 legacyEngine.js, 这就是"不能再喂一次 EnterGame"的原因)。
//
// 记录 = 1 + 16 + 16 + 4 = 37 字节, 与 versions/legacyEngine.js 的 playerRecordBytes 一致。
export class PlayerJoinedPacket {
  static id = PacketIds.PlayerJoined;
  static name = 'PlayerJoinedPacket';

  static encode(writer, packet) {
    writer.writeUInt8(packet.netId & 0xff);
    packet.uuid.encode(writer);
    writer.writeFixedString(packet.name ?? '', 16);
    writer.writeUInt32(packet.levelId);
  }
}
