#!/usr/bin/env python3
"""Print exactly what the storage-ABI audit wants read on hardware, and build the JSON from a GDB log.

Why this exists: audit-storage-interface.py ends with a live-observation requirement -

    build/darwinboot-storage-native-metadata/gdb-stop.json
      kernel_sha256            == the prelink candidate's sha256
      memory["<meta>:50"]     0x50 bytes of the original IOBlockStorageDevice metaclass
      memory["<service>:50"]  0x50 bytes of the IOService metaclass
      system_registers        TCR_EL1, SCTLR_EL1, TTBR0_EL1, TTBR1_EL1
      bounded_timeout_interrupted = false

- and without the record the audit stops. Two of those keys are addresses that have to be derived from
the candidate itself: IOBlockStorageDevice::gMetaClass lives inside the prelinked IOStorageFamily kext and
is not in the kernel's symbol table. The derivation below is copied verbatim from the audit (it decodes the
ADRP/ADD pair in the metaclass getter at vtable slot 7), so the addresses printed here are the ones the
audit will look for. IOService::gMetaClass is cross-checked against the kernel's own symbol table, which is
the sanity check that the decoding is right.

Usage:
  # 1. what to read (run against the prelink candidate produced by make-prelink-candidate.sh)
  emit-storage-abi-addresses.py --candidate build/xnu-prelink/native-apfs-load-positive.macho

  # 2. turn a GDB session log into the record
  emit-storage-abi-addresses.py --candidate ... --gdb-log gdb.txt --out build/darwinboot-storage-native-metadata/gdb-stop.json

The GDB log must contain, for each address printed in step 1, a line of the form

  <addr>: 0xXX 0xXX ...            (or `x/80xb <addr>` output, 80 bytes)

and a register dump line such as

  tcr_el1 0x22a51a251a   sctlr_el1 0x3454593d   ttbr0_el1 0x...   ttbr1_el1 0x...
"""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path
import subprocess
import sys


def load_kit(tree):
    """The prelink kit parses this file format (a prelinked collection); do not re-implement it."""
    sys.path.insert(0, str(Path(tree) / 'kits/XnuPrelink'))
    import native_prelink
    return native_prelink


def derive(args, np):
    """Find the storage vtable and its metaclass getter, using only the candidate itself.

    The plan written by emit_prelink does not carry the method list (that comes from the compiled
    reference object inside the audit), so the vtable is identified structurally instead: a 16-byte zero
    header followed by 190 non-zero pointers whose first two slots are the abstract complete/deleting
    destructors (brk #0x10 traps) and whose slot 7 is an ADRP/ADD/RET metaclass getter resolving into the
    kext's own __DATA_CONST. Those four facts together are what distinguishes this vtable.
    """
    kernel = Path(args.candidate).read_bytes()
    plan = json.loads(Path(args.plan).read_text())
    image = np.macho(kernel)

    def read(va, length):
        at = np.backed_offset(image['segments'], va, length, len(kernel))
        return kernel[at:at + length]

    extents = [e for e in plan['extents'] if e['module'] == 'com.apple.iokit.IOStorageFamily']
    const = next(e for e in extents if e['segment'] == '__DATA_CONST')
    text = next(e for e in extents if e['segment'] == '__TEXT_EXEC')
    blob = read(const['va'], const['size'])

    brk = struct.pack('<I', 0xd4200020)
    headers = 0        # 16 zero bytes at an 8-aligned offset
    with_pointers = 0  # ... followed by 190 non-zero pointers
    brk_slots = 0      # ... whose slots 0/1 are brk #0x10
    getter_ok = 0      # ... whose slot 7 is an ADRP/ADD/RET getter inside the kext text
    candidates = []
    for at in range(0, len(blob) - 192 * 8 + 1, 8):
        if blob[at:at + 16] != bytes(16):
            continue
        headers += 1
        pointers = struct.unpack_from('<190Q', blob, at + 16)
        if any(pointer == 0 for pointer in pointers):
            continue
        with_pointers += 1
        if read(pointers[0], 4) != brk or read(pointers[1], 4) != brk:
            continue
        brk_slots += 1
        getter = pointers[7]
        if not (text['va'] <= getter < text['va'] + text['size']):
            continue
        a, add, ret = struct.unpack('<3I', read(getter, 12))
        if a & 0x9f00001f != 0x90000000 or add & 0xffc003ff != 0x91000000 or ret != 0xd65f03c0:
            continue
        getter_ok += 1
        immediate = ((a >> 5 & 0x7ffff) << 2) | (a >> 29 & 3)
        if immediate & 0x100000:
            immediate -= 0x200000
        meta = (getter & ~4095) + immediate * 4096 + (add >> 10 & 4095)
        candidates.append((const['va'] + at, getter, meta))
    print(f"__DATA_CONST {const['va']:#x}+{const['size']:#x}: {headers} zero headers, "
          f"{with_pointers} with 190 non-zero pointers, {brk_slots} with brk slots, {getter_ok} with getters")
    for table, getter, meta in candidates:
        where = 'inside' if const['va'] <= meta < const['va'] + const['size'] else 'outside'
        print(f'  candidate vtable {table:#x} getter {getter:#x} meta {meta:#x} ({where} __DATA_CONST)')
    # The metaclass lives in the kext's writable __DATA, not in __DATA_CONST - the diagnostic run's single
    # candidate had meta 0xfffffff006add1d0, outside the segment the vtable sits in, and the audit never
    # asks for it to be inside. The four structural facts already leave exactly one candidate.
    if len(candidates) != 1:
        raise SystemExit(f'cannot uniquely identify the storage vtable (candidates {len(candidates)})')
    table, getter, meta = candidates[0]
    exports = np.exports(kernel, image)
    by_name = {name: va for va, names in exports.items() for name in names}
    service_meta = by_name['__ZN9IOService10gMetaClassE']
    return kernel, meta, service_meta, table, getter


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--candidate', required=True, type=Path,
                        help='prelink candidate (build/xnu-prelink/native-apfs-load-positive.macho)')
    parser.add_argument('--plan', required=True, type=Path,
                        help='the candidate .json plan written next to it by emit_prelink.py')
    parser.add_argument('--tree', type=Path, default=Path('.'),
                        help='assembled tree root (for kits/XnuPrelink)')
    parser.add_argument('--gdb-log', type=Path, help='GDB session log to parse into the record')
    parser.add_argument('--out', type=Path, help='where to write gdb-stop.json')
    args = parser.parse_args()

    np = load_kit(args.tree)
    kernel, meta, service_meta, table, getter = derive(args, np)
    digest = hashlib.sha256(kernel).hexdigest()
    print(f'candidate sha256        : {digest}')
    print(f'storage vtable          : {table:#x}')
    print(f'vocabulary getter slot7 : {getter:#x}')
    print(f'IOBlockStorageDevice meta: {meta:#x}   <- read 0x50 bytes here on the device')
    print(f'IOService meta          : {service_meta:#x}   <- and here')
    print()
    print('GDB, inside the booted prelinked kernel:')
    print(f'  x/80xb {meta:#x}')
    print(f'  x/80xb {service_meta:#x}')
    print('  info registers tcr_el1 sctlr_el1 ttbr0_el1 ttbr1_el1')
    addresses = {meta, service_meta}

    if not args.gdb_log:
        return
    log = args.gdb_log.read_text(errors='replace')
    memory = {}
    for match in re.finditer(r'(0x[0-9a-f]+):\s*((?:0x[0-9a-f]{1,2}\s*)+)', log):
        address = int(match.group(1), 16)
        values = bytes(int(value, 16) for value in match.group(2).split())
        if address in addresses:
            memory[f'{address:x}:50'] = values[:0x50].hex()
    registers = {}
    for name in ('tcr_el1', 'sctlr_el1', 'ttbr0_el1', 'ttbr1_el1'):
        found = re.search(rf'{name}\s+(0x[0-9a-f]+)', log, re.IGNORECASE)
        if found:
            registers[name.upper()] = found.group(1)
    missing = [f'{address:x}:50' for address in addresses if f'{address:x}:50' not in memory]
    if missing:
        raise SystemExit('the GDB log is missing memory for: ' + ', '.join(missing))
    if len(registers) != 4:
        raise SystemExit('the GDB log is missing some of TCR_EL1/SCTLR_EL1/TTBR0_EL1/TTBR1_EL1')
    record = {'kernel_sha256': digest, 'bounded_timeout_interrupted': False,
              'memory': memory, 'system_registers': registers}
    out = args.out or args.gdb_log.with_suffix('.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + '\n')
    print(f'\nwrote {out}')


if __name__ == '__main__':
    main()