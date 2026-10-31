// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Link-time bindings for the IOStorageFamily symbols that the vendored ShengUfs block
// personality references (NativeUfsBlock : IOBlockStorageDevice).
//
// Why: IOBlockStorageDevice lives in the com.apple.iokit.IOStorageFamily kext inside the prelinked
// kernel collection, and none of its symbols are exported to the kernel link. libShengUfs.a
// therefore references eleven of them (the linker's complete list):
//
//   IOBlockStorageDevice::IOBlockStorageDevice(OSMetaClass const*)
//   IOBlockStorageDevice::init(OSDictionary*)
//   IOBlockStorageDevice::getProperty(OSSymbol const*) const
//   IOBlockStorageDevice::setProperties(OSObject*)
//   IOBlockStorageDevice::requestIdle()
//   IOBlockStorageDevice::doSetPriority(IOBlockStorageDeviceExtent*, unsigned, unsigned char)
//   IOBlockStorageDevice::doUnmap(IOBlockStorageDeviceExtent*, unsigned, unsigned)
//   IOBlockStorageDevice::doGetProvisionStatus(u64, u64, unsigned*, IOBlockStorageProvisionDeviceExtent*, u64)
//   IOBlockStorageDevice::~IOBlockStorageDevice()
//   IOBlockStorageDevice::gMetaClass
//   vtable for IOBlockStorageDevice
//
// UniXNU's own answer is an "original bindings" object that redirects exactly these names to their
// real addresses inside the collection (scripts/build-virtio-mmio-bindings.py + kits/NativePrelink,
// fed by a qualified storage ABI audit). That is the next stage of this port.
//
// Until then this stage is READ ONLY: startRoot() returns before it can construct NativeUfsBlock
// (see ENUMA_UFS_STAGE_READONLY in NativeUfs.cpp), so none of these stubs is ever executed. They
// exist only so the kernel links, and this file must be deleted - not extended - once the real
// bindings land.
#include <IOKit/IOService.h>

extern "C" {

// vtable and metaclass storage: only ever touched by NativeUfsBlock's constructor.
char __ZTV20IOBlockStorageDevice[512] __attribute__ ((aligned (8)));

char __ZN20IOBlockStorageDevice10gMetaClassE[512] __attribute__ ((aligned (8)));

void *__ZN20IOBlockStorageDeviceC1EPK11OSMetaClass (void *, const OSMetaClass *)
{
  return nullptr;
}

void *__ZN20IOBlockStorageDeviceC2EPK11OSMetaClass (void *, const OSMetaClass *)
{
  return nullptr;
}

void __ZN20IOBlockStorageDeviceD1Ev (void *)
{
}

void __ZN20IOBlockStorageDeviceD2Ev (void *)
{
}

bool __ZN20IOBlockStorageDevice4initEP12OSDictionary (void *, OSDictionary *)
{
  return false;
}

OSObject *__ZNK20IOBlockStorageDevice11getPropertyEPK8OSSymbol (const void *, const OSSymbol *)
{
  return nullptr;
}

bool __ZN20IOBlockStorageDevice13setPropertiesEP8OSObject (void *, OSObject *)
{
  return false;
}

void __ZN20IOBlockStorageDevice11requestIdleEv (void *)
{
}

IOReturn __ZN20IOBlockStorageDevice13doSetPriorityEP27IOBlockStorageDeviceExtentjh (void *, void *, unsigned int, unsigned char)
{
  return kIOReturnUnsupported;
}

IOReturn __ZN20IOBlockStorageDevice7doUnmapEP27IOBlockStorageDeviceExtentjj (void *, void *, unsigned int, unsigned int)
{
  return kIOReturnUnsupported;
}

IOReturn __ZN20IOBlockStorageDevice20doGetProvisionStatusEyyPjP38IOBlockStorageProvisionDeviceExtenty (
  void *, unsigned long long, unsigned long long, unsigned int *, void *, unsigned long long)
{
  return kIOReturnUnsupported;
}

}  // extern "C"