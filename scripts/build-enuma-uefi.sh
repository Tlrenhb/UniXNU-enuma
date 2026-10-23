#!/bin/bash
# One firmware round on an enuma in fastboot (skeleton of UniXNU-pipa's uefi-round.sh).
#
# Flow: build the firmware + a menu-only boot image, `fastboot boot` it, wait for the tablet to
# return to fastboot by itself (the UEFI port's RamoopsLogLib restarts into fastboot on an ASSERT),
# then fetch the log with the log reader and print its end.
#
# TODO(enuma): fill the serial/product, the UEFI tree path and the log reader, then delete this note.
#   - SERIAL:      ./scripts/build-enuma-uefi.sh  (or export ENUMA_SERIAL=...)
#   - product:     fastboot getvar product   -> expect "enuma"   (pipa checks "product: pipa")
#   - uefi tree:   ../uefi-enuma  (fork of xVanTuring/uefi-pipa, enumaPkg)
#   - log reader:  port scripts/read-pipa-log.py from xVanTuring/UniXNU-pipa
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
unixnu=$(cd "$here/../UniXNU" && pwd)
uefi=${UNIXNU_UEFI:-$(cd "$here/../uefi-enuma" && pwd)}
serial=${ENUMA_SERIAL:-}
build=1
[ "${1:-}" = --no-build ] && { build=0; shift; }
lines=${1:-60}
image=$unixnu/out/enuma/boot/boot-menu-round.img

if [ $build = 1 ]; then
  while pgrep -f "unixnu (build|bootimg|fetch|defconfig)" > /dev/null; do echo "another unixnu is running, waiting"; sleep 10; done
  state=$(cat "$unixnu/out/state.json" 2>/dev/null)
  log=$unixnu/out/enuma/logs/uefi-round-build.log
  (cd "$unixnu" && UNIXNU_UEFI=$uefi ./unixnu build firmware -D "$here" > "$log" 2>&1 &&
    UNIXNU_UEFI=$uefi ./unixnu bootimg -D "$here" --dtb "$uefi/Resources/DTBs/enuma.dtb" --ramdisk none -o "$image" >> "$log" 2>&1)
  rc=$?
  [ -n "$state" ] && printf '%s\n' "$state" > "$unixnu/out/state.json"
  if [ $rc != 0 ]; then
    grep -E "error|Error" "$log" | grep -v PcdDebug | head -20
    echo "BUILD FAILED, see $log"; exit 1
  fi
fi

[ -n "$serial" ] || { echo "set ENUMA_SERIAL (fastboot devices)"; exit 2; }
fastboot devices | grep -q "^$serial" || { echo "enuma ($serial) is not in fastboot"; exit 2; }
[ "$(timeout 20 fastboot -s "$serial" getvar product 2>&1 | head -1)" = "product: enuma" ] \
  || { echo "$serial is not an enuma (fastboot getvar product)"; exit 2; }
echo "image $(shasum -a 256 "$image" | cut -c1-16)"
fastboot -s "$serial" boot "$image" > /dev/null 2>&1
while fastboot devices | grep -q "^$serial"; do sleep 1; done
for _ in $(seq 1 180); do fastboot devices | grep -q "^$serial" && break; sleep 1; done
if ! fastboot devices | grep -q "^$serial"; then
  echo "tablet did not return to fastboot; hold Volume Down + Power and retry"; exit 3
fi
echo "back in fastboot; TODO(enuma): fetch the firmware log here (port read-pipa-log.py)"
exit 0
