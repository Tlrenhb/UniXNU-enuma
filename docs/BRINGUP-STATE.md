# enuma bring-up state (2026-10-26)

## Where the board stops

`SEC → DecompressFirstFv → LoadDxeCoreFromFv → LoadDxeCoreFromFfsFile → LoadPeCoffImage`

Confirmed on hardware with framebuffer progress bands (the panel's early console stops producing
text the moment `ArmConfigureMmu()` enables the MMU, so every stage paints a colour band by direct
32-bit stores instead — those keep working):

| row | seen | meaning |
| --- | --- | --- |
| 1000-1068 | yes | ARM `ArmConfigureMmu` internals (all of `MU:01`..`MU:09`) |
| 1084 | yes | `DecompressFirstFv()` returned |
| 1130 | yes | entered `LoadDxeCoreFromFv()` |
| 1136 | yes | DXE core file found in the FV |
| 1148 | yes | PE32 section found |
| 1154 | ? | `LoadPeCoffImage()` returned (red = failed, green = ok) |
| 1160 | no | about to call `GetHobList()` before the jump |
| 1180+ | no | `DxeMain()` entry and its start-up |

## Root cause candidates that are already ruled out

- Firmware volume / PE integrity: the shipped FD parses cleanly — one `DXE_CORE` (PE32+ AARCH64,
  `SizeOfImage=192512`, `EntryPoint=0x123C`), 37 `DRIVER`, 61 `FREEFORM`, 6 `APP`.
- `CacheMaintenanceLib`: mapped to the real `ArmPkg/Library/ArmCacheMaintenanceLib`, not the
  `ASSERT(FALSE)` stub in `MdePkg`.
- Memory table: identical, row for row, to the wh1stl3 enuma port that reaches BDS.
- `KonaPkg.dsc.inc`: included (`enuma.dsc`).
- `APSS_HM` row (watchdog disable): present.
- `PcdDebugPrintErrorLevel = 0x8007EE0F` includes `DEBUG_LOAD`, so the `DEBUG("Loading DxeCore")`
  call in `LoadDxeCoreFromFfsFile` does execute; it sits inside the observed dead zone.

## Next single hardware run

Boot the image with status painting (`ci/instrument_mmu.py` in the private build repository). Every
step of `LoadPeCoffImage` now paints its label colour plus the `EFI_STATUS` as a 16-bit pattern
(row `1400+i*12`, white = 1, bit 0 at the top) and bit 63 at row 1600, then returns instead of
dead-looping in `ASSERT_EFI_ERROR`. One photo therefore gives:

- red 1154 + a label colour + bit pattern → which `LoadPeCoffImage` step failed and with what code;
- green 1154 but no 1160 → the failure is `BuildModuleHob` or the `DEBUG` right after it;
- cyan 1160 → the jump into `DxeMain` is next;
- white 1180 → `DxeMain` ran, i.e. the DXE core is up.
