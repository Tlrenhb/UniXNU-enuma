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
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <libkern/libkern.h>
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


// ---------------------------------------------------------------------------------------------
// One SCSI READ(10) through our own UTRL/UTRD (native-ufs-probe>=3), LBA 0 on LUN 0.
//
// Layouts are the Linux/vendor ones (drivers/scsi/ufs/ufshci.h, ufshcd.c):
//   UTRD   32 B: header DW0-3, UCD base lo/hi (DW4-5), then DW6 = {rsp_upiu_length, rsp_upiu_offset}
//                and DW7 = {prd_table_length, prd_table_offset}; the two offsets are in DWORDs.
//   UCD    4 KiB: command UPIU at +0 (512 B), response UPIU at +512 (512 B), PRDT at +1024.
//   PRDT   16 B per entry: base_lo, base_hi, reserved, size (bytes-1).
//   Command UPIU 32 B: 12-byte header, expected data length, 16-byte CDB.
// Offsets used: rsp offset 512>>2 = 128, rsp length 512>>2 = 128, prd offset 1024>>2 = 256.
// Registers: UTRLBA 0x50, UTRLBAU 0x54, UTRLDBR 0x58, IS 0x20 (bit0 UTRCS), UTRLRSR 0x60.
// Nothing is written to storage: this is READ(10) only.
struct ReadReport {
  uint32_t ocs;        // response UPIU byte 2
  uint32_t status;     // response UPIU byte 3
  uint32_t residual;   // response UPIU bytes 12-15 (big endian)
  uint32_t is_before, is_after, hcs, hce, utrldbr, utrlrsr;
  uint32_t utrba_lo, utrba_hi, dma_lo, dma_hi;
  uint8_t first[16];
};

static uint32_t swap32 (uint32_t v)
{
  return ((v & 0xffU) << 24) | ((v & 0xff00U) << 8) | ((v >> 8) & 0xff00U) | ((v >> 24) & 0xffU);
}

static void cacheClean (const void *start, size_t length)
{
  uintptr_t a = reinterpret_cast<uintptr_t> (start) & ~uintptr_t (63);
  for (uintptr_t end = reinterpret_cast<uintptr_t> (start) + length; a < end; a += 64) {
    __asm__ volatile ("dc civac, %0" :: "r" (a) : "memory");
  }
  __asm__ volatile ("dsb sy" ::: "memory");
}

static bool readSector0 (volatile uint32_t *hc, ReadReport *out)
{
  const uint64_t kDmaBytes = 0x3000;             // UTRL 1 KiB + UCD 4 KiB + data 4 KiB, page aligned
  IOBufferMemoryDescriptor *dma = IOBufferMemoryDescriptor::inTaskWithOptions (
    kernel_task,
    static_cast<IOOptionBits> (kIODirectionInOut) | static_cast<IOOptionBits> (kIOMemoryPhysicallyContiguous) |
      static_cast<IOOptionBits> (kIOMemoryMapperNone),
    kDmaBytes, 4096);
  if (!dma) {
    return false;
  }
  uint8_t *virt = static_cast<uint8_t *> (dma->getBytesNoCopy ());
  IOByteCount contiguous = 0;
  uint64_t phys = dma->getPhysicalSegment (0, &contiguous, kIOMemoryMapperNone);
  if (!virt || !phys || (phys & 4095) || contiguous < kDmaBytes) {
    return false;
  }
  bzero (virt, kDmaBytes);

  uint8_t *utrl = virt;                 // 0x0000, 1 KiB aligned, 32 slots
  uint8_t *ucd = virt + 0x1000;         // 0x1000
  uint8_t *data = virt + 0x2000;        // 0x2000
  uint64_t utrl_phys = phys;
  uint64_t ucd_phys = phys + 0x1000;
  uint64_t data_phys = phys + 0x2000;

  // ---- command UPIU (READ(10), LBA 0, 8 blocks = 4 KiB)
  uint8_t *cmd = ucd;
  cmd[0] = 0x01;                        // UPIU transaction: command
  cmd[1] = 0x40;                        // flags: R (device -> host)
  cmd[2] = 0x00;                        // LUN 0
  cmd[3] = 0x01;                        // task tag
  cmd[4] = 0x00;                        // IID
  cmd[5] = 0x00;                        // command set: SCSI
  cmd[6] = 0x00;
  cmd[7] = 0x00;
  cmd[8] = 0x00;                        // EHS length 0
  cmd[9] = 0x00; cmd[10] = 0x00; cmd[11] = 0x00;   // data segment length 0
  cmd[12] = 0x00; cmd[13] = 0x00; cmd[14] = 0x10; cmd[15] = 0x00;   // 4096, big endian
  uint8_t *cdb = cmd + 16;
  cdb[0] = 0x28;                        // READ(10)
  cdb[1] = 0x00;
  cdb[2] = 0; cdb[3] = 0; cdb[4] = 0; cdb[5] = 0;    // LBA 0
  cdb[6] = 0x00;
  cdb[7] = 0x00; cdb[8] = 0x08;         // 8 blocks
  cdb[9] = 0x00;

  // ---- PRDT entry 0 -> data buffer
  uint8_t *prdt = ucd + 1024;
  *reinterpret_cast<uint32_t *> (prdt + 0) = static_cast<uint32_t> (data_phys & 0xffffffffU);
  *reinterpret_cast<uint32_t *> (prdt + 4) = static_cast<uint32_t> (data_phys >> 32);
  *reinterpret_cast<uint32_t *> (prdt + 8) = 0;
  *reinterpret_cast<uint32_t *> (prdt + 12) = 4096 - 1;   // size field is bytes-1

  // ---- UTRD slot 0
  uint8_t *utrd = utrl;
  *reinterpret_cast<uint32_t *> (utrd + 16) = static_cast<uint32_t> (ucd_phys & 0xffffffffU);
  *reinterpret_cast<uint32_t *> (utrd + 20) = static_cast<uint32_t> (ucd_phys >> 32);
  *reinterpret_cast<uint16_t *> (utrd + 24) = 128;    // response UPIU length (dwords)
  *reinterpret_cast<uint16_t *> (utrd + 26) = 128;    // response UPIU offset (dwords)
  *reinterpret_cast<uint16_t *> (utrd + 28) = 1;      // PRDT entries
  *reinterpret_cast<uint16_t *> (utrd + 30) = 256;    // PRDT offset (dwords)

  cacheClean (virt, kDmaBytes);

  out->is_before = hc[0x20 >> 2];
  out->hcs = hc[0x30 >> 2];
  out->hce = hc[0x34 >> 2];
  out->dma_lo = static_cast<uint32_t> (phys & 0xffffffffU);
  out->dma_hi = static_cast<uint32_t> (phys >> 32);

  // ---- program the list, start it, ring the doorbell
  hc[0x50 >> 2] = static_cast<uint32_t> (utrl_phys & 0xffffffffU);
  hc[0x54 >> 2] = static_cast<uint32_t> (utrl_phys >> 32);
  __asm__ volatile ("dsb sy" ::: "memory");
  hc[0x60 >> 2] = 1;                                  // UTRLRSR: run
  __asm__ volatile ("dsb sy" ::: "memory");
  hc[0x20 >> 2] = out->is_before;                     // clear pending status bits
  hc[0x58 >> 2] = 1;                                  // doorbell: slot 0
  __asm__ volatile ("dsb sy" ::: "memory");

  // ---- poll for completion (bit0 UTRCS), bounded
  uint32_t is = 0;
  for (unsigned spin = 0; spin < 2000000U; ++spin) {
    is = hc[0x20 >> 2];
    if (is & 0x1U) {
      break;
    }
    __asm__ volatile ("yield" ::: "memory");
  }
  __asm__ volatile ("dsb sy" ::: "memory");
  out->is_after = is;
  out->utrldbr = hc[0x58 >> 2];
  out->utrlrsr = hc[0x60 >> 2];
  out->utrba_lo = hc[0x50 >> 2];
  out->utrba_hi = hc[0x54 >> 2];

  // ---- read back the response UPIU and the data
  __asm__ volatile ("dsb sy" ::: "memory");
  hc[0x20 >> 2] = 0xffffffffU;                        // clear all status bits
  const uint8_t *rsp = ucd + 512;
  out->ocs = rsp[2];
  out->status = rsp[3];
  out->residual = swap32 (*reinterpret_cast<const uint32_t *> (rsp + 12));
  for (unsigned i = 0; i < sizeof (out->first); ++i) {
    out->first[i] = data[i];
  }
  return true;
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

  // mode 3 also issues one READ(10) through our own UTRL/UTRD and reports what came back.
  ReadReport report = {};
  bool    didRead = false;
  if (mode >= 3) {
    volatile uint32_t *hc = mapWindow (UFS_HC_BASE, UFS_HC_SIZE);
    if (hc) {
      didRead = readSector0 (hc, &report);
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
         "pfr0=%016llx pfr1=%016llx mmfr0=%016llx ctr=%08llx\n"
         "read=%u ocs=%u status=%u resid=%u\n"
         "is_before=%08x is_after=%08x dbr=%08x rsr=%08x\n"
         "utrba=%08x%08x dma=%08x%08x\n"
         "sector0=%02x%02x%02x%02x %02x%02x%02x%02x %02x%02x%02x%02x %02x%02x%02x%02x",
         gdsc, axi, ahb, unipro, txsym, rxsym, aux, aggre,
         powered ? 1u : 0u, busClocks ? 1u : 0u, hcRead, cap, ver, hcs, hce, is, utrlba,
         isar1, isar2, apa, api, apa3,
         readMidr (), readRevIdr (), readIsar0 (), readPfr0 (), readPfr1 (), readMfr0 (), readCtr (),
         didRead ? 1u : 0u, report.ocs, report.status, report.residual,
         report.is_before, report.is_after, report.utrldbr, report.utrlrsr,
         report.utrba_hi, report.utrba_lo, report.dma_hi, report.dma_lo,
         report.first[0], report.first[1], report.first[2], report.first[3],
         report.first[4], report.first[5], report.first[6], report.first[7],
         report.first[8], report.first[9], report.first[10], report.first[11],
         report.first[12], report.first[13], report.first[14], report.first[15]);
  return false;
}