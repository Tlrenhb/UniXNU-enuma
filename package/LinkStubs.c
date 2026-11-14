// SPDX-License-Identifier: BSD-2-Clause-Patent
// No-op instrumentation hooks whose owning components are not linked by this board.
// The fork's diagnostic patches call the exec/exit hooks unconditionally; keep their exact ABIs.
#include <stdint.h>

// offline-component-load-probes.patch, bsd_init: called on offline-legacy-register=1 (NativeLegacyDisplay).
void native_legacy_display_probe(void);
void native_legacy_display_probe(void)
{
}

void native_legacy_count_exec(void)
{
}

void native_legacy_note_exit(uint32_t pid, const char *name, int reason_namespace, uint32_t reason_code, int signal)
{
	(void) pid;
	(void) name;
	(void) reason_namespace;
	(void) reason_code;
	(void) signal;
}

void native_legacy_paint_userspace_band(unsigned band)
{
	(void) band;
}

__attribute__((weak)) void boot_progress_poll(void)
{
}
