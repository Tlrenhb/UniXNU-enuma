// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Minimal, read-only UFS register probe for enuma (SM8250).
//
// Why this exists: the full ShengUfs driver (drivers/ufs/NativeUfs.cpp, vendored) derives its block
// personality from IOBlockStorageDevice, whose symbols live in the IOStorageFamily kext inside the
// prelinked kernel collection and are not exported to the kernel link. Supplying stand-ins did not
// resolve them (rounds 6-9), so the real driver waits for the original-bindings work
// (scripts/build-virtio-mmio-bindings.py + kits/NativePrelink).
//
// This file is the class the personality table expects - CONFIG_XNU_NATIVE_UFS=1 installs
// "IOClass = NativeUfs, IOProviderClass = IOPlatformExpertDevice" - so it needs no kernel patch and
// only reads what the bootloader left behind. With native-ufs-probe=2 it dumps the GCC branch
// state, the UFS PHY GDSC and the UFSHCI identification registers through panic(), which the panic
// screen draws. It never writes a register and never touches storage.
//
// Register values and windows: see GccUfs.h in this directory (every one cited to gcc-kona.c,
// kona.dtsi:3069 or the merged enuma device tree).
#include <IOKit/IOService.h>
#include <IOKit/IOPlatformExpert.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <pexpert/pexpert.h>
#include "GccUfs.h"

#define ENUMA_UFS_PROBE_MODE 2

class NativeUfs : public IOService {
  OSDeclareDefaultStructors (NativeUfs)
public:
  IOService *probe (IOService *provider, SInt32 *score) override;
  bool start (IOService *provider) override;
};

OSDefineMetaClassAndStructors (NativeUfs, IOService)

static volatile uint32_t *mapWindow (uint64_t phys, uint64_t length)
{
  IODeviceMemory *memory = IODeviceMemory::withRange (phys, length);
  if (!memory) {
    return nullptr;
  }
  // Deliberately leaked: the probe panics right after reading, so there is nothing to unwind.
  IOMemoryMap *mapping = memory->map (kIOMapAnywhere | kIOMapInhibitCache);
  memory->release ();
  return mapping ? reinterpret_cast<volatile uint32_t *> (mapping->getVirtualAddress ()) : nullptr;
}

static uint32_t at (volatile uint32_t *base, uint64_t absolute, uint64_t window)
{
  return base[static_cast<uint32_t> ((absolute - window) >> 2)];
}

// CPU capability, read alongside the UFS dump: does this SoC implement pointer authentication (the
// arm64e prerequisite)? ID_AA64ISAR1_EL1.APA/API and ID_AA64ISAR2_EL1.APA3 are the fields Apple's
// arm64e kernel relies on. 0 = not implemented, 1 = implemented (Armv8.3), 2 = QARMA3 variant.
static uint64_t idIsar1 (void)
{
  uint64_t v;
  __asm__ volatile ("isb; mrs %0, id_aa64isar1_el1" : "=r" (v));
  return v;
}

static uint64_t idIsar2 (void)
{
  uint64_t v;
  __asm__ volatile ("isb; mrs %0, id_aa64isar2_el1" : "=r" (v));
  return v;
}

// Anchor set for the ID-register readings: MIDR_EL1 identifies the core, so a plausible MIDR means
// the surrounding ID_AA64* reads can be trusted. (The first attempt printed an ID_AA64ISAR1_EL1 value
// with reserved field encodings, which is why this check exists.)
#define READ_SYSREG(fn, reg) \
  static uint64_t fn (void) { uint64_t v; __asm__ volatile ("isb; mrs %0, " #reg : "=r" (v)); return v; }
READ_SYSREG (readMidr,   midr_el1)
READ_SYSREG (readIsar0,  id_aa64isar0_el1)
READ_SYSREG (readPfr0,   id_aa64pfr0_el1)
READ_SYSREG (readPfr1,   id_aa64pfr1_el1)
READ_SYSREG (readMfr0,   id_aa64mmfr0_el1)
READ_SYSREG (readCtr,    ctr_el0)
READ_SYSREG (readRevIdr, revidr_el1)
#undef READ_SYSREG

IOService *NativeUfs::probe (IOService *provider, SInt32 *score)
{
  // Second personality: the root device starts before the platform expert publishes IOResources, so
  // this runs before the original AMFI (whose start needs the PMGR nub). Same choice as ShengUfs.
  if (!OSDynamicCast (IOPlatformExpertDevice, provider)) {
    return nullptr;
  }
  if (score) {
    *score = 500;
  }
  return this;
}

bool NativeUfs::start (IOService *provider)
{
  unsigned mode = 0;
  if (!PE_parse_boot_argn ("native-ufs-probe", &mode, sizeof (mode)) || mode < ENUMA_UFS_PROBE_MODE) {
    IOLog ("NativeUfs: probe disabled (native-ufs-probe=%u)\n", mode);
    return IOService::start (provider);
  }

  volatile uint32_t *gcc = mapWindow (GCC_BASE + GCC_BRANCH_WINDOW, 0x1000);
  volatile uint32_t *gdscPage = mapWindow (GCC_BASE + GCC_UFS_PHY_GDSCR_WINDOW, 0x1000);
  if (!gcc || !gdscPage) {
    panic ("NATIVE_UFS inspect: GCC map failed gcc=%p gdsc=%p", (void *) gcc, (void *) gdscPage);
  }

  uint32_t gdsc = at (gcc, GCC_UFS_PHY_GDSCR, GCC_UFS_PHY_GDSCR_WINDOW);
  uint32_t axi = at (gcc, GCC_UFS_PHY_AXI_ENABLE, GCC_BRANCH_WINDOW);
  uint32_t ahb = at (gcc, GCC_UFS_PHY_AHB_ENABLE, GCC_BRANCH_WINDOW);
  uint32_t unipro = at (gcc, GCC_UFS_PHY_UNIPRO_CORE_ENABLE, GCC_BRANCH_WINDOW);
  uint32_t txsym = at (gcc, GCC_UFS_PHY_TX_SYMBOL0_ENABLE, GCC_BRANCH_WINDOW);
  uint32_t rxsym = at (gcc, GCC_UFS_PHY_RX_SYMBOL0_ENABLE, GCC_BRANCH_WINDOW);
  uint32_t aux = at (gcc, GCC_UFS_PHY_PHY_AUX_ENABLE, GCC_BRANCH_WINDOW);
  uint32_t aggre = at (gcc, GCC_AGGRE_UFS_PHY_AXI_ENABLE, GCC_BRANCH_WINDOW);
  (void) gdscPage;

  // GDSCR bit31 PWR_ON; CBCR bit31 CLK_OFF, bit0 enable. Same reading rules as ShengUfs.
  bool powered = (gdsc & 0x80000000U) != 0;
  bool busClocks = ((axi & 0x80000000U) == 0) && (axi & GCC_BRANCH_ENABLE_BIT) && (ahb & GCC_BRANCH_ENABLE_BIT);

  uint32_t cap = 0, ver = 0, is = 0, hcs = 0, hce = 0, utrlba = 0, hcRead = 0;
  if (powered && busClocks) {
    volatile uint32_t *hc = mapWindow (UFS_HC_BASE, UFS_HC_SIZE);
    if (hc) {
      cap = at (hc, UFS_HC_BASE + 0x00, UFS_HC_BASE);
      ver = at (hc, UFS_HC_BASE + 0x08, UFS_HC_BASE);
      is = at (hc, UFS_HC_BASE + 0x20, UFS_HC_BASE);
      hcs = at (hc, UFS_HC_BASE + 0x30, UFS_HC_BASE);
      hce = at (hc, UFS_HC_BASE + 0x34, UFS_HC_BASE);
      utrlba = at (hc, UFS_HC_BASE + 0x50, UFS_HC_BASE);
      hcRead = 1;
    }
  }

  uint64_t isar1 = idIsar1 (), isar2 = idIsar2 ();
  uint32_t apa = static_cast<uint32_t> ((isar1 >> 4) & 0xf);    // APAddr: 1 = PAC in EL1&0
  uint32_t api = static_cast<uint32_t> ((isar1 >> 8) & 0xf);    // APInt
  uint32_t apa3 = static_cast<uint32_t> ((isar2 >> 4) & 0xf);   // QARMA3 variant

  panic ("NATIVE_UFS inspect (read only)\n"
         "gdsc=%08x axi=%08x ahb=%08x\n"
         "unipro=%08x txsym=%08x rxsym=%08x\n"
         "aux=%08x aggre=%08x\n"
         "powered=%u busclocks=%u hc_read=%u\n"
         "cap=%08x ver=%08x hcs=%08x\n"
         "hce=%08x is=%08x utrlba=%08x\n"
         "isar1=%016llx isar2=%016llx apa=%u api=%u apa3=%u\n"
         "midr=%08llx revidr=%016llx isar0=%016llx\n"
         "pfr0=%016llx pfr1=%016llx mmfr0=%016llx ctr=%08llx",
         gdsc, axi, ahb, unipro, txsym, rxsym, aux, aggre,
         powered ? 1u : 0u, busClocks ? 1u : 0u, hcRead, cap, ver, hcs, hce, is, utrlba,
         isar1, isar2, apa, api, apa3,
         readMidr (), readRevIdr (), readIsar0 (), readPfr0 (), readPfr1 (), readMfr0 (), readCtr ());
  return false;
}