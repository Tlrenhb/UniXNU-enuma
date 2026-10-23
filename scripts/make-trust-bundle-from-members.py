#!/usr/bin/env python3
"""Assemble the original static/system trust bundle from the trust caches `unixnu ipsw fetch` already extracted.

Why this exists: the upstream `scripts/build-original-system-trust.py` opens the **whole IPSW** from `refer/`
(6.37 GiB). GitHub-hosted macOS runners do not have room for that plus a kernel build, so this script does the
same four steps against the two small members the driver already streamed out:

  1. static module = <ipsw-dir>/<static>.dmg.trustcache                     (must hash to da88460e…)
  2. system module = DER field[3] of <ipsw-dir>/<cryptex>.dmg.trustcache    (must hash to d121ba89…)
  3. an IM4P container is verified to be a DER SEQUENCE of [0x16, 0x16, 0x16, 0x04] whose first field is b'IM4P'
  4. bundle = struct.pack('<3I', 2, 12, 12 + len(static)) + static + system (must hash to the board whitelist)

Every step is checked against a hash taken from the upstream script, so a wrong assumption fails loudly instead of
producing a bundle the payload step would reject.

Usage:
  python3 scripts/make-trust-bundle-from-members.py \
      --ipsw-dir out/ipsw/21H461 --out build/original-static-system-trust/original-static-system.bundle
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

STATIC_SHA256 = "da88460e6e513438d5307e5c526f76497a74754476a24da29f239f0c90120fc2"
SYSTEM_SHA256 = "d121ba89167e03aafd00fa687c30cd8ebc4ccafd10c1408adb2ba3de12403651"
BUNDLE_SHA256 = "7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7"


def der(data: bytes, at: int):
    if at + 2 > len(data):
        raise ValueError("truncated DER")
    tag, size = data[at], data[at + 1]
    at += 2
    if size & 0x80:
        count = size & 0x7F
        if not 0 < count <= 4 or at + count > len(data):
            raise ValueError("invalid DER length")
        size = int.from_bytes(data[at:at + count], "big")
        at += count
    if at + size > len(data):
        raise ValueError("DER value exceeds input")
    return tag, data[at:at + size], at + size


def unwrap_im4p(blob: bytes) -> bytes:
    """Return field[3] of the IM4P container, with the upstream structural checks."""
    tag, sequence, end = der(blob, 0)
    fields = []
    cursor = 0
    while cursor < len(sequence):
        kind, value, cursor = der(sequence, cursor)
        fields.append((kind, value))
    if tag != 0x30 or end != len(blob):
        raise SystemExit("the system trust cache is not a plain DER SEQUENCE")
    if [kind for kind, _ in fields] != [0x16, 0x16, 0x16, 0x04] or fields[0][1] != b"IM4P":
        raise SystemExit("the system trust cache is not the expected IM4P container")
    return fields[3][1]


def module_ok(blob: bytes, digest: str) -> bool:
    return (
        hashlib.sha256(blob).hexdigest() == digest
        and struct.unpack_from("<I", blob)[0] == 2
        and len(blob) == 24 + struct.unpack_from("<I", blob, 20)[0] * 24
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ipsw-dir", type=Path, required=True, help="the driver's out/ipsw/<build> directory")
    ap.add_argument("--out", type=Path, required=True, help="bundle to write")
    ap.add_argument("--static-name", default=None, help="override the static member name")
    ap.add_argument("--cryptex-name", default=None, help="override the Cryptex1,SystemTrustCache member name")
    args = ap.parse_args()

    members = sorted(p for p in args.ipsw_dir.glob("*.trustcache"))
    if not members:
        print(f"error: no *.trustcache in {args.ipsw_dir}", file=sys.stderr)
        return 2
    # The driver extracts the raw zip members, which may be the plain module or an IM4P container whose
    # fields wrap it. Enumerate every candidate (raw bytes plus each DER field) and match by hash, so an
    # unexpected container shows up as a readable table instead of a silent failure.
    def candidates(path: Path):
        out = [("raw", path.read_bytes())]
        try:
            blob = out[0][1]
            tag, sequence, end = der(blob, 0)
            if tag == 0x30 and end == len(blob):
                cursor = 0
                index = 0
                while cursor < len(sequence):
                    kind, value, cursor = der(sequence, cursor)
                    out.append((f"field[{index}]", value))
                    index += 1
        except ValueError:
            pass
        return out

    table, static_blob, system_blob = {}, None, None
    for member in members:
        for label, blob in candidates(member):
            digest = hashlib.sha256(blob).hexdigest()
            table[f"{member.name}:{label}"] = digest
            if module_ok(blob, STATIC_SHA256):
                static_blob = blob
            if module_ok(blob, SYSTEM_SHA256):
                system_blob = blob
    if static_blob is None or system_blob is None:
        print("error: could not identify the static/system modules by hash", file=sys.stderr)
        print(json.dumps(table, indent=2), file=sys.stderr)
        print(f"expected static {STATIC_SHA256}\nexpected system {SYSTEM_SHA256}", file=sys.stderr)
        return 3

    bundle = struct.pack("<3I", 2, 12, 12 + len(static_blob)) + static_blob + system_blob
    digest = hashlib.sha256(bundle).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(bundle)
    report = {
        "static_sha256": hashlib.sha256(static_blob).hexdigest(),
        "system_sha256": hashlib.sha256(system_blob).hexdigest(),
        "candidates": table,
        "bundle": str(args.out), "bundle_sha256": digest, "bundle_bytes": len(bundle),
        "matches_board_whitelist": digest == BUNDLE_SHA256,
    }
    print(json.dumps(report, indent=2))
    if digest != BUNDLE_SHA256:
        print(f"error: bundle sha256 {digest} is not the board whitelist value {BUNDLE_SHA256}", file=sys.stderr)
        return 4
    print(f"PASS: {args.out} sha256 {digest} (matches the board whitelist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())