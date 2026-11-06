// 版本适配层: 把"各客户端版本在 UDP 联机上的差异"集中为数据 + 策略。
//
// 背景
// ----
// 本服务原先只针对 0.22.0 验证。E:\Sky 下每个 APK 的 lib/arm64-v8a/libBootloader.so
// 被逐个解出并统计 UDP 协议字符串标志位(D:\工具\其他\ai\udp_apk_scan\apk_proto_scan.py),
// 共覆盖 74 个版本(0.6.5 .. 0.34.3)。按标志位可分为三族(见 versionMatrix.js):
//
//   legacy  0.6.5  .. 0.12.3   无 WebSocket / MQTT; C->S 包头 id:u8 + seq:u8(无 token)
//   mqtt    0.13.4 .. 0.21.5   带 MQTT(/account/ws); C->S 出现 6 字节 token 包头
//   modern  0.22.0 .. 0.34.3   MQTT 被移除; C->S 包头 token:u32 + id:u8 + seq:u8
//
// 三族共用同一套 PacketIds 与 GameMsgType 数值(已与 0.12.1 / 0.22.0 真机抓包交叉验证:
// 0.12.1 同样发 GameMsg type 3 / 11 / 14)。真正随版本变化的点如下, 全部收敛到本文件的
// PROFILES 里, 由 PacketCodec / JoinService / GameMessageService / RoomServer 按玩家判定后读取:
//
//   - C->S 包头布局(6B token+id+seq vs 2B id+seq)
//   - JoinGame 负载布局(严格版 vs 宽容版: uuid=前 16B, levelId=末 4B LE)
//   - LevelUpdate 负载是否带末尾 1 字节(0.20.0 之前恰为 82 字节, 无尾字节)
//   - NetLevelData(level data)的封装: raw NetLevelData / 22B 头 NetLevelDataMsg /
//     modern 客户端自打包的不透明块
//   - 聊天等社交承载: legacy/modern 走 GameMsg 中继; mqtt 族部分功能走 MQTT over WebSocket
//
// 说明: 检测只能从 UDP 握手得到"包头布局", 无法从 UDP 区分 mqtt 与 modern(MQTT 是另一条
// 传输)。UDP 传输行为一致, 但关卡状态/RPC 的序列化不同, 所以 2026-10-18 起
// effectiveFamily 不再把 mqtt 归并到 modern(见下方 PROFILES[MQTT] 的注释)。
import { versionMatrix } from './versionMatrix.js';
import { PacketIds } from './PacketIds.js';

export const FAMILY = Object.freeze({
  LEGACY: 'legacy',
  MQTT: 'mqtt',
  MODERN: 'modern'
});

// C->S 包头布局名(与 PacketCodec 的 CLIENT_HEADER_LAYOUTS[].name 对齐)。
export const HEADER_LAYOUT = Object.freeze({
  TOKEN: 'token+id+seq',
  LEGACY: 'id+seq'
});

// level data 封装布局候选(非不透明模式下按顺序尝试解析)。
export const LEVEL_DATA_LAYOUT = Object.freeze({
  RAW: 'raw',        // 裸露的 NetLevelData(老客户端)
  MSG22: 'msg22',    // 22 字节头(electedPlayer/levelId/...)+ NetLevelData
  OPAQUE: 'opaque'   // 客户端自打包的不透明块(modern, 默认)
});

// ── 各协议族的 UDP 策略 ─────────────────────────────────────────────────────
// headerLayouts: 解码 C->S 包时优先尝试的包头布局顺序(PacketCodec 仍会全部尝试, 这里只
//                决定"先试哪个", 命中即用)。
// effectiveFamily: UDP 层实际采用的策略族(把 mqtt 并入 modern)。
export const PROFILES = Object.freeze({
  [FAMILY.LEGACY]: Object.freeze({
    id: FAMILY.LEGACY,
    effectiveFamily: FAMILY.LEGACY,
    label: 'Legacy ENet (0.6.5–0.12.3)',
    versionRange: '0.6.5 .. 0.12.3',
    headerLayouts: [HEADER_LAYOUT.LEGACY, HEADER_LAYOUT.TOKEN],
    joinTolerant: true,
    levelUpdateHasTrailingByte: false,     // 0.20.0 之前 LevelUpdate 恰 82 字节
    levelDataLayouts: [LEVEL_DATA_LAYOUT.RAW, LEVEL_DATA_LAYOUT.MSG22, LEVEL_DATA_LAYOUT.OPAQUE],
    // 发给该族的 "进游戏" 包 id: 老引擎是 5(初始化 SnapshotWriter/SnapshotReader +
    // FriendBarn::NetRecvEnterGame), 新引擎(0.22.0+)是 17。见 PacketIds.js 的长注释。
    enterGamePacketIds: [5],
    mqtt: false,
    maxPlayersPerLevel: 8,
    snapshotKeyframeInterval: 10,
    // 入房是否广播 EnterGame / 何时 Elect —— 与 behaviour.js 的开关同名, 仅在开关未显式
    // 设置时作为该族的建议默认值。
    suggest: Object.freeze({
      enterGameOnJoin: true,
      elect: true,
      electOnJoin: false,
      opaqueLevelData: true,
      catchUpLevelData: true
    })
  }),

  [FAMILY.MQTT]: Object.freeze({
    id: FAMILY.MQTT,
    // 2026-10-18: 不再并入 modern。UDP 传输行为确实一致, 但**关卡状态/权威/RPC**
    // 的字节布局不同(0.13.4 的 RPC 枚举只到 16, 0.22+ 更多): 同一张图上混住时,
    // modern 客户端上传的 level state 发给 0.13.4 会直接
    // "BinaryStream ... val out of range: 20 in [0,16]" 断言闪退。
    // 隔离键只影响 level data / 权威 / GameMsg 转发, 不影响包头与发包逻辑。
    effectiveFamily: FAMILY.MQTT,
    label: 'MQTT-era ENet (0.13.4–0.21.5)',
    versionRange: '0.13.4 .. 0.21.5',
    headerLayouts: [HEADER_LAYOUT.TOKEN, HEADER_LAYOUT.LEGACY],
    joinTolerant: true,
    levelUpdateHasTrailingByte: false,
    levelDataLayouts: [LEVEL_DATA_LAYOUT.MSG22, LEVEL_DATA_LAYOUT.RAW, LEVEL_DATA_LAYOUT.OPAQUE],
    // 0.13.4 的 NetRecvMsg(case 5)证实这一段仍然是 5 —— mqtt 时代沿用了老 id 表。
    enterGamePacketIds: [5],
    mqtt: true,
    maxPlayersPerLevel: 8,
    snapshotKeyframeInterval: 10,
    suggest: Object.freeze({
      enterGameOnJoin: true,
      elect: true,
      electOnJoin: false,
      opaqueLevelData: true,
      catchUpLevelData: true
    })
  }),

  [FAMILY.MODERN]: Object.freeze({
    id: FAMILY.MODERN,
    effectiveFamily: FAMILY.MODERN,
    label: 'Modern ENet (0.22.0–0.34.3)',
    versionRange: '0.22.0 .. 0.34.3',
    headerLayouts: [HEADER_LAYOUT.TOKEN, HEADER_LAYOUT.LEGACY],
    joinTolerant: true,
    levelUpdateHasTrailingByte: true,      // 0.20.0+ 末尾有 1 字节
    levelDataLayouts: [LEVEL_DATA_LAYOUT.OPAQUE, LEVEL_DATA_LAYOUT.MSG22, LEVEL_DATA_LAYOUT.RAW],
    // 0.22.0+ 才是 17(实测 0.22.0 / 0.23.6 / 0.34.0 联机正常, 一直用的就是 17)。
    enterGamePacketIds: [17],
    mqtt: false,
    maxPlayersPerLevel: 8,
    snapshotKeyframeInterval: 10,
    suggest: Object.freeze({
      enterGameOnJoin: true,
      elect: true,
      electOnJoin: false,
      opaqueLevelData: true,
      catchUpLevelData: true
    })
  })
});

// 版本号字符串 -> {major,minor,patch,value} ; 解析失败返回 null。
export function parseVersion(text) {
  if (text === undefined || text === null) {
    return null;
  }
  const match = String(text).match(/(\d+)\.(\d+)\.(\d+)/);
  if (!match) {
    return null;
  }
  const [major, minor, patch] = [Number(match[1]), Number(match[2]), Number(match[3])];
  return {
    major,
    minor,
    patch,
    // 可比较数值: 0.13.4 -> 0.134, 0.32.2 -> 0.322
    value: major + minor / 100 + patch / 10000
  };
}

// 依据版本号判断协议族(优先查离线扫描矩阵, 命中即返回; 否则用版本区间兜底)。
export function familyForVersion(text) {
  const parsed = parseVersion(text);
  if (!parsed) {
    return null;
  }
  const entry = matrixEntry(`${parsed.major}.${parsed.minor}.${parsed.patch}`);
  if (entry) {
    return entry.family;
  }
  if (parsed.major !== 0) {
    // 目前观测到的都是 0.x; 非 0.x 视为最新族。
    return FAMILY.MODERN;
  }
  if (parsed.minor <= 12) {
    return FAMILY.LEGACY;
  }
  if (parsed.minor <= 21) {
    return FAMILY.MQTT;
  }
  return FAMILY.MODERN;
}

// 在离线矩阵里按精确版本号查一条记录(含 soBytes/crc 指纹)。
export function matrixEntry(versionText) {
  for (const row of versionMatrix) {
    if (row.version === versionText) {
      return row;
    }
  }
  return null;
}

export function profileForFamily(family) {
  return PROFILES[family] ?? PROFILES[FAMILY.MODERN];
}

// 玩家(或任意带 profile 的对象)在 **UDP 层** 实际生效的隔离键。
// 用途: 关卡状态的转发/缓存按族隔离(见 config/behaviour.js 的 familyIsolation)。
// 各版本专属引擎会覆盖成更细的键(sky071 -> 'legacy-071', sky0122 -> 'legacy-0122',
// sky0134/sky0155 -> 'mqtt-<server>-<client>'), 保证 RPC 集合不同的客户端不互发状态。
// 没有 profile(理论上不会发生, JoinService 一定带上)返回 null —— 两个 null 视为同族。
export function effectiveFamilyOf(entity) {
  const profile = entity?.profile;
  if (!profile) {
    return null;
  }
  return profile.effectiveFamily ?? profile.detectedFamily ?? profile.family ?? null;
}

// 两方是否属于同一(有效)协议族。用于"能不能互相转发关卡状态"的判定。
export function sameEffectiveFamily(a, b) {
  return effectiveFamilyOf(a) === effectiveFamilyOf(b);
}

// Compatibility policy for modern audience GameMsg types on 0.13.4.
// This limit does not describe NetRpc IDs: those are encoded in a bitstream
// against a runtime registration bound. The observed 20-in-[0,16] crash in
// Sky#W was caused by its GetCapeLevel hook, not evidence of an RPC ID limit.
const GAME_MSG_LIMIT_0134 = 16;

export function gameMsgTypeLimitOf(entity) {
  const family = effectiveFamilyOf(entity);
  return family === 'mqtt-284-541' || family === 'mqtt-284-542'
    ? GAME_MSG_LIMIT_0134
    : 255;
}

// 该玩家(按其协议族)应该收到哪个 id 的 "进游戏" 包。
// 老族(legacy/mqtt) 一律 5: 0.12.3 与 0.13.4 两个 dump 的 NetRecvMsg case 5 都是
//   "初始化快照读写器 + FriendBarn::NetRecvEnterGame", 而 case 17 在老引擎里只是
//   设标志位(不是 EnterGame), 发过去会让客户端去解未填充的快照缓冲区(断言闪退)。
// 新族(modern) 一律 17: 0.22.0 / 0.23.6 / 0.34.0 实测联机正常就是用 17。
// eraEnterGameId=false 时退回改动前的行为(所有族都用 17), 便于真机 A/B 对比。
export function enterGamePacketIdsFor(entity, { eraEnterGameId = true } = {}) {
  if (!eraEnterGameId) {
    return [PacketIds.EnterGame];
  }
  const ids = entity?.profile?.enterGamePacketIds;
  return Array.isArray(ids) && ids.length ? ids : [PacketIds.EnterGame];
}

export function profileForVersion(versionText) {
  return profileForFamily(familyForVersion(versionText) ?? FAMILY.MODERN);
}

// 从 UDP 握手(包头布局 / 是否宽容解析)推断协议族。
//   - 'id+seq' / 'tolerant-join'  => legacy
//   - 'token+id+seq'              => modern(mqtt 与 modern 共用同一 UDP 包头)
// 返回 { family, confidence, source }。
export function detectFromHandshake({ layout, tolerant } = {}) {
  if (layout === 'tolerant-join' || layout === HEADER_LAYOUT.LEGACY) {
    return { family: FAMILY.LEGACY, confidence: 'high', source: `layout:${layout ?? HEADER_LAYOUT.LEGACY}` };
  }
  if (tolerant) {
    return { family: FAMILY.LEGACY, confidence: 'medium', source: 'tolerant-flag' };
  }
  if (layout === HEADER_LAYOUT.TOKEN) {
    return { family: FAMILY.MODERN, confidence: 'medium', source: `layout:${HEADER_LAYOUT.TOKEN}` };
  }
  return { family: FAMILY.MODERN, confidence: 'low', source: 'default' };
}

// 解析出一个"玩家适配上下文"。优先级:
//   1) 强制族(env ROOM_FORCE_PROFILE, 便于真机对比)
//   2) 显式版本号(HTTP/登录侧拿到的 UA 版本, 若透传到这里)
//   3) UDP 握手推断
// effectiveFamily 用于 UDP 策略选择(mqtt 并入 modern)。
export function resolveProfile({ forcedFamily, version, layout, tolerant } = {}) {
  if (forcedFamily && PROFILES[forcedFamily]) {
    const profile = PROFILES[forcedFamily];
    return { ...profile, detectedFamily: forcedFamily, confidence: 'forced', source: 'env:ROOM_FORCE_PROFILE', version: version ?? null };
  }

  const detected = version
    ? { family: familyForVersion(version), confidence: 'high', source: `version:${version}` }
    : detectFromHandshake({ layout, tolerant });

  const profile = profileForFamily(detected.family);
  return {
    ...profile,
    detectedFamily: detected.family,
    confidence: detected.confidence,
    source: detected.source,
    version: version ?? null
  };
}

// /health 用的概览。
export function describeProfiles() {
  return Object.values(PROFILES).map(profile => ({
    id: profile.id,
    effectiveFamily: profile.effectiveFamily,
    label: profile.label,
    versionRange: profile.versionRange,
    headerLayouts: profile.headerLayouts,
    levelDataLayouts: profile.levelDataLayouts,
    mqtt: profile.mqtt,
    maxPlayersPerLevel: profile.maxPlayersPerLevel
  }));
}

// 离线矩阵概览(覆盖了哪些版本)。
export function describeMatrix() {
  return {
    total: versionMatrix.length,
    versions: versionMatrix.map(row => row.version),
    families: versionMatrix.reduce((acc, row) => {
      acc[row.family] = (acc[row.family] ?? 0) + 1;
      return acc;
    }, {})
  };
}
