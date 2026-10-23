#!/usr/bin/env bash
# evidence-hash.sh — 把一次上机尝试的证据与对应的 boot image 绑定（官方 guide/debugging.md 的要求）
#
# 用法：
#   scripts/evidence-hash.sh --dir docs/evidence/2026-11-01-stage2-menu \
#                            --boot out/enuma/boot/boot.img \
#                            [--stage "Stage 2: UniXNU menu"] \
#                            [--notes "cold boot, second attempt"] \
#                            [--extra out/enuma/boot/boot.json out/enuma/payload/payload.json]
#
# 产出（不修改任何原始文件）：
#   <dir>/boot.img.sha256   本次镜像哈希
#   <dir>/MANIFEST.md       人读清单
#   <dir>/manifest.json     机器可读清单
set -euo pipefail

DIR=""; BOOT=""; STAGE=""; NOTES=""; EXTRAS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="${2:?}"; shift 2 ;;
    --boot) BOOT="${2:?}"; shift 2 ;;
    --stage) STAGE="${2:-}"; shift 2 ;;
    --notes) NOTES="${2:-}"; shift 2 ;;
    --extra) shift; while [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; do EXTRAS+=("$1"); shift; done ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done
[ -n "$DIR" ] || { echo "缺少 --dir" >&2; exit 2; }
mkdir -p "$DIR"

sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }

BOOT_SHA=""
if [ -n "$BOOT" ]; then
  [ -f "$BOOT" ] || { echo "boot image 不存在：$BOOT" >&2; exit 3; }
  BOOT_SHA=$(sha "$BOOT")
  printf '%s  %s\n' "$BOOT_SHA" "$(basename "$BOOT")" > "$DIR/boot.img.sha256"
  echo "boot image sha256: $BOOT_SHA"
else
  echo "警告：未传 --boot，证据将无法绑定到具体镜像（官方要求必须绑定）" >&2
fi

for e in "${EXTRAS[@]:-}"; do
  [ -n "$e" ] || continue
  [ -f "$e" ] && cp -n "$e" "$DIR/" 2>/dev/null || true
done

python3 - "$DIR" "$BOOT_SHA" "$STAGE" "$NOTES" <<'PY'
import hashlib, json, os, pathlib, sys, datetime
d = pathlib.Path(sys.argv[1]); boot_sha = sys.argv[2]; stage = sys.argv[3]; notes = sys.argv[4]
def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
files = []
for p in sorted(d.rglob('*')):
    if p.is_file() and p.name not in ('MANIFEST.md', 'manifest.json'):
        files.append({'path': str(p.relative_to(d)), 'bytes': p.stat().st_size, 'sha256': sha256(p)})
manifest = {
    'stage': stage or None, 'notes': notes or None,
    'created': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
    'boot_image_sha256': boot_sha or None,
    'file_count': len(files), 'files': files,
}
(d / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
lines = ['# 证据清单', '',
         f"- 阶段：{stage or '(未填)'}",
         f"- 记录时间：{manifest['created']}",
         f"- **对应 boot image sha256：`{boot_sha or '(未填 —— 这份证据无法绑定到具体镜像)'}`**",
         f"- 备注：{notes or '(无)'}", '',
         '| 文件 | 字节 | sha256 |', '| --- | ---: | --- |']
for f in files:
    lines.append(f"| `{f['path']}` | {f['bytes']} | `{f['sha256'][:16]}…` |")
lines += ['', '> 原始文件未被修改。复核方式：对同一目录重跑本脚本，哈希应完全一致。', '']
(d / 'MANIFEST.md').write_text('\n'.join(lines), encoding='utf-8')
print(f"写入 {d/'MANIFEST.md'} 与 manifest.json（{len(files)} 个文件）")
PY

echo "完成：$DIR/MANIFEST.md"