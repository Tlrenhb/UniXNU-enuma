#!/usr/bin/env bash
# Produce the prelink candidate the storage audit expects.
#
# audit-storage-interface.py reads build/xnu-prelink/native-apfs-load-positive.macho and its .json plan:
# the candidate's module extents are how the original IOStorageFamily addresses are located, and
# build-* bindings refuse to run without them. Format 2's release writes its prelink output to
# <out>/kernel/kernel.macho instead, so the candidate is emitted here with the same roots the release
# uses (extracted from its own emit_prelink invocation; keep in sync if the OS profile changes).
set -euo pipefail
tree=${1:?usage: make-prelink-candidate.sh <assembled tree>}
cd "$tree"
kernel=build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped
prelinked=$(ls ../ipsw/*/kernelcache.decompressed | head -1)
mkdir -p build/xnu-prelink
rm -f build/xnu-prelink/native-apfs-load-positive.macho
python3 kits/XnuPrelink/emit_prelink.py \
  --kernel "$kernel" \
  --prelinked "$prelinked" \
  --slide=-0x1000000 \
  --root com.apple.AppleFSCompression.AppleFSCompressionTypeZlib \
  --root com.apple.driver.AppleARMPlatform \
  --root com.apple.driver.AppleARMWatchdogTimer \
  --root com.apple.driver.AppleAudioClockLibs \
  --root com.apple.driver.AppleEffaceableStorage \
  --root com.apple.driver.AppleIISController \
  --root com.apple.driver.AppleM2ScalerCSCDriver \
  --root com.apple.driver.AppleMobileFileIntegrity \
  --root com.apple.driver.ApplePMGR \
  --root com.apple.driver.IODARTFamily \
  --root com.apple.filesystems.apfs \
  --root com.apple.iokit.CoreAnalyticsFamily \
  --root com.apple.iokit.IOAVFamily \
  --root com.apple.iokit.IOAudio2Family \
  --root com.apple.iokit.IOCECFamily \
  --root com.apple.iokit.IOCryptoAcceleratorFamily \
  --root com.apple.iokit.IOGPUFamily \
  --root com.apple.iokit.IOHDCPFamily \
  --root com.apple.iokit.IOMobileGraphicsFamily \
  --root com.apple.iokit.IOReportFamily \
  --root com.apple.iokit.IOSlowAdaptiveClockingFamily \
  --root com.apple.iokit.IOStorageFamily \
  --root com.apple.iokit.IOSurface \
  --root com.apple.kec.Libm \
  --root com.apple.kec.corecrypto \
  --root com.apple.kext.AppleMatch \
  --root com.apple.kext.CoreTrust \
  --root com.apple.security.AppleImage4 \
  --root com.apple.security.sandbox \
  --root com.apple.iokit.IOHIDFamily \
  --root com.apple.iokit.IOHIDEventDriver \
  --early-security-init com.apple.driver.AppleMobileFileIntegrity=0xfffffff005ab4ae0 \
  --output build/xnu-prelink/native-apfs-load-positive.macho
ls -l build/xnu-prelink/native-apfs-load-positive.macho build/xnu-prelink/native-apfs-load-positive.json
