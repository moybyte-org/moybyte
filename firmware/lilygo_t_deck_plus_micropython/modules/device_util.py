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

MicroPython-compatible: `time.ticks_*` on device, wall-clock fallback on the host
test shim; `moybyte_diag` is imported lazily + fully guarded so its absence is a
no-op.
"""
import time


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _ticks_us():
    try:
        return time.ticks_us()
    except AttributeError:
        return int(time.time() * 1000000)


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
