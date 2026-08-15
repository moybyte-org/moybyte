"""The host's audio BINDING must stay a faithful door into libmoy (#97).

Every tier compiles libmoy now -- moy-spec's own C implementation of SPEC.md 8,
vendored into native/moy_audio/libmoy/: the boards and the web runner
natively, and since moycore stage 0 the host sim too, through the ctypes .so
runtime/audio_binding.py builds. The hand-maintained Python twin this suite
was written to police is DELETED; what can drift now is only the binding
itself -- a mangled verb argument, the bank's one JSON crossing, the render
buffer -- so the strict pass compares the binding's output against an
independently-driven reference render of the same source at the same
precision, where any difference is a marshalling bug by construction.

The device-precision pass survives unchanged in meaning: the host binds the
DOUBLE-WIDENED build (the recipe under which the retired twin was proven
bit-identical, so stage 0 moved no sample), the boards run float, and SPEC.md
8.3 promises only level/pitch/shape agreement across that gap -- measured
here, thresholds just below the float32 spread.

Needs a C compiler; skipped without one (the host is then deliberately silent
and the boards are unaffected either way). See
experiments/audio_parity/audio_parity.py for what is measured and why.
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "experiments", "audio_parity"))
import audio_parity  # noqa: E402

from unix_mp import require_unix_mp  # noqa: E402


requires_cc = pytest.mark.skipif(
    not (os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")),
    reason="no C compiler to build the libmoy reference")


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return str(tmp_path_factory.mktemp("audio-parity"))


@requires_cc
def test_host_binding_is_bit_identical_to_libmoy(work):
    """Every sample, on every scenario, against libmoy built at the binding's
    own (double-widened) precision. Not "close enough": identical -- the
    reference render and the AudioEngine path compile the same program, so any
    difference is the ctypes shim, a verb argument conversion, the bank's JSON
    crossing or the render buffer. This is the moy_audio unix-usermod smoke's
    exact sibling, aimed at the host's door instead of MicroPython's."""
    exe = audio_parity.build_reference(work, double=True)
    bad = audio_parity.run_strict(work, exe)
    assert not bad, "binding diverges from libmoy: " + ", ".join(
        "%s (%.3f%% exact, max delta %d)" % (n, pct * 100, d) for n, pct, d in bad)


@requires_cc
def test_host_binding_sounds_the_same_at_device_precision(work):
    """The same comparison against libmoy exactly as vendored -- single
    precision, as the S3 and P4 FPUs run it. Bit-equality is neither expected nor
    required here (8.3 says two hosts will not produce identical samples); what
    must hold across the double/float gap is level, pitch and waveform shape."""
    exe = audio_parity.build_reference(work)
    failures = []
    for name, bank, commands in audio_parity.scenarios():
        ref = audio_parity.render_reference(exe, work, name, bank, commands)
        got = audio_parity.render_python(bank, commands)
        m = audio_parity.compare(ref, got)
        if not m["ok"]:
            failures.append((name, m))
    assert not failures, "\n".join(
        "%s: rms %.2f%% block %.2f%% cross %.2f%% corr %.4f"
        % (n, m["rms_err"] * 100, m["block_err"] * 100, m["cross_err"] * 100,
           m["corr"]) for n, m in failures)


@requires_cc
def test_calibrated_instrument_loudness_is_unequal(work):
    """SPEC.md 8.3 makes instrument loudness deliberately unequal -- the triangle
    family peaks at about twice the square family, because ported music is
    balanced against exactly that. It is the single most consequential thing the
    adoption changed (the old synth normalised every wave to +-1.0), and a
    re-vendor that lost it would still pass a same-note-same-pitch check."""
    exe = audio_parity.build_reference(work)
    level = {}
    for name, bank, commands in audio_parity.scenarios():
        if name.startswith("wave"):
            level[name] = audio_parity._rms(
                audio_parity.render_reference(exe, work, name, bank, commands))
    # square (0) and pulse (4) are the quiet family; triangle (1) the loud one
    assert level["wave1"] > level["wave0"] * 1.10
    assert level["wave0"] == pytest.approx(level["wave4"], rel=0.01)
    for name, rms in level.items():
        assert rms > 0.0, name + " is silent"


def test_native_module_matches_libmoy_when_a_unix_build_exists(work):
    """The BINDING, driven through a real MicroPython VM.

    libmoy is compiled into the native module, so the bar is bit-equality: a
    difference here is the shim mangling a verb, the bank's one JSON crossing, or
    the render buffer -- never the synth. Needs the desktop MicroPython
    `make unix-micropython` builds; its absence is loud, not a silent skip
    (tests/unix_mp.py)."""
    mp_exe = require_unix_mp(
        "moy_audio",
        why="Without it the module the boards actually load is never run: the "
            "host binding is checked against libmoy, but the MicroPython "
            "binding -- the one crossing a board makes -- is not.")
    exe = audio_parity.build_reference(work)
    bad = []
    for name, bank, commands in audio_parity.scenarios():
        ref = audio_parity.render_reference(exe, work, name, bank, commands)
        got = audio_parity.render_native(mp_exe, work, name, bank, commands)
        if ref != got:
            same = sum(1 for a, b in zip(ref, got) if a == b)
            bad.append("%s (%d/%d exact)" % (name, same, len(ref)))
    assert not bad, "native module diverges from libmoy: " + ", ".join(bad)
