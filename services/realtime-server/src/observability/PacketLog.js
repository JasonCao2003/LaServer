// 真实 UDP 联机包记录器 (PacketLog)
// ==================================
// 「UDP 联机接口响应查看」的数据源。与 HTTP 请求监控一一对应:
//   HTTP 监控 -> 记录每个 HTTP 请求/响应
//   UDP  监控 -> 记录每个进出的 ENet 包(客户端->服务器 / 服务器->客户端)
//
// 每条记录保留: 方向、peerId、playerId、包 id/名称、负载长度、原始十六进制、
// 解码结果(布局/是否宽容解析/错误原因)、时间戳、客户端版本号(ver)。
// 用环形缓冲限制内存; /packets?since=<seq> 增量拉取, 供面板秒级刷新。
//
// 2026-10-17 新增磁盘缓存(用户要求: 监控允许缓存到磁盘, 下次打开直接拉取本次
// 私服运行期间的所有 UDP 信息):
//   * 每个包都追加写进 JSONL 文件(默认 data/packets/_udp_packets_<房间端口>.jsonl),
//     进程重启不丢; 启动时从文件末尾恢复 seq, 序号跨重启继续递增, 面板的
//     since 增量语义不受影响。
//   * 面板打开监控时用 GET /packets?all=1 一次性拉取缓存里的全部包(默认回读
//     最近 64MB), 所以"打开窗口之前"发生的入房 / JoinGame / EnterGame 也能看到。
//   * JoinGame 里客户端自报的 clientVersion / serverVersion / rpcHash(构建号,
//     例如 401861 = 0.34.0)作为 join 事件一并落盘并缓存, 每个包记录上也带 ver
//     字段, 供按客户端版本排查联机问题。
//
// 这里只记录**真实收发**的包, 不产生任何模拟/固定内容。

import fs from 'node:fs';
import path from 'node:path';
import { PacketIds } from '../protocol/PacketIds.js';

const ID_TO_NAME = Object.fromEntries(
  Object.entries(PacketIds).map(([name, id]) => [id, name])
);

// 单个包最多记录多少原始字节(够看清包头与结构, 又不至于把内存撑爆)
const MAX_HEX_BYTES = 256;

// 单个 JSONL 缓存文件上限(超过就轮转成 .1, 重新开一个, seq 继续递增)
const DEFAULT_JOURNAL_MAX_BYTES = 256 * 1024 * 1024;
// /packets?all=1 首次回读磁盘时最多读文件尾部多少字节(避免几十万行卡住事件循环)
const DEFAULT_HISTORY_WINDOW_BYTES = 64 * 1024 * 1024;
// 回读缓存最多在内存里保留多少条
const HISTORY_ENTRY_CAP = 200000;
// join / 版本事件最多保留多少条
const EVENT_CAP = 500;

export function packetName(id) {
  if (id === undefined || id === null) {
    return 'unknown';
  }
  return ID_TO_NAME[id] ?? ('id#' + id);
}

export class PacketLog {
  constructor({ limit = 2000, journalFile = null,
                journalMaxBytes = DEFAULT_JOURNAL_MAX_BYTES,
                journalHistoryBytes = DEFAULT_HISTORY_WINDOW_BYTES,
                bootMeta = null } = {}) {
    this.limit = limit;
    this.bootId = `${Date.now().toString(36)}-${process.pid}`;
    this.bootedAt = Date.now();
    this.seq = 0;
    this.entries = [];
    this.counters = {
      in: 0,
      out: 0,
      inBytes: 0,
      outBytes: 0,
      unsupported: 0,
      decodeErrors: 0,
      byType: {},
      startedAt: Date.now()
    };
    // 客户端版本号缓存: ver 字符串 -> 本次运行收到的包数(供面板顶部展示)
    this.versions = Object.create(null);
    // join 事件(含客户端版本号/构建号), 供面板展示与排查
    this.events = [];
    this.journal = {
      file: null,
      fd: null,
      bytes: 0,
      lines: 0,
      error: null,
      maxBytes: journalMaxBytes,
      historyBytes: journalHistoryBytes,
      rotations: 0,
      resumedFromSeq: 0
    };
    this._history = {
      entries: [],
      offset: 0,
      linesSeen: 0,
      truncated: 0,
      lastError: null
    };
    this._openJournal(journalFile, bootMeta);
  }

  // ---------------- 磁盘缓存 ----------------
  _openJournal(file, bootMeta) {
    if (!file) {
      return;
    }
    try {
      const abs = path.resolve(file);
      fs.mkdirSync(path.dirname(abs), { recursive: true });
      if (fs.existsSync(abs)) {
        // 跨重启继续序号: 从缓存文件末尾找最后一条带 seq 的记录。
        this.journal.resumedFromSeq = this._lastSeqOnDisk(abs);
        this.seq = Math.max(this.seq, this.journal.resumedFromSeq);
      }
      this.journal.file = abs;
      this.journal.fd = fs.openSync(abs, 'a');
      this.journal.bytes = fs.fstatSync(this.journal.fd).size;
      this._writeLine({
        t: 'boot',
        bootId: this.bootId,
        ts: this.bootedAt,
        pid: process.pid,
        node: process.version,
        cwd: process.cwd(),
        resumedFromSeq: this.journal.resumedFromSeq,
        meta: bootMeta ?? null
      });
    } catch (error) {
      this.journal.error = error?.message ?? String(error);
      this.journal.fd = null;
    }
  }

  _lastSeqOnDisk(file) {
    let fd = null;
    try {
      fd = fs.openSync(file, 'r');
      const size = fs.fstatSync(fd).size;
      if (size <= 0) {
        return 0;
      }
      // 末尾 1MB 足够覆盖最后若干行(单行最坏 ~700 字节)。
      const start = Math.max(0, size - 1024 * 1024);
      const buf = Buffer.alloc(size - start);
      fs.readSync(fd, buf, 0, buf.length, start);
      const lines = buf.toString('utf8').split('\n');
      for (let index = lines.length - 1; index >= 0; index -= 1) {
        const line = lines[index].trim();
        if (!line) {
          continue;
        }
        let obj = null;
        try {
          obj = JSON.parse(line);
        } catch {
          continue;
        }
        if (obj && typeof obj.seq === 'number') {
          return obj.seq;
        }
      }
    } catch {
      return 0;
    } finally {
      if (fd !== null) {
        try {
          fs.closeSync(fd);
        } catch {
          // ignore
        }
      }
    }
    return 0;
  }

  _writeLine(record) {
    if (this.journal.fd === null) {
      return;
    }
    try {
      const line = Buffer.from(JSON.stringify(record) + '\n', 'utf8');
      fs.writeSync(this.journal.fd, line);
      this.journal.bytes += line.length;
      this.journal.lines += 1;
      if (this.journal.bytes > this.journal.maxBytes) {
        this._rotateJournal();
      }
    } catch (error) {
      this.journal.error = error?.message ?? String(error);
    }
  }

  _rotateJournal() {
    const file = this.journal.file;
    if (!file) {
      return;
    }
    // 轮转前把旧文件剩余内容读进历史缓存, 不丢包。
    try {
      this._refreshHistoryFromDisk();
    } catch (error) {
      this._history.lastError = error?.message ?? String(error);
    }
    try {
      fs.closeSync(this.journal.fd);
    } catch {
      // ignore
    }
    this.journal.fd = null;
    try {
      const prev = file + '.1';
      try {
        fs.unlinkSync(prev);
      } catch {
        // ignore
      }
      fs.renameSync(file, prev);
    } catch (error) {
      this.journal.error = error?.message ?? String(error);
    }
    try {
      this.journal.fd = fs.openSync(file, 'a');
      this.journal.bytes = 0;
      this.journal.lines = 0;
      this.journal.rotations += 1;
      this._history.offset = 0;
      this._writeLine({
        t: 'rotate',
        bootId: this.bootId,
        ts: Date.now(),
        rotations: this.journal.rotations
      });
    } catch (error) {
      this.journal.error = error?.message ?? String(error);
      this.journal.fd = null;
    }
  }

  // 把磁盘上还没读过的记录读进 _history(面板?all=1 时用)。
  _refreshHistoryFromDisk() {
    const file = this.journal.file;
    if (!file) {
      return;
    }
    const stat = fs.statSync(file);
    let start = this._history.offset;
    if (start === 0) {
      // 首次回读只取尾部窗口, 避免大文件把事件循环卡住。
      start = Math.max(0, stat.size - this.journal.historyBytes);
    }
    if (start >= stat.size) {
      this._history.offset = stat.size;
      return;
    }
    const fd = fs.openSync(file, 'r');
    try {
      let pos = start;
      if (pos > 0) {
        // 从文件中间开始时跳过第一条不完整的行。
        const one = Buffer.alloc(1);
        while (pos < stat.size) {
          const n = fs.readSync(fd, one, 0, 1, pos);
          if (n <= 0) {
            break;
          }
          pos += 1;
          if (one[0] === 0x0a) {
            break;
          }
        }
      }
      const buf = Buffer.alloc(1024 * 1024);
      let pending = '';
      while (pos < stat.size) {
        const want = Math.min(buf.length, stat.size - pos);
        const n = fs.readSync(fd, buf, 0, want, pos);
        if (n <= 0) {
          break;
        }
        pos += n;
        pending += buf.subarray(0, n).toString('utf8');
        let nl = pending.indexOf('\n');
        while (nl >= 0) {
          const line = pending.slice(0, nl);
          pending = pending.slice(nl + 1);
          this._ingestJournalLine(line);
          nl = pending.indexOf('\n');
        }
      }
      this._history.offset = pos;
      this._history.lastError = null;
    } finally {
      fs.closeSync(fd);
    }
  }

  _ingestJournalLine(line) {
    if (!line) {
      return;
    }
    let obj = null;
    try {
      obj = JSON.parse(line);
    } catch {
      return;
    }
    if (!obj || typeof obj !== 'object') {
      return;
    }
    if (typeof obj.seq === 'number') {
      this._history.entries.push(obj);
      this._history.linesSeen += 1;
      if (this._history.entries.length > HISTORY_ENTRY_CAP) {
        const drop = this._history.entries.length - HISTORY_ENTRY_CAP;
        this._history.entries.splice(0, drop);
        this._history.truncated += drop;
      }
    } else if (obj.t === 'join') {
      this.events.push(obj);
      if (this.events.length > EVENT_CAP) {
        this.events.splice(0, this.events.length - EVENT_CAP);
      }
    }
  }

  // ---------------- 记录 ----------------
  // dir: 'in'  = 客户端 -> 服务器
  //      'out' = 服务器 -> 客户端
  // ver: 客户端版本号/构建号标签(见 Player.versionLabel), 不传则不带。
  record({ dir, peerId = null, playerId = null, buffer = null, packetId = null,
           name = null, note = null, ver = null }) {
    const len = buffer ? buffer.length : 0;

    this.seq += 1;
    const entry = {
      seq: this.seq,
      ts: Date.now(),
      dir,
      peerId: peerId === null || peerId === undefined ? null : String(peerId),
      playerId: playerId === null || playerId === undefined ? null : playerId,
      packetId: packetId === null || packetId === undefined ? null : packetId,
      name: name ?? packetName(packetId),
      len
    };
    if (note) {
      entry.note = note;
    }
    if (ver) {
      entry.ver = String(ver);
    }
    if (len > 0) {
      const take = Math.min(len, MAX_HEX_BYTES);
      entry.hex = buffer.subarray(0, take).toString('hex');
      if (len > take) {
        entry.truncated = len - take;
      }
    }

    this.entries.push(entry);
    if (this.entries.length > this.limit) {
      this.entries.splice(0, this.entries.length - this.limit);
    }

    const c = this.counters;
    if (dir === 'in') {
      c.in += 1;
      c.inBytes += len;
    } else {
      c.out += 1;
      c.outBytes += len;
    }
    if (name === 'unsupported') {
      c.unsupported += 1;
    }
    if (name === 'decode_error') {
      c.decodeErrors += 1;
    }
    const key = entry.name;
    c.byType[key] = (c.byType[key] ?? 0) + 1;
    if (entry.ver) {
      this.versions[entry.ver] = (this.versions[entry.ver] ?? 0) + 1;
    }

    // 落盘(JSONL, 一行一个包)
    this._writeLine(entry);

    return entry;
  }

  // 记录一条非包事件(目前只有 join): 客户端版本号就缓存在这里。
  recordEvent(event = {}) {
    const record = { t: 'join', ts: Date.now(), bootId: this.bootId, ...event };
    this.events.push(record);
    if (this.events.length > EVENT_CAP) {
      this.events.splice(0, this.events.length - EVENT_CAP);
    }
    this._writeLine(record);
    return record;
  }

  recentJoins(n = 30) {
    return this.events.slice(-n);
  }

  listSince(since = 0) {
    const from = Number(since) || 0;
    if (from <= 0) {
      return this.entries.slice();
    }
    return this.entries.filter(entry => entry.seq > from);
  }

  // 磁盘缓存里的全部包(跨重启, 除超过回读窗口/轮转的极老数据外)。
  fullHistory({ since = 0, limit = 0 } = {}) {
    try {
      this._refreshHistoryFromDisk();
    } catch (error) {
      this._history.lastError = error?.message ?? String(error);
    }
    let list = this._history.entries;
    if (since > 0) {
      list = list.filter(entry => entry.seq > since);
    }
    let dropped = 0;
    if (limit > 0 && list.length > limit) {
      dropped = list.length - limit;
      list = list.slice(-limit);
    }
    return {
      packets: list,
      total: this._history.entries.length,
      dropped,
      fromDisk: Boolean(this.journal.file),
      truncated: this._history.truncated,
      bytes: this.journal.bytes
    };
  }

  stats() {
    return {
      seq: this.seq,
      retained: this.entries.length,
      limit: this.limit,
      in: this.counters.in,
      out: this.counters.out,
      inBytes: this.counters.inBytes,
      outBytes: this.counters.outBytes,
      unsupported: this.counters.unsupported,
      decodeErrors: this.counters.decodeErrors,
      byType: { ...this.counters.byType },
      byVersion: { ...this.versions },
      uptimeMs: Date.now() - this.counters.startedAt,
      bootId: this.bootId,
      bootedAt: this.bootedAt,
      journal: {
        file: this.journal.file,
        bytes: this.journal.bytes,
        lines: this.journal.lines,
        error: this.journal.error,
        rotations: this.journal.rotations,
        resumedFromSeq: this.journal.resumedFromSeq
      },
      history: {
        entries: this._history.entries.length,
        linesSeen: this._history.linesSeen,
        truncated: this._history.truncated,
        lastError: this._history.lastError
      },
      joins: this.recentJoins(30)
    };
  }

  clear() {
    this.entries = [];
    return this.seq;
  }

  close() {
    try {
      if (this.journal.fd !== null) {
        fs.closeSync(this.journal.fd);
      }
    } catch {
      // ignore
    }
    this.journal.fd = null;
  }
}