#!/usr/bin/env python3
"""Archive the no-op probe hooks of kits/<board>/LinkStubs.c for the kernel link (CONFIG_XNU_EXTRA_LD_ARCHIVES)."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# The device repository's package/ appears in the assembled tree as kits/<board.kit> (official layout),
# so resolve the kit by glob instead of hardcoding pipa's name. Vendored from UniXNU-pipa.
candidates = sorted(ROOT.glob('kits/*/LinkStubs.c'))
if len(candidates) != 1:
    raise SystemExit(f'expected exactly one kits/*/LinkStubs.c, found {len(candidates)}: {candidates}')
source = candidates[0]
out = ROOT / 'build/link-stubs'
out.mkdir(parents=True, exist_ok=True)
obj, archive = out / 'LinkStubs.o', out / 'libLinkStubs.a'
subprocess.run(['xcrun', '--sdk', 'iphoneos', 'clang', '-arch', 'arm64', '-mkernel', '-miphoneos-version-min=17.0',
                '-Os', '-Wall', '-Werror', '-c', source, '-o', obj], check=True)
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, obj], check=True)
print(archive)
