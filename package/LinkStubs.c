// SPDX-License-Identifier: BSD-2-Clause-Patent
// No-op definitions of the probe hooks the patched XNU references while the corresponding component
// stays out of the build. Each hook only runs behind its boot argument, which board.toml does not
// pass; the kernel link needs the symbol anyway.
//
// 2026-11-14: the framework this board builds against (FFT-infra/UniXNU) added three more hooks to
// the offline-legacy path. Without them the kernel link dies with
//   "_native_legacy_count_exec", referenced from ___mac_execve in kern_exec.o
//   "_native_legacy_note_exit", referenced from _proc_exit in kern_exit.o
//   "_native_legacy_paint_userspace_band", referenced from _load_init_program in kern_exec.o
// They return 0: the callers test the result, and a stub that returns garbage would look like a
// successful probe.

// offline-component-load-probes.patch, bsd_init: called on offline-legacy-register=1 (NativeLegacyDisplay).
void native_legacy_display_probe(void);
void native_legacy_display_probe(void)
{
}

// The three offline-legacy hooks the newer framework references from the exec/exit path.
int native_legacy_count_exec(void);
int native_legacy_count_exec(void)
{
	return 0;
}

int native_legacy_note_exit(void);
int native_legacy_note_exit(void)
{
	return 0;
}

int native_legacy_paint_userspace_band(void);
int native_legacy_paint_userspace_band(void)
{
	return 0;
}
