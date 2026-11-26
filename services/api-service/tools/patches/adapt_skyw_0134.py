"""Build an independently installable LaServer test APK from the known Sky#W APK.

Uses caller-supplied Apktool and Java; never overwrites or signs the input APK.
The default endpoint requires `adb reverse tcp:8001 tcp:18443` on the test device.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import zipfile


SOURCE_SHA256 = '4fdc7a7f1a4a70b200a3f0c55eed026a12d7d2918068b9529e6e8d6b103c04b4'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Expected one occurrence: {old}')
    return text.replace(old, new)


def patch_manifest(data):
    for old, new in [('com.tgc.sky.android.xxwgk', 'com.tgc.sky.android.lasrv'),
                     ('Sky#W公开版', 'Sky#W测试版')]:
        count = 0
        for encoding in ('utf-8', 'utf-16le'):
            before, after = old.encode(encoding), new.encode(encoding)
            assert len(before) == len(after)
            count += data.count(before)
            data = data.replace(before, after)
        if not count:
            raise ValueError(f'Manifest string not found: {old}')
    return data


def patch_tls(data):
    script = Path(__file__).with_name('patch_libBootloader.py')
    spec = importlib.util.spec_from_file_location('sky_tls', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sections = module.parse_elf_sections(data)
    symbols = module.find_symbol_addresses(data, sections, list(module.PATCH_TABLE))
    if len(symbols) != 4:
        raise ValueError('Expected all four TLS symbols in this exact APK')
    phoff = struct.unpack_from('<Q', data, 32)[0]
    entsize, count = struct.unpack_from('<HH', data, 54)
    result, changes = bytearray(data), []
    for name, symbol in symbols.items():
        address = symbol['vaddr']
        offsets = []
        patch = module.PATCH_TABLE[name]['patch']
        for i in range(count):
            kind, flags, offset, va, _, size, _, _ = struct.unpack_from(
                '<IIQQQQQQ', data, phoff + i * entsize)
            if kind == 1 and flags & 1 and va <= address and address + len(patch) <= va + size:
                offsets.append(offset + address - va)
        if len(offsets) != 1 or symbol['size'] < len(patch):
            raise ValueError(f'Unmapped or undersized TLS function: {name}')
        offset = offsets[0]
        original = bytes(result[offset:offset + len(patch)])
        result[offset:offset + len(patch)] = patch
        changes.append({'symbol': name, 'file_offset': offset,
                        'original': original.hex(), 'patched': patch.hex()})
    return bytes(result), changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--java', required=True)
    parser.add_argument('--apktool', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--host', default='127.0.0.1:8001')
    parser.add_argument('--fix-mod-cape-level', action='store_true',
                        help='Limit the bundled GetCapeLevel hook to the engine maximum (16)')
    args = parser.parse_args()
    with args.apk.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError('Input is not the investigated Sky#W 0.13.4 APK')
    if not re.fullmatch(r'[a-zA-Z0-9.\-]+:[0-9]{1,5}', args.host):
        raise ValueError('Host must have hostname-or-IPv4:port format')
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    carrier, decoded = root / 'carrier.apk', root / 'decoded'
    output = root / 'SkyW-LaServer-unsigned.apk'
    if output.exists():
        raise FileExistsError(output)
    with zipfile.ZipFile(args.apk) as source:
        if not (decoded / 'apktool.yml').exists():
            with zipfile.ZipFile(carrier, 'w') as target:
                for name in ['AndroidManifest.xml', 'resources.arsc', 'classes.dex']:
                    target.writestr(name, source.read(name))
            subprocess.run([args.java, '-jar', str(args.apktool), 'd', '-r',
                            '-o', str(decoded), str(carrier)], check=True)
        activity = decoded / 'smali/com/skymodloader/pack/MainActivity.smali'
        text = activity.read_text(encoding='utf-8')
        text = replace_once(text, 'const-string v2, "0.23.6 (179643)"',
                            'const-string v2, "0.13.4"')
        text = replace_once(text, 'const v2, 0xf423f', 'const v2, 0x29994')
        text = replace_once(text,
            'invoke-virtual {p0, v0}, Lcom/skymodloader/pack/MainActivity;->startService(Landroid/content/Intent;)Landroid/content/ComponentName;',
            'nop  # LaServer test: bypass the bundled official-server proxy')
        activity.write_text(text, encoding='utf-8')
        config = decoded / 'smali/com/tgc/sky/BuildConfig.smali'
        config.write_text(replace_once(config.read_text(encoding='utf-8'),
            '"127.0.0.1:8001"', json.dumps(args.host)), encoding='utf-8')
        rebuilt = root / 'carrier-rebuilt.apk'
        subprocess.run([args.java, '-jar', str(args.apktool), 'b', str(decoded),
                        '-o', str(rebuilt)], check=True)
        with zipfile.ZipFile(rebuilt) as built:
            dex = built.read('classes.dex')
        native, changes = patch_tls(source.read('lib/arm64-v8a/libBootloader.so'))
        mod = source.read('lib/arm64-v8a/libciphered.so')
        if args.fix_mod_cape_level:
            offset = 0xf05f0
            if mod[offset:offset + 4] != bytes.fromhex('80028052'):
                raise ValueError('Unexpected bundled mod GetCapeLevel instruction')
            mod = mod[:offset] + bytes.fromhex('00028052') + mod[offset + 4:]
        with zipfile.ZipFile(output, 'w', allowZip64=True) as target:
            for entry in source.infolist():
                if entry.filename.startswith('META-INF/'):
                    continue
                data = source.read(entry)
                if entry.filename == 'classes.dex':
                    data = dex
                elif entry.filename == 'AndroidManifest.xml':
                    data = patch_manifest(data)
                elif entry.filename == 'lib/arm64-v8a/libBootloader.so':
                    data = native
                elif entry.filename == 'lib/arm64-v8a/libciphered.so':
                    data = mod
                target.writestr(entry, data)
    (root / 'adaptation-changes.json').write_text(json.dumps({
        'source_sha256': digest, 'package': 'com.tgc.sky.android.lasrv',
        'endpoint': args.host, 'reported_version': '0.13.4',
        'reported_build': 170388, 'local_proxy_started': False,
        'tls_test_patches': changes,
        'mod_cape_level': 16 if args.fix_mod_cape_level else 'unmodified',
        'limitations': 'Test APK accepts untrusted TLS certificates. MuMu ARM64 translation and network flows require runtime validation.'
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
