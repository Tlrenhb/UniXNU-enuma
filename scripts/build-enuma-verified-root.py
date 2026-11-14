#!/usr/bin/env python3
"""Build owner-signed, fully verified OS and SystemOS policies for enuma's UFS windows."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parents[3]
BUILD = os.environ['UNIXNU_IPSW_BUILD']
TARGETS = ROOT / '.cache/firmware/target-volumes'
IPSW = OUT / 'ipsw' / BUILD
TARGETS.mkdir(parents=True, exist_ok=True)
for name in ('044-76040-038.dmg', '044-76034-038.dmg'):
    source = IPSW / name
    if not source.is_file():
        raise SystemExit(f'missing pinned IPSW input: {source}')
    target = TARGETS / name
    if target.is_symlink() or target.exists():
        if target.resolve() != source.resolve():
            raise SystemExit(f'{target} already exists and does not refer to {source}')
    else:
        target.symlink_to(source)

host = ROOT / 'build/verified-root-host'
openssl = Path(subprocess.check_output(['brew', '--prefix', 'openssl@3'], text=True).strip())
subprocess.run([sys.executable, ROOT / 'kits/VerifiedRoot/tests/check.py', '--output', host,
                '--openssl-prefix', openssl], check=True)

mount = TARGETS / 'os-mount'
mount.mkdir(parents=True, exist_ok=True)
attached = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-readonly', '-nobrowse',
                                                   '-mountpoint', mount, '-plist', TARGETS / '044-76040-038.dmg']))
device = next((item['dev-entry'] for item in attached.get('system-entities', [])
               if item.get('dev-entry')), None)
try:
    if not device:
        raise SystemExit('hdiutil attached the image without reporting a device')
    subprocess.run([sys.executable, ROOT / 'scripts/build-owner-os-policy.py'], check=True)
finally:
    if device:
        subprocess.run(['hdiutil', 'detach', device], check=True)

subprocess.run([sys.executable, ROOT / 'scripts/build-owner-systemos-policy.py'], check=True)
for component in ('os', 'systemos'):
    record = json.loads((ROOT / f'build/verified-root-{component}/verification.json').read_text())
    if not record['real_c_signature_and_block_verification'] or record['original_image_modified'] is not False:
        raise SystemExit(f'{component}: real full-image verification did not pass')

subprocess.run([sys.executable, ROOT / 'scripts/build-verified-root-probe.py', '--component', 'os',
                '--include-systemos'], check=True)
print('PASS: pinned original OS and SystemOS fully verified; UFS policy archive built')
