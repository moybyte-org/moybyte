#!/usr/bin/env python3
"""The VM's per-thread state is a thread-local variable, not a FreeRTOS TLS slot.

    python3 tools/patch_thread_state_tls.py <micropython checkout>

Every `MP_STATE_THREAD` access -- each bytecode call's globals swap
(`mp_globals_set`), `mp_load_global`, the C stack check, every nlr push and
pop, `gc_alloc` -- goes through the esp32 port's `mp_thread_get_state`, which
asks FreeRTOS for TLS slot 1 of the running task:
`pvTaskGetThreadLocalStoragePointer(NULL, 1)`. On a multi-core build that
resolves the running task with `xTaskGetCurrentTaskHandle`, which masks
interrupts around its read of the core's current TCB. A PC profile of the
Waveshare P4 switching Editor tabs back to back (2026-10-27, moy_prof with the
interruptee's `ra`) put ~5% of every frame on that path:
`pvTaskGetThreadLocalStoragePointer` and `mp_thread_get_state` themselves, and
the samples the timer interrupt lands on as the mask comes off
(`vPortClearInterruptMaskFromISR`, whose caller is
`xTaskGetCurrentTaskHandle`).

The patch keeps the state pointer in a `__thread` variable as well: the port
gives each task its own thread-local block (the RISC-V port's `tp`, set per
task at creation and switched with its context), so a read is one load. The
TLS slot is still written, so anything that reads it keeps working.

Applied by `moybyte_patch_thread_state_tls` in tools/esp32_build_lib.sh, to
`ports/esp32/mpthreadport.c`. All-or-nothing: the stock pair must match
exactly once or nothing is written and the exit is non-zero. A tree already
carrying the marker is left as it is.
"""

from __future__ import annotations

import os
import sys

MARKER = "Moybyte: the thread state is a __thread variable"
FILE = "ports/esp32/mpthreadport.c"

STOCK = (
    "mp_state_thread_t *mp_thread_get_state(void) {\n"
    "    return pvTaskGetThreadLocalStoragePointer(NULL, 1);\n"
    "}\n"
    "\n"
    "void mp_thread_set_state(mp_state_thread_t *state) {\n"
    "    vTaskSetThreadLocalStoragePointer(NULL, 1, state);\n"
    "}\n"
)

PATCHED = (
    "// %s (tools/patch_thread_state_tls.py):\n"
    "// one load, where the TLS slot's lookup masks interrupts to find the task.\n"
    "static __thread mp_state_thread_t *moy_thread_state;\n"
    "\n"
    "mp_state_thread_t *mp_thread_get_state(void) {\n"
    "    return moy_thread_state;\n"
    "}\n"
    "\n"
    "void mp_thread_set_state(mp_state_thread_t *state) {\n"
    "    moy_thread_state = state;\n"
    "    vTaskSetThreadLocalStoragePointer(NULL, 1, state);\n"
    "}\n" % MARKER
)


def apply(mpy_dir):
    """Patch the checkout under `mpy_dir`; returns "patched", "already", or
    raises SystemExit when the stock pair does not match exactly once."""
    path = os.path.join(mpy_dir, FILE)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if MARKER in text:
        return "already"
    n = text.count(STOCK)
    if n != 1:
        sys.exit("!! thread-state patch did not apply -- %s's mp_thread_get_state/"
                 "set_state pair matches %d times (the stock lines changed shape); "
                 "nothing written" % (FILE, n))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(STOCK, PATCHED))
    return "patched"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mpy_dir")
    a = ap.parse_args(argv)
    print("== thread-state TLS: %s" % apply(a.mpy_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
