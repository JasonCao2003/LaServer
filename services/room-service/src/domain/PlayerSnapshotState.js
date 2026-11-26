import { SnapshotReader } from '../protocol/snapshot/SnapshotReader.js';
import { SnapshotWriter } from '../protocol/snapshot/SnapshotWriter.js';

export class PlayerSnapshotState {
  constructor() {
    this.reader = new SnapshotReader();
    this.writer = new SnapshotWriter();
    this.stats = {
      lastReadAt: undefined,
      lastReadSequence: undefined,
      lastReadPayloadBytes: 0,
      lastReadDataBytes: 0,
      lastReadError: undefined,
      lastReadStatus: undefined,
      lastWriteAt: undefined,
      lastWriteSequence: undefined,
      lastWritePayloadBytes: 0,
      lastWriteDataBytes: 0,
      lastWriteStatus: undefined,
      droppedWrites: 0
    };
    this.reader.onSync(status => {
      this.stats.lastReadStatus = status;
    });
    this.writer.onSync(status => {
      this.stats.lastWriteStatus = status;
    });
  }

  read(snapshot, payload) {
    const result = this.reader.read({ ...snapshot, payload });
    this.stats.lastReadAt = Date.now();
    this.stats.lastReadSequence = snapshot?.sequence;
    this.stats.lastReadPayloadBytes = payload?.length ?? 0;
    this.stats.lastReadDataBytes = result.ok ? result.data.length : 0;
    this.stats.lastReadError = result.ok ? undefined : result.reason;
    return result;
  }

  write(data) {
    const frame = this.writer.write(data);
    if (!frame) {
      this.stats.droppedWrites += 1;
      return undefined;
    }

    this.stats.lastWriteAt = Date.now();
    this.stats.lastWriteSequence = frame.sequence;
    this.stats.lastWritePayloadBytes = frame.payload.length;
    this.stats.lastWriteDataBytes = data.length;
    return frame;
  }

  ack(sequence) {
    this.writer.ack(sequence);
  }

  latestSequence() {
    return this.reader.latestSequence();
  }

  // 只重置接收侧(reader)。换图时用这个: 客户端那条快照链我们是接收方, 保留 history
  // 才能继续解它的 delta(详见 Player.changeLevel 的说明)。
  resetReader() {
    this.reader = new SnapshotReader();
    this.stats.lastReadAt = undefined;
    this.stats.lastReadSequence = undefined;
    this.stats.lastReadPayloadBytes = 0;
    this.stats.lastReadDataBytes = 0;
    this.stats.lastReadError = undefined;
    this.reader.onSync(status => {
      this.stats.lastReadStatus = status;
    });
  }

  // 只重置发送侧(writer)。换图时用这个: 给该玩家下发的链重新从关键帧(resync)起步,
  // 不依赖上一条链的 base。
  resetWriter() {
    this.writer = new SnapshotWriter();
    this.stats.lastWriteAt = undefined;
    this.stats.lastWriteSequence = undefined;
    this.stats.lastWritePayloadBytes = 0;
    this.stats.lastWriteDataBytes = 0;
    this.stats.droppedWrites = 0;
    this.writer.onSync(status => {
      this.stats.lastWriteStatus = status;
    });
  }

  reset() {
    this.resetReader();
    this.resetWriter();
    this.stats.lastReadStatus = 'reset';
    this.stats.lastWriteStatus = 'reset';
  }

  toApi() {
    return { ...this.stats };
  }
}
