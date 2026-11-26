"""Build an independent LAN test of the supplied 169938 download."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
import zlib
from adapt_skyw_0134 import patch_tls

ROOT = Path(r'C:\Users\Jason\Project\SkyLA')
SOURCE = ROOT / 'Others/sky-children-of-the-light-0-13-4-169938.apk'
OUT = ROOT / 'Sky/analysis-tools/adaptation/clean-0134-169938'
JAVA = r'C:\Users\Jason\scoop\apps\idea\current\IDE\jbr\bin\java.exe'
TOOLS = ROOT / 'Sky/analysis-tools'
PACKAGE = 'com.tgc.sky.android.la134n'

def main():
    digest = hashlib.file_digest(SOURCE.open('rb'), 'sha256').hexdigest()
    assert digest == 'daf58b798919622b5b07cf28d4f715e16a96d1e13e5862f4d6981da2e6f30a89'
    decoded = OUT / 'decoded'
    replacements = []
    for path in (decoded / 'smali').rglob('*.smali'):
        text = path.read_text(encoding='utf-8')
        if 'live.radiance.thatgamecompany.com' in text:
            path.write_text(text.replace('live.radiance.thatgamecompany.com', '192.168.123.136:18443'), encoding='utf-8')
            replacements.append(str(path.relative_to(decoded)))
    if not replacements:
        assert '192.168.123.136:18443' in (decoded / 'smali/com/tgc/sky/BuildConfig.smali').read_text(encoding='utf-8')
    manifest = decoded / 'AndroidManifest.xml'
    text = manifest.read_text(encoding='utf-8')
    if f'package="{PACKAGE}"' not in text:
        text = text.replace('com.tgc.sky.android', PACKAGE)
    text = text.replace('android:label="@string/app_name"', 'android:label="Sky 0134 原包测试"')
    manifest.write_text(text, encoding='utf-8')
    rebuilt = OUT / 'carrier-rebuilt.apk'
    subprocess.run([JAVA, '-jar', str(TOOLS / 'apktool_3.0.3.jar'), 'b', str(decoded), '-o', str(rebuilt)], check=True)
    output = OUT / 'Sky-0134-Downloaded-LaServer-unsigned.apk'
    if output.exists():
        raise FileExistsError(output)
    with zipfile.ZipFile(SOURCE) as source, zipfile.ZipFile(rebuilt) as built:
        native, patches = patch_tls(source.read('lib/arm64-v8a/libBootloader.so'))
        audio_names = ['assets/Data/Audio/Fmod/fmodandroid/Music_AP11' + suffix
                       for suffix in ('.bank', '.streams.bank', '.assets.bank')]
        donor = ROOT / 'Sky/analysis-tools/adaptation/original-0134-169938/source-base.apk'
        with zipfile.ZipFile(donor) as donor_zip:
            audio = {name: donor_zip.read(name) for name in audio_names}
        assert hashlib.sha256(audio[audio_names[1]]).hexdigest() == 'b10ffb3114ef21d394603921da8687bad10b4f7e9b00b4f6775ffd52a9c9fb5a'
        with zipfile.ZipFile(output, 'w', allowZip64=True) as target:
            for entry in source.infolist():
                if entry.filename.startswith('META-INF/'):
                    continue
                if entry.filename in audio:
                    # Keep metadata, stream and sample bank from one verified set.
                    data = audio[entry.filename]
                else:
                    data = source.read(entry)
                if entry.filename == 'classes2.dex':
                    data = built.read('classes.dex')
                elif entry.filename in ('AndroidManifest.xml', 'resources.arsc'):
                    data = built.read(entry.filename)
                elif entry.filename == 'lib/arm64-v8a/libBootloader.so':
                    data = native
                target.writestr(entry, data)
    (OUT / 'changes.json').write_text(json.dumps({'source_sha256': digest, 'package': PACKAGE,
        'host': '192.168.123.136:18443', 'host_smali': replacements, 'tls_test_patches': patches,
        'assets': 'source assets retained except the matched Music_AP11 bank set',
        'audio_replacement': {'donor': str(donor), 'members': audio_names,
            'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in audio.items()},
            'reason': 'source stream CRC mismatch; use matched metadata/stream/sample banks',
            'runtime_compatibility': 'pending'},
        'version': '0.13.4 (169938)'}, indent=2), encoding='utf-8')
    print(output)

if __name__ == '__main__':
    main()
