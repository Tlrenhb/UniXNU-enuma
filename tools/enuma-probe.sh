#!/usr/bin/env bash
# enuma-probe.sh — 上机一次性采集脚本（小米平板 5 Pro 5G / enuma / SM8250-AC）
#
# 目的：把 PLAN.md §4 的测量清单（M1–M9）做成"一条命令跑完"，产出：
#   <outdir>/raw/*      每一步的原始输出（失败也会留下文件+状态）
#   <outdir>/summary.json  机器可读汇总（供 scripts/enuma-fill.py 自动回填骨架 TODO）
#   <outdir>/NOTES.md   人读报告：哪些成功、哪些失败、下一步该干什么
#
# 用法：
#   scripts/enuma-probe.sh                 # 平板已连 adb（Android 正常启动）
#   scripts/enuma-probe.sh --with-fastboot  # 结束后提示你重启到 fastboot 再跑一次
#   ROOT_CMD="su -c" scripts/enuma-probe.sh # 自定义 root 提权方式（默认自动尝试 su -c / sudo）
#
# 安全：只读采集。除了 dtb/dtbo 的 dd 到 /data/local/tmp（可删）以外不写任何分区。
set -uo pipefail

OUT="${OUT:-enuma-probe-$(date +%Y%m%d-%H%M%S)}"
RAW="$OUT/raw"; mkdir -p "$RAW"
ADB="${ADB:-adb}"; FASTBOOT="${FASTBOOT:-fastboot}"
FASTBOOT_ONLY=0
case "${1:-}" in
  --fastboot-only) FASTBOOT_ONLY=1 ;;
  --with-fastboot) FASTBOOT_ONLY=0 ;;   # 兼容旧写法：默认就会采集（若设备在 fastboot）
esac
SKIP_FASTBOOT="${SKIP_FASTBOOT:-0}"
TMO="${TMO:-20}"                        # 单条 adb/fastboot 命令的超时秒数
STATUS="$OUT/status.tsv"; : > "$STATUS"

say() { printf '%s\n' "$*"; }
record() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$STATUS"; }   # item, status, note

# fastboot 在没有设备时会**永久等待**（它自己不超时），所以每条外部命令都必须带超时。
if command -v timeout >/dev/null 2>&1; then
  tmo() { timeout "$TMO" "$@"; }
else
  say "警告：系统没有 timeout 命令，无法给 adb/fastboot 加超时（macOS 可 brew install coreutils 用 gtimeout）"
  tmo() { "$@"; }
fi

need() { command -v "$1" >/dev/null 2>&1 || { say "缺少工具：$1（请安装 Android platform-tools）"; exit 2; }; }
need "$ADB"; need python3

# ---- 0. 设备与 root 能力探测 -------------------------------------------------
say "== 0. 设备状态 =="
tmo "$ADB" start-server >/dev/null 2>&1 || true
DEVS=$(tmo "$ADB" devices 2>/dev/null | awk 'NR>1 && $2=="device"{print $1}')
FB_DEVS=""
if command -v "$FASTBOOT" >/dev/null 2>&1; then
  FB_DEVS=$(tmo "$FASTBOOT" devices 2>/dev/null | awk '{print $1}' | head -3)
fi
if [ -n "$DEVS" ]; then
  record device OK "adb: $DEVS"
  say "   adb 设备：$DEVS（Android 模式）"
elif [ -n "$FB_DEVS" ]; then
  record device OK "fastboot: $FB_DEVS"
  say "   fastboot 设备：$FB_DEVS（fastboot 模式；本次只采集 fastboot 侧）"
  FASTBOOT_ONLY=1
else
  record device FAIL "adb 与 fastboot 都看不到设备"
  say "！adb 与 fastboot 都没有设备。请解锁屏幕允许 USB 调试（或让平板进 fastboot）后重跑。"
fi

ROOT_CMD="${ROOT_CMD:-}"
if [ -z "$ROOT_CMD" ] && [ -n "$DEVS" ]; then
  if "$ADB" shell "su -c id" 2>/dev/null | grep -q "uid=0"; then ROOT_CMD="su -c"
  elif "$ADB" shell "id" 2>/dev/null | grep -q "uid=0"; then ROOT_CMD=""
  else ROOT_CMD="__NO_ROOT__"; fi
fi
say "   root 方式：${ROOT_CMD:-<未知>}"
# 关键测量项（用户/lead 已确认的两项 + LBA 块大小）：只有这些真的取到，脚本才算成功
CRITICAL_MEASURED=0

sh_() { # 在设备上执行（自动套 root），失败返回非 0 但不会中断脚本
  local cmd="$1"
  if [ "$ROOT_CMD" = "__NO_ROOT__" ]; then "$ADB" shell "$cmd" 2>&1; else "$ADB" shell "$ROOT_CMD \"$cmd\"" 2>&1; fi
}
grab() { # grab <名称> <设备命令> [--root] [--critical]
  local name="$1" cmd="$2"; shift 2
  local mode="" critical=0
  for a in "$@"; do
    [ "$a" = "--root" ] && mode="--root"
    [ "$a" = "--critical" ] && critical=1
  done
  local f="$RAW/$name.txt"
  if [ "$mode" = "--root" ] && [ "$ROOT_CMD" = "__NO_ROOT__" ]; then
    say "   [$name] 跳过：需要 root"; record "$name" SKIP "需要 root"; return 0
  fi
  if sh_ "$cmd" > "$f" 2>&1 && [ -s "$f" ] && ! grep -qiE "not found|no such file|permission denied|inaccessible" "$f"; then
    say "   [$name] ok ($(wc -c < "$f") B)"; record "$name" OK "$(head -c 80 "$f" | tr '\n' ' ')"
  else
    say "   [$name] FAIL"; record "$name" FAIL "$(head -c 80 "$f" 2>/dev/null | tr '\n' ' ')"
  fi
}

# ---- 1. fastboot 侧（可选） ---------------------------------------------------
say "== 1. fastboot 变量（只在确实处于 fastboot 模式时采集）=="
if [ "$SKIP_FASTBOOT" = "1" ]; then
  record fastboot_product SKIP "SKIP_FASTBOOT=1"
  say "   已按 SKIP_FASTBOOT=1 跳过"
elif command -v "$FASTBOOT" >/dev/null 2>&1; then
  if [ -z "$FB_DEVS" ]; then
    record fastboot_product SKIP "没有 fastboot 设备；getvar 会永久等待，已跳过（需要时让平板进 fastboot 再跑 --fastboot-only）"
    say "   跳过：没有 fastboot 设备（避免 getvar 挂住）"
  elif tmo "$FASTBOOT" getvar product > "$RAW/fastboot-product.txt" 2>&1 && grep -qi "product" "$RAW/fastboot-product.txt"; then
    record fastboot_product OK "$(grep -i product "$RAW/fastboot-product.txt" | head -1)"
    tmo "$FASTBOOT" getvar all > "$RAW/fastboot-all.txt" 2>&1 || true
    grep -iE "partition-size:userdata|current-slot|unlocked|slot-count|max-download-size" "$RAW/fastboot-all.txt" > "$RAW/fastboot-key.txt" 2>&1 || true
    say "   fastboot product: $(grep -i 'product' "$RAW/fastboot-product.txt" | head -1)"
  else
    record fastboot_product SKIP "fastboot getvar 超时或失败（设备可能已离开 fastboot）"
    say "   fastboot getvar 超时/失败"
  fi
else
  record fastboot_product SKIP "未安装 fastboot"
fi

# ---- 2. Android 侧采集 -------------------------------------------------------
if [ -n "$DEVS" ] && [ "$FASTBOOT_ONLY" != "1" ]; then
  say "== 2. Android 侧采集 =="
  grab compatible          "cat /sys/firmware/devicetree/base/compatible" --root
  grab wm_size             "wm size"
  grab display_info        "dumpsys display | head -60"
  grab input_devices       "getevent -pl"
  grab dmesg_touch         "dmesg | grep -iE 'novatek|nt36|goodix|touch|fts'"
  grab meminfo             "cat /proc/meminfo"
  grab iomem               "cat /proc/iomem" --root --critical
  grab cmdline             "cat /proc/cmdline"
  grab partitions          "cat /proc/partitions"
  grab byname              "ls -l /dev/block/by-name/"
  grab sgdisk              "sgdisk -p /dev/block/sda" --root --critical
  grab userdata_blocksize  "blockdev --getss /dev/block/by-name/userdata" --root --critical
  grab userdata_size       "blockdev --getsize64 /dev/block/by-name/userdata" --root
  grab df_data             "df -h /data"
  grab ufs_version         "cat /sys/class/scsi_host/host0/version 2>/dev/null; dmesg | grep -iE 'ufs|ufshc' | head -30"
  grab panel_dt            "ls /sys/firmware/devicetree/base/soc*/ 2>/dev/null | head -40"
  grab prop_device         "getprop ro.product.device"
  grab prop_model          "getprop ro.product.model"
  grab prop_build          "getprop ro.build.display.id"
  grab prop_hardware       "getprop ro.boot.hardware"
  # dtb / dtbo dump（写 /data/local/tmp，之后可删）
  say "   [dtb/dtbo] dump 到设备 /data/local/tmp …"
  sh_ "dd if=/dev/block/by-name/dtb  of=/data/local/tmp/enuma-dtb.img  bs=4096" > "$RAW/dump-dtb.txt" 2>&1 \
    && record dump_dtb OK "dd ok" || { record dump_dtb FAIL "见 raw/dump-dtb.txt"; }
  sh_ "dd if=/dev/block/by-name/dtbo of=/data/local/tmp/enuma-dtbo.img bs=4096" > "$RAW/dump-dtbo.txt" 2>&1 \
    && record dump_dtbo OK "dd ok" || { record dump_dtbo FAIL "见 raw/dump-dtbo.txt"; }
  "$ADB" pull /data/local/tmp/enuma-dtb.img  "$OUT/enuma-dtb.img"  >/dev/null 2>&1 && record pull_dtb OK "$(wc -c < "$OUT/enuma-dtb.img" 2>/dev/null) B" || record pull_dtb FAIL "pull 失败"
  "$ADB" pull /data/local/tmp/enuma-dtbo.img "$OUT/enuma-dtbo.img" >/dev/null 2>&1 && record pull_dtbo OK "$(wc -c < "$OUT/enuma-dtbo.img" 2>/dev/null) B" || record pull_dtbo FAIL "pull 失败"
  if command -v dtc >/dev/null 2>&1; then
    for f in enuma-dtb enuma-dtbo; do
      [ -s "$OUT/$f.img" ] && dtc -I dtb -O dts "$OUT/$f.img" > "$OUT/$f.dts" 2>"$RAW/$f-dtc.txt" \
        && record "${f}_dts" OK "已反编译" || record "${f}_dts" FAIL "dtc 失败（见 raw/${f}-dtc.txt）"
    done
  else
    record dtc SKIP "本机没有 dtc（macOS: brew install dtc）"
  fi
fi

# ---- 2.5 关键项内容校验（空输出不算成功） -------------------------------------
critical_ok() {
  local n=0
  grep -q "userdata" "$RAW/sgdisk.txt" 2>/dev/null && n=$((n + 1))
  grep -q "System RAM" "$RAW/iomem.txt" 2>/dev/null && n=$((n + 1))
  grep -qE "^[0-9]+$" "$RAW/userdata_blocksize.txt" 2>/dev/null && n=$((n + 1))
  echo "$n"
}
CRITICAL_MEASURED=$(critical_ok)
say "== 2.5 关键测量项校验：$CRITICAL_MEASURED / 3（sgdisk / iomem / blockdev）=="
record critical_measurements "$([ "$CRITICAL_MEASURED" -ge 1 ] && echo OK || echo FAIL)" "$CRITICAL_MEASURED of 3"

# ---- 3. 汇总 -----------------------------------------------------------------
say "== 3. 生成 summary.json 与 NOTES.md =="
python3 - "$OUT" "$STATUS" <<'PY'
import json,sys,os,re,pathlib
out=pathlib.Path(sys.argv[1]); status=pathlib.Path(sys.argv[2])
items=[]
for line in status.read_text(encoding='utf-8',errors='ignore').splitlines():
    p=line.split('\t')
    if len(p)>=3: items.append({'item':p[0],'status':p[1],'note':p[2]})
raw=out/'raw'
def rd(name):
    f=raw/f'{name}.txt'
    return f.read_text(encoding='utf-8',errors='ignore') if f.exists() else ''
def first(pat,text,cast=str):
    m=re.search(pat,text,re.M|re.I)
    return cast(m.group(1)) if m else None
s={}
# userdata GPT（sgdisk 优先：6 列十进制扇区；退回 blockdev 的块大小/容量）
sg=rd('sgdisk'); m=re.search(r'^\s*\d+\s+(\d+)\s+(\d+)\s+.*?\buserdata\b\s*$',sg,re.M)
if m:
    s['userdata_first_lba']=hex(int(m.group(1))); s['userdata_last_lba']=hex(int(m.group(2)))
    s['userdata_first_lba_dec']=int(m.group(1)); s['userdata_last_lba_dec']=int(m.group(2))
bs=first(r'^(\d+)\s*$',rd('userdata_blocksize'),int)
if bs:
    s['userdata_lba_bytes']=bs; s['userdata_lba_bytes_source']='measured (blockdev --getss)'
else:
    # 绝不默认成 4096 冒充实测：没量到就不写这个键
    s['userdata_lba_bytes']=None; s['userdata_lba_bytes_source']='NOT MEASURED (needs root: blockdev --getss)'
sz=first(r'^(\d+)\s*$',rd('userdata_size'),int)
if sz: s['userdata_bytes']=sz
# 内存
s['memtotal_kb']=first(r'MemTotal:\s*(\d+)\s*kB',rd('meminfo'),int)
iom=rd('iomem')
s['iomem_system_ram_entries']=re.findall(r'^([0-9a-f]{8,})-([0-9a-f]{8,})\s*:\s*System RAM',iom,re.M)[:8]
# 机型 / 面板 / 触摸
s['product_device']=(rd('prop_device').strip().splitlines() or [None])[0]
s['product_model']=(rd('prop_model').strip().splitlines() or [None])[0]
s['build_id']=(rd('prop_build').strip().splitlines() or [None])[0]
s['wm_size']=first(r'Physical size:\s*(\d+x\d+)',rd('wm_size'))
s['compatible_root']=rd('compatible').strip().replace('\x00',' ').replace('\n',' ')
s['touch_hits']=[l for l in rd('dmesg_touch').splitlines() if re.search(r'novatek|nt36|fts|goodix',l,re.I)][:5]
s['fastboot_product']=first(r'product:\s*(\S+)',rd('fastboot-product'))
s['compatible_root']=rd('compatible').strip().replace('\x00',' ')
(out/'summary.json').write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
ok=[i for i in items if i['status']=='OK']; bad=[i for i in items if i['status']=='FAIL']; sk=[i for i in items if i['status']=='SKIP']
notes=[f"# enuma 上机采集报告","",f"输出目录：`{out}`","",
       f"成功 {len(ok)} / 失败 {len(bad)} / 跳过 {len(sk)}","","## 关键值（自动提取）","",
       "```json",json.dumps(s,indent=2,ensure_ascii=False),"```","","## 需要人工补的","",
       "- 实际面板变体：看 `raw/dmesg_touch.txt` 与 `enuma-dtbo.dts` 里的 panel 节点（k81_42 还是 k81_35）",
       "- UFS 实测 gear/lane：看 `raw/ufs_version.txt`","",
       "## 失败项",""]+[f"- {i['item']}: {i['note']}" for i in bad]+["","## 跳过项",""]+[f"- {i['item']}: {i['note']}" for i in sk]+["",
       "## 下一步","","```sh","python3 scripts/enuma-fill.py --probe "+str(out)+" --skeleton UniXNU-enuma-skeleton","```"]
(out/'NOTES.md').write_text('\n'.join(notes)+'\n',encoding='utf-8')
print(f"summary.json: {json.dumps(s,ensure_ascii=False)[:300]}")
PY

say ""
say "== 关键值（可直接抄进 board.toml，或直接用 enuma-fill.py 自动回填）=="
python3 - "$OUT" <<'PYEOF'
import json, pathlib, sys
s = json.loads((pathlib.Path(sys.argv[1]) / "summary.json").read_text())
first, last, bs = s.get("userdata_first_lba"), s.get("userdata_last_lba"), s.get("userdata_lba_bytes")
print(f"  [userdata] first_lba = {first}   last_lba = {last}   lba_bytes = {bs}")
banks = s.get("iomem_system_ram_entries") or []
print(f"  iomem System RAM banks = {banks}")
if banks:
    ends = []
    for a, b in banks:
        try: ends.append(int(b, 16))
        except Exception: pass
    if ends:
        size = max(ends) + 1 - 0x80000000
        print(f"  → 建议 [payload] dram_size = {hex(size)}（= 最高 bank 末尾 − 0x80000000，请人工复核）")
print(f"  fastboot product = {s.get('fastboot_product')}   机型 = {s.get('product_device')}   面板 = {s.get('wm_size')}")
PYEOF
say ""
say "完成。输出在：$OUT"
say "  人读：$OUT/NOTES.md"
say "  机器读：$OUT/summary.json"
say "  下一步：python3 scripts/enuma-fill.py --probe $OUT --skeleton UniXNU-enuma-skeleton"
say ""
if [ -z "$DEVS" ] && [ -z "$FB_DEVS" ]; then
  say "！！adb 与 fastboot 都没有发现设备。"
  say "   退出码 3 —— 这不是成功。请接好平板、允许 USB 调试后重跑。"
  exit 3
fi
if [ "$FASTBOOT_ONLY" = "1" ]; then
  say ""
  say "本次是【fastboot 模式】采集：变量已拿到，但 Android 侧的数值（userdata LBA、/proc/iomem）"
  say "必须让平板正常启动 Android 后再跑一次本脚本。"
  say "   退出码 0（本次 pass 成功）。"
  exit 0
fi
if [ "$CRITICAL_MEASURED" -lt 1 ]; then
  say "！！关键测量（sgdisk / /proc/iomem / blockdev）一项都没拿到——通常是缺少 root。"
  say "   退出码 4 —— 请先 root（Magisk/KernelSU）或用 ROOT_CMD=\"su -c\" 指定提权方式后重跑。"
  exit 4
fi
say "注意：若 fastboot 变量显示 SKIP，请把平板重启到 fastboot 模式后重跑本脚本（只读，不改任何分区）。"