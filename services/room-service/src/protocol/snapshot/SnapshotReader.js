import { fnv1a8 } from './fnv1a.js';
import { Snapshot } from './Snapshot.js';
import { SNAPSHOT_MAX_DATA_BYTES } from './constants.js';

export class SnapshotReader {
  static maxWindow = 16;

  constructor({ baseSize = SNAPSHOT_MAX_DATA_BYTES } = {}) {
    this.baseSize = baseSize;
    this.history = [];
    this.resyncing = false;
    this.onStatus = undefined;
    this.reset();
  }

  reset() {
    this.history = [new Snapshot(1, Buffer.alloc(this.baseSize))];
    this.resyncing = false;
    // 真正收到并接受的最后一个序号(0 = 还没收到任何东西)。
    // 不能用 history 头: 新 reader 的 history 里有个 seq=1 的种子关键帧, 拿它去 ack
    // 等于告诉客户端"我收到过 seq 1"(实际没收到) -> 客户端那边 ack 语义错乱。
    this.lastSequence = 0;
  }

  latestSequence() {
    return this.lastSequence;
  }

  onSync(callback) {
    this.onStatus = typeof callback === 'function' ? callback : undefined;
  }

  read({ sequence, base, checksum, payload }) {
    if (fnv1a8(payload) !== checksum) {
      this.onStatus?.('hasherror');
      return { ok: false, reason: 'checksum' };
    }

    if (sequence !== 0 && base === 0 && !this.resyncing) {
      this.reset();
      this.resyncing = true;
      this.onStatus?.('resyncstart');
    }

    if (base === 0) {
      const snapshot = new Snapshot(sequence, payload);
      this.save(snapshot);
      return { ok: true, data: snapshot.data, ack: sequence, raw: true };
    }

    const baseSnapshot = this.find(base);
    if (!baseSnapshot) {
      if (!this.resyncing) {
        this.onStatus?.('nokeyframe');
      }
      return { ok: false, reason: this.resyncing ? 'resyncing' : 'nokeyframe' };
    }

    const snapshot = Snapshot.applyDelta(baseSnapshot.clone(), sequence, payload);
    if (snapshot.data.length > this.baseSize) {
      return { ok: false, reason: 'snapshot_oversize' };
    }
    this.save(snapshot);
    if (this.resyncing) {
      this.resyncing = false;
      this.onStatus?.('resyncsuccess');
    }

    return {
      ok: true,
      data: snapshot.data,
      ack: sequence,
      raw: false
    };
  }

  find(sequence) {
    return this.history.find(snapshot => snapshot.sequence === sequence);
  }

  save(snapshot) {
    if (!snapshot.sequence) {
      return;
    }

    this.lastSequence = snapshot.sequence;
    this.history.push(snapshot);
    while (this.history.length > SnapshotReader.maxWindow) {
      this.history.shift();
    }
  }
}
