"""Audio parity: runtime/audio.py's synth vs. the vendored libmoy library (#97).

The device and the web runner COMPILE libmoy (native/moy_audio/libmoy/), so their
audio is SPEC.md 8 by construction. The host sim can't -- linking C would put a
compiler in `make setup` -- so `runtime/audio.AudioEngine` is a hand-maintained
Python twin of that exact file. This harness is what stops the twin drifting: it
renders the same scenario through both and compares.

It checks two things, because there are two different questions.

STRICT -- is the Python arithmetic the same arithmetic?
    libmoy is single-precision throughout (the S3 and P4 FPUs are); CPython has
    only doubles. Comparing them directly buries a real porting bug under an
    unavoidable rounding difference, so this mode compiles the vendored source
    at DOUBLE precision -- mechanically, two regexes over a copy in a temp dir,
    never the vendored file -- and then demands that every single sample match
    exactly. It does: all scenarios are bit-identical. That turns "the port is
    faithful" from a judgement call into a boolean, and it is what caught the
    last divergence in the original port (a closed-form 2**(n/12) in place of
    libmoy's pitch table: inaudible on its own, but enough to walk a square-wave
    edge a whole sample sideways after a few thousand phase accumulations).

DEVICE PRECISION -- does it still sound the same at the precision that ships?
    The same comparison against the source exactly as vendored. Bit-equality is
    neither expected nor required here -- SPEC.md 8.3 says outright that two
    hosts will not produce identical samples -- so this measures what 8.3 does
    promise:

      level  -- RMS overall and per block. Catches the calibrated instrument
                loudness (square 0.25 vs triangle 0.5), vol/7, the master level,
                the *0.5 mixdown headroom, and both fades.
      pitch  -- zero-crossing rate. Catches note frequency, the Hz-linear slide,
                vibrato depth, drop, arpeggio rate, the 109/110 phaser detune.
      shape  -- normalised cross-correlation per block. Catches waveform
                identity (a saw rendered as a triangle correlates poorly at
                equal RMS and equal pitch), the noise walk, the de-click slew.

    Correlation is the one metric single precision genuinely moves: a half-LSB
    phase difference at a square-wave edge is a full-amplitude sample error
    while the note is, audibly, the same. So its threshold is loose and the
    STRICT mode is what actually gates.

Run it directly for a report:

    .venv/bin/python experiments/audio_parity/audio_parity.py -v
"""

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
_LIBMOY = os.path.join(_ROOT, "firmware", "lilygo_t_deck_plus_micropython",
                       "native", "moy_audio", "libmoy")

if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# -- scenarios ---------------------------------------------------------------
# A scenario is (name, bank_dict, [command, ...]). Commands are the tiny language
# libmoy_render.c reads; this module both EMITS them for the C reference and
# interprets them against the Python engine, so there is one definition.
#
# Renders are kept short (<= 0.4 s). Float-vs-double phase drift grows with time,
# and nothing here needs a long tail to prove its point.

RATE = 22050


def _note(pitch, wave, vol, eff=None):
    return [pitch, wave, vol] if eff is None else [pitch, wave, vol, eff]


def _steps(*notes):
    return {"speed": 8, "loop": False, "steps": list(notes)}


def scenarios():
    """Every scenario as (name, bank, commands)."""
    out = []

    # One sustained note per waveform: this is the loudness-calibration test.
    # Under the pre-adoption synth every wave peaked at 1.0; under SPEC 8.3 the
    # square family peaks at 0.25 and the triangle family at 0.5, so a scenario
    # per wave pins each instrument's own level as well as its shape.
    for wave in range(8):
        bank = {"sfx": [{"speed": 4, "loop": False,
                         "steps": [_note(57, wave, 7)]}], "music": []}
        out.append((f"wave{wave}", bank, ["sfx 0 0", f"render {RATE // 4}"]))

    # Each per-note effect over a two-step sfx, so slide/arpeggio have a previous
    # note and a group to work with.
    for eff, name in ((1, "slide"), (2, "vibrato"), (3, "drop"),
                      (4, "fade_in"), (5, "fade_out"),
                      (6, "arp_fast"), (7, "arp_slow")):
        bank = {"sfx": [{"speed": 8, "loop": False, "steps": [
            _note(45, 0, 6), _note(69, 0, 6, eff),
            _note(52, 0, 5), _note(64, 0, 4),
        ]}], "music": []}
        out.append((f"eff_{name}", bank, ["sfx 0 0", f"render {RATE // 2}"]))

    # A keyed rest (vol 0, real pitch) must still become the slide origin --
    # SPEC 8.1. The pre-adoption engine required vol > 0 and slid from the wrong
    # note here.
    out.append(("keyed_rest_slide", {"sfx": [{"speed": 8, "loop": False, "steps": [
        _note(45, 0, 0), _note(69, 0, 6, 1),
    ]}], "music": []}, ["sfx 0 0", f"render {RATE // 3}"]))

    # Noise across the register: 8.3's low-pass tracks the note and lifts the
    # bass, so a low and a high noise note must differ in level AND spectrum.
    out.append(("noise_low", {"sfx": [{"speed": 4, "loop": False,
                                       "steps": [_note(24, 3, 6)]}], "music": []},
                ["sfx 0 0", f"render {RATE // 4}"]))
    out.append(("noise_high", {"sfx": [{"speed": 4, "loop": False,
                                        "steps": [_note(84, 3, 6)]}], "music": []},
                ["sfx 0 0", f"render {RATE // 4}"]))

    # Volume levels: the master is 0..7 and scales linearly.
    for lvl in (0, 3, 7):
        bank = {"sfx": [{"speed": 4, "loop": False,
                         "steps": [_note(57, 1, 7)]}], "music": []}
        out.append((f"volume{lvl}", bank,
                    [f"volume {lvl}", "sfx 0 0", f"render {RATE // 5}"]))

    # beep is engine-native: an exact frequency, off the four channels, so it
    # must sound WHILE a 4-channel-wide bank is playing and must not be snapped
    # to a semitone.
    out.append(("beep", {"sfx": [], "music": []},
                ["beep 443.7 0.1", f"render {RATE // 4}"]))

    # Channel allocation: an sfx while music plays must land on a free voice,
    # never on the track's. A 1-channel track owns voice 3.
    music_bank = {
        "sfx": [
            {"speed": 8, "loop": False, "steps": [_note(45, 1, 5), _note(52, 1, 5)]},
            {"speed": 8, "loop": False, "steps": [_note(69, 0, 6), _note(64, 0, 6)]},
            {"speed": 8, "loop": False, "steps": [_note(33, 2, 7), _note(35, 2, 7)]},
        ],
        "music": [
            {"speed": 4, "loop": True, "pattern": [0, 1, 0, 2]},
            {"speed": 4, "loop": True, "pattern": [[0, 1], [2, 0], [1, 2]]},
            {"speed": 4, "loop": False, "pattern": [0, 1],
             "row_secs": [0.13, 0.21]},
        ],
    }
    out.append(("music_mono", music_bank, ["music 0 1", f"render {RATE // 2}"]))
    out.append(("music_multi", music_bank, ["music 1 1", f"render {RATE // 2}"]))
    out.append(("music_row_secs", music_bank, ["music 2 0", f"render {RATE // 2}"]))
    out.append(("music_plus_sfx", music_bank,
                ["music 0 1", f"render {RATE // 8}", "sfx 2",
                 f"render {RATE // 4}"]))
    out.append(("music_stop", music_bank,
                ["music 0 1", f"render {RATE // 8}", "music_stop",
                 f"render {RATE // 8}"]))
    out.append(("sound_stop_all", music_bank,
                ["music 0 1", "sfx 1 0", f"render {RATE // 8}", "sound_stop",
                 f"render {RATE // 8}"]))

    # A looping sfx with a loop_start pickup (SPEC 8.1's loop range).
    out.append(("loop_start", {"sfx": [{
        "speed": 12, "loop": True, "loop_start": 2, "steps": [
            _note(45, 0, 6), _note(47, 0, 6), _note(52, 0, 6), _note(55, 0, 6),
        ]}], "music": []}, ["sfx 0 0", f"render {int(RATE * 0.4)}"]))

    return out


# -- the C reference ---------------------------------------------------------

def _widen_to_double(workdir):
    """Copy the vendored source into `workdir` with its arithmetic widened from
    float to double, and return the include dir.

    Two mechanical substitutions -- the type keyword, and the `f` suffix on
    float literals -- so the transformed source is the same program evaluated at
    CPython's precision. It is written to a temp dir; the vendored file is never
    touched. Nothing here can quietly produce a vacuous pass: a substitution
    that mangled the source would fail to compile, and the same run also builds
    and compares the source exactly as vendored.
    """
    import re
    out = os.path.join(workdir, "double")
    os.makedirs(out, exist_ok=True)
    for name in ("moy_audio.c", "moy_audio.h"):
        with open(os.path.join(_LIBMOY, name)) as fh:
            src = fh.read()
        src = re.sub(r"\bfloat\b", "double", src)
        src = re.sub(r"(\d)[fF]\b", r"\1", src)     # 0.5f -> 0.5, never 0x7FFF
        with open(os.path.join(out, name), "w") as fh:
            fh.write(src)
    return out


def build_reference(workdir, double=False):
    """Compile libmoy_render against the VENDORED libmoy. Returns the binary
    path, or None when there's no C compiler (the harness then skips).

    `double` widens the reference to CPython's precision -- see the module
    docstring. -ffp-contract=off keeps the compiler from fusing multiply-adds,
    which rounds differently from the two separate operations Python performs.
    """
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        return None
    inc = _widen_to_double(workdir) if double else _LIBMOY
    exe = os.path.join(workdir, "libmoy_render_double" if double
                       else "libmoy_render")
    cmd = [cc, "-std=c99", "-O2", "-ffp-contract=off", "-I", inc,
           os.path.join(_HERE, "libmoy_render.c"),
           os.path.join(inc, "moy_audio.c"), "-o", exe]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("libmoy_render build failed:\n" + proc.stderr)
    return exe


def write_scenario(workdir, name, bank, commands):
    """Emit the bank JSON + the command script. ONE file drives every engine."""
    import json
    bank_path = os.path.join(workdir, name + ".json")
    with open(bank_path, "w") as fh:
        json.dump(bank, fh)
    script_path = os.path.join(workdir, name + ".script")
    with open(script_path, "w") as fh:
        fh.write("rate %d\nbank %s\n" % (RATE, bank_path))
        fh.write("\n".join(commands) + "\n")
    return script_path


def _read_pcm(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return list(struct.unpack("<%dh" % (len(raw) // 2), raw))


def render_reference(exe, workdir, name, bank, commands):
    """Run one scenario through libmoy; returns a list of int16 samples."""
    script_path = write_scenario(workdir, name, bank, commands)
    pcm_path = os.path.join(workdir, name + ".pcm")
    proc = subprocess.run([exe, script_path, pcm_path], capture_output=True,
                          text=True)
    if proc.returncode != 0:
        raise RuntimeError("libmoy_render failed on %s:\n%s" % (name, proc.stderr))
    return _read_pcm(pcm_path)


def render_native(mp_exe, workdir, name, bank, commands):
    """Run one scenario through the NATIVE moy_audio module under a real
    MicroPython VM -- the binding the T-Deck and the web runner actually load."""
    script_path = write_scenario(workdir, name, bank, commands)
    pcm_path = os.path.join(workdir, name + ".mp.pcm")
    proc = subprocess.run(
        [mp_exe, os.path.join(_HERE, "mp_render.py"), script_path, pcm_path],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("mp_render failed on %s:\n%s%s"
                           % (name, proc.stdout, proc.stderr))
    return _read_pcm(pcm_path)


def find_micropython():
    """The unix-port binary built WITH the moy_audio usermod, if someone made
    one. Returns None otherwise -- the native pass is then skipped, since
    building MicroPython is not something a test run should do on its own."""
    env = os.environ.get("MOYBYTE_MICROPYTHON")
    if env and os.path.exists(env):
        return env
    cand = os.path.join(
        _ROOT, "firmware", "lilygo_t_deck_plus_micropython", ".build",
        "lvgl_micropython", "lib", "micropython", "ports", "unix",
        "build-moyaudio", "micropython")
    return cand if os.path.exists(cand) else None


# -- the Python engine -------------------------------------------------------

def render_python(bank_dict, commands):
    """Run one scenario through runtime/audio.AudioEngine; int16 samples."""
    from runtime.audio import AudioBank, AudioEngine

    engine = AudioEngine(AudioBank.from_dict(bank_dict), rate=RATE)
    out = []
    for line in commands:
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        if cmd == "sfx":
            n = int(args[0])
            chan = int(args[1]) if len(args) > 1 else -1
            engine.play_sfx(n, None if chan < 0 else chan)
        elif cmd == "beep":
            engine.play_beep(float(args[0]), float(args[1]))
        elif cmd == "music":
            loop = bool(int(args[1])) if len(args) > 1 else True
            engine.play_music(int(args[0]), loop)
        elif cmd == "music_stop":
            engine.stop_music()
        elif cmd == "sound_stop":
            chan = int(args[0]) if args else -1
            engine.stop(None if chan < 0 else chan)
        elif cmd == "volume":
            engine.set_volume(int(args[0]))
        elif cmd == "render":
            n = int(args[0])
            buf = bytearray(n * 2)
            engine.render_into(buf, n)
            out.extend(struct.unpack("<%dh" % n, bytes(buf)))
        else:
            raise ValueError("unknown command " + cmd)
    return out


# -- comparison --------------------------------------------------------------

BLOCK = 512          # ~23 ms at 22050: long enough for a stable RMS, short
                     # enough that a fade or an arpeggio step shows up as its
                     # own block rather than being averaged away.


def _rms(xs):
    if not xs:
        return 0.0
    return math.sqrt(sum(float(x) * x for x in xs) / len(xs))


def _crossings(xs):
    return sum(1 for i in range(1, len(xs)) if (xs[i - 1] < 0) != (xs[i] < 0))


def _corr(a, b):
    """Normalised cross-correlation of two equal-length blocks, in [-1, 1].
    Silence-vs-silence is defined as 1.0 (identical, trivially)."""
    na = math.sqrt(sum(float(x) * x for x in a))
    nb = math.sqrt(sum(float(x) * x for x in b))
    if na == 0.0 and nb == 0.0:
        return 1.0
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(float(x) * float(y) for x, y in zip(a, b)) / (na * nb)


def compare(ref, got):
    """Metrics for one scenario. Returns a dict; `ok` folds the thresholds."""
    n = min(len(ref), len(got))
    ref, got = ref[:n], got[:n]
    r_rms, g_rms = _rms(ref), _rms(got)
    r_cross, g_cross = _crossings(ref), _crossings(got)

    # Level: relative RMS error over the whole scenario, and the worst block.
    scale = max(r_rms, g_rms, 1.0)
    rms_err = abs(r_rms - g_rms) / scale
    worst_block_err, worst_corr = 0.0, 1.0
    for i in range(0, n - BLOCK + 1, BLOCK):
        rb, gb = ref[i:i + BLOCK], got[i:i + BLOCK]
        br, bg = _rms(rb), _rms(gb)
        blk_scale = max(br, bg, 1.0)
        # A block that is silence in both is not an error (it is agreement).
        if blk_scale > 1.0:
            worst_block_err = max(worst_block_err, abs(br - bg) / blk_scale)
        worst_corr = min(worst_corr, _corr(rb, gb))

    # Pitch: crossing counts, relative. Silence has none, which agrees trivially.
    cross_scale = max(r_cross, g_cross, 1)
    cross_err = abs(r_cross - g_cross) / cross_scale

    m = {
        "n": n,
        "rms_ref": r_rms, "rms_got": g_rms, "rms_err": rms_err,
        "block_err": worst_block_err,
        "cross_ref": r_cross, "cross_got": g_cross, "cross_err": cross_err,
        "corr": worst_corr,
        "len_ref": len(ref), "len_got": len(got),
    }
    m["ok"] = (rms_err <= RMS_TOL and worst_block_err <= BLOCK_TOL
               and cross_err <= CROSS_TOL and worst_corr >= CORR_TOL)
    return m


# Device-precision thresholds. Level and pitch are unaffected by single
# precision and stay tight -- a swapped waveform, a 4x mixdown or a wrong
# arpeggio rate blows through them. Correlation is loose BECAUSE it is the one
# metric precision moves (see the module docstring); the strict pass is what
# actually gates, and these numbers sit just below the measured float32 spread.
RMS_TOL = 0.02       # 2% overall level
BLOCK_TOL = 0.06     # 6% on any 23 ms block (fades/arpeggios move fast)
CROSS_TOL = 0.02     # 2% on zero-crossing rate == pitch
CORR_TOL = 0.80      # waveform shape, worst block; float32 measures >= 0.875


def run_strict(work, exe, only=None, verbose=False):
    """Every sample must match the double-precision reference. Returns the list
    of (name, exact_fraction, max_delta) that did not."""
    bad = []
    for name, bank, commands in scenarios():
        if only and name not in only:
            continue
        ref = render_reference(exe, work, name, bank, commands)
        got = render_python(bank, commands)
        n = min(len(ref), len(got))
        same = sum(1 for i in range(n) if ref[i] == got[i])
        worst = max((abs(ref[i] - got[i]) for i in range(n)), default=0)
        if same != len(ref) or len(ref) != len(got):
            bad.append((name, same / float(len(ref) or 1), worst))
            if verbose:
                print("FAIL %-18s exact %7.3f%%  max|delta| %d"
                      % (name, 100.0 * same / (len(ref) or 1), worst))
    return bad


def run_parity(verbose=False, only=None):
    """Compare the Python engine to the vendored libmoy, strictly and at device
    precision. True if both pass; None (skipped) with no C compiler."""
    with tempfile.TemporaryDirectory(prefix="moy-audio-parity-") as work:
        exe = build_reference(work)
        if exe is None:
            if verbose:
                print("no C compiler -- skipping. (The device and the web "
                      "runner COMPILE libmoy, so they are unaffected; this only "
                      "checks the host's Python twin of it.)")
            return None

        if verbose:
            print("== strict: bit-exact vs libmoy at CPython's precision ==")
        strict_bad = run_strict(work, build_reference(work, double=True),
                                only=only, verbose=verbose)
        if verbose and not strict_bad:
            print("all scenarios bit-identical, every sample.\n")

        # The native binding, if a unix-port MicroPython was built with it. This
        # is libmoy compiled INTO the module the boards load, so the bar is
        # bit-equality: any difference is the shim mangling a verb, the bank
        # crossing, or the render buffer -- never the synth.
        mp_exe = find_micropython()
        if verbose:
            print("== native: the moy_audio module under a real MicroPython VM ==")
        native_bad = []
        if mp_exe is None:
            if verbose:
                print("no unix-port build with the usermod -- skipped. Build one:\n"
                      "  ln -s $PWD/firmware/lilygo_t_deck_plus_micropython/native"
                      "/moy_audio /tmp/usermods/moy_audio\n"
                      "  make -C <micropython>/ports/unix VARIANT=standard "
                      "BUILD=build-moyaudio USER_C_MODULES=/tmp/usermods\n")
        else:
            for name, bank, commands in scenarios():
                if only and name not in only:
                    continue
                ref = render_reference(exe, work, name, bank, commands)
                got = render_native(mp_exe, work, name, bank, commands)
                if ref != got:
                    same = sum(1 for a, b in zip(ref, got) if a == b)
                    native_bad.append((name, same, len(ref)))
                    if verbose:
                        print("FAIL %-18s %d/%d exact" % (name, same, len(ref)))
            if verbose and not native_bad:
                print("all scenarios bit-identical to libmoy, every sample.\n")

        if verbose:
            print("== device precision: libmoy exactly as vendored (float) ==")
        failures = []
        for name, bank, commands in scenarios():
            if only and name not in only:
                continue
            ref = render_reference(exe, work, name, bank, commands)
            got = render_python(bank, commands)
            m = compare(ref, got)
            if verbose:
                print("%s %-18s rms %8.1f/%8.1f (%5.2f%%)  blk %5.2f%%  "
                      "cross %5d/%5d (%5.2f%%)  corr %6.4f"
                      % ("ok  " if m["ok"] else "FAIL", name,
                         m["rms_ref"], m["rms_got"], m["rms_err"] * 100,
                         m["block_err"] * 100, m["cross_ref"], m["cross_got"],
                         m["cross_err"] * 100, m["corr"]))
            if not m["ok"]:
                failures.append((name, m))
        if verbose and (failures or strict_bad or native_bad):
            print("\n%d scenario(s) diverge from libmoy (%d not bit-exact, "
                  "%d native)" % (len(failures), len(strict_bad), len(native_bad)))
        return not failures and not strict_bad and not native_bad


if __name__ == "__main__":
    only = set(sys.argv[1:]) - {"-v", "--verbose"}
    result = run_parity(verbose=True, only=only or None)
    sys.exit(0 if result is not False else 1)
