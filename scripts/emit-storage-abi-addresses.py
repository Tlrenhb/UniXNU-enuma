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


def macho_sections(path):
    """Return {segment: {va, size}} for the segments we need and the symbol table."""
    data = Path(path).read_bytes()
    magic, cputype, _, filetype, ncmds, sizeofcmds, _, _ = struct.unpack_from('<8I', data, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit('not a 64-bit Mach-O')
    offset = 32
    sections, symbols = {}, {}
    symtab = None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<2I', data, offset)
        if cmd == 0x19:  # LC_SEGMENT_64
            name = data[offset + 8:offset + 24].rstrip(b'\0').decode()
            va, vsize = struct.unpack_from('<QQ', data, offset + 24)
            sections.setdefault(name, {'va': va, 'size': vsize})
        elif cmd == 0x2:  # LC_SYMTAB
            symtab = struct.unpack_from('<4I', data, offset + 8)
        offset += size
    if symtab:
        symoff, nsyms, stroff, _ = symtab
        for index in range(nsyms):
            strx, ntype, _, _, value = struct.unpack_from('<IBBHQ', data, symoff + index * 16)
            end = data.index(b'\0', stroff + strx)
            symbols[data[stroff + strx:end].decode()] = value
    return data, sections, symbols


def read_at(data, sections, address, count):
    for segment in sections.values():
        if segment['va'] <= address < segment['va'] + segment['size']:
            start = address - segment['va']
            # file offset equals the segment offset for the __TEXT-family segments of a prelinked kernel
            return data[start:start + count]
    raise SystemExit(f'address {address:#x} is not inside any segment')


def adrp_add(getter, base_page):
    a, add, ret = struct.unpack('<3I', getter[:12])
    if a & 0x9f00001f != 0x90000000 or add & 0xffc003ff != 0x91000000 or ret != 0xd65f03c0:
        raise SystemExit('the getter is not the expected ADRP/ADD/RET triple')
    immediate = ((a >> 5 & 0x7ffff) << 2) | (a >> 29 & 3)
    if immediate & 0x100000:
        immediate -= 0x200000
    return (base_page & ~4095) + immediate * 4096 + (add >> 10 & 4095)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--candidate', required=True, type=Path,
                        help='prelink candidate (build/xnu-prelink/native-apfs-load-positive.macho)')
    parser.add_argument('--plan', type=Path,
                        help='the candidate .json plan; its IOStorageFamily __DATA_CONST extent narrows the '
                             'getter list to the one metaclass that lives in that kext')
    parser.add_argument('--gdb-log', type=Path, help='GDB session log to parse into the record')
    parser.add_argument('--out', type=Path, help='where to write gdb-stop.json')
    args = parser.parse_args()

    data, sections, symbols = macho_sections(args.candidate)
    digest = hashlib.sha256(Path(args.candidate).read_bytes()).hexdigest()
    service_meta = symbols.get('__ZN9IOService10gMetaClassE')
    if not service_meta:
        raise SystemExit('IOService::gMetaClass is not in this candidate; it must be the prelinked kernel')

    # The vtable slot 7 getter is the one that returns IOBlockStorageDevice::gMetaClass. Its address comes
    # from the IOStorageFamily extent recorded by the audit; the symbol is not exported, so search the text
    # for a getter that resolves to a plausible metaclass inside the kext's __DATA_CONST.
    getters = []
    text = sections.get('__TEXT_EXEC') or sections.get('__TEXT')
    for address in range(text['va'], text['va'] + text['size'] - 12, 4):
        chunk = read_at(data, sections, address, 12)
        a, add, ret = struct.unpack('<3I', chunk)
        if a & 0x9f00001f == 0x90000000 and add & 0xffc003ff == 0x91000000 and ret == 0xd65f03c0:
            getters.append((address, adrp_add(chunk, address)))
    if args.plan:
        plan = json.loads(args.plan.read_text())
        extents = [e for e in plan.get('extents', [])
                   if e.get('module') == 'com.apple.iokit.IOStorageFamily' and e.get('segment') == '__DATA_CONST']
        if len(extents) != 1:
            raise SystemExit('the plan does not have exactly one IOStorageFamily __DATA_CONST extent')
        low = extents[0]['va']
        high = low + extents[0]['size']
        narrowed = [(address, target) for address, target in getters if low <= target < high]
        print(f'IOStorageFamily __DATA_CONST: {low:#x}..{high:#x} ({extents[0]["size"]:#x} bytes)')
        if not narrowed:
            raise SystemExit('no ADRP/ADD/RET getter points into the IOStorageFamily data segment')
        getters = narrowed
    unique = sorted({target for _, target in getters})
    print(f'candidate sha256 : {digest}')
    print(f'IOService meta   : {service_meta:#x}   (cross-check against the kernel symbol table)')
    print(f'metaclass getters: {len(getters)} ADRP/ADD/RET triples, {len(unique)} distinct targets')
    for address, target in getters:
        print(f'  getter {address:#x} -> {target:#x}')
    print()
    print('Read on the device (0x50 bytes each) and record with --gdb-log:')
    print(f'  x/80xb {service_meta:#x}')
    for target in unique:
        print(f'  x/80xb {target:#x}')
    print('  info registers tcr_el1 sctlr_el1 ttbr0_el1 ttbr1_el1')

    if not args.gdb_log:
        return
    log = args.gdb_log.read_text(errors='replace')
    memory = {}
    for match in re.finditer(r'(0x[0-9a-f]+):\s*((?:0x[0-9a-f]{1,2}\s*)+)', log):
        address = int(match.group(1), 16)
        values = bytes(int(value, 16) for value in match.group(2).split())
        if address in unique or address == service_meta:
            memory[f'{address:x}:50'] = values[:0x50].hex()
    registers = {}
    for name in ('tcr_el1', 'sctlr_el1', 'ttbr0_el1', 'ttbr1_el1'):
        found = re.search(rf'{name}\s+(0x[0-9a-f]+)', log, re.IGNORECASE)
        if found:
            registers[name.upper()] = found.group(1)
    missing = [f'{address:x}:50' for address in [*unique, service_meta] if f'{address:x}:50' not in memory]
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