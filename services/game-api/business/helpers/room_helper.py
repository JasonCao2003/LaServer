# -*- coding: utf-8 -*-
"""
房间池管理 (room pool)
=====================
所有用户通用的 UDP 房间池。规则:

- 服务器启动时自动创建 3 个空闲房间(无人房间)。
- 始终保持有 3 个空闲房间: 一旦某个空闲房间被玩家加入(变为占用),
  立即再补一个新的空闲房间, 使空闲数恢复为 3。
- find_previous_or_empty 请求时:
    * 优先复用该用户上一次所在的房间(若仍有效);
    * 否则从池子里取一个空闲房间分配给他, 然后补池。

实现说明:
- 复用现有 GameServer 表存房间, ServerAssignment 表存"用户->房间"分配。
- GameServer.current_players==0 即"空闲房间"。
- 房间 uri 形如 host:port。host 取**动态隧道公网地址**(.sky_tunnel_<port>.json
  里的隧道主机名), 公网对外即用隧道地址; 拿不到隧道时回退到 config/room_config.json
  的 host, 再回退本机回环。端口在已用端口基础上自增, 保证唯一。
- 隧道地址在停服重开后会变, 每次补池/分配前会把现有房间(及分配记录)的 host 段
  同步成当前隧道地址(自愈), 保证发给客户端的永远是当前可达地址。
- 加了进程内锁, 避免并发请求重复建房/超量分配。
"""
from paths import SERVICE_ROOT, STATE_DIR

import os
import re
import json
import time
import hashlib
import threading

# 维持的空闲房间数量(需求: 始终保持 3 个空闲房间)
IDLE_ROOM_TARGET = 3
ROOM_CAPACITY = 50

_lock = threading.RLock()

# 老客户端(< 0.20.0)的 UDP 网络层断言: 房间 uri 串长必须 < INET6_ADDRSTRLEN(46),
# 且只认 IP 地址。隧道域名(60+ 字符)会触发 TGCNet.cpp 断言崩溃。
# 故对老客户端把房间 host 换成短 IP(ip_host, 默认 127.0.0.1), 端口不变, 重算签名。
INET6_ADDRSTRLEN = 46
NEW_CLIENT_MIN = (0, 20, 0)


def _base_dir():
    return SERVICE_ROOT


def _config_path():
    return os.path.join(_base_dir(), "config", "room_config.json")


def _detect_lan_ip():
    """探测本机局域网 IPv4(默认路由出口地址, 即同 WiFi 玩家能连上的那个)。

    用 UDP connect 技巧: 不真正发包, 只让系统按路由表选出出口网卡的源地址。
    失败返回 None。
    """
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip or None
    except Exception:
        return None


def _local_ipv4_addrs():
    """本机所有 IPv4 地址(含探测到的默认出口地址)。用于判断配置里的 IP 是否仍属本机。"""
    import socket
    addrs = set()
    probes = (
        lambda: socket.gethostbyname_ex(socket.gethostname())[2],
        lambda: [i[4][0] for i in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET)],
    )
    for probe in probes:
        try:
            addrs.update(a for a in probe() if a)
        except Exception:
            pass
    d = _detect_lan_ip()
    if d:
        addrs.add(d)
    addrs.discard("127.0.0.1")
    return addrs


def _is_private_ipv4(ip):
    """10.0.0.0/8、172.16.0.0/12、192.168.0.0/16 这些私网地址。"""
    try:
        a, b = [int(x) for x in str(ip).split(".")[:2]]
    except Exception:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def _ip_host():
    """客户端实时层能直连的短 IP host(不走隧道域名)。

    读 room_config.json 的 ip_host, 并做"换网自愈":
      - 没配 ip_host            -> 用实时探测到的局域网 IP;
      - 配的是私网 IP, 但它已不是本机地址(换了 WiFi / DHCP 改了地址)
                                -> 换成探测到的本机局域网 IP;
      - 配的是公网 IP(内网穿透/VPS 转发) -> 原样保留, 不干扰穿透场景。
    这样私服重启/换网后, 下发给客户端的永远是当前可达的本机地址, 不会因为
    配置里残留的旧 IP 让同一个 WiFi 下的玩家连不上。
    """
    cfg_ip = ""
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        cfg_ip = str(data.get("ip_host") or "").strip()
    except Exception:
        pass

    detected = _detect_lan_ip()
    if not cfg_ip:
        _pin_ip_host(detected)
        return detected or "127.0.0.1"
    if _is_private_ipv4(cfg_ip) and cfg_ip not in _local_ipv4_addrs() and detected:
        # 换网自愈: 顺手把新地址写回配置文件 —— 这样机器上的**每一个**后续请求都会
        # 下发同一个 host, 房间 uri 完全稳定(用户要求: 同局域网永远在同一个房间)。
        _pin_ip_host(detected)
        return detected
    return cfg_ip


def _pin_ip_host(ip):
    """把自愈选出的本机局域网地址写回 config/room_config.json 的 ip_host。

    只在"配置里的地址已经不是本机地址"时被调用一次: 写回去以后 cfg_ip 就属于本机,
    自愈分支不再命中, 所以不会每请求写盘。写失败无所谓(仍然返回探测到的地址)。
    公网 IP(VPS/frp/内网穿透场景)绝不改写 —— 那条分支由 _ip_host 自己保留原值。
    """
    if not ip or not _is_private_ipv4(ip):
        return False
    try:
        import logging
        with open(_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or str(data.get("ip_host") or "") == ip:
            return False
        data["ip_host"] = ip
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        logging.getLogger(__name__).info(
            "room_helper: ip_host 自愈为 %s (配置里的旧地址已不是本机地址)", ip)
        return True
    except Exception:
        return False


def _single_room_config():
    """读取'人人同房'配置: (enabled, host, port)。

    enabled  : 局域网 UDP 联机总开关。由两个键共同决定(都为真才启用):
               - lan_udp_enabled   (主控面板"局域网UDP联机"按钮, 缺省视为开)
               - single_shared_room(旧键, 缺省视为开)
               关掉任意一个 -> find_previous_or_empty 不再下发本机的真房间,
               退回"原来的假房间"(房间池 game_server/server_assignment)。
    host     : 客户端可达的短 IPv4(ip_host)。
    port     : 联机 ENet/UDP 服务器端口(realtime_port, 须与 udp源码 ROOM_PORT 一致)。

    开启后, 所有玩家的 find_previous_or_empty 都返回 host:port 这一个地址,
    于是全部连进同一个 ENet 房间、彼此可见。
    """
    enabled = True
    host = _ip_host()
    port = 25565
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        shared = bool(data.get("single_shared_room", True))
        lan_udp = bool(data.get("lan_udp_enabled", True))
        enabled = shared and lan_udp
        rp = data.get("realtime_port")
        if rp:
            port = int(rp)
    except Exception:
        pass
    return enabled, host, port


def single_shared_room_response():
    """'人人同房'模式下的房间响应: 固定指向联机服务器, 所有玩家同一地址。
    未开启该模式时返回 None(走旧房间池逻辑)。"""
    enabled, host, port = _single_room_config()
    if not enabled:
        return None
    uri = "%s:%d" % (host, port)
    return {
        "uri": uri,
        "move_ts": int(time.time()),
        "signature": _gen_sig(uri, "signature"),
        "sig": _gen_sig(uri, "sig"),
        "ps_only": False,
        "platform_filter": 0,
    }


def to_ip_uri(uri):
    """把 host:port 的 host 段换成短 IP(ip_host), 端口保留。供老客户端避免断言崩溃。"""
    host, port = _split_uri(uri)
    if port is None:
        return uri
    return "%s:%d" % (_ip_host(), port)


def downgrade_response_for_old_client(resp):
    """把房间响应里的 uri 改成短 IP 形式并重算签名(老客户端 < 0.20.0 用)。
    resp 为 assign_room_for_user 返回的 dict; 原样修改并返回。"""
    if not resp or not resp.get("uri"):
        return resp
    new_uri = to_ip_uri(resp["uri"])
    resp["uri"] = new_uri
    resp["signature"] = _gen_sig(new_uri, "signature")
    resp["sig"] = _gen_sig(new_uri, "sig")
    return resp


def _server_port():
    """当前服务器端口(与隧道状态文件命名一致)。"""
    try:
        return int(os.environ.get("SKY_PORT", "8443") or "8443")
    except Exception:
        return 8443


def _tunnel_host():
    """读取动态隧道公网主机名(.sky_tunnel_<port>.json 由主控面板写入)。

    公网对外使用隧道地址: 房间 host 取隧道主机名。隧道地址只在停服重开时变,
    热更新/重开面板不变(地址锁定), 所以房间 host 也随之稳定。读不到返回 None。
    """
    port = _server_port()
    candidates = [
        os.path.join(STATE_DIR, ".sky_tunnel_%d.json" % port),
        os.path.join(STATE_DIR, ".sky_tunnel_8443.json"),
    ]
    for p in candidates:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            url = (data or {}).get("url") or ""
            m = re.match(r'https?://([a-z0-9-]+\.trycloudflare\.com)', url,
                         re.IGNORECASE)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


def _load_room_config():
    """读取房间池配置(host / 起始端口 / 区域)。无文件则返回默认。

    host 优先级: 动态隧道公网 > 配置文件 host > 本机回环。
    """
    cfg = {"host": "127.0.0.1", "base_port": 42578, "region": "default"}
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in ("host", "base_port", "region")
                        if k in data})
    except Exception:
        pass
    # 公网使用动态隧道地址: 覆盖 host(拿不到隧道时保留配置/默认 host)。
    th = _tunnel_host()
    if th:
        cfg["host"] = th
    return cfg


def _split_uri(uri):
    """拆出 (host, port)。端口缺失返回 (uri, None)。"""
    if uri and ":" in uri:
        host, _, port = uri.rpartition(":")
        try:
            return host, int(port)
        except Exception:
            return uri, None
    return uri, None


def _gen_sig(uri, salt):
    """为房间生成一个稳定的伪签名(私服客户端证书校验已被 patch, 仅占位用)。"""
    h = hashlib.sha256((uri + "|" + salt).encode("utf-8")).hexdigest()
    return h


def _next_port(GameServer, base_port):
    """在已存在房间端口的基础上取下一个可用端口。"""
    used = set()
    for gs in GameServer.query.all():
        _, port = _split_uri(gs.server_uri or "")
        if port is not None:
            used.add(port)
    p = base_port
    while p in used:
        p += 1
    return p


def _sync_rooms_to_host(db, GameServer, ServerAssignment, host):
    """把所有房间(及用户分配记录)的 host 段同步成当前公网 host, 端口不变。

    隧道公网地址在停服重开后会变, 旧房间 uri 里残留的是旧 host。这里在补池/分配
    前统一改写, 保证发给客户端的永远是当前可达的隧道公网地址(自愈)。
    """
    if not host:
        return
    changed = False
    for gs in GameServer.query.all():
        h, port = _split_uri(gs.server_uri or "")
        if port is None or h == host:
            continue
        new_uri = "%s:%d" % (host, port)
        # 同步引用了旧 uri 的分配记录
        for sa in ServerAssignment.query.filter_by(server_uri=gs.server_uri).all():
            sa.server_uri = new_uri
            sa.signature = _gen_sig(new_uri, "signature")
            sa.sig = _gen_sig(new_uri, "sig")
        gs.server_uri = new_uri
        changed = True
    if changed:
        db.session.commit()


def _create_idle_room(db, GameServer):
    """创建一个新的空闲房间并返回。调用方需持有 _lock。"""
    cfg = _load_room_config()
    port = _next_port(GameServer, int(cfg["base_port"]))
    uri = "%s:%d" % (cfg["host"], port)
    room = GameServer(
        server_uri=uri,
        region=cfg.get("region", "default"),
        capacity=ROOM_CAPACITY,
        current_players=0,
        is_active=True,
    )
    db.session.add(room)
    db.session.commit()
    return room


def ensure_idle_rooms(db, GameServer, ServerAssignment=None):
    """确保池中至少有 IDLE_ROOM_TARGET 个空闲房间。

    服务器启动时调用一次(建满 3 个); 每次分配房间后也调用(补池)。
    并把现有房间 host 同步为当前隧道公网地址(自愈)。返回当前空闲房间数。
    """
    with _lock:
        if ServerAssignment is not None:
            try:
                _sync_rooms_to_host(db, GameServer, ServerAssignment,
                                    _tunnel_host())
            except Exception:
                db.session.rollback()
        try:
            idle = GameServer.query.filter_by(is_active=True,
                                              current_players=0).count()
        except Exception:
            return 0
        created = 0
        while idle + created < IDLE_ROOM_TARGET:
            try:
                _create_idle_room(db, GameServer)
                created += 1
            except Exception:
                db.session.rollback()
                break
        return idle + created


def assign_room_for_user(db, GameServer, ServerAssignment, user_id,
                         force_empty=False):
    """为用户分配一个房间, 返回房间的响应字段 dict(含 uri/move_ts/签名等)。

    - force_empty=False 且该用户上次有有效分配 -> 复用旧房间(快速重进同房)。
    - 否则取一个空闲房间, 占用它(current_players+1), 并补池保持 3 个空闲。
    - 没有任何空闲房间时, 先尝试补池再取; 仍失败返回 None。
    - 所有发出的 uri host 均为当前隧道公网地址。
    """
    with _lock:
        now = int(time.time())
        # 先把房间地址同步成当前公网隧道地址, 保证复用/分配都用最新可达地址。
        try:
            _sync_rooms_to_host(db, GameServer, ServerAssignment, _tunnel_host())
        except Exception:
            db.session.rollback()

        # 1) 复用上次房间
        if not force_empty:
            prev = (ServerAssignment.query
                    .filter_by(user_id=user_id)
                    .order_by(ServerAssignment.updated_at.desc())
                    .first())
            if prev and prev.server_uri:
                room = GameServer.query.filter_by(server_uri=prev.server_uri,
                                                  is_active=True).first()
                if room:
                    return {
                        "uri": prev.server_uri,
                        "move_ts": prev.move_ts,
                        "signature": prev.signature,
                        "sig": prev.sig,
                        "ps_only": bool(prev.ps_only),
                        "platform_filter": prev.platform_filter or 0,
                    }

        # 2) 取一个空闲房间(没有就先补池)
        room = (GameServer.query
                .filter_by(is_active=True, current_players=0)
                .order_by(GameServer.id)
                .first())
        if not room:
            ensure_idle_rooms(db, GameServer, ServerAssignment)
            room = (GameServer.query
                    .filter_by(is_active=True, current_players=0)
                    .order_by(GameServer.id)
                    .first())
        if not room:
            return None

        # 3) 占用该房间
        room.current_players = (room.current_players or 0) + 1
        uri = room.server_uri
        signature = _gen_sig(uri, "signature")
        sig = _gen_sig(uri, "sig")

        # 4) 记录用户->房间分配(覆盖式: 更新已有, 否则新建)
        existing = ServerAssignment.query.filter_by(user_id=user_id).first()
        if existing:
            existing.server_uri = uri
            existing.move_ts = now
            existing.signature = signature
            existing.sig = sig
            existing.ps_only = False
            existing.platform_filter = 0
        else:
            db.session.add(ServerAssignment(
                user_id=user_id, server_uri=uri, move_ts=now,
                signature=signature, sig=sig, ps_only=False,
                platform_filter=0))
        db.session.commit()

        # 5) 玩家占了一个空闲房间 -> 立刻补一个新的空闲房间, 保持 3 个空闲
        ensure_idle_rooms(db, GameServer, ServerAssignment)

        return {
            "uri": uri,
            "move_ts": now,
            "signature": signature,
            "sig": sig,
            "ps_only": False,
            "platform_filter": 0,
        }

