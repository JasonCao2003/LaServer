import assert from 'node:assert/strict';
import test from 'node:test';
import { PacketCodec } from './PacketCodec.js';

const codec = new PacketCodec();

function oldJoin(collisionId) {
  const payload = Buffer.alloc(1927);
  payload[2] = collisionId;
  payload.writeUInt16LE(284, 82);
  payload.writeUInt16LE(542, 84);
  payload.writeUInt32LE(170388, 86);
  payload.writeUInt32LE(3526133726, 103);
  return Buffer.concat([Buffer.from([3, 0]), payload]);
}

for (const id of [1, 12, 14]) {
  test(`0.13.4 JoinGame wins over UUID byte matching packet ${id}`, () => {
    const packet = codec.decodeClient(oldJoin(id));
    assert.equal(packet.name, 'JoinGamePacket');
    assert.equal(packet.layout, 'tolerant-join');
    assert.equal(packet.payload.sky0134, true);
    assert.equal(packet.payload.levelId, 3526133726);
  });
}

test('a real token-layout Disconnect still decodes normally', () => {
  const packet = codec.decodeClient(Buffer.from([3, 0, 0, 0, 1, 7]));
  assert.equal(packet.name, 'DisconnectPacket');
  assert.equal(packet.layout, 'token+id+seq');
  assert.equal(packet.token, 3);
  assert.equal(packet.sequence, 7);
});

test('a token-layout ping whose token starts with 3 is preserved', () => {
  const packet = codec.decodeClient(Buffer.from([3, 0, 0, 0, 12, 7, 42, 0]));
  assert.equal(packet.name, 'NetTimePingPacket');
  assert.equal(packet.payload.requestId, 42);
  assert.equal(packet.layout, 'token+id+seq');
});

test('established old layout preserves LevelUpdate when payload resembles Disconnect', () => {
  const buffer = Buffer.alloc(84);
  buffer[0] = 4;
  buffer[4] = 1;
  buffer.writeUInt32LE(3526133726, 16);
  for (const layout of ['tolerant-join', 'id+seq']) {
    const packet = codec.decodeClient(buffer, { layout });
    assert.equal(packet.name, 'LevelUpdatePacket');
    assert.equal(packet.layout, 'id+seq');
    assert.equal(packet.payload.levelId, 3526133726);
  }
});
