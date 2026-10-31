// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Link-time bindings for the IOStorageFamily symbols that the vendored ShengUfs block
// personality references (NativeUfsBlock : IOBlockStorageDevice).
//
// Why this file exists: IOBlockStorageDevice lives in the com.apple.iokit.IOStorageFamily kext
// inside the prelinked kernel collection, and its symbols are not exported to the kernel link, so
// libShengUfs.a comes up with four undefined references:
//
//   __ZTV20IOBlockStorageDevice                      (vtable)
//   __ZN20IOBlockStorageDeviceD1Ev / D2Ev            (destructors)
//   __ZN20IOBlockStorageDevice10getPropertyEPK8OSSymbol
//
// UniXNU's own solution is an "original bindings" object that redirects those names to the real
// addresses inside the collection (scripts/build-virtio-mmio-bindings.py, kits/NativePrelink).
// That audit needs the qualified storage ABI inputs and is the next stage of this port.
//
// Until then this stage is READ ONLY and never constructs the block personality: startRoot() bails
// out early when ENUMA_UFS_STAGE_READONLY is defined, so none of these stubs can be reached. They
// exist to satisfy the linker, and they must be deleted - not kept - once the real bindings land.
#include <IOKit/IOService.h>

extern "C" {

// A zero-filled vtable of generous size: only ever referenced from NativeUfsBlock's constructor.
char __ZTV20IOBlockStorageDevice[512] __attribute__ ((aligned (8)));

void __ZN20IOBlockStorageDeviceD1Ev (void *)
{
}

void __ZN20IOBlockStorageDeviceD2Ev (void *)
{
}

OSObject *__ZN20IOBlockStorageDevice10getPropertyEPK8OSSymbol (void *, const OSSymbol *)
{
  return nullptr;
}

}  // extern "C"