# -*- coding: utf-8 -*-
"""
Service接口: 留影蜡烛 v2 (0.30+ 客户端)

客户端在 0.30 以后把留影拆成"分块上传 + 断点播放":

  POST /service/recording/api/v2/create   {"recording_id", "level_id", "pool_type",
                                           "recording_data": {...}}       -> 第 0 块
  POST /service/recording/api/v2/update   {"recording_id", "chunk": 1..N,
                                           "recording_data": {...}}       -> 第 N 块
  POST /service/recording/api/v2/get      {"recording_id"}                -> 取 seek_table/link
  GET  /ugc/<user_id>/<recording_id>.ndjson (Range 请求按 seek_table 取块)

官方响应(抓包 0.32.2):
  create/update: {"status":"OK","recording_id":...,"link":"...",
                  "seek_table":{"0":{"start":0,"end":1464},...}}
  get:           {"MongoID":"...","status":"OK","recording_id":...,
                  "seek_table":{...},"link":"...","user_id":...,"level_id":...}

之前只实现了 v1(把整段 recording_data 塞进一个 JSON 文件)。0.30+ 客户端走 v2:
create 没有路由 -> 落到兜底 {} -> 客户端拿不到 recording_id/link -> 录完留影
"没有创建出现留影"; 播放时 get 也拿不到 seek_table -> 点留影没反应。

本文件的做法:
  - 每块 recording_data 序列化成一行 NDJSON 追加到 instance/recordings/<id>.ndjson;
  - seek_table 记录每行的字节区间(start/end), get 时原样返回;
  - link 指向本服 /ugc/<user_id>/<id>.ndjson, 由下面的 serve_recording_ugc 提供
    (支持 Range, 客户端按 seek_table 的 start/end 做分块拉取);
  - 同时把第 0 块写进 v1 的 <id>.json, 老客户端/旧代码仍能按 v1 取到数据。
"""
from flask import request, Response
import hashlib
import json
import logging
import os
import threading
import time

from . import store

logger = logging.getLogger(__name__)

# 同一录影的块是顺序追加的, 并发 update 必须串行, 否则字节区间会算错。
_chunk_lock = threading.Lock()

_NDJSON_EXT = ".ndjson"


def _resp(body, status=200):
    return Response(response=json.dumps(body, ensure_ascii=False, sort_keys=False),
                    status=status, mimetype="application/json")


def _public_base():
    """本服对外地址(优先反向代理头)。link 要让客户端能直接 GET 到。"""
    proto = (request.headers.get("X-Forwarded-Proto") or request.scheme or "http")
    host = (request.headers.get("X-Forwarded-Host") or request.headers.get("Host")
            or request.host or "")
    proto = proto.split(",")[0].strip() or "http"
    return ("%s://%s" % (proto, host)).rstrip("/")


def _link_for(recording_id, user_id):
    return "%s/ugc/%s/%s%s" % (_public_base(), user_id or "_", store.safe_id(recording_id),
                               _NDJSON_EXT)


def _ndjson_path(recording_id):
    return os.path.join(store.REC_DIR, store.safe_id(recording_id) + _NDJSON_EXT)


def _mongo_id(recording_id):
    """官方 get 响应里的 MongoID(24 位十六进制)。只要稳定即可, 客户端不解析。"""
    return hashlib.sha1(str(recording_id).encode("utf-8")).hexdigest()[:24]


def _author(data):
    """作者 id: user_id 常是全 0 占位, 真实用户看 user。"""
    uid = data.get("user") or ""
    claimed = str(data.get("user_id") or "")
    if uid and set(claimed) <= {"0", "-", ""}:
        claimed = uid
    return uid or claimed or "00000000-0000-0000-0000-000000000000"


def _chunk_data(data):
    rec = data.get("recording_data")
    if isinstance(rec, str):
        try:
            rec = json.loads(rec)
        except Exception:
            rec = {}
    if not isinstance(rec, dict):
        rec = {}
    return rec


def _line(rec_data):
    return json.dumps(rec_data, ensure_ascii=False, separators=(",", ":"))


def _write_first(path, line):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(line)
    os.replace(tmp, path)


def _append(path, line):
    """追加一行, 返回 (start, end) 字节区间; 第一行前面不加换行。"""
    size = os.path.getsize(path) if os.path.exists(path) else 0
    prefix = "\n" if size > 0 else ""
    start = size + len(prefix.encode("utf-8"))
    end = start + len(line.encode("utf-8")) - 1
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(prefix + line)
    return start, end


def _rebuild_seek_table(path):
    """没有元数据时按文件行重建 seek_table(行内 JSON 不含换行, 可以按 \\n 切)。"""
    table = {}
    if not os.path.isfile(path):
        return table
    with open(path, "rb") as f:
        offset = 0
        for idx, raw in enumerate(f.read().split(b"\n")):
            if not raw:
                offset += 1
                continue
            table[str(idx)] = {"start": offset, "end": offset + len(raw) - 1}
            offset += len(raw) + 1
    return table


def _existing_meta(rid):
    meta = store.load(rid)
    return meta if isinstance(meta, dict) else None


def _ok_payload(rid, meta):
    return {
        "status": "OK",
        "recording_id": meta.get("recording_id") or rid,
        "link": _link_for(meta.get("recording_id") or rid, meta.get("user_id")),
        "seek_table": meta.get("seek_table") or {},
    }


def recording_v2_create():
    """第 0 块。recording_id 可能是客户端自己生成后带过来的(重试/重录同一 id)。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        author = _author(data)
        rid = str(data.get("recording_id") or "").strip()
        if not rid or not store.safe_id(rid):
            rid = hashlib.sha1(("%s|%s|%s" % (author, time.time(), os.urandom(8)))
                               .encode("utf-8")).hexdigest()[:24]
        level_id = str(data.get("level_id") or "")
        rec_data = _chunk_data(data)

        with _chunk_lock:
            meta = _existing_meta(rid)
            if meta:
                if meta.get("user_id") not in (author, None, ""):
                    return _resp({"status": "ERROR", "error": "RECORDING_ID_CONFLICT",
                                  "recording_id": rid}, 409)
                # 幂等: 客户端 create 重试(上一次响应丢了)直接回同一条
                if meta.get("seek_table"):
                    return _resp(_ok_payload(rid, meta))
                # 有 id 没块(旧 v1 记录): 用这次的块补成 v2
            os.makedirs(store.REC_DIR, exist_ok=True)
            path = _ndjson_path(rid)
            line = _line(rec_data)
            try:
                _write_first(path, line)
            except Exception as e:
                logger.warning("recording v2 create 写 ndjson 失败(%s): %s", rid, e)
                return _resp({"status": "ERROR", "error": "STORAGE_ERROR",
                              "recording_id": rid}, 500)

            seek_table = {"0": {"start": 0, "end": len(line.encode("utf-8")) - 1}}
            now = int(time.time())
            record = {
                "recording_id": rid,
                "user_id": author,
                "level_id": level_id,
                "pool_type": str(data.get("pool_type") or ""),
                "name": str(data.get("name") or data.get("title") or data.get("message") or ""),
                "created_at": (meta or {}).get("created_at") or now,
                "updated_at": now,
                "recording_data": rec_data,
                "seek_table": seek_table,
                "chunk_count": 1,
                "last_chet": rec_data.get("chet") or 0,
                "mode": "v2",
            }
            store.save(rid, record)
            logger.info("recording v2 create: id=%s user=%s level=%s bytes=%d",
                        rid, author, level_id, len(line.encode("utf-8")))
            return _resp(_ok_payload(rid, record))
    except Exception as e:
        logger.error("recording v2 create 异常: %s", e, exc_info=True)
        return _resp({"status": "ERROR", "error": str(e)}, 200)


def recording_v2_update():
    """第 1..N 块。chunk=0 表示重录, 从空文件重新开始。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        rid = str(data.get("recording_id") or "").strip()
        if not rid:
            return _resp({"status": "ERROR", "error": "MISSING_RECORDING_ID"}, 400)
        try:
            chunk = int(data.get("chunk"))
        except Exception:
            return _resp({"status": "ERROR", "error": "INVALID_CHUNK"}, 400)
        if chunk < 0:
            return _resp({"status": "ERROR", "error": "INVALID_CHUNK"}, 400)
        rec_data = _chunk_data(data)

        with _chunk_lock:
            meta = _existing_meta(rid)
            if meta is None:
                return _resp({"status": "ERROR", "error": "RECORDING_NOT_FOUND",
                              "recording_id": rid}, 404)
            author = _author(data)
            if meta.get("user_id") not in (author, None, "") and data.get("user_id"):
                if str(data.get("user_id")) != str(meta.get("user_id")):
                    return _resp({"status": "ERROR", "error": "RECORDING_FORBIDDEN",
                                  "recording_id": rid}, 403)

            path = _ndjson_path(rid)
            seek_table = {} if chunk == 0 else dict(meta.get("seek_table") or {})
            if chunk != 0 and str(chunk) in seek_table:
                # 幂等: 这一块之前已经落盘(响应丢了重发), 直接回当前进度
                return _resp(_ok_payload(rid, meta))
            if chunk != 0:
                expected = len(seek_table)
                if chunk != expected:
                    return _resp({"status": "ERROR", "error": "OUT_OF_ORDER_CHUNK",
                                  "expected": expected, "received": chunk,
                                  "recording_id": rid}, 409)

            line = _line(rec_data)
            try:
                if chunk == 0:
                    _write_first(path, line)
                    start, end = 0, len(line.encode("utf-8")) - 1
                else:
                    start, end = _append(path, line)
            except Exception as e:
                logger.warning("recording v2 update 写 ndjson 失败(%s): %s", rid, e)
                return _resp({"status": "ERROR", "error": "STORAGE_ERROR",
                              "recording_id": rid}, 500)

            seek_table[str(chunk)] = {"start": start, "end": end}
            now = int(time.time())
            meta.update({
                "seek_table": seek_table,
                "chunk_count": len(seek_table),
                "last_chet": rec_data.get("chet") or meta.get("last_chet") or 0,
                "updated_at": now,
                "mode": "v2",
            })
            if chunk == 0:
                # v1 兜底数据也换成最新的第 0 块
                meta["recording_data"] = rec_data
            store.save(rid, meta)
            logger.info("recording v2 update: id=%s chunk=%d bytes=%d..%d",
                        rid, chunk, start, end)
            return _resp(_ok_payload(rid, meta))
    except Exception as e:
        logger.error("recording v2 update 异常: %s", e, exc_info=True)
        return _resp({"status": "ERROR", "error": str(e)}, 200)


def recording_v2_get():
    """取 seek_table + link。客户端播放时按 seek_table 逐块 GET link。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        rid = ""
        for k in ("recording_id", "resource_id", "id", "recording"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                rid = v.strip()
                break
        if rid and not store.load(rid):
            # feed id(uuid / "<uuid>:<8hex>") -> 反查真正的 recording_id
            try:
                from service.social_feed import extra_store
                for cand in (extra_store.resource_id_for_feed(rid.split(":")[0]),
                             extra_store.resource_id_for_feed(rid)):
                    if cand and store.load(cand):
                        rid = cand
                        break
            except Exception:
                pass
        meta = store.load(rid) if rid else None
        if meta is None:
            return _resp({"status": "NOT_FOUND", "recording_id": rid})

        seek_table = meta.get("seek_table") or {}
        if not seek_table:
            seek_table = _rebuild_seek_table(_ndjson_path(rid))
            if seek_table:
                meta["seek_table"] = seek_table
                store.save(rid, meta)
        return _resp({
            "MongoID": _mongo_id(meta.get("recording_id") or rid),
            "status": "OK",
            "recording_id": meta.get("recording_id") or rid,
            "seek_table": seek_table,
            "link": _link_for(meta.get("recording_id") or rid, meta.get("user_id")),
            "user_id": meta.get("user_id") or data.get("user") or "",
            "level_id": meta.get("level_id") or data.get("level_id") or "",
        })
    except Exception as e:
        logger.error("recording v2 get 异常: %s", e, exc_info=True)
        return _resp({"status": "NOT_FOUND"})


def _parse_range(header, total):
    """解析 bytes= 请求; 返回 (start, end) 或 None(无 Range) / "invalid"。"""
    if not header:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return "invalid"
    spec = header[6:].split(",")[0].strip()
    if "-" not in spec:
        return "invalid"
    a, b = spec.split("-", 1)
    try:
        if a == "":
            start = max(0, total - int(b))
            end = total - 1
        else:
            start = int(a)
            end = int(b) if b else total - 1
    except Exception:
        return "invalid"
    if start < 0 or start > end or start >= total:
        return "invalid"
    return start, min(end, total - 1)


def serve_recording_ugc(subpath):
    """GET /ugc/<user_id>/<recording_id>.ndjson —— 客户端按 Range 拉留影块。"""
    name = str(subpath or "").replace("\\", "/").split("/")[-1]
    if not name:
        return Response("not found", status=404, mimetype="text/plain")
    path = os.path.join(store.REC_DIR, store.safe_id(name))
    rec_dir = os.path.abspath(store.REC_DIR)
    if not os.path.abspath(path).startswith(rec_dir + os.sep) or not os.path.isfile(path):
        return Response("not found", status=404, mimetype="text/plain")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:
        logger.warning("ugc 读取失败(%s): %s", name, e)
        return Response("not found", status=404, mimetype="text/plain")

    total = len(data)
    rng = _parse_range(request.headers.get("Range"), total)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
        "Content-Type": "text/plain; charset=utf-8",
    }
    if rng == "invalid":
        headers["Content-Range"] = "bytes */%d" % total
        return Response(b"", status=416, headers=headers)
    if rng:
        start, end = rng
        headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, total)
        return Response(data[start:end + 1], status=206, headers=headers)
    return Response(data, status=200, headers=headers)
