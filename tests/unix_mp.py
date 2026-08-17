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
# moy_audio, moy_web) compiled in. Note moy_lua is the vendored VM and no longer
# a module -- `import moy_lua` is MEANT to fail; moycore is the runtime that
# binds it. moy_web is the browser console baked into the firmware image, here
# so its flash-mapped memoryview is exercised somewhere other than a board.
CANONICAL = (ROOT / ".build" / "unix_micropython" / "micropython" / "ports"
             / "unix" / "build-moybyte" / "micropython")

# One candidate. The hand-built legacy trees that used to be listed here lived
# under the fork's .build/lvgl_micropython/, which was deleted with the fork
# (2026-08-17) -- nothing creates them and the paths can no longer exist.
CANDIDATES = (CANONICAL,)

_PROBED = {}


def _provides(exe, modules):
    """Does this binary import every one of `modules`? Cached per (exe, mods)."""
    if not modules:
        return True
    key = (exe, modules)
    if key not in _PROBED:
        try:
            out = subprocess.run([exe, "-c", "import " + ", ".join(modules)],
                                 capture_output=True, text=True, timeout=60)
            _PROBED[key] = out.returncode == 0
        except OSError:
            _PROBED[key] = False
    return _PROBED[key]


def find_unix_mp(*modules):
    """The first desktop MicroPython that provides `modules`, or None."""
    env = os.environ.get("MOYBYTE_MICROPYTHON")
    if env and os.path.exists(env) and _provides(env, modules):
        return env
    for cand in CANDIDATES:
        if cand.exists() and _provides(str(cand), modules):
            return str(cand)
    return None


def missing_message(modules=(), why=""):
    msg = ["the check did not run: no desktop MicroPython with the native "
           "usermods%s. Build one -- it takes about fifteen seconds:"
           % (" (needs " + ", ".join(modules) + ")" if modules else "")]
    msg.append("")
    msg.append("    make unix-micropython")
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
    assert not kw, kw
    exe = find_unix_mp(*modules)
    if exe is not None:
        return exe
    text = missing_message(modules, why)
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_UNIX_MP"):
        pytest.fail(text)
    warnings.warn(UserWarning(text), stacklevel=2)
    pytest.skip("no desktop MicroPython with %s (see the warning above)"
                % (", ".join(modules) or "the native usermods"))
