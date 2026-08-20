"""Leaf utilities shared across the device backend (extracted from moy_runtime.py).

This is the bottom of the device-side import DAG: it depends only on `time` (and a
lazy `moybyte_diag`), and NOTHING here imports back into `moy_runtime` or any other
device module. That is the whole point of the module -- the tick helpers
(_ticks_ms/_ticks_diff/_ticks_us) and diag shims (_diag_note/_diag_log) are used by
nearly every device cluster (canvas, audio, wifi, input, webview, run_desktop), so
if they stayed in moy_runtime.py an extracted cluster would have to
`from moy_runtime import _ticks_us` and moy_runtime would import that cluster back --
a cycle MicroPython's frozen loader tolerates far worse than CPython. Pulling these
into a leaf lets every extracted device module import *down* the DAG, never back into
core (same reason block_editor_ui injects NAMES instead of importing console back).

The tick trio (+ `_sleep_ms`) is RE-EXPORTED from the shared `runtime/ticks.py`
(one body, 2026-08-17 -- this module used to carry its own copy, one of nine in
the tree), so the device tier's import surface (`from device_util import
_ticks_ms`) is unchanged; `moybyte_diag` is imported lazily + fully guarded so
its absence is a no-op.
"""

try:                                    # device: ticks is frozen flat
    from ticks import _ticks_ms, _ticks_us, _ticks_diff, _sleep_ms
except ImportError:                     # host: the runtime package
    from runtime.ticks import _ticks_ms, _ticks_us, _ticks_diff, _sleep_ms


def sram_census(stage):
    """Print one STAGE line: internal-SRAM free / largest block / total, plus
    free PSRAM (#66/#67, 2026-08-10). MEMBENCH + LUAMEM proved internal RAM is
    EXHAUSTED by desktop-up (a 77KB boot alloc fails; celeste's Lua heap wins
    9.2KB, then everything is PSRAM -- the measured-2x regime). The deltas
    between these lines name the stage that eats it, which no per-capability
    heap dump can. Regions >=1MB are PSRAM; the rest is internal DRAM. Cheap
    (~us) and unconditional like the other boot chatter; fully guarded."""
    try:
        import esp32
        int_tot = int_free = int_big = ps_free = 0
        for reg in esp32.idf_heap_info(esp32.HEAP_DATA):
            tot, free, big = reg[0], reg[1], reg[2]
            if tot >= 1024 * 1024:
                ps_free += free
            else:
                int_tot += tot
                int_free += free
                if big > int_big:
                    int_big = big
        _diag_note("STAGE", "%s int_free=%dk big=%dk int_tot=%dk ps_free=%dk"
                   % (stage, int_free // 1024, int_big // 1024,
                      int_tot // 1024, ps_free // 1024))
    except Exception:
        pass


def _diag_note(tag, msg):
    """Module-level convenience for call sites that don't hold a `diag` handle
    (the audio/wifi/keyboard backends): lazily import moybyte_diag and persist +
    print the line (logp). Falls back to a plain print if diag is unavailable.
    Fully guarded -- a diag failure here must never affect the caller."""
    try:
        import moybyte_diag

        moybyte_diag.logp(tag, msg)
        return
    except Exception:
        pass
    try:
        print("Moybyte", tag, msg)
    except Exception:
        pass


def _diag_log(tag, msg, diag):
    """Persist a line to the diag ring AND print it live (boot serial is still
    useful). logp() does both; falls back to a plain print if diag is absent.
    `diag` is the already-imported module (or None) -- this is the hot-path
    variant used inside run_desktop, avoiding a per-call import."""
    if diag is not None:
        try:
            diag.logp(tag, msg)
            return
        except Exception:
            pass
    try:
        print("Moybyte", tag, msg)
    except Exception:
        pass
