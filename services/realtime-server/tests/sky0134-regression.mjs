import { PacketCodec } from '../src/protocol/PacketCodec.js';
import { TgcUuid } from '../src/protocol/types/TgcUuid.js';
import { resolveProfile } from '../src/protocol/VersionProfile.js';
import { engineForEntity, SKY0134_ENGINE, SKY0155_ENGINE, LEGACY_ENGINE, MODERN_ENGINE } from '../src/protocol/versions/index.js';
import { sky0134Profile, isSky0134TolerantJoin } from '../src/protocol/versions/sky0134Engine.js';
import { sky0155Profile, isSky0155TolerantJoin } from '../src/protocol/versions/sky0155Engine.js';
import { BroadcastService } from '../src/services/BroadcastService.js';

const LEVEL = 3526133726;
let failures = 0;
function check(name, cond, detail = '') {
  if (cond) { console.log('PASS', name); }
  else { failures += 1; console.log('FAIL', name, detail); }
}

function buildJoinPayload({ sv, cv, buildOrHash, levelId }) {
  const p = Buffer.alloc(200);
  Buffer.from('00112233445566778899aabbccddeeff', 'hex').copy(p, 0);
  p.writeUInt16LE(sv, 82);
  p.writeUInt16LE(cv, 84);
  if (buildOrHash !== null) p.writeUInt32LE(buildOrHash >>> 0, 86);
  p.writeUInt32LE(levelId >>> 0, 103);
  p.writeUInt32LE(0x3f800000, 107);
  return p;
}
function packetFor(payload, id = 3, seq = 1) {
  return Buffer.concat([Buffer.from([id, seq]), payload]);
}

const codec = new PacketCodec();

// ---- 1) 0.13.4 tolerant join 识别 + 关卡号 ----
const p134 = buildJoinPayload({ sv: 284, cv: 541, buildOrHash: 169938, levelId: LEVEL });
check('0134 fingerprint (raw)', isSky0134TolerantJoin(p134) === true);
check('0134 not 0155', isSky0155TolerantJoin(p134) === false);
const otherBuild = buildJoinPayload({ sv: 284, cv: 541, buildOrHash: 170000, levelId: LEVEL });
check('0134 build scoped (other build rejected)', isSky0134TolerantJoin(otherBuild) === false);

const dec = codec.decodeClient(packetFor(p134));
check('0134 decoded as JoinGame', dec.id === 3 && dec.name === 'JoinGamePacket', JSON.stringify({ id: dec.id, layout: dec.layout }));
check('0134 tolerant layout', dec.layout === 'tolerant-join' && dec.tolerant === true, dec.layout);
check('0134 marker', dec.payload.sky0134 === true);
check('0134 levelId recovered from summary', dec.payload.levelId === LEVEL, String(dec.payload.levelId));

const prof = sky0134Profile(sky0155Profile(resolveProfile({ layout: dec.layout, tolerant: dec.tolerant }), dec.payload), dec.payload);
check('0134 profile id', prof.id === 'sky0134' && prof.sky0134 === true, prof.id);
check('0134 enterGame ids [17]', JSON.stringify(prof.enterGamePacketIds) === '[17]', JSON.stringify(prof.enterGamePacketIds));
const entity134 = { profile: prof };
check('0134 engine', engineForEntity(entity134) === SKY0134_ENGINE);

// ---- 2) EnterGame(17) 名册编码: [count][37B records] ----
const uuid = new TgcUuid(Buffer.from('aabbccddeeff00112233445566778899', 'hex'));
const rosterPacket = { id: 17, payload: { players: [
  { netId: 1, uuid, name: '', levelId: LEVEL },
  { netId: 2, uuid, name: '', levelId: LEVEL }
] } };
const rosterBuf = codec.encodeServer(rosterPacket);
check('0134 EnterGame outer id 17', rosterBuf[0] === 17);
check('0134 EnterGame len = 1+2*37', rosterBuf.readUInt16LE(1) === 75, String(rosterBuf.readUInt16LE(1)));
check('0134 EnterGame count=2 at payload[0]', rosterBuf[3] === 2);
check('0134 EnterGame record1 netId', rosterBuf[4] === 1);

// ---- 3) 引擎声明: 新人通知 = 重发整份 EnterGame(与 0.15.5 相同的 SyncPlayerMsg) ----
check('0134 announce with EnterGame', SKY0134_ENGINE.announceJoinWith === 'EnterGame', String(SKY0134_ENGINE.announceJoinWith));
check('0134 no PlayerJoined ids', SKY0134_ENGINE.playerJoinedPacketIds.length === 0);
check('0134 no single-record mode', SKY0134_ENGINE.announceJoinSingleRecord === undefined);
check('0134 playerLeft id 7', SKY0134_ENGINE.playerLeftPacketIds[0] === 7, JSON.stringify(SKY0134_ENGINE.playerLeftPacketIds));

// ---- 4) 0.15.5 回归: 指纹/引擎/整份 EnterGame 通知不变 ----
const p155 = buildJoinPayload({ sv: 294, cv: 4852, buildOrHash: 179644, levelId: LEVEL });
check('0155 fingerprint still works', isSky0155TolerantJoin(p155) === true);
check('0155 not 0134', isSky0134TolerantJoin(p155) === false);
const dec155 = codec.decodeClient(packetFor(p155));
check('0155 marker', dec155.payload.sky0155 === true && dec155.payload.sky0134 === false);
const prof155 = sky0134Profile(sky0155Profile(resolveProfile({ layout: dec155.layout, tolerant: dec155.tolerant }), dec155.payload), dec155.payload);
check('0155 profile unchanged', prof155.id === 'sky0155' && engineForEntity({ profile: prof155 }) === SKY0155_ENGINE, prof155.id);

// ---- 5) 普通老客户端回归: 没有指纹 -> 原 profile / 原引擎 ----
const plain = buildJoinPayload({ sv: 111, cv: 222, buildOrHash: null, levelId: LEVEL });
const decPlain = codec.decodeClient(packetFor(plain));
check('plain no markers', decPlain.payload.sky0134 === false && decPlain.payload.sky0155 === false);
const profPlain = sky0134Profile(sky0155Profile(resolveProfile({ layout: decPlain.layout, tolerant: decPlain.tolerant }), decPlain.payload), decPlain.payload);
check('plain stays legacy', profPlain.detectedFamily === 'legacy' && engineForEntity({ profile: profPlain }) === LEGACY_ENGINE, profPlain.detectedFamily);
const legacyEnter = codec.encodeServer({ id: 5, payload: { players: [] } });
check('legacy EnterGame bytes unchanged (leading 0 + count)', legacyEnter[0] === 5 && legacyEnter[3] === 0 && legacyEnter[4] === 0);

// ---- 6) BroadcastService: 只发同引擎, 0.13.4 / 0.15.5 都用整份名册 ----
function makeSession(peerId, player) {
  const s = { peerId, player, closed: false };
  player.session = s;
  return s;
}
const sessions = [];
const sent = [];
const broadcaster = new BroadcastService({
  codec,
  transport: { send: (peerId, buffer) => sent.push({ peerId, buffer }) },
  sessions: { list: () => sessions },
  metrics: null,
  packetLog: null
});
function newPlayer(id, profile, versionLabel) {
  const p = { id, uuid, name: '', levelId: LEVEL, profile, versionLabel };
  return p;
}
const pA = newPlayer(1, prof);                 // 0.13.4 已在房
const pB = newPlayer(2, prof);                 // 0.13.4 已在房
const pC = newPlayer(3, prof155);              // 0.15.5 已在房
const pM = newPlayer(4, resolveProfile({ layout: 'token+id+seq' })); // modern 已在房
makeSession('peerA', pA); makeSession('peerB', pB); makeSession('peerC', pC); makeSession('peerM', pM);
sessions.push(pA.session, pB.session, pC.session, pM.session);

const newcomer = newPlayer(5, prof);
makeSession('peerNew', newcomer);
sessions.push(newcomer.session);
const roster = { players: [{ netId: 5, uuid, name: '', levelId: LEVEL }] };
const res = broadcaster.announceJoin(newcomer, roster);
check('0134 announce mode EnterGame', res.mode === 'EnterGame', res.mode);
check('0134 announce targets 2 (same engine only)', res.targets === 2, String(res.targets));
check('0134 announce sent 2 packets', sent.length === 2, String(sent.length));
// 负载是 SyncPlayerMsg: [count=1][netId 5][uuid][name][levelId] -> 1+37 = 38
check('0134 announce every packet id17 len38', sent.every(x => x.buffer[0] === 17 && x.buffer.readUInt16LE(1) === 38),
  JSON.stringify(sent.map(x => [x.buffer[0], x.buffer.readUInt16LE(1)])));
check('0134 announce every packet count=1 netId=5', sent.every(x => x.buffer[3] === 1 && x.buffer[4] === 5));

// 0.15.5 新人: 仍应 re-broadcast EnterGame(count+records), 只给同一 0.15.5 引擎
sent.length = 0;
const newcomer155 = newPlayer(6, prof155);
makeSession('peerNew155', newcomer155);
sessions.push(newcomer155.session);
const res155 = broadcaster.announceJoin(newcomer155, { players: [{ netId: 6, uuid, name: '', levelId: LEVEL }] });
check('0155 announce mode unchanged', res155.mode === 'EnterGame', res155.mode);
check('0155 announce targets 1', res155.targets === 1, String(res155.targets));
check('0155 announce packet is EnterGame full roster', sent.length === 1 && sent[0].buffer[0] === 17
  && sent[0].buffer.readUInt16LE(1) === 38 && sent[0].buffer[3] === 1, JSON.stringify(sent.map(x => x.buffer.readUInt16LE(1))));

// modern 新人: 原逻辑不变
sent.length = 0;
const newcomerM = newPlayer(7, pM.profile);
makeSession('peerNewM', newcomerM);
sessions.push(newcomerM.session);
const resM = broadcaster.announceJoin(newcomerM, { players: [] });
check('modern announce mode unchanged', resM.mode === 'EnterGame', resM.mode);
check('modern announce packet unchanged', sent.length === 1 && sent[0].buffer[0] === 17 && sent[0].buffer.readUInt16LE(1) === 1);

console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);

