export class BinaryWriter {
  constructor(initialSize = 256) {
    this.buffer = Buffer.allocUnsafe(initialSize);
    this.offset = 0;
  }

  ensure(size) {
    const required = this.offset + size;
    if (required <= this.buffer.length) {
      return;
    }

    let nextSize = this.buffer.length;
    while (nextSize < required) {
      nextSize *= 2;
    }

    const next = Buffer.allocUnsafe(nextSize);
    this.buffer.copy(next, 0, 0, this.offset);
    this.buffer = next;
  }

  writeUInt8(value) {
    this.ensure(1);
    this.buffer.writeUInt8(value & 0xff, this.offset++);
  }

  writeUInt16(value) {
    this.ensure(2);
    this.buffer.writeUInt16LE(value & 0xffff, this.offset);
    this.offset += 2;
  }

  writeUInt32(value) {
    this.ensure(4);
    this.buffer.writeUInt32LE(value >>> 0, this.offset);
    this.offset += 4;
  }

  writeUInt64(value) {
    this.ensure(8);
    this.buffer.writeBigUInt64LE(BigInt(value), this.offset);
    this.offset += 8;
  }

  writeDouble(value) {
    this.ensure(8);
    this.buffer.writeDoubleLE(Number(value), this.offset);
    this.offset += 8;
  }

  writeBytes(value) {
    const buffer = Buffer.from(value);
    this.ensure(buffer.length);
    buffer.copy(this.buffer, this.offset);
    this.offset += buffer.length;
  }

  // 定长字符串: 客户端的名字字段都是定长槽位(老引擎 `SerializeCString(name, 0x10)` /
  // EnterGame 里 `SerializeBytes(name, 0x10)`), 所以短了补 0、长了截断, 长度恒定。
  writeFixedString(value, size) {
    const buffer = Buffer.alloc(size);
    Buffer.from(String(value ?? ''), 'utf8').copy(buffer, 0, 0, size);
    this.writeBytes(buffer);
  }

  pad(size) {
    this.ensure(size);
    this.buffer.fill(0, this.offset, this.offset + size);
    this.offset += size;
  }

  toBuffer() {
    return Buffer.from(this.buffer.subarray(0, this.offset));
  }
}
