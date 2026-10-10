"""The desktop MicroPython with the native usermods: where it is, and what to
do when it is not there.

Six suites drive the REAL native modules under a real MicroPython VM -- the
compiled-vs-compiled raster check, the flush-fold bands, the gate-pal walk, the
moycore frame loop, the semantic trace pin, the moy_audio render. Every one of
them used to carry its own copy of a path into a hand-built tree and its own
`pytest.skip` when the file was missing, which meant they ran on the one
machine that had followed some prose in a README and were silently ABSENT
everywhere else -- on a fresh clone, and in CI. Five files, five paths, five
skips, and nothing that could tell you the checks were not happening.

So: ONE candidate list, ONE lookup, ONE decision about the absence, and a real
Makefile target (`make unix-micropython`) plus a CI step that produce the
binary. `MOYBYTE_MICROPYTHON` overrides everything, which is the escape hatch
for a build somewhere else.

The absence is LOUD. `require_unix_mp` warns locally (pytest prints warnings in
its summary even under -q) and FAILS wherever a build is expected -- `CI`,
which GitHub Actions sets itself, or `MOYBYTE_REQUIRE_UNIX_MP` for anyone who
wants the same locally. Deleting the build step from the workflow therefore
turns these suites red rather than quiet.

Callers name the MODULES they need and the binary is PROBED for them, because
the legacy candidates below are partial builds: the tree that has moy_gfx does
not have moycore, and picking it for a moycore suite would produce an
ImportError inside a driver script -- a red test caused by a stale local build,
which reads exactly like a real regression. A probe turns that back into the
honest answer ("no suitable build"), and it also validates a
MOYBYTE_MICROPYTHON that points somewhere wrong.

pytest is imported lazily, inside `require_unix_mp`, so that
experiments/audio_parity/audio_parity.py -- a standalone script, no pytest --
can share the lookup instead of keeping a sixth copy of the path.
"""

import os
import subprocess
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What `make unix-micropython` builds: mainline MicroPython's unix port with
# every native module that ships a Makefile fragment (moy_gfx, moy_lua, moycore,
# moy_audio, moy_web, moy_png, moy_index as the twin UNIX_MP_INDEX names, and
# moy_spine)
# compiled in. Note moy_lua is the vendored VM and no longer
# a module -- `import moy_lua` is MEANT to fail; moycore is the runtime that
# binds it. moy_web is the browser console baked into the firmware image, here
# so its flash-mapped memoryview is exercised somewhere other than a board.
CANONICAL = (ROOT / ".build" / "unix_micropython" / "micropython" / "ports"
             / "unix" / "build-moybyte" / "micropython")

# One candidate. The hand-built legacy trees that used to be listed here lived
# under the fork's .build/lvgl_micropython/, which was deleted with the fork
# (2026-08-17) -- nothing creates them and the paths can no longer exist.
CANDIDATES = (CANONICAL,)

# The same target's second binary: the same modules in the BOARDS' model
# (32-bit words, REPR_C, single-precision floats, threads under one GIL). A heap
# allocation measured on it is one a board makes; on the 64-bit binary every
# float result is a heap object too, and its threads run in parallel with no
# GIL, which no board's do. `board_model=True` asks for it, and the probe checks
# the word and the GIL.
BOARD_MODEL = CANONICAL.parent.parent / "build-moybyte-board" / "micropython"

_PROBED = {}


def _provides(exe, modules, board_model=False):
    """Does this binary import every one of `modules` (and, for the board
    model, run on 32-bit words)? Cached per (exe, mods, model)."""
    if not modules and not board_model:
        return True
    key = (exe, modules, board_model)
    if key not in _PROBED:
        code = "import sys"
        if modules:
            code += "; import " + ", ".join(modules)
        if board_model:
            code += "; assert sys.maxsize == 2147483647"
        try:
            out = subprocess.run([exe, "-c", code],
                                 capture_output=True, text=True, timeout=60)
            _PROBED[key] = out.returncode == 0
        except OSError:
            _PROBED[key] = False
    return _PROBED[key]


# What a GIL does that parallel threads do not: a thread inside one C call keeps
# every other thread out of the interpreter. Two threads each sort a list in a
# single C call, released together, and note when each call started and ended.
# Under a GIL the two calls cannot overlap, ever; with parallel threads (the
# unix port's default) they overlap, and a build whose workers die or whose
# calls never finish fails the probe too.
_GIL_PROBE = """
import _thread, time
go = [False]
ready = [0]
done = [0]
span = {}


def work(name):
    data = [(i * 7919) % 400000 for i in range(400000)]
    ready[0] += 1
    while not go[0]:
        pass
    t0 = time.ticks_us()
    data.sort()
    span[name] = (t0, time.ticks_us())
    done[0] += 1


def wait(counter, want, ms):
    t = time.ticks_ms()
    while counter[0] < want:
        assert time.ticks_diff(time.ticks_ms(), t) < ms, (counter[0], want)
        time.sleep_ms(5)


_thread.start_new_thread(work, ("a",))
_thread.start_new_thread(work, ("b",))
wait(ready, 2, 30000)
go[0] = True
wait(done, 2, 30000)
(a0, a1), (b0, b1) = span["a"], span["b"]
for lo, hi in (span["a"], span["b"]):
    assert time.ticks_diff(hi, lo) > 10000, "a sort too short to tell: %d us" % time.ticks_diff(hi, lo)
assert time.ticks_diff(b0, a1) >= 0 or time.ticks_diff(a0, b1) >= 0, span
"""


def has_gil(exe):
    """Does this binary run its threads under a GIL, as every board does?
    Cached per binary. A build that does not is the unix port's default, and
    the console's input poller races its frame loop on it."""
    key = ("gil", exe)
    if key not in _PROBED:
        try:
            out = subprocess.run([exe, "-X", "heapsize=64M", "-c", _GIL_PROBE],
                                 capture_output=True, text=True, timeout=120)
            _PROBED[key] = out.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _PROBED[key] = False
    return _PROBED[key]


def find_unix_mp(*modules, board_model=False):
    """The first desktop MicroPython that provides `modules`, or None."""
    if board_model:
        if (BOARD_MODEL.exists() and _provides(str(BOARD_MODEL), modules, True)
                and has_gil(str(BOARD_MODEL))):
            return str(BOARD_MODEL)
        return None
    env = os.environ.get("MOYBYTE_MICROPYTHON")
    if env and os.path.exists(env) and _provides(env, modules):
        return env
    for cand in CANDIDATES:
        if cand.exists() and _provides(str(cand), modules):
            return str(cand)
    return None


def missing_message(modules=(), why="", board_model=False):
    msg = ["the check did not run: no desktop MicroPython with the native "
           "usermods%s%s. Build one -- it takes about fifteen seconds:"
           % (" in the boards' model (32-bit, REPR_C, one GIL)" if board_model
              else "",
              " (needs " + ", ".join(modules) + ")" if modules else "")]
    msg.append("")
    msg.append("    make unix-micropython")
    if board_model:
        msg.append("")
        msg.append("which builds that one only where a 32-bit C toolchain "
                   "exists (apt install gcc-multilib).")
    if why:
        msg.append("")
        msg.append(why.strip())
    return "\n".join(msg)


def require_unix_mp(*modules, **kw):
    """The binary, or a LOUD absence.

    A bare `pytest.skip` was the old behaviour and it is what let these checks
    be absent for months: a skip is one `s` in the progress line, and the thing
    being skipped is the only lane that runs the real native code.
    """
    import pytest                       # lazy: audio_parity.py has no pytest

    why = kw.pop("why", "")
    board_model = kw.pop("board_model", False)
    assert not kw, kw
    exe = find_unix_mp(*modules, board_model=board_model)
    if exe is not None:
        return exe
    text = missing_message(modules, why, board_model)
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_UNIX_MP"):
        pytest.fail(text)
    warnings.warn(UserWarning(text), stacklevel=2)
    pytest.skip("no desktop MicroPython with %s (see the warning above)"
                % (", ".join(modules) or "the native usermods"))
