#!/usr/bin/env python3
"""Generate the enuma (Xiaomi Pad 5 Pro 5G, SM8250-AC) Apple-format device tree for the first boot.

This is the task-7 skeleton of `make-enuma-adt.py`. It follows the structure of
UniXNU-sheng's `scripts/make-sheng-adt.py` and xVanTuring/UniXNU-pipa's
`scripts/make-pipa-adt.py`, with the same required nodes and properties the
source XNU boot converged on.

Values fall into two classes, and the script keeps them apart on purpose:

  SoC-level (SM8250 / Kona, shared with pipa, from public sources):
    - GICv3 distributor 0x17a00000/0x10000, redistributor 0x17a60000/0x100000,
      stride 0x20000                                   (sm8250.dtsi, Linux mainline;
                                                        the same values pipa uses)
    - CPU timebase 19200000 Hz                         (Qualcomm SM8xxx arch counter)
    - DRAM base 0x80000000                             (sm8250.dtsi /memory)
    - cores numbered in Affinity1: 0x000..0x300 Silver (Cortex-A55),
      0x400..0x700 Gold/Prime (Cortex-A77)              (sm8250.dtsi)

  Board-level (enuma, NOT publicly determinable -> must be passed in):
    --dram-size, --armio-base, --root-compatible, --product-name,
    --artwork-device-subtype, --button-names, --splash... (see --help)

The board-level options have NO defaults: the script refuses to invent hardware
numbers. Run it with `--help` for the list and with `--print-todo` to see the
on-device command that produces each value.

TODO(enuma): once the measurements exist, hard-code the chosen values here with
their source comment (the way pipa pins its SM8250 constants) and drop the
corresponding required option, so a build is reproducible from the repository.
"""
from pathlib import Path
import argparse
import os
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# SoC-level constants: SM8250 (Kona). Source: sm8250.dtsi (Linux mainline),
# cross-checked against UniXNU-pipa's make-pipa-adt.py + its README.
# ---------------------------------------------------------------------------
DRAM_BASE = 0x80000000
ARMIO_SIZE = 0x08000000          # APSS peripheral window that contains the GIC
GIC_DIST = 0x17a00000
GIC_DIST_SIZE = 0x10000
GIC_REDIST = 0x17a60000
GIC_REDIST_SIZE = 0x100000
GIC_REDIST_STRIDE = 0x20000      # GICv3 without virtual LPIs: two 64 KiB frames per core
TIMEBASE = 19200000

# The on-device measurement that produces each required board-level value.
TODO_COMMANDS = {
    'dram-size': "adb shell su -c 'cat /proc/iomem'; adb shell cat /proc/meminfo",
    'armio-base': "read the APSS/arm-io window from the stock DT (dtc -I dtb -O dts on the dtb partition)",
    'root-compatible': "adb shell su -c 'cat /sys/firmware/devicetree/base/compatible'",
    'product-name': "adb shell wm size; adb shell dumpsys display | head -40 (panel), then the stock DT model",
    'artwork-device-subtype': "panel geometry: adb shell wm size (the iPad tree index that matches the panel)",
    'button-names': "adb shell getevent -pl (side buttons); stock DT /buttons",
    'owned-memory-size': "adb shell su -c 'cat /proc/iomem' (free run, 256 MiB..8 GiB, 2 MiB aligned)",
    'chip-id-json': "software identity seed; 207 is the value sheng and pipa both use (not an Apple silicon ID)",
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
    """Apple multi-string from a shell-friendly argument: 'a;b' -> b'a\\0b\\0'.

    bash cannot carry NUL bytes in an argument, so the ADT tool takes the Apple
    multi-string form with ';' as the separator. The build driver passes the
    value from board.toml, so this matters mainly for manual runs.
    """
    parts = [part for part in value.split(';') if part]
    return b''.join(part.encode() + b'\0' for part in parts) or b'\0'


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/enuma/boot/kernel.macho.adt')
    parser.add_argument('--print-todo', action='store_true',
                        help='print the on-device command that produces every required board value, then exit')

    # ---------- board-level values: required, no defaults ----------
    parser.add_argument('--dram-size', type=lambda value: int(value, 0), required=False,
                        help='[TODO(enuma)] observed DRAM size, e.g. from /proc/iomem')
    parser.add_argument('--armio-base', type=lambda value: int(value, 0), required=False,
                        help='[TODO(enuma)] APSS/arm-io window base (SM8250 reference 0x17000000, verify on the device)')
    parser.add_argument('--root-compatible', default=None,
                        help='[TODO(enuma)] stock DT root compatible; separate multiple strings with ";" (e.g. xiaomi,enuma;qcom,sm8250)')
    parser.add_argument('--product-name', default=None,
                        help='[TODO(enuma)] /product product-name')
    parser.add_argument('--artwork-device-subtype', type=int, default=None,
                        help='[TODO(enuma)] panel geometry index (sheng uses 2732 for its 12.4" panel)')
    parser.add_argument('--button-names', default=None,
                        help='[TODO(enuma)] e.g. "hold;voldown;volup" from the stock DT (";" separates strings)')

    # ---------- values that are shared with sheng/pipa or are software seeds ----------
    parser.add_argument('--owned-memory-size', type=lambda value: int(value, 0), default=0x100000000,
                        help='DarwinBoot contiguous window request, 256 MiB..8 GiB in 2 MiB steps (start: pipa 4 GiB)')
    parser.add_argument('--root-match-volume-uuid', default=None,
                        help='the OS volume UUID of the target IPSW (same image as sheng/pipa)')
    parser.add_argument('--unique-chip-id', type=lambda value: int(value, 16), default=None,
                        help='platform seed (8 bytes); software identity seed, generate locally')
    parser.add_argument('--chip-id', type=lambda value: int(value, 16), default=None,
                        help='chip-id (4 bytes); the value sheng/pipa use is 207')
    parser.add_argument('--cpu-regs', default='0',
                        help='comma-separated Affinity1 values; first boot declares the boot CPU only')
    parser.add_argument('--psci-method', choices=['smc', 'hvc'], default='smc',
                        help='SM8250 board DTs declare smc')
    parser.add_argument('--ipad-identity', action='store_true',
                        help='write the iPad7,1-class identity properties (MobileGestalt)')
    parser.add_argument('--packed-data-ramdisk', action='store_true')
    parser.add_argument('--data-ramdisk', action='store_true')
    parser.add_argument('--trust-cache', action='store_true')
    args = parser.parse_args()

    if args.print_todo:
        print('board-level values and the on-device command that produces them:')
        for key, command in TODO_COMMANDS.items():
            print(f'  {key:24s} {command}')
        return 0

    # Refuse to invent: every board-level value must be supplied.
    missing = [name for name in ('dram_size', 'armio_base', 'root_compatible', 'product_name',
                                 'artwork_device_subtype', 'button_names')
               if getattr(args, name) in (None, '')]
    if missing:
        print('refusing to generate an ADT without measured board values:', file=sys.stderr)
        for name in missing:
            key = name.replace('_', '-')
            print(f'  --{key:24s} {TODO_COMMANDS.get(key, "(see --help)")}', file=sys.stderr)
        print('\nrun with --print-todo for the full list; see ../porting-plan.md section 4.', file=sys.stderr)
        return 2

    if (args.unique_chip_id is None) != (args.chip_id is None):
        parser.error('--unique-chip-id and --chip-id must be supplied together')
    if args.unique_chip_id is not None and not (0 < args.unique_chip_id < 1 << 64 and 0 < args.chip_id < 1 << 32):
        parser.error('--unique-chip-id must fit 8 bytes and --chip-id 4 bytes, both nonzero')
    if not (0x10000000 <= args.owned_memory_size <= 0x200000000) or args.owned_memory_size % 0x200000:
        parser.error('--owned-memory-size must be 256 MiB to 8 GiB, aligned to 2 MiB')

    cpu_regs = [int(value, 0) for value in args.cpu_regs.split(',')]
    if (not cpu_regs or cpu_regs[0] != 0 or len(set(cpu_regs)) != len(cpu_regs) or len(cpu_regs) > 8
            or any(reg < 0 or reg > 0xffff or reg & 0xff for reg in cpu_regs)):
        parser.error('--cpu-regs: up to eight distinct Affinity1-numbered cores, boot CPU 0 first')

    cpus = [node('cpu%d' % index,
                 [('state', b'running\0' if index == 0 else b'waiting\0'),
                  ('reg', struct.pack('<I', reg)),
                  ('device_type', b'cpu\0'),
                  # TODO(enuma): 按实际核心类型写 compatible（SM8250: A55 / A77），
                  #              来源 sm8250.dtsi 的 cpu@ 节点与 stock DT。
                  ('compatible', b'arm,cortex-a77\0' if reg >= 0x400 else b'arm,cortex-a55\0'),
                  ('timebase-frequency', struct.pack('<I', TIMEBASE))])
            for index, reg in enumerate(cpu_regs)]

    tree = node('device-tree', [
        ('model', b'iPad7,1\0' if args.ipad_identity else b'enuma-first-boot\0'),
        # TODO(enuma): target-type 要与所选 iPad 身份一致（sheng 用 J120）。
        *([('target-type', b'J120\0')] if args.ipad_identity else []),
        ('compatible', apple_string_list(args.root_compatible)),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
    ], [
        node('chosen', [
            ('random-seed', os.urandom(64)),
            *([('unique-chip-id', struct.pack('<Q', args.unique_chip_id)),
               ('chip-id', struct.pack('<I', args.chip_id))] if args.unique_chip_id is not None else []),
            ('osenvironment', b'main\0'),
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
            ('crypto-hash-method', b'sha2-384\0'),
            *([('root-matching', ('<dict><key>IOProviderClass</key><string>IOMedia</string>'
                                  '<key>IOPropertyMatch</key><dict><key>UUID</key><string>' +
                                  args.root_match_volume_uuid.upper() +
                                  '</string></dict></dict>\0').encode())]
              if args.root_match_volume_uuid else []),
            ('amfi-only-platform-code', struct.pack('<I', 1)),
            *empty_nvram_proxy()],
            [node('memory-map', ([("PackedRAMDisk" if args.packed_data_ramdisk else "RAMDisk", bytes(16))]
                                 if (args.data_ramdisk or args.packed_data_ramdisk) else []) +
                                ([('TrustCache', bytes(16))] if args.trust_cache else [])),
             node('manifest-properties')]),
        node('cpus', children=cpus),
        *([node('psci', [('compatible', b'arm,psci-1.0\0'),
                         ('method', args.psci_method.encode() + b'\0')])] if len(cpus) > 1 else []),
        node('defaults', [('sleep-disabled', struct.pack('<I', 1))]),
        node('options', []),
        # TODO(enuma): 侧键与 Home 键语义按实机 getevent 结果定；sheng 无 Home 键时 home-button-type = 2。
        *([node('buttons', [('compatible', b'buttons\0'),
                            ('device_type', b'buttons\0'),
                            ('button-names', apple_string_list(args.button_names)),
                            ('home-button-type', struct.pack('<I', 2))])] if args.ipad_identity else []),
        *([node('product', [('product-name', args.product_name.encode() + b'\0'),
                            # TODO(enuma): model 用 stock DT 的 xiaomi,enuma
                            ('model', b'xiaomi,enuma\0'),
                            *([('product-description', args.product_name.encode() + b'\0'),
                               ('artwork-device-idiom', b'pad\0'),
                               ('artwork-device-subtype', struct.pack('<I', args.artwork_device_subtype)),
                               ('artwork-scale-factor', struct.pack('<I', 2)),
                               ('artwork-display-gamut', b'P3\0'),
                               ('compatible-device-fallback', b'iPad6,7\0'),
                               ('thin-bezel', struct.pack('<I', 1))] if args.ipad_identity else [])])]
          if args.product_name else []),
        node('arm-io', [('device_type', b'arm-io\0'),
                        ('#address-cells', struct.pack('<I', 2)),
                        ('#size-cells', struct.pack('<I', 2)),
                        ('ranges', struct.pack('<QQQ', 0, args.armio_base, ARMIO_SIZE))],
             [node('intc', [('compatible', b'arm,gic-v3\0'),
                            ('reg', struct.pack('<4Q',
                                                GIC_DIST - args.armio_base, GIC_DIST_SIZE,
                                                GIC_REDIST - args.armio_base, GIC_REDIST_SIZE)),
                            ('redistributor-stride', struct.pack('<Q', GIC_REDIST_STRIDE))]),
              # TODO(enuma): UFS 实例地址与 compatible 以 sm8250.dtsi / stock DT 为准（SM8250 = UFS 3.1）。
              node('ufshc', [('compatible', b'qcom,sm8250-ufshc\0')])]),
    ])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(tree)
    print(f'enuma ADT: {len(tree)} bytes, dram {args.dram_size:#x}, timebase {TIMEBASE}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())