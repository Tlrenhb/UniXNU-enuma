#!/usr/bin/env python3
"""Offline preflight for the enuma board repository.

Stage: task-16 "pre-dump complete" state. Everything derivable offline is filled in
and cited in board.toml; this script makes sure nothing silently regressed.

What it checks without the tablet and without the UEFI tree:
  1. board.toml placeholders: which on-device measurements are still missing
  2. the IPSW block: identity tuple, member pins, fetch list
  3. [kernel] archives: the EXPECTED SET is present (each of the nine builders the minimal
     stages link), well formed, and (with --tree) each one resolves in the tree
     -- the kernel step fails with "no builder makes it, supply the archive" when an
     archive has no builder (UniXNU scripts/unixnu/kernel.py), so we replicate the rule.
     Deleting an entry used to be invisible (only "archives is non-empty" was checked);
     ARCHIVES_REQUIRED now makes a missing entry a hard error.
  4. the four staged defconfigs: exist, are cumulative, and never enable an SM8550-only flag
  5. [firmware]: preflight path, the six-patch DXE checklist (5 upstream Mu-Silicium +
     Gic-Spurious), enumaPkg / Resources/Configs/enuma.toml (with --uefi)
  6. boot image: [bootimg.image] header_version/pagesize/offsets and the splash
     [[bootimg.dtb_node]] (0x9c000000 + 0x2300000, from astide kona.dtsi:508-511)
  7. [payload.adt]: psci method, cpu_regs shape, owned_memory_size bounds -- the same
     assertions make-enuma-adt.py applies, caught earlier instead of at build time

Exit codes: 0 = no hard errors (outstanding TODOs are listed), 1 = hard errors.

Not implemented (needs the UEFI checkout or the tablet):
  - MemoryMapLib descriptor page alignment / overlap / "UEFI FD" match
  - the appended DTB splash region inside the "Display Reserved" descriptor
  Reference: xVanTuring/UniXNU-pipa scripts/check-pipa.py, docs/zh/porting/uefi-firmware.md.
"""
from pathlib import Path
import argparse
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / 'configs'

SM8550_ONLY = {
    'CONFIG_XNU_NATIVE_ADRENO': 'Adreno 740 kit (enuma is Adreno 650)',
    'CONFIG_XNU_VERIFIED_ROOT_PROBE': 'needs owner-signed volume policies (stage 4)',
    'CONFIG_XNU_OWNER_ROOT_AUDIT': 'owner chain (stage 4)',
    'CONFIG_XNU_OWNER_ROOT_TRUST': 'owner chain (stage 4)',
    'CONFIG_XNU_OWNER_CACHE_MOUNT': 'owner chain (stage 4)',
    'CONFIG_XNU_OWNER_CACHE_STARTUP': 'owner chain (stage 4)',
    'CONFIG_XNU_OWNER_BOOT_MANIFEST': 'owner chain (stage 4)',
    'CONFIG_XNU_OWNER_DATA_MOUNT': 'owner chain (stage 4)',
    'CONFIG_XNU_OWNER_UNION_SYSTEMOS': 'owner chain (stage 4)',
    'CONFIG_XNU_BOOT_PROGRESS': 'needs the owner chain (stage 4)',
}
# stage 1 must stay clean of hardware it cannot drive yet
STAGE1_FORBIDDEN = dict(SM8550_ONLY)
STAGE1_FORBIDDEN.update({
    'CONFIG_XNU_NATIVE_UFS': 'UFS needs the SM8250 register port (stage 2)',
    'CONFIG_XNU_NATIVE_LEGACY_DISPLAY': 'display is stage 3',
    'CONFIG_XNU_NATIVE_MOBILE_DISPLAY': 'display is stage 3',
    'CONFIG_XNU_SOURCE_MOCKFS': 'no fixture root yet (stage 4)',
    'CONFIG_XNU_NATIVE_RAM_BLOCK': 'no Data seed yet (stage 4)',
})
# The nine builder entries the minimal stages link. Provenance of each one (which repo
# actually ships it -- verified 2026-10-23 against main / sheng / umi / pipa) is tabulated
# in board.toml next to [kernel] archives and in README.md.
# Deleting an entry from board.toml must NOT pass this script: without it the kernel step
# dies with the official error below, which is exactly what a preflight is meant to catch.
ARCHIVES_REQUIRED = [
    'build-corecrypto-14.py',
    'build-corecrypto-14.py --namespace',
    'build-arm-driver.py ArmDTPlatform',
    'build-arm-driver.py ArmCpu',
    'build-arm-driver.py NativeAES',
    'build-native-aes-bindings.py',
    'build-libpthread-kernel.py',
    'build-link-stubs.py',
    'build-platform-power.py',
]
REQUIRED_DEFCONFIG = [
    'CONFIG_XNU_SOURCE_CORECRYPTO14', 'CONFIG_XNU_SOURCE_PTHREAD', 'CONFIG_XNU_ARM_DT_PLATFORM',
    'CONFIG_XNU_ARMCPU', 'CONFIG_XNU_NATIVE_AES', 'CONFIG_XNU_TRUST_CACHE_RUNTIME',
    'CONFIG_XNU_NATIVE_CS_HASH_CACHE', 'CONFIG_XNU_DT_PANIC_REGION', 'CONFIG_XNU_NATIVE_ARCH_TIMER',
    'CONFIG_XNU_NATIVE_PAN', 'CONFIG_XNU_SHENG_PMGR',
]
STAGES = [
    ('enuma_defconfig', 'stage 1: kernel entry / panic screen'),
    ('enuma_ufs_defconfig', 'stage 2: UFS root volumes'),
    ('enuma_display_touch_defconfig', 'stage 3: display + touch'),
    ('enuma_owner_defconfig', 'stage 4: owner chain / persistent Data'),
]
STAGE4_REQUIRED = [
    'CONFIG_XNU_VERIFIED_ROOT_PROBE', 'CONFIG_XNU_OWNER_ROOT_AUDIT', 'CONFIG_XNU_OWNER_ROOT_TRUST',
    'CONFIG_XNU_OWNER_CACHE_MOUNT', 'CONFIG_XNU_OWNER_CACHE_STARTUP', 'CONFIG_XNU_OWNER_BOOT_MANIFEST',
    'CONFIG_XNU_OWNER_DATA_MOUNT', 'CONFIG_XNU_OWNER_UNION_SYSTEMOS', 'CONFIG_XNU_BOOT_PROGRESS',
]
# Mu-Silicium upstream ships five MuPatches; UniXNU additionally requires Gic-Spurious.
UPSTREAM_PATCHES = ['AsmMacroLib.patch', 'Auth-Service.patch', 'Boot-Manager.patch',
                    'Timer.patch', 'Usb-Bus.patch']
UNIXNU_PATCH = 'Gic-Spurious.patch'
# from astide kona.dtsi:508-511 (cont_splash_region@9c000000, size 0x2300000)
SPLASH_BASE = 0x9C000000
SPLASH_SIZE = 0x2300000


def enabled_flags(path: Path) -> set:
    if not path.exists():
        return set()
    return {m.group(1) for m in re.finditer(r'^(CONFIG_XNU_[A-Z0-9_]+)=y\s*$', path.read_text(), re.M)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--uefi', type=Path, default=None, help='Mu-Silicium (enumaPkg) checkout')
    parser.add_argument('--tree', type=Path, default=None,
                        help='build tree root: every [kernel] archives entry must resolve here')
    args = parser.parse_args()
    errors, todos, notes = [], [], []

    text = (ROOT / 'board.toml').read_text()
    board = tomllib.loads(text)

    # 1. placeholders / identity ------------------------------------------------
    userdata = board.get('userdata', {})
    if not userdata.get('first_lba') or not userdata.get('last_lba'):
        todos.append('[userdata] first_lba/last_lba: adb shell su -c \'sgdisk -p /dev/block/sda\'')
    if userdata.get('lba_bytes') != 4096:
        todos.append('[userdata] lba_bytes: confirm with blockdev --getss')
    if str(board.get('payload', {}).get('dram_size', '0x0')) in ('0x0', '0'):
        todos.append('[payload] dram_size: adb shell su -c \'cat /proc/iomem\' (umi measured 3 banks = 0x2f9900000)')
    if board.get('board', {}).get('soc') not in ('SM8250', 'SM8250-AC'):
        errors.append(f"board.soc = {board.get('board', {}).get('soc')!r}; expected SM8250 or SM8250-AC")

    # 2. IPSW block -------------------------------------------------------------
    ipsw = board.get('ipsw', {})
    for key in ('url', 'product', 'version', 'build', 'device_class', 'chip_id', 'board_id'):
        if key not in ipsw:
            errors.append(f'[ipsw] has no {key}')
    if ipsw.get('product') == 'iPad7,1' and (ipsw.get('build'), ipsw.get('version')) != ('21H461', '17.7.11'):
        errors.append('iPad7,1 identity must match build 21H461 / 17.7.11 (same image as sheng/pipa/umi)')
    pins = ipsw.get('sha256', {})
    if len(pins) < 6:
        todos.append(f'[ipsw.sha256] has {len(pins)} pins; umi pins 6 members of this same IPSW '
                     '(kernelcache/DeviceTree/two dmg/two trustcache)')

    # 3. [kernel] archives ------------------------------------------------------
    archives = board.get('kernel', {}).get('archives')
    if not archives:
        errors.append('[kernel] archives is missing or empty: the kernel step raises '
                      '"no builder makes it, supply the archive" for every unbuilt *_ARCHIVE')
    else:
        for index, entry in enumerate(archives):
            if not isinstance(entry, str) or not entry.strip():
                errors.append(f'[kernel] archives[{index}] is not a non-empty string')
                continue
            script = entry.split()[0]
            if not script.endswith(('.py', '.sh')):
                errors.append(f'[kernel] archives[{index}] = {entry!r}: first token should be a builder script')
            if args.tree:
                candidates = [args.tree / 'scripts' / script, args.tree / script]
                if not any(c.exists() for c in candidates):
                    errors.append(f'[kernel] archives[{index}] = {entry!r}: no builder in {args.tree} '
                                  f'(looked for scripts/{script}); pin the revision that ships it')
        # expected-set gate: a deleted entry must be a hard error, not a silent pass
        present = {entry.strip() for entry in archives if isinstance(entry, str)}
        for required in ARCHIVES_REQUIRED:
            if required not in present:
                errors.append(f'Missing archive: {required} (no builder makes it, supply the archive)')
        extra = sorted(present - set(ARCHIVES_REQUIRED))
        if extra:
            notes.append(f'[kernel] archives has {len(extra)} extra entr(ies) beyond the nine checked: '
                         f'{extra}; make sure each one resolves in the pinned tree')

    # 4. staged defconfigs ------------------------------------------------------
    stage_flags = {}
    for name, label in STAGES:
        path = CONFIGS / name
        if not path.exists():
            errors.append(f'configs/{name} is missing ({label})')
            continue
        stage_flags[name] = enabled_flags(path)
    for name, label in STAGES:
        if name not in stage_flags:
            continue
        for flag in REQUIRED_DEFCONFIG:
            if flag not in stage_flags[name]:
                errors.append(f'configs/{name} is missing {flag}=y ({label})')
    for flag, why in STAGE1_FORBIDDEN.items():
        if flag in stage_flags.get('enuma_defconfig', set()):
            errors.append(f'enuma_defconfig enables {flag}=y ({why}); it belongs to a later stage')
    for earlier, later in zip([n for n, _ in STAGES], [n for n, _ in STAGES][1:]):
        if earlier in stage_flags and later in stage_flags:
            missing = stage_flags[earlier] - stage_flags[later]
            if missing:
                errors.append(f'{later} drops {sorted(missing)} from {earlier}: stages must be cumulative')
    for flag in STAGE4_REQUIRED:
        if flag not in stage_flags.get('enuma_owner_defconfig', set()):
            errors.append(f'enuma_owner_defconfig is missing {flag}=y (owner chain)')
    base = (ROOT / 'configs' / 'enuma_defconfig').read_text()
    if re.search(r'^CONFIG_XNU_CRYPTO_BOOTSTRAP="prelinked"', base, re.M) is None:
        notes.append('enuma_defconfig does not set CRYPTO_BOOTSTRAP="prelinked"; umi baseline uses "source" '
                     'and flips to prelinked in its phone profile -- keep this intentional divergence documented')

    # 5. firmware: preflight + the six-patch checklist --------------------------
    firmware = board.get('firmware', {})
    preflight = firmware.get('preflight', [])
    if not preflight:
        errors.append('[firmware] preflight is empty: the firmware step runs no offline check')
    for entry in preflight:
        if not (ROOT / entry).exists():
            errors.append(f'[firmware] preflight entry {entry!r} does not exist in this repository')
    patches = firmware.get('patches', [])
    if not any(UNIXNU_PATCH in p for p in patches):
        errors.append(f'[firmware] patches must include Resources/MuPatches/{UNIXNU_PATCH} '
                      '(masks the unhandled GIC interrupt; without it IPCC 0x105 loops forever on sheng)')
    if args.uefi:
        platform = args.uefi / 'Platforms/Xiaomi/enumaPkg'
        config = args.uefi / 'Resources/Configs/enuma.toml'
        if not config.exists():
            errors.append('Resources/Configs/enuma.toml is missing (upstream Mu-Silicium has no enuma config)')
        if not platform.exists():
            errors.append('Platforms/Xiaomi/enumaPkg is missing (fork pipaPkg; same SoC)')
        else:
            dsc = next(platform.glob('*.dsc'), None)
            if dsc is None:
                errors.append(f'{platform} has no .dsc')
            else:
                dsc_text = dsc.read_text()
                for wanted in ('UniXNUPkg', 'DarwinBoot', 'ShengBootMemoryDxe'):
                    if wanted not in dsc_text:
                        errors.append(f'{dsc.name} does not list {wanted}')
            if next(platform.glob('*.fdf'), None) is None:
                errors.append(f'{platform} has no .fdf')
        mu_patches = args.uefi / 'Resources/MuPatches'
        for patch in UPSTREAM_PATCHES + [UNIXNU_PATCH]:
            if not (mu_patches / patch).exists():
                errors.append(f'Resources/MuPatches/{patch} is missing from the UEFI checkout '
                              '(five come from Mu-Silicium upstream, Gic-Spurious from the port)')
        binaries = args.uefi / 'Binaries' / 'enuma'
        if not binaries.exists():
            todos.append(f'{binaries} does not exist: determine whether enuma needs Mu-Silicium vendor binaries')
        notes.append('MemoryMapLib descriptor and DTB splash checks are NOT implemented '
                     '(port them from xVanTuring/UniXNU-pipa scripts/check-pipa.py)')
    else:
        notes.append('run with --uefi <checkout> for the enumaPkg + six-patch checks, '
                     'and --tree <build-tree> for the archives resolution check')

    # 6. boot image -------------------------------------------------------------
    image = board.get('bootimg', {}).get('image', {})
    if not image:
        errors.append('[bootimg.image] is missing: header_version decides whether stock ABL accepts the image')
    else:
        version = image.get('header_version')
        if version not in (0, 1, 2):
            errors.append(f'[bootimg.image] header_version = {version!r}; expected 0, 1 or 2')
        elif version == 1:
            notes.append('header_version = 1 (umi, hardware-proven on SM8250); '
                         'sheng uses 0 -- fall back to 0 with an explicit [boot_image] block if ABL refuses')
        pagesize = image.get('pagesize')
        if pagesize is not None and (pagesize & (pagesize - 1)) != 0:
            errors.append(f'[bootimg.image] pagesize = {pagesize}; must be a power of two')

    nodes = board.get('bootimg', {}).get('dtb_node', [])
    splash = next((n for n in nodes if 'splash' in n.get('path', '')), None)
    if splash is None:
        todos.append('[[bootimg.dtb_node]] splash_region: stock ABL powers the panel down without it '
                     '(refill from astide kona.dtsi:508-511)')
    else:
        reg = next((p.get('value') for p in splash.get('props', []) if p.get('name') == 'reg'), None)
        if not reg or len(reg) != 4:
            errors.append('[[bootimg.dtb_node]] splash reg must be a four-cell <hi lo size_hi size_lo>')
        else:
            addr, size = int(reg[1], 0), int(reg[3], 0)
            if addr != SPLASH_BASE or size != SPLASH_SIZE:
                notes.append(f'splash region = {addr:#x}+{size:#x}; the SM8250 default in the kernel tree is '
                             f'{SPLASH_BASE:#x}+{SPLASH_SIZE:#x} -- confirm against the stock dtbo')
            if addr < 0x80000000 or addr + size > 0x100000000 or size < 0x100000:
                errors.append(f'splash region {addr:#x}+{size:#x} is outside the plausible DRAM/display window')

    # 7. ADT contract (mirrors make-enuma-adt.py assertions) --------------------
    adt = board.get('payload', {}).get('adt', {})
    if adt.get('psci_method') not in ('smc', 'hvc'):
        errors.append(f"[payload.adt] psci_method = {adt.get('psci_method')!r}; expected 'smc' or 'hvc'")
    elif adt.get('psci_method') != 'smc':
        notes.append('psci_method is not smc; umi (same SoC) uses smc')
    cpu_regs = adt.get('cpu_regs')
    if cpu_regs is not None:
        try:
            regs = [int(v, 0) for v in str(cpu_regs).split(',')]
        except ValueError:
            errors.append(f'[payload.adt] cpu_regs = {cpu_regs!r} is not a comma-separated integer list')
        else:
            if not regs or regs[0] != 0 or len(set(regs)) != len(regs) or len(regs) > 8:
                errors.append(f'[payload.adt] cpu_regs = {cpu_regs!r}: must start at 0, be unique and <= 8 entries')
            elif any(r < 0 or r > 0xFFFF or r & 0xFF for r in regs):
                errors.append(f'[payload.adt] cpu_regs = {cpu_regs!r}: each value must be 0..0xffff and 0x100-aligned')
    owned = adt.get('owned_memory_size')
    if owned is not None:
        value = int(str(owned), 0)
        if not (0x10000000 <= value <= 0x200000000) or value % 0x200000:
            errors.append(f'[payload.adt] owned_memory_size = {owned} outside 256 MiB..8 GiB or not 2 MiB aligned')
    if not adt.get('unique_chip_id'):
        errors.append('[payload.adt] unique_chip_id is unset; the platform seed is per-build and never committed')

    print(f'board.toml: {len(todos)} outstanding measurement(s), {len(errors)} error(s)')
    for line in todos:
        print(f'TODO(enuma): {line}')
    for line in notes:
        print(f'note: {line}')
    for line in errors:
        print(f'error: {line}')
    if errors:
        print('FAILED: fix the errors above before building.')
    elif todos:
        print('pre-dump state: structure is valid, on-device measurements outstanding.')
    else:
        print('all offline checks passed.')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())