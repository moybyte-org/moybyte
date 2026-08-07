"""The host's Python synth must stay a faithful twin of libmoy (#97).

The device and the web runner COMPILE libmoy -- moy-spec's own C implementation
of SPEC.md 8, vendored into native/moy_audio/libmoy/ -- so their audio is the
spec by construction. The host sim can't: linking C would put a compiler in
`make setup`. So runtime/audio.py is a hand-maintained port of that exact file,
and this is what stops it drifting.

Drift here is uniquely hard to notice. SPEC.md 8.3 deliberately exempts audio
from the pixel conformance that catches divergence everywhere else, and the
symptom is "the music sounds a bit off on the simulator", which nobody files.
The original port had five such divergences at once -- the instrument loudness
balance, white noise instead of the pitched walk, a semitone-linear slide, the
wrong phaser detune, and a music scheduler coarse enough to swallow a whole
short track -- and every one of them was inaudible as a bug and obvious as a
measurement.

Needs a C compiler; skipped without one, since the boards are unaffected either
way. See experiments/audio_parity/audio_parity.py for what is measured and why.
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "experiments", "audio_parity"))
import audio_parity  # noqa: E402


requires_cc = pytest.mark.skipif(
    not (os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")),
    reason="no C compiler to build the libmoy reference")


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    return str(tmp_path_factory.mktemp("audio-parity"))


@requires_cc
def test_python_synth_is_bit_identical_to_libmoy(work):
    """Every sample, on every scenario, against libmoy built at CPython's
    precision. Not "close enough": identical. The two differ only in float width,
    so anything else is a porting bug -- which is exactly how the last one was
    found (a closed-form 2**(n/12) where libmoy has a pitch TABLE; inaudible on
    its own, but enough to walk a square-wave edge a whole sample sideways after
    a few thousand phase accumulations)."""
    exe = audio_parity.build_reference(work, double=True)
    bad = audio_parity.run_strict(work, exe)
    assert not bad, "diverges from libmoy: " + ", ".join(
        "%s (%.3f%% exact, max delta %d)" % (n, pct * 100, d) for n, pct, d in bad)


@requires_cc
def test_python_synth_sounds_the_same_at_device_precision(work):
    """The same comparison against libmoy exactly as vendored -- single
    precision, as the S3 and P4 FPUs run it. Bit-equality is neither expected nor
    required here (8.3 says two hosts will not produce identical samples); what
    must hold is level, pitch and waveform shape."""
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
    the render buffer -- never the synth. Skipped unless someone has built the
    unix port with the usermod (building MicroPython is not this suite's job);
    experiments/audio_parity/audio_parity.py prints the two commands."""
    mp_exe = audio_parity.find_micropython()
    if mp_exe is None:
        pytest.skip("no ports/unix MicroPython built with the moy_audio usermod")
    exe = audio_parity.build_reference(work)
    bad = []
    for name, bank, commands in audio_parity.scenarios():
        ref = audio_parity.render_reference(exe, work, name, bank, commands)
        got = audio_parity.render_native(mp_exe, work, name, bank, commands)
        if ref != got:
            same = sum(1 for a, b in zip(ref, got) if a == b)
            bad.append("%s (%d/%d exact)" % (name, same, len(ref)))
    assert not bad, "native module diverges from libmoy: " + ", ".join(bad)
