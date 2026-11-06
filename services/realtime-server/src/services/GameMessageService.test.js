import assert from 'node:assert/strict';
import test from 'node:test';
import { GameMessageService } from './GameMessageService.js';
import { GameMsgType } from '../protocol/packets/GameMsgPacket.js';

function setup(targetFamily = 'mqtt-284-541') {
  const source = { id: 1, levelId: 100, profile: { effectiveFamily: 'mqtt-284-541' } };
  source.session = {};
  const target = { id: 2, levelId: 100, levelChangeCount: 3,
    profile: { effectiveFamily: targetFamily }, session: {} };
  const sent = [];
  const service = new GameMessageService({
    players: { listInLevel: () => [source, target] }, levels: {},
    broadcaster: { send: (session, packet) => sent.push({ session, packet }) },
    logger: { debug() {} }
  });
  return { service, source, target, sent };
}

test('same-version RPC bitstream is relayed even when its first byte exceeds 16', () => {
  const { service, source, target, sent } = setup();
  const bytes = Buffer.from([0x44, 1, 2, 3]);
  assert.equal(service.forwardToLevel(source, source.session, {
    type: GameMsgType.NetRpc, playerId: 1, rpcData: bytes
  }), 1);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].session, target.session);
  assert.deepEqual(sent[0].packet.payload.rpcData, bytes);
  assert.equal(sent[0].packet.payload.levelChangeCount, 3);
});

test('RPC bitstreams remain isolated between incompatible protocol families', () => {
  const { service, source, sent } = setup('mqtt-284-542');
  assert.equal(service.forwardToLevel(source, source.session, {
    type: GameMsgType.NetRpc, rpcData: Buffer.from([0x44])
  }), 0);
  assert.equal(sent.length, 0);
});

test('modern audience GameMsg remains blocked for 0.13.4 targets', () => {
  const { service, source, sent } = setup();
  assert.equal(service.forwardToLevel(source, source.session, {
    type: GameMsgType.AudienceSocialBroadcast, payloadBytes: Buffer.from([1])
  }), 0);
  assert.equal(sent.length, 0);
});
