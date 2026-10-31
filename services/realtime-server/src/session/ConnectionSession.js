let nextSessionId = 1;

export class ConnectionSession {
  constructor({ peerId, roomId }) {
    this.id = nextSessionId++;
    this.peerId = peerId;
    this.roomId = roomId;
    this.connectedAt = Date.now();
    this.lastPacketAt = this.connectedAt;
    this.player = null;
    this.joinTimer = null;
    // 连上后"催话"定时器(见 RoomServer.scheduleConnectNudge): 老客户端连上后会
    // 等服务端先说话, 太久没收到任何客户端包就补发一个 EnterGame。
    this.connectNudgeTimer = null;
    // 收到过多少个客户端包(判断"这个 peer 到底说没说话", 见 scheduleConnectNudge)。
    this.packetsReceived = 0;
    this.closed = false;
    this.badPackets = 0;
  }

  markPacket(now = Date.now()) {
    this.lastPacketAt = now;
  }

  attachPlayer(player) {
    this.player = player;
    if (this.connectNudgeTimer) {
      clearTimeout(this.connectNudgeTimer);
      this.connectNudgeTimer = null;
    }
  }

  close() {
    this.closed = true;
    if (this.joinTimer) {
      clearTimeout(this.joinTimer);
      this.joinTimer = null;
    }
    if (this.connectNudgeTimer) {
      clearTimeout(this.connectNudgeTimer);
      this.connectNudgeTimer = null;
    }
  }

  registerBadPacket() {
    this.badPackets += 1;
    return this.badPackets;
  }

  toApi() {
    return {
      id: this.id,
      roomId: this.roomId,
      peerId: this.peerId.toString(),
      connectedAt: this.connectedAt,
      lastPacketAt: this.lastPacketAt,
      playerId: this.player?.id,
      badPackets: this.badPackets
    };
  }
}
