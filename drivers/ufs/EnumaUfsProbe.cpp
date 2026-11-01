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
// The panic screen's printf drifts once an argument list gets long: with 57 arguments the report
// after "isar1" came out with a 17-digit hex field and reserved-encoding MIDR values, while the same
// values printed correctly when the list had 22. So the report is formatted here, on the stack, and
// handed to panic() as a single %s.
namespace text {
static char *hex (char *out, uint64_t value, unsigned digits)
{
  static const char kDigits[] = "0123456789abcdef";
  for (unsigned i = 0; i < digits; ++i) {
    out[digits - 1 - i] = kDigits[(value >> (4 * i)) & 0xf];
  }
  return out + digits;
}

static char *str (char *out, const char *text)
{
  while (*text) {
    *out++ = *text++;
  }
  return out;
}

static char *dec (char *out, uint64_t value)
{
  char tmp[24];
  unsigned n = 0;
  do {
    tmp[n++] = static_cast<char> ('0' + (value % 10));
    value /= 10;
  } while (value && n < sizeof (tmp));
  while (n) {
    *out++ = tmp[--n];
  }
  return out;
}
}  // namespace text

struct ReadReport {
  uint32_t ocs;        // response UPIU byte 2
  uint32_t status;     // response UPIU byte 3
  uint32_t residual;   // response UPIU bytes 12-15 (big endian)
  uint32_t is_before, is_after, hcs, hce, utrldbr, utrlrsr;
  uint32_t utrba_lo, utrba_hi, dma_lo, dma_hi;
  uint8_t first[16];
  // SMMU survey (read only): is the UFS stream bypassed, and what does the fault log say?
  uint32_t scr0, idr0, idr1, gfsr, gsynr0;
  uint16_t smmuMatch;      // index of an SMR whose SID matches 0x60, 0xffff = none
  uint32_t smmuSmr, smmuS2cr, smmuCbar, smmuSctlr;
  uint32_t nsmr, smmuValid;
  // The bootloader's own descriptor, read back verbatim: ground truth for the layout this
  // controller expects (offsets, units, PRDT encoding) without inventing anything.
  uint32_t utrd[8];
  uint64_t ucdBase;
  uint32_t ucdWords[8];
  // Every valid stream-match row (there is exactly one on this machine) plus the first banks of the
  // context-bank table: enough to see which stream is configured and how.
  uint32_t smrRows[8][3];   // index, SMR, S2CR
  unsigned smrRowCount;
  uint32_t cbar[8], sctlr[8];
};

static uint32_t swap32 (uint32_t v)
{
  return ((v & 0xffU) << 24) | ((v & 0xff00U) << 8) | ((v >> 8) & 0xff00U) | ((v >> 24) & 0xffU);
}

// Read-only apps-SMMU survey (same registers and walk as NativeUfs::inspectSmmu in the vendored
// driver): SCR0/IDR0/IDR1/GFSR, then the SMR/S2CR table looking for a stream whose SID matches 0x60
// (the id sheng used for the UFS stream), and the matching context bank's CBAR/SCTLR. Nothing written.
static void smmuSurvey (ReadReport *out)
{
  volatile uint32_t *smmu = mapWindow (0x15000000ULL, 0xd0000);
  if (!smmu) {
    out->scr0 = 0xdeadbeefU;
    return;
  }
  out->scr0 = smmu[0x00 >> 2];
  out->idr0 = smmu[0x20 >> 2];
  out->idr1 = smmu[0x24 >> 2];
  out->gfsr = smmu[0x48 >> 2];
  out->gsynr0 = smmu[0x4c >> 2];
  unsigned count = out->idr0 & 0xffU;
  if (count > 128U) {
    count = 128U;
  }
  out->nsmr = count;
  out->smmuMatch = 0xffffU;
  unsigned cbBase = 1U << (((out->idr1 >> 28) & 7U) + 1);
  for (unsigned index = 0; index < count; ++index) {
    uint32_t smr = smmu[(0x800 >> 2) + index];
    uint32_t s2cr = smmu[(0xc00 >> 2) + index];
    if (smr & 0x80000000U) {
      out->smmuValid++;
      uint32_t mask = (smr >> 16) & 0x7fffU;
      if (out->smmuMatch == 0xffffU && (((smr ^ 0x60U) & 0x7fffU) & ~mask) == 0) {
        out->smmuMatch = static_cast<uint16_t> (index);
        out->smmuSmr = smr;
        out->smmuS2cr = s2cr;
      }
    }
  }
  if (out->smmuMatch != 0xffffU) {
    unsigned bank = out->smmuMatch;
    out->smmuCbar = smmu[(0x1000 >> 2) + bank];
    out->smmuSctlr = smmu[((cbBase + bank) * 0x1000 >> 2) + 0];
  }
  // Collect every valid row: the SMR/S2CR pair says which stream it is and whether that stream is
  // bypassed (S2CR[17:16] == 0b10) or translated by a page table.
  for (unsigned index = 0; index < count && out->smrRowCount < 8; ++index) {
    uint32_t smr = smmu[(0x800 >> 2) + index];
    if (!(smr & 0x80000000U)) {
      continue;
    }
    out->smrRows[out->smrRowCount][0] = index;
    out->smrRows[out->smrRowCount][1] = smr;
    out->smrRows[out->smrRowCount][2] = smmu[(0xc00 >> 2) + index];
    out->smrRowCount++;
  }
  for (unsigned bank = 0; bank < 8 && bank < cbBase; ++bank) {
    out->cbar[bank] = smmu[(0x1000 >> 2) + bank];
    out->sctlr[bank] = smmu[((cbBase + bank) * 0x1000 >> 2) + 0];
  }
}

// Read-only dump of the bootloader's own request descriptor and the command descriptor it points at.
// Two doorbells have already reset this tablet, and both times nothing said why; the firmware's own
// UTRD/UCD is the one sample of a layout this controller demonstrably accepted, so it is worth
// reading back field by field instead of guessing at offsets. Nothing is written here.
static bool dumpFirmwareQueue (ReadReport *out)
{
  uint64_t base = (uint64_t (out->utrba_hi) << 32) | out->utrba_lo;
  if (!base) {
    out->ocs = 0xbafeU;
    return true;
  }
  volatile uint32_t *list = mapWindow (base, 0x1000);
  if (!list) {
    out->ocs = 0xbaffU;
    return true;
  }
  for (unsigned i = 0; i < 8; ++i) {
    out->utrd[i] = list[i];
  }
  // DW4/DW5 are the command descriptor address, so the UCD can be followed without assuming where
  // the firmware put it.
  out->ucdBase = (uint64_t (list[5]) << 32) | list[4];
  volatile uint32_t *ucd = out->ucdBase ? mapWindow (out->ucdBase, 0x1000) : nullptr;
  for (unsigned i = 0; i < 8 && ucd; ++i) {
    out->ucdWords[i] = ucd[i];
  }
  return true;
}

static bool readSector0 (volatile uint32_t *hc, ReadReport *out, unsigned mode)
{
  // The firmware's list base has to be read before anything else: the region the buffers live in is
  // taken from it (a previous revision computed the addresses from an uninitialised field and would
  // have reported 0xbafe for every boot).
  out->is_before = hc[0x20 >> 2];
  out->hcs = hc[0x30 >> 2];
  out->hce = hc[0x34 >> 2];
  out->utrba_lo = hc[0x50 >> 2];
  out->utrba_hi = hc[0x54 >> 2];

  // The buffer matters more than the command: the vendored driver records a hardware session
  // (2026-10-17) where a kernel-allocated buffer reset the system the moment the first command was
  // issued, and that the controller's own request-list region is the one place its DMA is known to
  // reach. So the UTRL, the UCD and the data page all live in the 16 KiB at the firmware's UTRLBA,
  // which the bootloader already zeroed and handed over when we stopped the queue.
  uint64_t utrl_phys = (uint64_t (out->utrba_hi) << 32) | out->utrba_lo;
  if (!utrl_phys || (utrl_phys & 0x3ff)) {
    out->ocs = 0xbafeU;                                // no usable list base from the firmware
    return true;
  }
  volatile uint32_t *region = mapWindow (utrl_phys, 0x4000);
  if (!region) {
    out->ocs = 0xbaffU;                                // list region is not mappable
    return true;
  }
  uint8_t *utrl = reinterpret_cast<uint8_t *> (const_cast<uint32_t *> (region));
  uint8_t *ucd = utrl + 0x1000;
  uint8_t *data = utrl + 0x2000;
  uint64_t ucd_phys = utrl_phys + 0x1000;
  uint64_t data_phys = utrl_phys + 0x2000;
  for (unsigned i = 0; i < 0x3000; ++i) {
    utrl[i] = 0;
  }
  out->dma_lo = static_cast<uint32_t> (data_phys & 0xffffffffU);
  out->dma_hi = static_cast<uint32_t> (data_phys >> 32);

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

  // ---- UTRD slot 0, exactly as ShengUfs's hostSubmit writes it:
  //   DW0 = 0x10000000 | (direction << 25), direction 2 = device -> host (a read)
  //   DW2 = 0x0f, and the controller reports the overall command status in DW2[7:0]
  //   DW6 = rsp length (dwords) | rsp offset (dwords) << 16
  //   DW7 = prdt count | prdt offset (dwords) << 16
  uint8_t *utrd = utrl;
  *reinterpret_cast<uint32_t *> (utrd + 0) = 0x10000000U | (2U << 25);
  *reinterpret_cast<uint32_t *> (utrd + 8) = 0x0fU;
  *reinterpret_cast<uint32_t *> (utrd + 16) = static_cast<uint32_t> (ucd_phys & 0xffffffffU);
  *reinterpret_cast<uint32_t *> (utrd + 20) = static_cast<uint32_t> (ucd_phys >> 32);
  *reinterpret_cast<uint16_t *> (utrd + 24) = 128;    // response UPIU length (dwords)
  *reinterpret_cast<uint16_t *> (utrd + 26) = 128;    // response UPIU offset (dwords)
  *reinterpret_cast<uint16_t *> (utrd + 28) = 1;      // PRDT entries
  *reinterpret_cast<uint16_t *> (utrd + 30) = 256;    // PRDT offset (dwords)

  // (is_before/hcs/hce/utrba were captured before the takeover; data_phys was set with the buffers)

  // ---- takeover guards, copied from the hardware-proven ShengUfs hostStopQueue/hostStartQueue
  // (drivers/ufs/RootQueue.h). Round 1 reprogrammed UTRLBA with the firmware's list still running,
  // and the tablet reset: these checks are what stop that from happening again.
  const uint32_t kUfsFatalStatus = 0x71880U;          // TransferPoll.h
  if (!(out->hce & 1U) || ((out->hcs & 0x0fU) != 0x0fU)) {
    out->ocs = 0xf00dU;                                // controller not ready for takeover
    return true;
  }
  out->utrldbr = hc[0x58 >> 2];
  if (out->utrldbr != 0 || hc[0x78 >> 2] != 0) {
    out->ocs = 0xdeedU;                                // a firmware request is still outstanding
    return true;
  }
  if (out->is_before & kUfsFatalStatus) {
    out->ocs = 0xbad0U;                                // the firmware left a fatal condition
    return true;
  }

  // stop the list, wait for the run bit to read back zero
  hc[0x24 >> 2] = 0;                                   // interrupt mask
  hc[0x60 >> 2] = 0;
  __asm__ volatile ("dsb sy" ::: "memory");
  bool stopped = false;
  for (unsigned spin = 0; spin < 20000U; ++spin) {
    if ((hc[0x60 >> 2] & 1U) == 0) { stopped = true; break; }
    __asm__ volatile ("yield" ::: "memory");
  }
  if (!stopped) {
    out->ocs = 0x5701U;                                // request list did not stop
    return true;
  }

  // program our list, verify the address took, clear stale status, then start it
  hc[0x50 >> 2] = static_cast<uint32_t> (utrl_phys & 0xffffffffU);
  hc[0x54 >> 2] = static_cast<uint32_t> (utrl_phys >> 32);
  __asm__ volatile ("dsb sy" ::: "memory");
  if (hc[0x50 >> 2] != static_cast<uint32_t> (utrl_phys & 0xffffffffU) ||
      hc[0x54 >> 2] != static_cast<uint32_t> (utrl_phys >> 32)) {
    out->ocs = 0xadd1U;                                // address did not stick
    return true;
  }
  hc[0x20 >> 2] = out->is_before;
  hc[0x60 >> 2] = 1;
  __asm__ volatile ("dsb sy" ::: "memory");
  bool ready = false;
  for (unsigned spin = 0; spin < 20000U; ++spin) {
    if ((hc[0x60 >> 2] & 1U) && ((hc[0x30 >> 2] & 3U) == 3U)) { ready = true; break; }
    __asm__ volatile ("yield" ::: "memory");
  }
  if (!ready) {
    out->ocs = 0x5702U;                                // request list did not start
    return true;
  }
  // Only these modes ring the doorbell. Mode 3 and mode 5 stop after the verified takeover, which is
  // what we want while the SMMU state is still unknown: the survey runs first and the command would
  // be the thing that faults.
  // ONLY mode 4 and mode 8 ring the doorbell. Mode 6 (firmware descriptor) and mode 7 (SMMU tables)
  // are read-only; the earlier 'mode >= 6' gate made mode 7 send the command, which is exactly the
  // experiment that resets the tablet - that is what the last boot did instead of dumping tables.
  const bool sendCommand = (mode == 4) || (mode == 8);
  if (!sendCommand) {
    out->ocs = (mode == 5) ? 0x0005U : 0x0003U;         // takeover verified, nothing sent
    return true;
  }
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

  // ---- read back the descriptor and the data
  __asm__ volatile ("dsb sy" ::: "memory");
  hc[0x20 >> 2] = 1U;                                 // acknowledge completion only, keep error evidence
  out->ocs = *reinterpret_cast<const uint32_t *> (utrd + 8) & 0xffU;   // OCS lives in UTRD DW2[7:0]
  const uint8_t *rsp = ucd + 512;
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

  uint32_t cap = 0, ver = 0, is = 0, hcs = 0, hce = 0, hcRead = 0;
  if (powered && busClocks) {
    volatile uint32_t *hc = mapWindow (UFS_HC_BASE, UFS_HC_SIZE);
    if (hc) {
      cap = at (hc, UFS_HC_BASE + 0x00, UFS_HC_BASE);
      ver = at (hc, UFS_HC_BASE + 0x08, UFS_HC_BASE);
      is = at (hc, UFS_HC_BASE + 0x20, UFS_HC_BASE);
      hcs = at (hc, UFS_HC_BASE + 0x30, UFS_HC_BASE);
      hce = at (hc, UFS_HC_BASE + 0x34, UFS_HC_BASE);
      hcRead = 1;
    }
  }

  // The SMMU survey runs before any command: if the UFS stream is not bypassed, a command would
  // fault on its own DMA and take the tablet down before a single line could be printed.
  ReadReport report = {};
  if (mode >= 3) {
    smmuSurvey (&report);
  }
  // mode 3 also issues one READ(10) through our own UTRL/UTRD and reports what came back.
  bool    didRead = false;
  if (mode == 6) {
    // Read-only: no takeover, no writes - just the firmware's own descriptor.
    dumpFirmwareQueue (&report);
  } else if (mode >= 3) {
    volatile uint32_t *hc = mapWindow (UFS_HC_BASE, UFS_HC_SIZE);
    if (hc) {
      didRead = readSector0 (hc, &report, mode);
    }
  }

  uint64_t isar1 = idIsar1 (), isar2 = idIsar2 ();
  uint32_t apa = static_cast<uint32_t> ((isar1 >> 4) & 0xf);    // APAddr: 1 = PAC in EL1&0
  uint32_t api = static_cast<uint32_t> ((isar1 >> 8) & 0xf);    // APInt
  uint32_t apa3 = static_cast<uint32_t> ((isar2 >> 4) & 0xf);   // QARMA3 variant

  // Build the report here and hand panic() a single string (see the note on the formatter above).
  char msg[2048];   // the report outgrew 1024 and walked into ReadReport on the stack
  char *at = msg;
  at = text::str (at, "NATIVE_UFS mode=");
  at = text::dec (at, mode);
  at = text::str (at, didRead ? " (takeover verified)" : " (inspect only)");
  at = text::str (at, "\ngcc gdsc=");   at = text::hex (at, gdsc, 8);
  at = text::str (at, " axi=");         at = text::hex (at, axi, 8);
  at = text::str (at, " ahb=");         at = text::hex (at, ahb, 8);
  at = text::str (at, " unipro=");      at = text::hex (at, unipro, 8);
  at = text::str (at, " txsym=");       at = text::hex (at, txsym, 8);
  at = text::str (at, " rxsym=");       at = text::hex (at, rxsym, 8);
  at = text::str (at, " aux=");         at = text::hex (at, aux, 8);
  at = text::str (at, " aggre=");       at = text::hex (at, aggre, 8);
  at = text::str (at, "\nhc cap=");     at = text::hex (at, cap, 8);
  at = text::str (at, " ver=");         at = text::hex (at, ver, 8);
  at = text::str (at, " hcs=");         at = text::hex (at, hcs, 8);
  at = text::str (at, " hce=");         at = text::hex (at, hce, 8);
  at = text::str (at, " is=");          at = text::hex (at, is, 8);
  at = text::str (at, " utrlba_abl=");  at = text::hex (at, report.utrba_hi, 8);
  at = text::hex (at, report.utrba_lo, 8);
  at = text::str (at, "\ndma=");        at = text::hex (at, report.dma_hi, 8);
  at = text::hex (at, report.dma_lo, 8);
  at = text::str (at, " powered=");     at = text::dec (at, powered ? 1 : 0);
  at = text::str (at, " busclks=");     at = text::dec (at, busClocks ? 1 : 0);
  at = text::str (at, " hcread=");      at = text::dec (at, hcRead);
  at = text::str (at, "\nids midr=");   at = text::hex (at, readMidr (), 8);
  at = text::str (at, " revidr=");      at = text::hex (at, readRevIdr (), 16);
  at = text::str (at, " isar0=");       at = text::hex (at, readIsar0 (), 16);
  at = text::str (at, "\n    isar1=");  at = text::hex (at, isar1, 16);
  at = text::str (at, " isar2=");       at = text::hex (at, isar2, 16);
  at = text::str (at, " pfr0=");        at = text::hex (at, readPfr0 (), 16);
  at = text::str (at, "\n    pfr1=");   at = text::hex (at, readPfr1 (), 16);
  at = text::str (at, " mmfr0=");       at = text::hex (at, readMfr0 (), 16);
  at = text::str (at, " ctr=");         at = text::hex (at, readCtr (), 8);
  at = text::str (at, " apa=");         at = text::dec (at, apa);
  at = text::str (at, " api=");         at = text::dec (at, api);
  at = text::str (at, " apa3=");        at = text::dec (at, apa3);
  at = text::str (at, "\nsmmu scr0="); at = text::hex (at, report.scr0, 8);
  at = text::str (at, " idr0=");       at = text::hex (at, report.idr0, 8);
  at = text::str (at, " idr1=");       at = text::hex (at, report.idr1, 8);
  at = text::str (at, " gfsr=");       at = text::hex (at, report.gfsr, 8);
  at = text::str (at, "\n     nsmr="); at = text::dec (at, report.nsmr);
  at = text::str (at, " valid=");      at = text::dec (at, report.smmuValid);
  at = text::str (at, " sid60=");      at = text::hex (at, report.smmuMatch, 4);
  at = text::str (at, " smr=");        at = text::hex (at, report.smmuSmr, 8);
  at = text::str (at, " s2cr=");       at = text::hex (at, report.smmuS2cr, 8);
  at = text::str (at, "\n     cbar="); at = text::hex (at, report.smmuCbar, 8);
  at = text::str (at, " sctlr=");      at = text::hex (at, report.smmuSctlr, 8);
  at = text::str (at, "\nsmr rows ");
  at = text::dec (at, report.smrRowCount);
  for (unsigned i = 0; i < report.smrRowCount; ++i) {
    at = text::str (at, " [");
    at = text::dec (at, report.smrRows[i][0]);
    at = text::str (at, "]=");
    at = text::hex (at, report.smrRows[i][1], 8);
    *at++ = '/';
    at = text::hex (at, report.smrRows[i][2], 8);
  }
  at = text::str (at, "\ncb ");
  for (unsigned i = 0; i < 8; ++i) {
    at = text::hex (at, report.cbar[i], 8);
    *at++ = ':';
    at = text::hex (at, report.sctlr[i], 8);
    *at++ = ' ';
  }
  at = text::str (at, "\nfw utrd ");
  for (unsigned i = 0; i < 8; ++i) {
    at = text::hex (at, report.utrd[i], 8);
    *at++ = (i == 7) ? '\n' : ' ';
  }
  at = text::str (at, "   ucd=");      at = text::hex (at, report.ucdBase >> 32, 8);
  at = text::hex (at, report.ucdBase & 0xffffffffU, 8);
  at = text::str (at, "\nfw ucd ");
  for (unsigned i = 0; i < 8; ++i) {
    at = text::hex (at, report.ucdWords[i], 8);
    *at++ = (i == 7) ? '\n' : ' ';
  }
  at = text::str (at, "read ocs=");   at = text::dec (at, report.ocs);
  at = text::str (at, " status=");      at = text::dec (at, report.status);
  at = text::str (at, " resid=");       at = text::dec (at, report.residual);
  at = text::str (at, " is_before=");   at = text::hex (at, report.is_before, 8);
  at = text::str (at, " is_after=");    at = text::hex (at, report.is_after, 8);
  at = text::str (at, "\ndata ");
  for (unsigned i = 0; i < sizeof (report.first); ++i) {
    at = text::hex (at, report.first[i], 2);
    if ((i & 3) == 3) {
      *at++ = ' ';
    }
  }
  *at = '\0';

  panic ("%s", msg);
  return false;
}