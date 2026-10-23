#!/usr/bin/env python3
"""Fetch a pinned enuma DTS include closure from a sm8250 kernel tree and compile a real DTB.

Sources
-------
Two kernel trees are supported (`--source`, default `astide`), each pinned to a verified commit:

    astide     AstideLabs/android_kernel_xiaomi_sm8250  android17-aptusitu  4ae1f1e4c89ce26c09846655da746e2e7cd38901
    lineageos  LineageOS/android_kernel_xiaomi_sm8250   lineage-24.0        6b34d24765327d746074c8e53b5ad0d24a51acc7

`astide` is a fork of the LineageOS tree (same 4.19.325 base) and is the default because it is
newer and carries three enuma-specific corrections that LineageOS lacks. Verified by diffing the
two board files at the pinned commits -- exactly three changes, +4/-2 lines:

    arch/arm64/boot/dts/vendor/qcom/enuma-sm8250.dtsi
      * header comment: "attribution only of umi / And public attribution of xiaomi platforms"
        -> "attribution only of enuma"                                [cosmetic]
      * + novatek,fw-pen-name = "novatek_nt36523_k81_fw01_pen.bin";   [stylus firmware]
      * + novatek,mp-pen-name = "novatek_nt36523_k81_mp01_pen.bin";
      * + ktz8866,panelid-gpio = <&tlmm 38 0x00>;                     [panel-ID GPIO]

`enuma-pinctrl.dtsi` and `enuma-sm8250-overlay.dts` are byte-identical between the two sources.
The `ktz8866,panelid-gpio` line is the first in-tree lead for the **panel variant** question: a
panel-ID strap on TLMM GPIO 38. Its exact semantics still have to be confirmed against the
stock dtbo and the ktz8866 driver.

Xiaomi's own MiCode OSS kernel is not usable as a DTS source: `elish-r-oss` carries
`enuma_user_defconfig` but no device trees at all.

Base selection: kona-v2.1, because enuma is SM8250-AC
----------------------------------------------------
`enuma-sm8250.dtsi` is a *fragment*, not a tree: it opens with overrides of labels that live in
the SoC base (`&sde_dsi_active`, `&tlmm`, ...). The vendor Makefile is explicit about the
artifact and the candidate bases (same line in both sources):

    arch/arm64/boot/dts/vendor/qcom/Makefile:44   enuma-sm8250-overlay.dtbo-base := kona.dtb kona-v2.dtb kona-v2.1.dtb

The Makefile lists that same trio for alioth, apollo, cas, cmi, dagu, enuma, elish, lmi and
munch, so the base is chosen by **SoC revision, not by device**. The revision is carried by
`qcom,msm-id`:

    arch/arm64/boot/dts/vendor/qcom/kona.dtsi:32      qcom,msm-id = <356 0x10000>;   # SM8250 (865)     = v1
    arch/arm64/boot/dts/vendor/qcom/kona-v2.dtsi:6    qcom,msm-id = <356 0x20000>;   # SM8250-AB (865+) = v2
    arch/arm64/boot/dts/vendor/qcom/kona-v2.1.dtsi:7  qcom,msm-id = <356 0x20001>;   # SM8250-AC (870)  = v2.1

The Xiaomi Pad 5 Pro (elish) is SM8250-AC and mainline annotates the same revision for it
(`sm8250-xiaomi-elish-common.dtsi`: `qcom,msm-id = <QCOM_ID_SM8250 0x20001>; /* SM8250 v2.1 */`).
enuma is SM8250-AC as well, so the default base is **`kona-v2.1`** and the compiled base is
checked to report `0x20001`. `--base {kona,kona-v2,kona-v2.1}` overrides it: if the stock dtbo
dumped off the tablet reports another `qcom,msm-id`, switch and re-run.

Pipeline
--------
1. Resolve the transitive `#include "..."` / `#include <...>` closure of the board fragment and
   of the overlay by fetching single files over raw.githubusercontent.com at the pinned commit.
   Nothing is cloned and nothing outside the closure is downloaded.
2. Verify the three load-bearing files against the source's pinned size + sha256 (prefix) and
   fail loudly on any mismatch -- an unpinned or drifted tree must never pass silently.
3. Splice each root with the C preprocessor used the way the kernel build uses it
   (`-nostdinc -undef -D__DTS__ -x assembler-with-cpp -P`); dtc alone cannot expand `#include`.
   A `.dtsi` has no `/dts-v1/;`, so one is added when splicing a fragment.
4. Compile the base and the overlay with `-@`, merge them with `fdtoverlay`, and validate the
   result: touch compatible string, IRQ 39, `qcom,msm-id` revision, and the source-specific
   pen-firmware and panel-ID properties.

Caching and outputs are per source, so the two trees never overwrite each other:

    .cache/linux-dts/<source>/...        fetched closure + sources.json (commit, base, hashes)
    out/<source>/...                     enuma.dts, enuma-base.*, enuma-overlay.*, enuma.dtb

`--fetch` (default) fetches, compiles and validates. `--check` re-verifies the cached closure,
the pin, the recorded hashes, source and base, and re-compiles, all without the network.
`--compare OTHER` (offline) diffs this source's closure against another source's closure.

Repeated runs at the same pin, source and base are deterministic.

Exit codes
----------
0 success | 2 usage error | 3 incomplete closure (missing includes listed) | 4 pin/size/hash,
source or base mismatch | 5 preprocess or dtc/fdtoverlay failure | 6 validation failure
"""
import argparse
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

DTS_DIR = 'arch/arm64/boot/dts/vendor/qcom'
MAKEFILE = f'{DTS_DIR}/Makefile'
BOARD_DTS = f'{DTS_DIR}/enuma-sm8250.dtsi'
BOARD_OVERLAY = f'{DTS_DIR}/enuma-sm8250-overlay.dts'
OVERLAY_NAME = 'enuma-sm8250-overlay'

# source -> repository, branch, verified commit, and the pins of the three load-bearing files
# (size, sha256[:16]). astide's board file is 100 bytes longer than LineageOS's; the pinctrl and
# overlay hashes are identical in both trees.
SOURCES = {
    'astide': {
        'repo': 'AstideLabs/android_kernel_xiaomi_sm8250',
        'branch': 'android17-aptusitu',
        'commit': '4ae1f1e4c89ce26c09846655da746e2e7cd38901',
        'note': 'fork of the LineageOS tree; newer, carries the enuma pen firmware and panel-ID GPIO',
        'pinned': {
            f'{DTS_DIR}/enuma-sm8250.dtsi': (31472, 'fdcf896b0abcd57d'),
            f'{DTS_DIR}/enuma-pinctrl.dtsi': (9397, '8ee37ea0cb452b1d'),
            f'{DTS_DIR}/enuma-sm8250-overlay.dts': (426, '055ca4e58000c143'),
        },
        'expect_pen_firmware': True,
        'expect_panelid_gpio': True,
    },
    'lineageos': {
        'repo': 'LineageOS/android_kernel_xiaomi_sm8250',
        'branch': 'lineage-24.0',
        'commit': '6b34d24765327d746074c8e53b5ad0d24a51acc7',
        'note': 'upstream CAF-derived tree; no enuma pen firmware, no panel-ID GPIO',
        'pinned': {
            f'{DTS_DIR}/enuma-sm8250.dtsi': (31372, '09ed2b95e76e88ad'),
            f'{DTS_DIR}/enuma-pinctrl.dtsi': (9397, '8ee37ea0cb452b1d'),
            f'{DTS_DIR}/enuma-sm8250-overlay.dts': (426, '055ca4e58000c143'),
        },
        'expect_pen_firmware': False,
        'expect_panelid_gpio': False,
    },
}
DEFAULT_SOURCE = 'astide'

# The SoC revision a base carries in qcom,msm-id (second cell). enuma is SM8250-AC -> v2.1.
DEFAULT_BASE = 'kona-v2.1'
BASE_MSM_ID = {'kona': 0x10000, 'kona-v2': 0x20000, 'kona-v2.1': 0x20001}

TOUCH_COMPATIBLE = 'novatek,NVT-ts-spi'
TOUCH_IRQ_PROPERTY = 'novatek,irq-gpio'
TOUCH_IRQ_GPIO = 39                      # `novatek,irq-gpio = <&tlmm 39 0x2001>;`
PEN_PROPERTIES = ('novatek,fw-pen-name', 'novatek,mp-pen-name')
PANELID_PROPERTY = 'ktz8866,panelid-gpio'
PANELID_GPIO = 38                        # `ktz8866,panelid-gpio = <&tlmm 38 0x00>;` (astide only)

SKELETON = Path(__file__).resolve().parents[1]
INCLUDE = re.compile(r'^\s*#\s*include\s*([<"])([^>"\n]+)[>"]', re.M)
BASE_LINE = re.compile(rf'^{re.escape(OVERLAY_NAME)}\.dtbo-base\s*:?=\s*(.+?)\s*$', re.M)
# A file whose whole body is a relative path is a symlink served as its target text by
# raw.githubusercontent.com (e.g. include/dt-bindings/input/linux-event-codes.h).
SYMLINK = re.compile(r'^\s*((?:\.\./)+[\w/.,+-]+)\s*$')
SYMLINK_MAX = 512

# Where a <...> include may live in this repository, in resolution order.
ANGLE_PREFIXES = ('include/', '', f'{DTS_DIR}/', 'arch/arm64/boot/dts/')

EXIT_OK, EXIT_USAGE, EXIT_INCOMPLETE, EXIT_PIN, EXIT_BUILD, EXIT_VALIDATE = 0, 2, 3, 4, 5, 6


class Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Layout:
    """Per-source cache and output paths, so two sources never overwrite each other."""

    def __init__(self, source):
        self.source = source
        self.spec = SOURCES[source]
        self.raw_base = (f'https://raw.githubusercontent.com/{self.spec["repo"]}/'
                         f'{self.spec["commit"]}/')
        self.repo_url = f'https://github.com/{self.spec["repo"]}'
        self.cache = SKELETON / '.cache' / 'linux-dts' / source
        self.sources_json = self.cache / 'sources.json'
        self.out = SKELETON / 'out' / source
        self.dts = self.out / 'enuma.dts'
        self.board_dtb = self.out / 'enuma-board.dtb'
        self.board_log = self.out / 'enuma-board-fragment-error.txt'
        self.overlay_dts = self.out / 'enuma-overlay.dts'
        self.overlay_dtbo = self.out / 'enuma-overlay.dtbo'
        self.base_dts = self.out / 'enuma-base.dts'
        self.base_dtb = self.out / 'enuma-base.dtb'
        self.dtb = self.out / 'enuma.dtb'
        self.dtb_dts = self.out / 'enuma.dtb.decompiled.dts'
        self.json = self.out / 'enuma-dts.json'


P = None            # the active Layout, set once in main()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def local_path(repo_path):
    return P.cache / repo_path


def read_cached(repo_path):
    path = local_path(repo_path)
    return path.read_bytes() if path.is_file() else None


def http_get(repo_path):
    """Return the bytes of one repository file at the pinned commit, or None on 404.

    curl is preferred over urllib: this sandbox reaches the internet through an HTTP proxy that
    answers urllib's CONNECT with `426 Upgrade Required` while curl's request succeeds. urllib
    stays as the fallback for hosts without curl.
    """
    url = P.raw_base + repo_path
    if shutil.which('curl'):
        scratch = P.cache / '.http-scratch'
        scratch.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(['curl', '-sS', '-L', '--max-time', '60', '-o', str(scratch),
                                 '-w', '%{http_code}', url], capture_output=True, text=True)
        code = result.stdout.strip()
        if code == '200':
            return scratch.read_bytes()
        if code == '404':
            return None
        raise Failure(EXIT_BUILD,
                      f'{url}: HTTP {code or "no response"} {result.stderr.strip()}'.strip())
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise Failure(EXIT_BUILD, f'{url}: HTTP {error.code}') from error
    except urllib.error.URLError as error:
        raise Failure(EXIT_BUILD, f'{url}: {error.reason}') from error


def includes_of(text):
    return [(('quote' if opening == '"' else 'angle'), name)
            for opening, name in INCLUDE.findall(text)]


def resolve_quote(name, including):
    """A quoted include: relative to the including file, then the vendor dir, then include/."""
    base = str(PurePosixPath(including).parent)
    seen, ordered = set(), []
    for candidate in (str(PurePosixPath(base) / name), f'{DTS_DIR}/{name}', f'include/{name}'):
        candidate = str(PurePosixPath(candidate))
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def resolve_angle(name):
    return [prefix + name for prefix in ANGLE_PREFIXES]


def cached_or_fetch(repo_path):
    """Return (bytes, from_cache) or (None, False) when the path does not exist at the pin."""
    data = read_cached(repo_path)
    if data is not None:
        return data, True
    data = http_get(repo_path)
    if data is None:
        return None, False
    path = local_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data, False


def fetch_one(repo_path, files, missing, symlinks, verbose=True):
    """Ensure `repo_path` is in the cache with real content; follow symlinks; None if absent."""
    if repo_path in files:
        return files[repo_path]
    data, from_cache = cached_or_fetch(repo_path)
    if data is None:
        return None
    text = data.decode('utf-8', 'replace')
    match = SYMLINK.match(text) if len(data) <= SYMLINK_MAX else None
    if match:
        target = posixpath.normpath(str(PurePosixPath(repo_path).parent / match.group(1)))
        if target.startswith('../') or target.startswith('/'):
            raise Failure(EXIT_PIN, f'{repo_path}: symlink escapes the source tree -> {target}')
        resolved = fetch_one(target, files, missing, symlinks, verbose=verbose)
        if resolved is None:
            missing.append(f'{repo_path} -> {target}')
            if verbose:
                print(f'  MISSING  {repo_path} -> {target}', file=sys.stderr)
            return None
        symlinks[repo_path] = target
        # Store the resolved bytes at the symlink's own path so the preprocessor finds content.
        path = local_path(repo_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resolved)
        files[repo_path] = resolved
        if verbose:
            print(f'  symlink  {repo_path} -> {target}')
        return resolved
    path = local_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    files[repo_path] = data
    if verbose and not from_cache:
        print(f'  fetched  {len(data):>7} B  {repo_path}')
    return data


def fetch_closure(roots, files, missing, resolutions, symlinks=None, verbose=True):
    """Fetch the transitive include closure of every root into `files` (modified in place)."""
    symlinks = {} if symlinks is None else symlinks
    pending = list(roots)
    while pending:
        repo_path = pending.pop(0)
        if repo_path in files or repo_path in missing:
            continue
        data = fetch_one(repo_path, files, missing, symlinks, verbose=verbose)
        if data is None:
            missing.append(repo_path)
            if verbose:
                print(f'  MISSING  {repo_path}', file=sys.stderr)
            continue
        for kind, name in includes_of(data.decode('utf-8', 'replace')):
            if kind == 'angle':
                if name in resolutions:
                    if resolutions[name]:
                        pending.append(resolutions[name])
                    continue
                candidates = resolve_angle(name)
                chosen = next((c for c in candidates if read_cached(c) is not None), None)
                if chosen is None:
                    for candidate in candidates:
                        if http_get(candidate) is not None:
                            chosen = candidate
                            break
                if chosen is None:
                    resolutions[name] = None
                    missing.append(f'<{name}>  (tried: {", ".join(candidates)})')
                    if verbose:
                        print(f'  MISSING  <{name}>', file=sys.stderr)
                    continue
                resolutions[name] = chosen
                pending.append(chosen)
            else:
                candidates = resolve_quote(name, repo_path)
                hit = next((c for c in candidates if c in files or read_cached(c) is not None), None)
                pending.append(hit or candidates[0])
    return files, missing, resolutions


def load_closure_from_cache():
    """--check: rebuild the file list from sources.json, then re-verify every byte."""
    if not P.sources_json.is_file():
        raise Failure(EXIT_INCOMPLETE, f'{P.sources_json} not found; run --fetch first')
    recorded = json.loads(P.sources_json.read_text())
    if recorded.get('commit') != P.spec['commit']:
        raise Failure(EXIT_PIN, f'{P.sources_json} is for commit {recorded.get("commit")}, not the '
                                f'{P.source} pin {P.spec["commit"]}; run --fetch')
    files, stale, absent = {}, [], []
    for repo_path, expected in sorted(recorded.get('sha256', {}).items()):
        data = read_cached(repo_path)
        if data is None:
            absent.append(repo_path)
        elif sha256(data) != expected:
            stale.append(repo_path)
        else:
            files[repo_path] = data
    if absent:
        raise Failure(EXIT_INCOMPLETE, 'cached closure is incomplete:\n  ' + '\n  '.join(absent)
                                       + '\nrun --fetch')
    if stale:
        raise Failure(EXIT_PIN, 'cached files do not match their recorded sha256:\n  '
                                + '\n  '.join(stale))
    return files, recorded


def verify_pins(files):
    """Fail on any size/sha256 drift of the three load-bearing files."""
    problems = []
    for repo_path, (size, prefix) in sorted(P.spec['pinned'].items()):
        data = files.get(repo_path)
        if data is None:
            problems.append(f'{repo_path}: not in the closure')
            continue
        digest = sha256(data)
        if len(data) != size:
            problems.append(f'{repo_path}: size {len(data)} != pinned {size}')
        elif not digest.startswith(prefix):
            problems.append(f'{repo_path}: sha256 {digest[:16]} != pinned {prefix}')
        else:
            print(f'  pin ok   {size:>7} B  {digest[:16]}  {repo_path}')
    if problems:
        raise Failure(EXIT_PIN, f'pinned files do not match {P.source} at '
                                f'{P.spec["commit"]}:\n  ' + '\n  '.join(problems))


def preprocessor():
    for name in ('cpp', 'clang'):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def preprocess(root):
    """Expand the includes of one cached root; returns the text with dtc's line markers removed."""
    tool = preprocessor()
    if tool is None:
        raise Failure(EXIT_BUILD, 'a C preprocessor is required to expand #include in the DTS '
                                  'closure; install cpp (gcc) or clang')
    command = tool + ['-nostdinc', '-undef', '-P', '-D__DTS__', '-x', 'assembler-with-cpp']
    for prefix in ANGLE_PREFIXES:
        command += ['-I', str(P.cache / prefix.rstrip('/'))]
    command += [str(local_path(root))]
    result = subprocess.run(command, capture_output=True, text=True, cwd=str(P.cache))
    if result.returncode != 0:
        raise Failure(EXIT_BUILD, f'{" ".join(command)}\n{result.stderr.strip()}')
    return result.stdout


def splice(root, destination, verbose=True):
    """Write one preprocessed root as a self-contained .dts, adding /dts-v1/ when it is a .dtsi."""
    text = preprocess(root)
    if '/dts-v1/' not in text:
        text = '/dts-v1/;\n\n' + text
    P.out.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)
    if verbose:
        print(f'  spliced  {len(text):>7} B  {destination.relative_to(SKELETON)}')
    return destination


def run_dtc(source, destination, extra=()):
    """Returns (ok, output). Never raises: the board fragment is allowed to fail, on purpose."""
    command = ['dtc', '-I', 'dts', '-O', 'dtb', *extra, '-o', str(destination), str(source)]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0, f'{" ".join(command)}\n{result.stderr.strip()}'


def compile_required(source, destination, extra=()):
    ok, output = run_dtc(source, destination, extra)
    if not ok:
        raise Failure(EXIT_BUILD, output)
    data = destination.read_bytes()
    print(f'  compiled {len(data):>7} B  sha256 {sha256(data)}  {destination.relative_to(SKELETON)}')
    return data


def base_dts_from_makefile(wanted):
    """Read `.dtbo-base` from the vendor Makefile, check `wanted` is offered, map .dtb -> .dts."""
    data, _ = cached_or_fetch(MAKEFILE)
    if data is None:
        raise Failure(EXIT_BUILD, f'{MAKEFILE} is not present at the pin; cannot learn the base')
    match = BASE_LINE.search(data.decode('utf-8', 'replace'))
    if not match:
        raise Failure(EXIT_BUILD, f'{MAKEFILE} has no {OVERLAY_NAME}.dtbo-base entry')
    available = [item[:-4] for item in match.group(1).split() if item.endswith('.dtb')]
    if not available:
        raise Failure(EXIT_BUILD, f'the .dtbo-base entry lists no .dtb: {match.group(1)!r}')
    if wanted not in available:
        raise Failure(EXIT_USAGE, f'--base {wanted} is not offered by {MAKEFILE} for '
                                  f'{OVERLAY_NAME}: {", ".join(available)}')
    print(f'  base     {" ".join(available)}  (from {MAKEFILE}; using {wanted}, '
          f'msm-id {BASE_MSM_ID[wanted]:#07x})')
    return f'{DTS_DIR}/{wanted}.dts', available, data


def merge_base_and_overlay(base_dtb, overlay_dtbo, destination):
    if not shutil.which('fdtoverlay'):
        raise Failure(EXIT_BUILD, 'fdtoverlay is required to apply the overlay to the base '
                                  '(package device-tree-compiler)')
    command = ['fdtoverlay', '-i', str(base_dtb), '-o', str(destination), str(overlay_dtbo)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise Failure(EXIT_BUILD, f'{" ".join(command)}\n{result.stderr.strip()}')
    data = destination.read_bytes()
    print(f'  merged   {len(data):>7} B  sha256 {sha256(data)}  {destination.relative_to(SKELETON)}')
    return data


def validate(dtb, source_hashes, base_name, extras):
    """Check the merged DTB for the touch nodes, IRQ 39, the SoC revision, and source features."""
    problems = []
    if TOUCH_COMPATIBLE.encode() not in dtb:
        problems.append(f'{TOUCH_COMPATIBLE!r} not found in {P.dtb.name}')
    else:
        print(f'  dtb grep hit: {TOUCH_COMPATIBLE}')
    decompiled = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', str(P.dtb)],
                               capture_output=True, text=True)
    if decompiled.returncode != 0:
        raise Failure(EXIT_VALIDATE, f'dtc could not decompile {P.dtb}:\n{decompiled.stderr.strip()}')
    P.dtb_dts.write_text(decompiled.stdout)

    match = re.search(rf'{re.escape(TOUCH_IRQ_PROPERTY)}\s*=\s*'
                      r'<(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)', decompiled.stdout)
    irq_dump = match.group(0) if match else 'not found'
    if not match:
        problems.append(f'{TOUCH_IRQ_PROPERTY} not found in the decompiled {P.dtb.name}')
    elif int(match.group(2), 16) != TOUCH_IRQ_GPIO:
        problems.append(f'{TOUCH_IRQ_PROPERTY} is GPIO {int(match.group(2), 16)}, '
                        f'expected {TOUCH_IRQ_GPIO}')
    else:
        print(f'  dtb node ok: {irq_dump}')

    expected_msm = BASE_MSM_ID[base_name]
    msm = re.search(r'qcom,msm-id\s*=\s*<(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)', decompiled.stdout)
    msm_dump = msm.group(0) if msm else 'not found'
    if not msm:
        problems.append(f'qcom,msm-id not found in the decompiled {P.dtb.name}')
    elif int(msm.group(2), 16) != expected_msm:
        problems.append(f'qcom,msm-id revision is {int(msm.group(2), 16):#07x}, expected '
                        f'{expected_msm:#07x} for base {base_name}')
    else:
        print(f'  msm-id ok: {msm_dump}  (base {base_name})')

    # Source-specific features: present in astide, absent upstream. A mismatch means the two
    # trees are not what the source table claims, so it is a validation failure, not a note.
    pen = {name: (name.encode() in dtb) for name in PEN_PROPERTIES}
    pen_ok = all(pen.values())
    if pen_ok != P.spec['expect_pen_firmware']:
        problems.append(f'pen firmware properties present={pen_ok}, expected '
                        f'{P.spec["expect_pen_firmware"]} for source {P.source}')
    else:
        print(f'  pen firmware {"present" if pen_ok else "absent as expected"}: '
              + ', '.join(f'{k.split(",")[-1]}={"yes" if v else "no"}' for k, v in pen.items()))

    panel = re.search(rf'{re.escape(PANELID_PROPERTY)}\s*=\s*'
                      r'<(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)', decompiled.stdout)
    panel_dump = panel.group(0) if panel else 'not found'
    panel_ok = bool(panel) and int(panel.group(2), 16) == PANELID_GPIO
    if panel_ok != P.spec['expect_panelid_gpio']:
        problems.append(f'{PANELID_PROPERTY} ok={panel_ok} ({panel_dump}), expected '
                        f'{P.spec["expect_panelid_gpio"]} for source {P.source}')
    else:
        print(f'  panelid  {"present" if panel_ok else "absent as expected"}: {panel_dump}')

    board_text = re.sub(r'\s+', ' ', P.dts.read_text())
    if f'&tlmm {TOUCH_IRQ_GPIO}' not in board_text:
        problems.append(f'`&tlmm {TOUCH_IRQ_GPIO}` not present in {P.dts.name}')
    else:
        print(f'  dts grep hit: {TOUCH_IRQ_PROPERTY} = <&tlmm {TOUCH_IRQ_GPIO} 0x2001>')

    summary = {
        'source': P.source,
        'repository': P.repo_url,
        'branch': P.spec['branch'],
        'commit': P.spec['commit'],
        'files': len(source_hashes),
        'base': base_name,
        'base_msm_id': f'{expected_msm:#07x}',
        'msm_id_dtb_line': msm_dump,
        'dtb': str(P.dtb.relative_to(SKELETON)),
        'dtb_bytes': len(dtb),
        'dtb_sha256': sha256(dtb),
        'board_fragment_dts_bytes': P.dts.stat().st_size,
        'board_fragment_dts_sha256': sha256(P.dts.read_bytes()),
        'touch_compatible': TOUCH_COMPATIBLE,
        'touch_irq_property': TOUCH_IRQ_PROPERTY,
        'touch_irq_gpio': TOUCH_IRQ_GPIO,
        'touch_irq_dtb_line': irq_dump,
        'pen_firmware_properties': {k: bool(v) for k, v in pen.items()},
        'panelid_gpio_property': PANELID_PROPERTY,
        'panelid_gpio_dtb_line': panel_dump,
        **extras,
    }
    P.json.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    if problems:
        raise Failure(EXIT_VALIDATE, 'DTB validation failed:\n  ' + '\n  '.join(problems))
    return summary


def build_and_validate(files, missing, source_hashes, base_name, verbose=True):
    base_dts, available, _ = base_dts_from_makefile(base_name)

    # The board fragment on its own: compiled so the failure is evidence, not so it passes.
    splice(BOARD_DTS, P.dts, verbose=verbose)
    ok, output = run_dtc(P.dts, P.board_dtb)
    fragment_error = None
    if ok:
        print(f'  NOTE: {BOARD_DTS} compiled standalone after all; no fragment fallback needed')
        P.board_log.unlink(missing_ok=True)
        P.board_dtb.unlink(missing_ok=True)
    else:
        fragment_error = output
        P.board_log.write_text(output + '\n')
        errors = [line for line in output.splitlines() if line.startswith('Error:')]
        print(f'  expected: the board fragment does not compile standalone '
              f'({len(errors)} errors, full log {P.board_log.name})')
        for line in errors[:3]:
            print(f'    {line}')

    # The real artifact: base + overlay, exactly as the vendor Makefile builds it.
    splice(base_dts, P.base_dts, verbose=verbose)
    base_dtb_data = compile_required(P.base_dts, P.base_dtb, extra=('-@',))
    splice(BOARD_OVERLAY, P.overlay_dts, verbose=verbose)
    overlay_data = compile_required(P.overlay_dts, P.overlay_dtbo, extra=('-@',))
    merged = merge_base_and_overlay(P.base_dtb, P.overlay_dtbo, P.dtb)

    if missing:
        raise Failure(EXIT_INCOMPLETE, 'the include closure is incomplete; the artifacts above '
                                       'are not usable:\n  ' + '\n  '.join(missing))
    return validate(merged, source_hashes, base_name, {
        'base_dts': base_dts,
        'base_choices': available,
        'base_dtb': str(P.base_dtb.relative_to(SKELETON)),
        'base_dtb_bytes': len(base_dtb_data),
        'base_dtb_sha256': sha256(base_dtb_data),
        'overlay_dtbo_bytes': len(overlay_data),
        'overlay_dtbo_sha256': sha256(overlay_data),
        'board_fragment_standalone_compile': 'ok' if fragment_error is None else 'failed-as-expected',
        'board_fragment_error_file': None if fragment_error is None else P.board_log.name,
        'merge_tool': 'fdtoverlay',
    })


def report_missing(missing):
    if missing:
        print(f'includes that could not be resolved ({len(missing)}):', file=sys.stderr)
        for item in missing:
            print(f'  {item}', file=sys.stderr)


def cmd_fetch(args):
    base_name = args.base or DEFAULT_BASE
    print(f'enuma DTS closure: source {P.source} ({P.spec["repo"]}) @ {P.spec["commit"]} '
          f'({P.spec["branch"]}), base {base_name}')
    files, missing, resolutions, symlinks = {}, [], {}, {}
    fetch_closure([BOARD_DTS, BOARD_OVERLAY], files, missing, resolutions, symlinks)
    print(f'board + overlay closure: {len(files)} files')
    base_dts, available, makefile = base_dts_from_makefile(base_name)
    files[MAKEFILE] = makefile
    fetch_closure([base_dts], files, missing, resolutions, symlinks)
    print(f'with the {base_dts.rsplit("/", 1)[-1]} base closure: {len(files)} files')
    report_missing(missing)
    verify_pins(files)
    sources = {
        'source': P.source,
        'repository': P.repo_url,
        'branch': P.spec['branch'],
        'commit': P.spec['commit'],
        'base': base_name,
        'base_choices': available,
        'roots': [BOARD_DTS, BOARD_OVERLAY, base_dts],
        'angle_resolutions': resolutions,
        'symlinks': symlinks,
        'missing': missing,
        'sha256': {path: sha256(data) for path, data in sorted(files.items())},
    }
    P.sources_json.parent.mkdir(parents=True, exist_ok=True)
    P.sources_json.write_text(json.dumps(sources, indent=2, sort_keys=True) + '\n')
    print(f'  wrote    {P.sources_json.relative_to(SKELETON)}')
    summary = build_and_validate(files, missing, sources['sha256'], base_name)
    print(f'PASS: {summary["dtb"]} {summary["dtb_bytes"]} B sha256 {summary["dtb_sha256"]} '
          f'(source {P.source}, base {base_name} + overlay) from {summary["files"]} source files '
          f'at {P.spec["commit"]}')
    return EXIT_OK


def cmd_check(args):
    print(f'enuma DTS closure check (offline): source {P.source} @ {P.spec["commit"]}')
    files, recorded = load_closure_from_cache()
    recorded_base = recorded.get('base')
    if recorded_base is None:
        raise Failure(EXIT_PIN, f'{P.sources_json} has no recorded base (written by an older '
                                f'version); run --fetch')
    if args.base is not None and args.base != recorded_base:
        raise Failure(EXIT_PIN, f'{P.sources_json} was fetched for base {recorded_base}; '
                                f'run --fetch --base {args.base} to build that base')
    verify_pins(files)
    missing = recorded.get('missing', [])
    report_missing(missing)
    summary = build_and_validate(files, missing, recorded['sha256'], recorded_base, verbose=False)
    print(f'PASS (offline): {summary["dtb"]} {summary["dtb_bytes"]} B sha256 '
          f'{summary["dtb_sha256"]} (source {P.source}, base {recorded_base} + overlay) from '
          f'{summary["files"]} source files at {P.spec["commit"]}')
    return EXIT_OK


def load_recorded(source):
    path = SKELETON / '.cache' / 'linux-dts' / source / 'sources.json'
    if not path.is_file():
        raise Failure(EXIT_INCOMPLETE, f'{path} not found; run --fetch --source {source} first')
    return json.loads(path.read_text())


def cmd_compare(args):
    """Offline diff of this source's closure against another source's closure."""
    other = args.compare
    if other == P.source:
        raise Failure(EXIT_USAGE, '--compare needs a different source')
    mine, theirs = load_recorded(P.source), load_recorded(other)
    a, b = mine.get('sha256', {}), theirs.get('sha256', {})
    only_mine = sorted(set(a) - set(b))
    only_theirs = sorted(set(b) - set(a))
    differing = sorted(path for path in set(a) & set(b) if a[path] != b[path])
    print(f'closure diff: {P.source} ({len(a)} files, base {mine.get("base")}) vs '
          f'{other} ({len(b)} files, base {theirs.get("base")})')
    print(f'  identical: {len(set(a) & set(b)) - len(differing)}')
    print(f'  differing: {len(differing)}')
    for path in differing:
        print(f'    DIFFERS   {path}')
        print(f'              {P.source:9s} {a[path]}')
        print(f'              {other:9s} {b[path]}')
    print(f'  only in {P.source}: {len(only_mine)}')
    for path in only_mine:
        print(f'    ONLY      {path}')
    print(f'  only in {other}: {len(only_theirs)}')
    for path in only_theirs:
        print(f'    ONLY      {path}')
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--fetch', action='store_true',
                      help='fetch the closure, compile and validate (default)')
    mode.add_argument('--check', action='store_true',
                      help='validate the cached closure off-line and re-compile; no network')
    mode.add_argument('--compare', metavar='OTHER', choices=sorted(SOURCES),
                      help="off-line: diff this source's cached closure against OTHER's")
    parser.add_argument('--source', choices=sorted(SOURCES), default=DEFAULT_SOURCE,
                        help=f'kernel tree to read the enuma DTS from; default {DEFAULT_SOURCE}')
    parser.add_argument('--base', choices=sorted(BASE_MSM_ID), default=None,
                        help=f'kona base to merge the overlay onto; default {DEFAULT_BASE} '
                             f'(SM8250-AC, msm-id {BASE_MSM_ID[DEFAULT_BASE]:#07x}). '
                             f'Override when the dumped stock dtbo reports another qcom,msm-id')
    return parser


def main(argv=None):
    global P
    args = build_parser().parse_args(argv)
    P = Layout(args.source)
    try:
        if args.compare:
            return cmd_compare(args)
        return cmd_check(args) if args.check else cmd_fetch(args)
    except Failure as failure:
        print(f'FAIL: {failure}', file=sys.stderr)
        return failure.code
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())