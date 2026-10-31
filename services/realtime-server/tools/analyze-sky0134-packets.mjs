import fs from 'node:fs';
import { PacketCodec } from '../src/protocol/PacketCodec.js';
const lines = fs.readFileSync(new URL('../data/packets/_udp_packets_25566.jsonl', import.meta.url), 'utf8').split('\n');
const codec = new PacketCodec();
let hit = 0;
for (const line of lines) {
  if (!line || (!line.includes('"packetId":3') && !line.includes('"packetId": 3'))) continue;
  let r; try { r = JSON.parse(line); } catch { continue; }
  if (r.dir !== 'in' || r.packetId !== 3 || !r.hex) continue;
  const buf = Buffer.from(r.hex, 'hex');
  if (buf.length < 100) continue;
  let dec; try { dec = codec.decodeClient(buf); } catch { continue; }
  if (!dec || dec.id !== 3) continue;
  const p = dec.payload;
  if (p.sky0134 || p.sky0155 || hit < 1) {
    console.log(r.note, 'len', r.len, 'sky0134=', p.sky0134, 'sky0155=', p.sky0155, 'levelId=', p.levelId);
  }
  if (p.sky0134) { hit += 1; if (hit >= 2) break; }
}
console.log('0134 real-capture hits:', hit);
