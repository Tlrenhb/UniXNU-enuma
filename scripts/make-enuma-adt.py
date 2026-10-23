#!/usr/bin/env python3
"""Generate the enuma hardware Apple-format DT for the first boot.

Structure is ported from xVanTuring/UniXNU-pipa `scripts/make-pipa-adt.py` (the
same layout UniXNU-sheng's make-sheng-adt.py converged on): a flat Apple ADT with
`/chosen`, `/cpus`, `/psci`, `/defaults`, `/options`, `/buttons`, `/product`,
`/arm-io` and `/manifest-properties`.

WHY THIS FILE CHANGED (task-19): the cloud payload run failed here. The build
driver (UniXNU `scripts/unixnu/payload.py`) passes exactly
`--dram-size --owned-memory-size --root-match-volume-uuid --chip-id --cpu-regs
--psci-method --unique-chip-id --trust-cache --ipad-identity`
and never passed the five extra options the task-7 skeleton demanded
(`--armio-base/--root-compatible/--product-name/--artwork-device-subtype/
--button-names`). Those five are now **built-in constants** (still overridable on
the command line), exactly like pipa does. The only value this script still
refuses to invent is `--dram-size`, which genuinely requires the tablet.

Value classes
-------------
(A) SoC constants -- SM8250 (Kona), all verified in this project's own pinned
    kernel tree `AstideLabs/android_kernel_xiaomi_sm8250@4ae1f1e` unless noted:
      GICv3 distributor 0x17a00000/0x10000, redistributor 0x17a60000/0x100000
        (astide `arch/arm64/boot/dts/vendor/qcom/kona.dtsi:643-650`,
         `interrupt-controller@17a00000` with `reg = <0x17a00000 0x10000>, <0x17a60000 0x100000>`)
      redistributor stride 0x20000: GICv3 without virtual LPIs, two 64 KiB frames
        per core (pipa make-pipa-adt.py:34)
      CPU timebase 19200000 Hz (astide kona.dtsi:704, `arch_timer clock-frequency`)
      DRAM base 0x80000000 (astide kona.dtsi:374 `hyp_region@80000000`; pipa :27)
      cores numbered in Affinity1 0x000..0x700, cluster split at 0x400
        (astide kona.dtsi:59-244 cpu@0/100/.../700 and the cpu-map at :267-302:
         cluster0 = CPU0..CPU3, cluster1 = CPU4..CPU6, cluster2 = CPU7)
      arm-io APSS window base 0x17000000 / size 0x08000000 (pipa :28-29; the GIC
        at 0x17a00000 sits inside it. UFS at 0x01d84000 is BELOW the window, which
        is why the `ufshc` node carries no `reg` -- same as pipa :191-193.)
    Cross-check on the device (does not block a build): `dtc -I dtb -O dts` on the
    stock dtbo and grep the `interrupt-controller`/`timer` nodes.

(B) Donor-identity constants -- these describe the UNCHANGED iPad7,1 image whose
    userland we boot; they are not measurements of this tablet and are the same
    values sheng/pipa write (`make-pipa-adt.py:121-122,169-181`):
      model iPad7,1, target-type J120, product-name "iPad Pro (12.9-inch) (2nd generation)",
      artwork-device-subtype 2732, artwork-scale-factor 2, artwork-display-gamut P3,
      compatible-device-fallback iPad6,7, thin-bezel 1
      buttons: button-names hold/voldown/volup, home-button-type 2 (no `menu`:
      the tablet has no Home button). Reviewable with `getevent -pl`, not required.

(C) True on-device measurement -- exactly one value:
      `--dram-size`   -> refuse with the measurement command when absent or 0.

Override knobs: `--armio-base/--root-compatible/--product-name/
--artwork-device-subtype/--button-names` still exist for manual runs and future
board variants; their defaults are the constants above, so a driver invocation
never has to pass them.
"""
from pathlib import Path
import argparse
import os
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# (A) SoC constants -- SM8250 / Kona. See the module docstring for file:line.
# ---------------------------------------------------------------------------
DRAM_BASE = 0x80000000           # astide kona.dtsi:374 (first reserved region @0x80000000)
ARMIO_BASE = 0x17000000          # APSS peripheral block containing the GIC (pipa:28)
ARMIO_SIZE = 0x08000000
GIC_DIST = 0x17A00000            # astide kona.dtsi:643-650
GIC_DIST_SIZE = 0x10000
GIC_REDIST = 0x17A60000
GIC_REDIST_SIZE = 0x100000
GIC_REDIST_STRIDE = 0x20000      # GICv3 without virtual LPIs: two 64 KiB frames per core
TIMEBASE = 19200000              # astide kona.dtsi:704 (arch_timer clock-frequency)
LITTLE_CORES = 4                 # astide kona.dtsi:267-302 cpu-map cluster0 = CPU0..CPU3
MAX_AFFINITY1 = 0x700            # astide kona.dtsi:241 CPU7 = Affinity1 0x700

# ---------------------------------------------------------------------------
# (B) Donor-identity constants -- the unchanged iPad7,1 tree (pipa:121-122,169-181).
# ---------------------------------------------------------------------------
ROOT_COMPATIBLE = b'xiaomi,enuma\0qcom,sm8250\0'   # board model + SoC; verify against the
                                                   # stock DT root `compatible` when we have it
ENUMA_MODEL = b'xiaomi,enuma\0'
DONOR_MODEL = b'iPad7,1\0'
DONOR_TARGET_TYPE = b'J120\0'
DONOR_PRODUCT_NAME = b'iPad Pro (12.9-inch) (2nd generation)\0'
DONOR_ARTWORK_SUBTYPE = 2732     # iPad7,1 artwork index (pipa:177)
DONOR_ARTWORK_SCALE = 2
DONOR_ARTWORK_GAMUT = b'P3\0'
DONOR_COMPATIBLE_FALLBACK = b'iPad6,7\0'
BUTTON_NAMES = b'hold\0voldown\0volup\0'   # no `menu`: no Home button on this tablet
HOME_BUTTON_TYPE = 2             # non-mechanical; the value sheng/pipa pass through
# Pre-rendered forms for --help/--print-todo (kept out of f-strings: <3.12 forbids
# backslashes inside f-string expressions).
ROOT_COMPATIBLE_TEXT = ROOT_COMPATIBLE.replace(b'\0', b';').decode().rstrip(';')
DONOR_PRODUCT_NAME_TEXT = DONOR_PRODUCT_NAME.replace(b'\0', b'').decode()
BUTTON_NAMES_TEXT = BUTTON_NAMES.replace(b'\0', b';').decode().rstrip(';')

# (C) The on-device measurements that are still required, with the command that
# produces each one. Keep this list honest: it must not grow to cover values we
# can derive from the SoC or the donor image.
TODO_COMMANDS = {
    'dram-size': "adb shell su -c 'cat /proc/iomem'  (sum the DRAM banks; enuma ships 6/8 GiB)",
}
# Printed by --print-todo for context; these are built in and need no measurement.
BUILTIN_NOTES = {
    'armio-base': f'{ARMIO_BASE:#x} (pipa:28 + astide kona.dtsi:643-650 GIC inside the window)',
    'root-compatible': ROOT_COMPATIBLE.replace(b'\0', b';').decode().rstrip(';'),
    'product-name': DONOR_PRODUCT_NAME.replace(b'\0', b'').decode(),
    'artwork-device-subtype': f'{DONOR_ARTWORK_SUBTYPE} (iPad7,1 artwork index)',
    'button-names': BUTTON_NAMES.replace(b'\0', b';').decode().rstrip(';'),
    'owned-memory-size': 'DarwinBoot window request (driver passes it; default 4 GiB)',
}


def node(name, properties=(), children=()):
    """Apple-flat node encoding: u32 prop count, u32 child count, 32-byte names."""
    properties = [('name', name.encode() + b'\0'), *properties]
    result = struct.pack('<II', len(properties), len(children))
    for key, data in properties:
        result += struct.pack('<32sI', key.encode(), len(data))
        result += data + bytes(-len(data) % 4)
    return result + b''.join(children)


def empty_nvram_proxy():
    """A fresh volatile CHRP bank for upstream IODTNVRAM, with no controller."""
    def header(name, size):
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, size // 16, name))
        checksum = sum(raw)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        raw[1] = checksum
        return raw

    image = bytearray(8192)
    image[:16] = header(b'nvram', 32)
    image[32:48] = header(b'common', 16 + 2048)
    free_at = 32 + 16 + 2048
    image[free_at:free_at + 16] = header(b'free', len(image) - free_at)
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return [('nvram-bank-size', struct.pack('<I', len(image))),
            ('nvram-bank-count', struct.pack('<I', 1)),
            ('nvram-current-bank', struct.pack('<I', 0)),
            ('nvram-proxy-data', bytes(image))]


def apple_string_list(value):
    """Apple multi-string from a shell-friendly argument: 'a;b' -> b'a\\0b\\0'."""
    parts = [part for part in value.split(';') if part]
    return b''.join(part.encode() + b'\0' for part in parts) or b'\0'


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/enuma/boot/kernel.macho.adt')
    parser.add_argument('--print-todo', action='store_true',
                        help='print what is still measured on the device and what is built in, then exit')

    # ---------- (C) the one value that requires the tablet ----------
    parser.add_argument('--dram-size', type=lambda value: int(value, 0), required=False, default=None,
                        help='REQUIRED: observed DRAM size; the script refuses 0/missing (see --print-todo)')

    # ---------- values the build driver passes ----------
    parser.add_argument('--owned-memory-size', type=lambda value: int(value, 0), default=0x100000000,
                        help='DarwinBoot contiguous window request, 256 MiB..8 GiB in 2 MiB steps')
    parser.add_argument('--root-match-volume-uuid', default=None,
                        help='/chosen/root-matching: select the root by the UUID of an IOMedia')
    parser.add_argument('--unique-chip-id', type=lambda value: int(value, 16), default=None,
                        help='platform seed (8 bytes); software identity seed, generate locally')
    parser.add_argument('--chip-id', type=lambda value: int(value, 16), default=None,
                        help='chip-id (4 bytes); enuma = 0x164, derived from qcom,msm-id (astide kona-v2.1.dtsi:7)')
    parser.add_argument('--cpu-regs', default='0',
                        help='comma-separated Affinity1 values 0x000..0x700; first is the boot CPU. '
                             'More than one adds /psci')
    parser.add_argument('--psci-method', choices=['smc', 'hvc'], default='smc',
                        help='SM8250 board DTs declare smc')
    parser.add_argument('--ipad-identity', action='store_true',
                        help='write the iPad7,1-class identity properties (MobileGestalt reads /product and /buttons)')
    parser.add_argument('--packed-data-ramdisk', action='store_true',
                        help='hand off validated DBRDISK1 bytes; sets /chosen/ephemeral-storage')
    parser.add_argument('--data-ramdisk', action='store_true',
                        help='/chosen/memory-map RAMDisk placeholder')
    parser.add_argument('--trust-cache', action='store_true',
                        help='/chosen/memory-map TrustCache placeholder')

    # ---------- (A)+(B) built-in constants, kept overridable for manual runs ----------
    parser.add_argument('--armio-base', type=lambda value: int(value, 0), default=ARMIO_BASE,
                        help=f'override the APSS window base (default {ARMIO_BASE:#x}, pipa:28)')
    parser.add_argument('--root-compatible', default=None,
                        help='override the root compatible multi-string (";" separated); '
                             f'default {ROOT_COMPATIBLE_TEXT}')
    parser.add_argument('--product-name', default=None,
                        help='override /product product-name; '
                             f'default "{DONOR_PRODUCT_NAME_TEXT}"')
    parser.add_argument('--artwork-device-subtype', type=int, default=DONOR_ARTWORK_SUBTYPE,
                        help=f'override the artwork index (default {DONOR_ARTWORK_SUBTYPE} = iPad7,1)')
    parser.add_argument('--button-names', default=None,
                        help='override /buttons button-names (";" separated); '
                             f'default {BUTTON_NAMES_TEXT}')
    args = parser.parse_args()

    if args.print_todo:
        print('still requires the tablet:')
        for key, command in TODO_COMMANDS.items():
            print(f'  --{key:24s} {command}')
        print('built in (no measurement needed; overridable):')
        for key, note in BUILTIN_NOTES.items():
            print(f'  --{key:24s} {note}')
        return 0

    # Refuse only what genuinely needs the device. 0x0 is what board.toml carries
    # today; a "0-sized DRAM" tree would boot nowhere, so fail loudly instead.
    if not args.dram_size or args.dram_size <= 0:
        print('refusing to generate an ADT without the measured DRAM size:', file=sys.stderr)
        print(f'  --dram-size              {TODO_COMMANDS["dram-size"]}', file=sys.stderr)
        print('  (board.toml [payload] dram_size is 0x0 until this is measured; do not invent it)',
              file=sys.stderr)
        return 2
    if args.dram_size > 0x200000000:
        print(f'warning: dram-size {args.dram_size:#x} exceeds 8 GiB; enuma ships 6/8 GiB variants',
              file=sys.stderr)

    if (args.unique_chip_id is None) != (args.chip_id is None):
        parser.error('--unique-chip-id and --chip-id must be supplied together')
    if args.unique_chip_id is not None and not (0 < args.unique_chip_id < 1 << 64 and 0 < args.chip_id < 1 << 32):
        parser.error('--unique-chip-id must fit 8 bytes and --chip-id 4 bytes, both nonzero')
    if not (0x10000000 <= args.owned_memory_size <= 0x200000000) or args.owned_memory_size % 0x200000:
        parser.error('--owned-memory-size must be 256 MiB to 8 GiB, aligned to 2 MiB')

    cpu_regs = [int(value, 0) for value in args.cpu_regs.split(',')]
    if (not cpu_regs or cpu_regs[0] != 0 or len(set(cpu_regs)) != len(cpu_regs) or len(cpu_regs) > 8
            or any(reg < 0 or reg > MAX_AFFINITY1 or reg & 0xff for reg in cpu_regs)):
        parser.error(f'--cpu-regs: up to eight distinct Affinity1-numbered cores 0x000..{MAX_AFFINITY1:#x}, '
                     'boot CPU 0 first')

    root_compatible = apple_string_list(args.root_compatible) if args.root_compatible else ROOT_COMPATIBLE
    product_name = (args.product_name.encode() + b'\0') if args.product_name else (
        DONOR_PRODUCT_NAME if args.ipad_identity else b'Xiaomi Pad 5 Pro 5G\0')
    button_names = apple_string_list(args.button_names) if args.button_names else BUTTON_NAMES

    cpus = [node('cpu%d' % index,
                 [('state', b'running\0' if index == 0 else b'waiting\0'),
                  ('reg', struct.pack('<I', reg)),
                  ('device_type', b'cpu\0'),
                  # Cluster split per astide kona.dtsi:267-302 (cluster0 = 0x000..0x300).
                  # pipa writes the same arm,cortex-a55/a77 strings (make-pipa-adt.py:115);
                  # the stock DT itself says "qcom,kryo" (kona.dtsi:243).
                  ('compatible', b'arm,cortex-a55\0' if reg >> 8 < LITTLE_CORES else b'arm,cortex-a77\0'),
                  ('timebase-frequency', struct.pack('<I', TIMEBASE))])
            for index, reg in enumerate(cpu_regs)]

    tree = node('device-tree', [
        # hw.product/hw.machine/hw.target come from the root model/target-type (IODTPlatformExpert);
        # libMobileGestalt prefix-matches hw.product against iPhone/iPod/iPad, so the donor identity
        # needs both here (pipa:119-122).
        ('model', DONOR_MODEL if args.ipad_identity else b'enuma-first-boot\0'),
        *([('target-type', DONOR_TARGET_TYPE)] if args.ipad_identity else []),
        # Root compatible strings of the board DT.
        ('compatible', root_compatible),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
    ], [
        node('chosen', [
            ('random-seed', os.urandom(64)),
            # Without these XNU never publishes IOPlatformUUID and gethostuuid() blocks forever
            # for callers (configd) that pass a zero timeout (pipa:129-132).
            *([('unique-chip-id', struct.pack('<Q', args.unique_chip_id)),
               ('chip-id', struct.pack('<I', args.chip_id))] if args.unique_chip_id is not None else []),
            ('osenvironment', b'main\0'),
            # Security status as on a dev-fused, undemoted board (tuple 0,0,1,1); same values as
            # sheng/pipa (pipa:136-140).
            *([('certificate-production-status', struct.pack('<I', 0)),
               ('effective-production-status-ap', struct.pack('<I', 0)),
               ('certificate-security-mode', struct.pack('<I', 1)),
               ('effective-security-mode-sep', struct.pack('<I', 1))] if args.ipad_identity else []),
            *([('ephemeral-storage', struct.pack('<I', 1))]
              if (args.data_ramdisk or args.packed_data_ramdisk) else []),
            *([('darwinboot-memory-size', struct.pack('<Q', args.owned_memory_size))]
              if args.owned_memory_size else []),
            ('dram-base', struct.pack('<Q', DRAM_BASE)),
            ('dram-size', struct.pack('<Q', args.dram_size)),
            # Image4 hash algorithm: selects the algorithm only (pipa:147-149).
            ('crypto-hash-method', b'sha2-384\0'),
            *([('root-matching', ('<dict><key>IOProviderClass</key><string>IOMedia</string>'
                                  '<key>IOPropertyMatch</key><dict><key>UUID</key><string>' +
                                  args.root_match_volume_uuid.upper() +
                                  '</string></dict></dict>\0').encode())]
              if args.root_match_volume_uuid else []),
            # Strict policy for the original AMFI: platform code only; keeps AMFI enforcing.
            ('amfi-only-platform-code', struct.pack('<I', 1)),
            *empty_nvram_proxy()],
            [node('memory-map', ([("PackedRAMDisk" if args.packed_data_ramdisk else "RAMDisk", bytes(16))]
                                 if (args.data_ramdisk or args.packed_data_ramdisk) else []) +
                                ([('TrustCache', bytes(16))] if args.trust_cache else [])),
             # Read by the original AppleImage4 at start-up; present and empty here.
             node('manifest-properties')]),
        node('cpus', children=cpus),
        *([node('psci', [('compatible', b'arm,psci-1.0\0'),
                         ('method', args.psci_method.encode() + b'\0')])] if len(cpus) > 1 else []),
        # Suspend/resume is not implemented; use the original root-domain policy input rather than
        # letting the CPU park forever (pipa:164-166).
        node('defaults', [('sleep-disabled', struct.pack('<I', 1))]),
        node('options', []),
        # The original AppleImage4 panics without a product node (pipa:168-172).
        *([node('buttons', [('compatible', b'buttons\0'),
                            ('device_type', b'buttons\0'),
                            ('button-names', button_names),
                            ('home-button-type', struct.pack('<I', HOME_BUTTON_TYPE))])]
          if args.ipad_identity else []),
        node('product', [('product-name', product_name),
                         ('model', ENUMA_MODEL),
                         *([('product-description', product_name),
                            ('artwork-device-idiom', b'pad\0'),
                            ('artwork-device-subtype', struct.pack('<I', args.artwork_device_subtype)),
                            ('artwork-scale-factor', struct.pack('<I', DONOR_ARTWORK_SCALE)),
                            ('artwork-display-gamut', DONOR_ARTWORK_GAMUT),
                            ('compatible-device-fallback', DONOR_COMPATIBLE_FALLBACK),
                            ('thin-bezel', struct.pack('<I', 1))] if args.ipad_identity else [])]),
        node('arm-io', [('device_type', b'arm-io\0'),
                        ('#address-cells', struct.pack('<I', 2)),
                        ('#size-cells', struct.pack('<I', 2)),
                        ('ranges', struct.pack('<QQQ', 0, args.armio_base, ARMIO_SIZE))],
             [node('intc', [('compatible', b'arm,gic-v3\0'),
                            ('reg', struct.pack('<4Q',
                                                GIC_DIST - args.armio_base, GIC_DIST_SIZE,
                                                GIC_REDIST - args.armio_base, GIC_REDIST_SIZE)),
                            ('redistributor-stride', struct.pack('<Q', GIC_REDIST_STRIDE))]),
              # UFS at 0x01d84000 lies BELOW the arm-io window, so the node carries no reg; it
              # exists for class matching only (pipa:191-193). The SM8250 controller facts live in
              # astide kona.dtsi:3069-3079 (reg/IRQ SPI 265/2 lanes/19.2 MHz ref).
              node('ufshc', [('compatible', b'qcom,sm8250-ufshc\0')])]),
    ])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(tree)
    print(f'enuma ADT: {len(tree)} bytes, dram {args.dram_size:#x}, cpus {len(cpus)}, timebase {TIMEBASE}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())