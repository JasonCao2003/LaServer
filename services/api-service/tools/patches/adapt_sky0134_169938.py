"""Adapt the investigated phone-extracted 169938 APK without overwriting it."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zipfile

from adapt_skyw_0134 import patch_tls, replace_once

SOURCE_SHA256 = '58def64365f260c507371531e8cb904b749e02033a7cb4ecfe0199136d8d22c6'


def test_manifest(data):
    old, new = 'com.tgc.sky.android.p134a', 'com.tgc.sky.android.l134a'
    changed = 0
    for encoding in ('utf-8', 'utf-16le'):
        before, after = old.encode(encoding), new.encode(encoding)
        assert len(before) == len(after)
        changed += data.count(before)
        data = data.replace(before, after)
    if not changed:
        raise ValueError('Source manifest package not found')
    data = bytearray(data)
    offset = struct.unpack_from('<H', data, 2)[0]
    pool_offset = None
    while offset < len(data):
        kind, header_size, size = struct.unpack_from('<HHI', data, offset)
        if kind == 1:
            pool_offset = offset
            break
        offset += size
    if pool_offset is None:
        raise ValueError('No manifest string pool')
    start = pool_offset
    _, header_size, size, count, styles, flags, strings_start, styles_start = struct.unpack_from('<HH6I', data, start)
    if styles or styles_start or header_size != 28:
        raise ValueError('Unexpected styled manifest string pool')
    pool = bytes(data[start:start + size])
    offsets = struct.unpack_from('<' + 'I' * count, pool, header_size)
    utf8 = bool(flags & 0x100)

    def read_length(pos, width):
        if width == 1:
            length = pool[pos]
            return (((length & 127) << 8) | pool[pos + 1], pos + 2) if length & 128 else (length, pos + 1)
        length = struct.unpack_from('<H', pool, pos)[0]
        return (((length & 32767) << 16) | struct.unpack_from('<H', pool, pos + 2)[0], pos + 4) if length & 32768 else (length, pos + 2)

    strings = []
    for relative in offsets:
        length, pos = read_length(strings_start + relative, 1 if utf8 else 2)
        if utf8:
            length, pos = read_length(pos, 1)
            strings.append(pool[pos:pos + length].decode('utf-8'))
        else:
            strings.append(pool[pos:pos + 2 * length].decode('utf-16le'))
    label = 'Sky 0.13.4 私服测试'
    encoded = label.encode('utf-8' if utf8 else 'utf-16le')
    text_length = len(label.encode('utf-16le')) // 2
    encoded = (bytes([text_length, len(encoded)]) + encoded + b'\0') if utf8 else (struct.pack('<H', text_length) + encoded + b'\0\0')
    old_strings = pool[strings_start:]
    new_pool = bytearray(pool[:header_size] + pool[header_size:header_size + count * 4]
                         + struct.pack('<I', len(old_strings))
                         + pool[header_size + count * 4:strings_start] + old_strings + encoded)
    new_pool.extend(b'\0' * (-len(new_pool) % 4))
    struct.pack_into('<I', new_pool, 4, len(new_pool))
    struct.pack_into('<I', new_pool, 8, count + 1)
    struct.pack_into('<I', new_pool, 16, flags & ~1)
    struct.pack_into('<I', new_pool, 20, strings_start + 4)
    data[start:start + size] = new_pool
    struct.pack_into('<I', data, 4, len(data))
    offset = start + len(new_pool)
    labels = 0
    while offset < len(data):
        kind, _, size = struct.unpack_from('<HHI', data, offset)
        if kind == 0x102:
            tag = strings[struct.unpack_from('<I', data, offset + 20)[0]]
            attr_start, attr_size, attr_count = struct.unpack_from('<HHH', data, offset + 24)
            if tag in ('application', 'activity'):
                for i in range(attr_count):
                    attr = offset + 16 + attr_start + i * attr_size
                    name = strings[struct.unpack_from('<I', data, attr + 4)[0]]
                    if name == 'label':
                        struct.pack_into('<I', data, attr + 8, count)
                        data[attr + 15] = 3
                        struct.pack_into('<I', data, attr + 16, count)
                        labels += 1
        if not size:
            raise ValueError('Invalid manifest chunk')
        offset += size
    if not labels:
        raise ValueError('No application label found')
    return bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--java', required=True)
    parser.add_argument('--apktool', required=True, type=Path)
    parser.add_argument('--host', required=True)
    args = parser.parse_args()
    with args.apk.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError('Not the investigated 169938 source APK')
    if any(c in args.host for c in ['"', '\n', '\r', '\\', '/']) or ':' not in args.host:
        raise ValueError('Expected hostname-or-IPv4:port')
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root / 'Sky-0134-LaServer-unsigned.apk'
    if output.exists():
        raise FileExistsError(output)
    carrier, decoded = root / 'carrier2.apk', root / 'decoded2'
    with zipfile.ZipFile(args.apk) as source:
        if not (decoded / 'apktool.yml').exists():
            with zipfile.ZipFile(carrier, 'w') as target:
                for name in ['AndroidManifest.xml', 'resources.arsc']:
                    target.writestr(name, source.read(name))
                target.writestr('classes.dex', source.read('classes2.dex'))
            subprocess.run([args.java, '-jar', str(args.apktool), 'd', '-r', '-o', str(decoded), str(carrier)], check=True)
        config = decoded / 'smali/com/tgc/sky/BuildConfig.smali'
        config.write_text(replace_once(config.read_text(encoding='utf-8'),
                          '"live.radiance.thatgamecompany.com"', json.dumps(args.host)), encoding='utf-8')
        rebuilt = root / 'carrier2-rebuilt.apk'
        subprocess.run([args.java, '-jar', str(args.apktool), 'b', str(decoded), '-o', str(rebuilt)], check=True)
        with zipfile.ZipFile(rebuilt) as built:
            dex = built.read('classes.dex')
        native, changes = patch_tls(source.read('lib/arm64-v8a/libBootloader.so'))
        with zipfile.ZipFile(output, 'w', allowZip64=True) as target:
            for entry in source.infolist():
                if entry.filename.startswith('META-INF/'):
                    continue
                data = source.read(entry)
                if entry.filename == 'classes2.dex':
                    data = dex
                elif entry.filename == 'AndroidManifest.xml':
                    data = test_manifest(data)
                elif entry.filename == 'lib/arm64-v8a/libBootloader.so':
                    data = native
                target.writestr(entry, data)
    (root / 'changes.json').write_text(json.dumps({
        'source_sha256': digest, 'package': 'com.tgc.sky.android.l134a',
        'version': '0.13.4.169938', 'host': args.host, 'tls_test_patches': changes,
        'note': 'Independent test package; accepts untrusted certificates; existing host settings dialog preserved.'
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
