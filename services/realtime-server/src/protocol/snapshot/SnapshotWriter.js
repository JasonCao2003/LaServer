import { fnv1a8 } from './fnv1a.js';
import { Snapshot } from './Snapshot.js';
import { SnapshotReader } from './SnapshotReader.js';
import { SNAPSHOT_MAX_DATA_BYTES } from './constants.js';

export class SnapshotWriter {
  constructor({ keyframeInterval = 10, maxDataLength = SNAPSHOT_MAX_DATA_BYTES } = {}) {
    this.keyframeInterval = keyframeInterval;
    this.maxDataLength = maxDataLength;
    this.history = [];
    this.nextSequence = 1;
    this.frameCount = 0;
    this.resyncing = true;
    this.onStatus = undefined;
  }

  onSync(callback) {
    this.onStatus = typeof callback === 'function' ? callback : undefined;
  }

  write(data) {
    const buffer = Buffer.from(data);
    if (buffer.length > this.maxDataLength) {
      return undefined;
    }

    if (this.history.length >= SnapshotReader.maxWindow) {
      if (!this.resyncing) {
        this.resyncing = true;
        this.frameCount = 0;
        this.onStatus?.('resyncstart');
      } else {
        this.history = this.history.slice(Math.floor(SnapshotReader.maxWindow / 2));
        this.onStatus?.('resyncfailed');
      }
    }

    const shouldKeyframe = this.resyncing
      ? this.frameCount % Math.max(1, Math.floor(this.keyframeInterval / 2)) === 0
      : this.frameCount % this.keyframeInterval === 0;

    let sequence = shouldKeyframe ? this.allocateSequence() : 0;
    const current = new Snapshot(sequence, buffer);
    let base = this.resyncing ? undefined : this.history[0];
    let payload = base ? current.createDelta(base) : current.data;

    // 2026-10-06 修 联机闪退: delta 不是永远可逆的 —— 只要"当前缓冲比基准缓冲长",
    // createDelta 会把超出基准长度的尾巴原样写进去, 尾巴里出现一个 0x00 就会被
    // applyDelta 当成 (0x00,N) 的跳过标记, 收方解出来的长度直接错掉
    // (实测: base 5 字节 + 当前 7 字节 -> 收方拿到 14 字节的烂缓冲)。
    // 玩家状态缓冲长度本来就会变(实测 89..242 字节), 所以这种包一直有: 客户端拿到
    // 长度不对的玩家状态, 解析越界 -> 闪退; 同时它的 ack 卡死在最后一次成功的序号上
    // (日志: pAck 冻在 20 整整 11 秒, 期间我们发给它的包从 164 涨到 247 = 全量大小),
    // 而 writer 要等 history 攒满 16 个关键帧(约 19 秒)才会补一次全量帧自愈。
    // 修法: 这种不可逆的 delta(以及压不动、把全量当 delta 发的退化帧)一律改成全量帧
    // (base=0) —— 体积和原来那坨烂 delta 一样, 但收方 reader 会走 base===0 分支
    // 重新建立基准, 不会再烂链。
    if (base) {
      const restored = Snapshot.applyDelta(base.clone(), current.sequence, payload);
      if (
        !restored.data.equals(current.data) ||
        current.data.length !== base.data.length ||
        payload.length >= buffer.length
      ) {
        base = undefined;
        payload = current.data;
        if (!sequence) {
          sequence = this.allocateSequence();
          current.sequence = sequence;
        }
        this.onStatus?.('fullframe');
      }
    }

    if (sequence) {
      this.history.push(current);
      while (this.history.length > SnapshotReader.maxWindow) {
        this.history.shift();
      }
    }

    this.frameCount += 1;

    return {
      sequence,
      base: base?.sequence ?? 0,
      checksum: fnv1a8(payload),
      payload
    };
  }

  ack(sequence) {
    const index = this.history.findIndex(snapshot => snapshot.sequence === sequence);
    if (index === -1) {
      return;
    }

    this.history = this.history.slice(index);
    if (this.resyncing) {
      this.resyncing = false;
      this.onStatus?.('resyncsuccess');
    }
  }

  allocateSequence() {
    this.nextSequence = (this.nextSequence + 1) & 0x7f;
    if (this.nextSequence === 0) {
      this.nextSequence = 1;
    }
    return this.nextSequence;
  }
}
