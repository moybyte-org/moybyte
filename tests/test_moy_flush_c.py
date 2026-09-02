"""The shared banded-flush ENGINE, executed (`native/moy_flush/moy_flush.c`).

#208 ranks this file first: 376 lines under both S3 boards -- the core-0 feeder
task, the band handoff, the reset-order invariant and three bounded waits --
and its own header says *every clause was a race once*. What looked like
coverage was `tests/test_banded_panel.py`, which drives the PYTHON
`BandedCompositor` against a `FakeLcd`; the C never ran anywhere but on glass.

So it runs here. `tests/moy_flush_harness/` supplies the ESP-IDF and FreeRTOS
surface as stub headers and COMPILES THE REAL FILE against them, unmodified,
with `-Wall -Wextra -Werror` -- the moy_ppa precedent, and the only shape that
needs no board. It is compiled rather than transcribed on purpose: a
transcription is a second body that can agree with the test while the shipped C
disagrees with both, which is exactly how `provisional_tline` passed on the host
and failed on the P4 (2026-08-06).

The stubs are a deterministic discrete-event scheduler, not threads: one
context runs at a time, switches happen only inside the blocking primitives,
and the clock is virtual. A 500 ms flush deadline and a 600 ms stop timeout are
therefore assertions about exact numbers that cost no wall-clock time, and a
bounded wait whose bound is deleted trips a watchdog instead of hanging.

Each scenario runs in its OWN PROCESS -- the engine's state is one file-scope
struct plus a file-scope ops pointer, and sharing that across scenarios would
make each one depend on the order of the rest.

The absence is loud, in the shape `tests/unix_mp.py` uses for the desktop
MicroPython: no compiler WARNS locally and FAILS under `CI` or
`MOYBYTE_REQUIRE_MOY_FLUSH`. A compiler that is present but cannot build the
harness is never quiet -- that is a broken tree, not an absent toolchain.
"""

import hashlib
import os
import shutil
import subprocess
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "moy_flush_harness"
ENGINE = ROOT / "native" / "moy_flush"
CACHE = ROOT / ".build" / "moy_flush_c"

# The gate itself: the engine has to survive -Wall -Wextra -Werror against a
# surface it was never compiled against before, which is the cheapest real
# check a shared C body can have.
CFLAGS = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-g", "-O1"]

SOURCES = ("harness.c", "main.c")


def _cc():
    return os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")


# The engine and the GAME FOLD that rides beside it. moy_fold.c is here for the
# same reason moy_flush.c is: it runs on the feeder with no MP context, it is one
# body under both boards, and its band synthesis has an ORACLE off a board (the
# composite it replaced), which tests/test_flush_fold.py is what exploits.
ENGINE_SOURCES = ("moy_flush.c", "moy_fold.c")


def _inputs():
    """Everything the binary depends on, for the cache key."""
    files = sorted(HARNESS.rglob("*.c")) + sorted(HARNESS.rglob("*.h"))
    files += [ENGINE / name for name in ENGINE_SOURCES]
    files += [ENGINE / "moy_flush.h", ENGINE / "moy_fold.h"]
    return files


def _key(cc):
    h = hashlib.sha256()
    for path in _inputs():
        h.update(str(path.relative_to(ROOT)).encode())
        h.update(path.read_bytes())
    h.update(" ".join(CFLAGS).encode())
    try:
        ver = subprocess.run([cc, "--version"], capture_output=True, text=True,
                             timeout=10).stdout.splitlines()[:1]
        h.update((ver[0] if ver else "").encode())
    except OSError:                      # version only refines the key
        pass
    return h.hexdigest()[:16]


def build():
    """The harness binary, or (None, reason). Raises on a compile FAILURE --
    an absent compiler and a broken tree are not the same absence."""
    cc = _cc()
    if cc is None:
        return None, "no C compiler (tried $CC, cc, gcc)"
    try:
        exe = CACHE / ("moy_flush_harness-" + _key(cc))
        if exe.exists():
            return str(exe), None
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = str(exe) + ".tmp%d" % os.getpid()
        cmd = ([cc] + CFLAGS
               + ["-I", str(HARNESS / "stubs"), "-I", str(HARNESS),
                  "-I", str(ENGINE)]
               + [str(HARNESS / name) for name in SOURCES]
               + [str(ENGINE / name) for name in ENGINE_SOURCES]
               + ["-o", tmp])
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:               # $CC points at nothing runnable
        return None, "cannot run %s (%s)" % (cc, exc)
    if proc.returncode != 0:
        raise RuntimeError("the moy_flush harness did not build:\n%s\n%s"
                           % (" ".join(cmd), proc.stderr))
    os.replace(tmp, exe)
    return str(exe), None


def _try_build():
    try:
        return build() + (False,)
    except RuntimeError as exc:
        return None, str(exc), True


_EXE, _WHY, _BROKEN = _try_build()


def require_harness():
    if _EXE is not None:
        return _EXE
    if _BROKEN:
        # -Wall -Wextra -Werror against the stub surface IS one of the checks,
        # and a tree where that no longer compiles is red everywhere, not
        # skipped quietly on the machine that noticed.
        pytest.fail(_WHY)
    text = ("the moy_flush C engine did not run: %s. It needs nothing but a C "
            "compiler -- there is no ESP-IDF and no board involved." % _WHY)
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_MOY_FLUSH"):
        pytest.fail(text)
    warnings.warn(UserWarning(text), stacklevel=2)
    pytest.skip("no moy_flush harness build (see the warning above)")


def _scenarios():
    if _EXE is None:
        return ["(the harness did not build)"]
    out = subprocess.run([_EXE, "--list"], capture_output=True, text=True,
                         timeout=60)
    return [line for line in out.stdout.split() if line]


SCENARIOS = _scenarios()


# The `fold_` scenarios belong to tests/test_flush_fold.py -- same binary, same
# process-per-scenario shape, but a different subject, so a failure names it.
ENGINE_SCENARIOS = [s for s in SCENARIOS if not s.startswith("fold_")]


@pytest.mark.parametrize("scenario", ENGINE_SCENARIOS)
def test_scenario(scenario):
    """One protocol scenario, driven through the real C in its own process."""
    exe = require_harness()
    proc = subprocess.run([exe, scenario], capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, "\n" + proc.stdout + proc.stderr
    assert proc.stdout.startswith("PASS "), proc.stdout


def test_the_harness_drives_the_shipped_engine_and_not_a_copy():
    """The value of this suite is entirely that the SHIPPED file is what runs.

    A transcription would pass this suite and the board would still be wrong,
    which is the recorded lesson of the 2026-08-06 `provisional_tline` failure:
    the host compared itself against a Python transcription of the kernel and
    could not see the C being wrong.
    """
    require_harness()
    assert (ENGINE / "moy_flush.c").exists()
    engine = (ENGINE / "moy_flush.c").read_text(encoding="utf-8")
    for symbol in ("moy_flush_run_frame", "moy_flush_pump", "moy_flush_kick",
                   "moy_flush_drain", "moy_flush_stop"):
        assert symbol in engine
    for path in sorted(HARNESS.rglob("*.c")) + sorted(HARNESS.rglob("*.h")):
        src = path.read_text(encoding="utf-8")
        for symbol in ("moy_flush_run_frame", "static void moy_flush_pump",
                       "moy_flush_task_fn", "moy_flush_free_bounce"):
            assert symbol not in src, "%s re-implements %s" % (path.name, symbol)


def test_the_scenarios_cover_the_clauses_the_header_calls_races():
    """A ratchet on the list itself: these are the paragraphs of moy_flush.h
    ("THE HANDOFF PROTOCOL", "THE RESET-ORDER INVARIANT", the three bounded
    waits, the failure counters), and a scenario deleted from the C harness
    should turn this red rather than shrink the run silently."""
    require_harness()
    for name in ("normal_frame", "reset_order", "stale_completions_at_the_arm",
                 "feed_timeout", "tail_timeout", "drain_bounded_wait",
                 "kick_clears_a_stale_done_credit",
                 "frame_busy_clears_before_the_give",
                 "stop_failure_keeps_the_bounce",
                 "failure_counters_are_positional",
                 "slot_pacing_two_slots", "isr_without_a_feeder"):
        assert name in ENGINE_SCENARIOS, name
    assert len(ENGINE_SCENARIOS) >= 30
