"""`device/device_audio.py`, EXECUTED (#208, the single-consumer list).

515 lines, one importer (`moy_runtime`), and until this file nothing off-glass
ever ran a statement of it. The net over it was `tests/test_micropython_spike.py`
grepping the frozen source, which is the shape #208 exists to stop: a substring
cannot tell `sfx()` that syncs the bank BEFORE the trigger from one that syncs
after, cannot see the bank crossing once per FRAME instead of once per cart, and
cannot notice a ring top-up that stopped topping up.

What this module owns is narrow, and this suite aims at exactly that:

  * the bank crosses to libmoy ONCE per cart (and again only on `AudioBank.rev`),
  * the six SPEC.md 8.2 verbs forward with the arguments the C expects,
  * the I2S plumbing -- which feed is armed, and the legacy ring top-up.

WHAT IS DELIBERATELY NOT HERE. The SYNTH is not in this file: `moy_audio` is a
binding over vendored libmoy and `tests/test_audio_parity.py` pins it sample for
sample. So `moy_audio` and `machine` are installed as RECORDING DOUBLES at the
import boundary (both are imported lazily inside methods, which is what makes
that possible), and the assertions are about what CROSSES the boundary -- call
order, call counts, argument values -- not about sound.

WHAT CANNOT BE EXECUTED ON A HOST, named rather than left as silence:

  * the I2S peripheral itself. `machine.I2S`'s constructor arguments, the
    non-blocking `irq()` registration and the byte counts handed to `write()`
    are all pinned below; whether the MAX98357 makes a noise, and whether the
    DMA ring drains at 22050 Hz, is glass.
  * the core-1 FreeRTOS feeder task (`moy_audio.audio_start`). Only the call,
    its arguments and the flag its return value sets are observable here.
  * MicroPython's `time.ticks_ms` wrap semantics. The module-level `time`
    binding is swapped for a linear driveable clock (see `_Clock`), so
    `_rate_probe`'s arithmetic is pinned but tick rollover is not.
  * `diag_state`'s OUTER `try`. Every probe inside it already carries its own
    guard, so nothing can reach it -- the two statements this suite leaves
    unexecuted, said out loud rather than left looking like an oversight.

Every sys.modules stub is removed in fixture teardown -- a leaked fake
`moy_audio` would silently break `tests/test_audio_parity.py` in the same
session. The models for the whole shape are `tests/test_banded_panel.py` (the
flat-sibling loader, the recording double) and `tests/test_idle_blank.py`.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device"

from runtime.audio import SFX, AudioBank, MusicTrack            # noqa: E402


def _load_device_audio():
    """Load the REAL file. `device_audio` imports its sibling FLAT
    (`from device_util import _diag_note`), the way the frozen device tree
    resolves it, so that name is installed first -- the same dance
    `tests/test_banded_panel.py::_device_diag` does.

    The module is NOT registered in sys.modules under its own name: nothing on
    the host imports `device_audio`, and leaving it out keeps this suite from
    deciding what some other suite's import would resolve to.
    """
    if "device_util" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "device_util", DEVICE / "device_util.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["device_util"] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location(
        "device_audio", DEVICE / "device_audio.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DA = _load_device_audio()


# -- the doubles ---------------------------------------------------------------


class _Clock:
    """`time.ticks_ms`/`ticks_diff` are MicroPython's, not CPython's, so the
    module-level `time` binding -- exactly the object `import time` produced
    inside the real body -- is swapped for this. `_rate_probe` is its only
    reader, and it wants a driveable clock anyway: the probe's whole thesis is
    that WALL TIME, not summed loop dt, is the reference."""

    def __init__(self):
        self.ms = 0

    def ticks_ms(self):
        return self.ms

    def ticks_diff(self, a, b):
        return a - b


class FakeNative:
    """A recording double for the native `moy_audio` module.

    Every call lands in `calls` as `(name, *args)`, so ORDER is observable --
    which is the point: "sync the bank, then trigger" and "set the rate, load
    the bank, then set the volume" are both sequences a grep cannot check.
    """

    missing = frozenset()           # names an OLDER native module did not have

    def __init__(self, bank_ok=True, start_ok=True, running=False, active=0,
                 sig=(22050, 3, 7.5000, 0.117000), frames=None, raises=(),
                 missing=()):
        self.missing = frozenset(missing)
        self.calls = []
        self.raises = set(raises)
        self.bank_ok = bank_ok
        self.start_ok = start_ok
        self.running_flag = running
        self.active_mask = active
        self.sig = sig
        self.frames = frames
        self.render_fill = 0

    def __getattribute__(self, name):
        """`missing` models a native module built before a verb existed --
        `engine_sig` and `frames_out` are both reached through a bare
        `try: ... except Exception`, so their absence has to be a real
        AttributeError and not a stub returning None."""
        if name in object.__getattribute__(self, "missing"):
            raise AttributeError(name)
        return object.__getattribute__(self, name)

    # -- observation ---------------------------------------------------------

    def _rec(self, name, *args):
        self.calls.append((name,) + args)
        if name in self.raises:
            raise RuntimeError("native %s failed" % name)

    def names(self):
        return [c[0] for c in self.calls]

    def count(self, name):
        return sum(1 for c in self.calls if c[0] == name)

    def last(self, name):
        for c in reversed(self.calls):
            if c[0] == name:
                return c
        raise AssertionError("no %s call: %r" % (name, self.names()))

    def args_of(self, name):
        return [c[1:] for c in self.calls if c[0] == name]

    # -- the verb set device_audio requires ----------------------------------

    def set_rate(self, rate):
        self._rec("set_rate", rate)

    def bank_load(self, text):
        self._rec("bank_load", text)
        return self.bank_ok

    def engine_sig(self):
        self._rec("engine_sig")
        return self.sig

    def volume(self, level):
        self._rec("volume", level)

    def running(self):
        self._rec("running")
        return self.running_flag

    def audio_start(self, bck, ws, dout, rate):
        self._rec("audio_start", bck, ws, dout, rate)
        return self.start_ok

    def sfx(self, n, chan):
        self._rec("sfx", n, chan)

    def beep(self, freq, dur):
        self._rec("beep", freq, dur)

    def music(self, track, loop=1):
        self._rec("music", track, loop)

    def music_stop(self):
        self._rec("music_stop")

    def sound_stop(self, chan):
        self._rec("sound_stop", chan)

    def active(self):
        self._rec("active")
        return self.active_mask

    def render(self, buf, n):
        self._rec("render", n)
        self.render_fill = (self.render_fill + 1) & 0xFF
        for i in range(min(len(buf), n * 2)):
            buf[i] = self.render_fill

    def frames_out(self):
        self._rec("frames_out")
        return self.frames


def _fake_machine(i2s_fails=False, write_fails=False):
    """A `machine` module with just the two names the fallback feed imports."""
    made = []

    class Pin:
        def __init__(self, n):
            self.n = n

        def __eq__(self, other):
            return isinstance(other, Pin) and other.n == self.n

        def __repr__(self):
            return "Pin(%d)" % self.n

    class I2S:
        TX = "TX"
        MONO = "MONO"

        def __init__(self, port, **kw):
            if i2s_fails:
                raise OSError("no I2S peripheral")
            self.port = port
            self.kw = kw
            self.cb = None
            self.writes = []            # (nbytes, backing bytearray)
            made.append(self)

        def irq(self, cb):
            self.cb = cb

        def write(self, mv):
            if write_fails:
                raise OSError("i2s write failed")
            self.writes.append((len(mv), mv.obj))
            return len(mv)

    mod = types.ModuleType("machine")
    mod.I2S = I2S
    mod.Pin = Pin
    mod.made = made
    return mod


class _Diag:
    def __init__(self):
        self.lines = []

    def logp(self, tag, msg):
        self.lines.append((tag, msg))

    def all(self, tag):
        return [m for t, m in self.lines if t == tag]

    def line(self, tag):
        got = self.all(tag)
        return got[0] if got else None


class Engine:
    """The MODEL half of `runtime.audio.AudioEngine` -- bank / rate / master /
    set_volume, which is the whole surface DeviceAudio touches.

    The real class is not constructed here on purpose: its `__init__` builds the
    ctypes libmoy binding, i.e. the synthesizer this module deliberately does
    not own and `tests/test_audio_parity.py` owns instead. `set_volume`'s clamp
    is copied from it verbatim, because `volume()` forwarding the CLAMPED master
    rather than the raw level is one of the things pinned below.
    """

    def __init__(self, bank=None, master=7):
        self.bank = bank if bank is not None else make_bank()
        self.rate = 11025           # what the shared engine is built at
        self.master = master
        self.set_calls = []

    def set_volume(self, level):
        self.set_calls.append(level)
        try:
            v = int(level)
        except (TypeError, ValueError):
            return
        self.master = 0 if v < 0 else (7 if v > 7 else v)


def make_bank(nsfx=3, nmusic=1, sfx_speeds=None, music_speeds=None):
    """A REAL `AudioBank` -- the bank crossing is `json.dumps(bank.to_dict())`,
    so the payload assertions are only worth anything against the real one."""
    sfx = []
    for i in range(nsfx):
        speed = sfx_speeds[i] if sfx_speeds and i < len(sfx_speeds) else 8
        sfx.append(SFX([[36 + i, 0, 6]], speed=speed))
    music = []
    for i in range(nmusic):
        speed = music_speeds[i] if music_speeds and i < len(music_speeds) else 4
        music.append(MusicTrack([0], speed=speed))
    return AudioBank(sfx, music)


# -- the harness ---------------------------------------------------------------


_MISSING = object()
_STUBBED = ("moy_audio", "machine", "moybyte_diag")


class Harness:
    def __init__(self, diag, clock):
        self.diag = diag
        self.clock = clock
        self.na = None
        self.machine = None

    def build(self, engine=None, native=True, i2s_fails=False,
              write_fails=False, **na_kw):
        """Construct the REAL DeviceAudio against the doubles.

        `native=False` puts `None` in sys.modules under `moy_audio`, which is
        the documented way to make `import moy_audio` raise -- i.e. a build with
        the usermod left out.
        """
        if native:
            self.na = FakeNative(**na_kw)
            sys.modules["moy_audio"] = self.na
        else:
            assert not na_kw
            self.na = None
            sys.modules["moy_audio"] = None
        self.machine = _fake_machine(i2s_fails, write_fails)
        sys.modules["machine"] = self.machine
        return DA.DeviceAudio(engine if engine is not None else Engine())

    @property
    def i2s(self):
        made = self.machine.made
        return made[-1] if made else None


@pytest.fixture
def h(monkeypatch):
    saved = {k: sys.modules.get(k, _MISSING) for k in _STUBBED}
    clock = _Clock()
    monkeypatch.setattr(DA, "time", clock)
    # The one-shot latch is module-level state that outlives a test; setting it
    # here clears whatever a prior test left AND registers the restore.
    monkeypatch.setattr(DA, "_SELFDUMP_DONE", False)
    diag = _Diag()
    dmod = types.ModuleType("moybyte_diag")
    dmod.logp = diag.logp
    sys.modules["moybyte_diag"] = dmod
    try:
        yield Harness(diag, clock)
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _core1(h, **kw):
    """A backend on the preferred feed: the core-1 task took the peripheral."""
    return h.build(start_ok=True, **kw)


def _legacy(h, **kw):
    """A backend on the fallback feed: no core-1 task, so tick() feeds I2S."""
    return h.build(start_ok=False, **kw)


# -- construction: which feed is armed -----------------------------------------


def test_the_device_rate_is_forced_onto_the_shared_engine(h):
    """The shared AudioEngine is built at 11025; the device runs 22050 --
    PICO-8's own, and the rate SPEC.md 8.3's character is DEFINED at. Nothing
    else re-states it, so a backend that left the engine at 11025 would hand
    the fallback render path the wrong clock."""
    da = _core1(h)
    assert Engine().rate == 11025               # what it was handed
    assert da.engine.rate == 22050              # what it made of it
    assert h.na.last("set_rate") == ("set_rate", 22050)


def test_the_rate_reaches_the_native_module_before_the_bank(h):
    """`bank_load` resets libmoy's engine state, so the order matters twice
    over: the rate first, and the master level LAST because the load clears
    it."""
    da = _core1(h)
    order = [c for c in h.na.names() if c in ("set_rate", "bank_load", "volume")]
    assert order == ["set_rate", "bank_load", "volume"]
    assert h.na.last("volume") == ("volume", da.engine.master)


def test_each_backend_gets_its_own_diag_sequence_number(h):
    """`diag_state`'s first field is how a Player diagnostic tells one per-cart
    backend from the next; a constant would make a rebuilt backend invisible."""
    a = _core1(h)
    b = _core1(h)
    assert b._diag_seq == a._diag_seq + 1
    assert a.diag_state()[0] == a._diag_seq


def test_the_core1_feeder_is_preferred_and_takes_the_peripheral(h):
    """The crackle fix (#41): when the task starts, tick() must do no feeding
    and `machine.I2S` must never be opened -- two owners of one peripheral."""
    da = _core1(h)
    assert da._core1 is True
    assert da.i2s is None
    assert h.machine.made == []
    assert h.na.last("audio_start") == (
        "audio_start", DA.I2S_BCK, DA.I2S_WS, DA.I2S_DOUT, DA.AUDIO_RATE)


def test_a_task_that_was_already_alive_is_reported_as_REUSED(h):
    da = _core1(h, running=True)
    assert da._core1 is True and da._reused_core1 is True
    assert "reused" in h.diag.line("audio")


def test_a_task_started_fresh_is_not_reported_as_reused(h):
    da = _core1(h, running=False)
    assert da._reused_core1 is False
    assert "started" in h.diag.line("audio")


def test_an_unanswerable_running_probe_still_starts_the_task(h):
    """`running()` is an older-module hazard, not a precondition: its failure
    only costs the reused/started word in the diag line."""
    da = _core1(h, running=True, raises=("running",))
    assert da._core1 is True and da._reused_core1 is False


def test_a_task_that_declines_falls_back_to_the_legacy_I2S_feed(h):
    da = _legacy(h)
    assert da._core1 is False
    assert da.i2s is not None
    assert "core-1 task unavailable" in " ".join(h.diag.all("audio"))


def test_a_task_that_throws_falls_back_to_the_legacy_I2S_feed(h):
    da = h.build(raises=("audio_start",))
    assert da._core1 is False
    assert da.i2s is not None
    assert "core-1 start failed" in " ".join(h.diag.all("audio"))


def test_the_revert_flag_forces_the_legacy_feed_with_no_rebuild(h, monkeypatch):
    """MOY_AUDIO_CORE1 exists so the crackle fix can be reverted on a board
    without a firmware build; a flag nothing reads is not a revert."""
    monkeypatch.setattr(DA, "MOY_AUDIO_CORE1", False)
    da = h.build(start_ok=True)
    assert da._core1 is False
    assert da.i2s is not None
    assert "audio_start" not in h.na.names()


def test_the_legacy_I2S_is_opened_with_the_boards_pins_and_format(h):
    """Pins, rate and ibuf are the board's, and MONO is what puts samples on
    the MAX98357's one input slot. None of it is reachable on a host beyond
    this: whether the amp makes a sound is glass."""
    da = _legacy(h)
    i2s = h.i2s
    Pin = h.machine.Pin
    assert i2s.port == 0
    assert i2s.kw == {
        "sck": Pin(DA.I2S_BCK), "ws": Pin(DA.I2S_WS), "sd": Pin(DA.I2S_DOUT),
        "mode": h.machine.I2S.TX, "bits": 16,
        "format": h.machine.I2S.MONO, "rate": DA.AUDIO_RATE,
        "ibuf": DA.AUDIO_IBUF,
    }
    assert da.i2s is i2s


def test_the_completion_irq_is_registered_so_write_never_blocks(h):
    """irq() is what flips the port into NON_BLOCKING mode; without it every
    write() stalls the single-threaded desktop loop."""
    da = _legacy(h)
    assert h.i2s.cb == da._on_done


def test_an_I2S_that_will_not_open_is_silent_and_LOUD_about_it(h):
    da = _legacy(h, i2s_fails=True)
    assert da.i2s is None
    assert "I2S UNAVAILABLE" in " ".join(h.diag.all("audio"))
    da.tick(0.02)                                # and it must not crash the loop


# -- no native module: SILENCE, not a crash (#97) ------------------------------


def test_a_build_without_moy_audio_is_silent_and_still_boots(h, capsys):
    """The stated project rule: the Python fallback synth died with moycore
    stage 0, so an absent usermod means the verbs no-op. Nothing may raise, and
    no I2S is opened -- there would be nothing to render into it."""
    da = h.build(native=False)
    assert da._na is None and da.i2s is None and da._core1 is False
    assert h.machine.made == []
    assert "absent" in capsys.readouterr().out


def test_every_verb_no_ops_without_the_native_module(h):
    da = h.build(native=False)
    da.sfx(3)
    da.beep(440)
    da.music(2)
    da.music_stop()
    da.sound_stop()
    da.tick(0.02)
    assert da.is_active() is False
    assert da.diag_state() == (da._diag_seq, 0, 0, -1, -1, 0)


def test_the_master_level_is_still_modelled_without_a_native_module(h):
    """`self.engine` survives for the MODEL: Settings and the store read the
    master level off it, and a build with no amp must still remember it."""
    da = h.build(native=False)
    da.volume(3)
    assert da.engine.master == 3


# -- the bank: ONE crossing per cart -------------------------------------------


def test_the_bank_crosses_as_the_sounds_json_libmoys_own_parser_reads(h):
    da = _core1(h)
    payload = h.na.last("bank_load")[1]
    assert json.loads(payload) == da.engine.bank.to_dict()


def test_the_bank_crosses_once_per_cart_not_once_per_frame(h):
    """The docstring's headline claim, and a testable one: a ~20 KB transient
    string per frame is what the rev compare exists to prevent."""
    da = _core1(h)
    assert h.na.count("bank_load") == 1
    for _ in range(50):
        da.sfx(1)
        da.music(0)
        da.tick(0.016)
    assert h.na.count("bank_load") == 1


def test_an_edited_bank_is_re_pushed_exactly_once_at_the_next_trigger(h):
    """The Music editor mutates the bank IN PLACE and bumps `rev`; libmoy holds
    its own parsed copy, so the next trigger has to re-parse -- once."""
    da = _core1(h)
    da.engine.bank.touch()
    assert h.na.count("bank_load") == 1          # not eagerly, at the trigger
    da.sfx(0)
    assert h.na.count("bank_load") == 2
    da.sfx(0)
    da.music(0)
    assert h.na.count("bank_load") == 2


def test_the_re_push_precedes_the_trigger_it_was_provoked_by(h):
    """Sync AFTER the trigger plays the OLD bank and the edit is heard one
    trigger late -- an ordering a source grep cannot see."""
    da = _core1(h)
    da.engine.bank.touch()
    h.na.calls.clear()
    da.sfx(4)
    order = [c for c in h.na.names() if c in ("bank_load", "sfx")]
    assert order == ["bank_load", "sfx"]


def test_a_swapped_in_bank_is_picked_up_when_its_rev_differs(h):
    da = _core1(h)
    fresh = make_bank(nsfx=5)
    fresh.touch()                                # rev 1 against the pushed rev 0
    da.engine.bank = fresh
    da.sfx(0)
    assert json.loads(h.na.last("bank_load")[1]) == fresh.to_dict()


def test_a_rejected_bank_is_reported_and_not_retried_every_trigger(h, capsys):
    """Over libmoy's fixed capacities the bank is left zeroed and SILENT, which
    is otherwise a mystery -- so it is said out loud. But `_bank_rev` still
    advances: re-parsing a bank libmoy already refused, once per trigger, would
    cost the whole ~20 KB crossing for nothing."""
    da = _core1(h, bank_ok=False)
    assert "REJECTED" in " ".join(h.diag.all("audio"))
    assert "rejected" in capsys.readouterr().out
    assert da._bank_rev == da.engine.bank.rev
    da.sfx(0)
    assert h.na.count("bank_load") == 1


def test_beep_does_not_re_push_the_bank(h):
    """Pinned as intent, not accident: libmoy's beep synthesises a tone and
    reads no bank entry, so it is the one verb with nothing to re-parse for."""
    da = _core1(h)
    da.engine.bank.touch()
    da.beep(440)
    assert h.na.count("bank_load") == 1


def test_the_BANKSIG_line_fingerprints_what_actually_crosses(h):
    """The celeste "still too fast" hunt's instrument: frac>0 says the cart on
    the card carries the authored fractional speeds, frac=0 says an old
    conversion rounded them. Needs no serial RX."""
    bank = make_bank(nsfx=12, nmusic=6,
                     sfx_speeds=[7.5, 8, 3.75, 8, 8, 8, 8, 8, 8, 8, 15.5, 8],
                     music_speeds=[4, 4, 4, 4, 0.117, 4])
    _core1(h, engine=Engine(bank))
    line = h.diag.all("BANKSIG")[0]
    assert "sfx=12" in line
    assert "frac=3" in line
    assert "s10=15.5000" in line
    assert "m4=0.117000" in line


def test_a_short_bank_reports_the_sentinel_rather_than_indexing_off_the_end(h):
    """Exactly 10 sfx and exactly 4 music: the two off-by-one boundaries. An
    IndexError here is swallowed by the diag guard, so the failure mode of a
    wrong comparison is a BANKSIG line that silently stops appearing."""
    bank = make_bank(nsfx=10, nmusic=4)
    _core1(h, engine=Engine(bank))
    line = h.diag.all("BANKSIG")[0]
    assert "sfx=10" in line and "s10=-1.0000" in line and "m4=-1.000000" in line


def test_the_C_side_signature_is_read_back_after_the_load(h):
    """The last unverified hop: what the C actually HOLDS, not what was sent."""
    _core1(h, sig=(22050, 12, 7.5, 0.117))
    lines = h.diag.all("BANKSIG")
    assert any(l.startswith("C: rate=22050 nsfx=12") for l in lines)
    assert "s10=7.5000" in lines[-1] and "m4=0.117000" in lines[-1]


def test_an_older_native_module_with_no_engine_sig_still_loads_the_bank(h):
    """Both BANKSIG blocks are diag-only and guarded; losing the readback must
    not cost the push."""
    da = _core1(h, missing=("engine_sig",))
    assert h.na.count("bank_load") == 1
    assert da._bank_rev == da.engine.bank.rev
    assert h.diag.all("BANKSIG") == ["sfx=3 frac=0 s10=-1.0000 m4=-1.000000"]


def test_pushing_a_bank_with_no_native_module_is_a_no_op(h):
    """The guard that makes `_push_bank` safe to call from anywhere; both of its
    real callers check first, so nothing else would ever notice it going."""
    da = h.build(native=False)
    da._push_bank()
    assert da._bank_rev is None
    assert h.diag.all("BANKSIG") == []


def test_a_broken_diag_never_costs_the_bank_push(h, monkeypatch):
    """Both BANKSIG blocks are instrumentation wrapped around the one crossing
    that matters. A card that went away mid-log must not take the bank with
    it."""
    da = _core1(h)

    def boom(tag, msg):
        raise OSError("card gone")

    monkeypatch.setattr(DA, "_diag_note", boom)
    da.engine.bank.touch()
    da.sfx(0)
    assert h.na.count("bank_load") == 2
    assert h.na.count("sfx") == 1


def test_a_native_module_that_throws_at_boot_never_fails_the_boot(h):
    """`__init__` must come up whatever the usermod does -- a board that cannot
    boot because audio failed is far worse than a quiet one."""
    da = h.build(raises=("set_rate",))
    assert "bank push failed" in " ".join(h.diag.all("audio"))
    assert da._core1 is True                     # ...and the feed still started


# -- the six SPEC.md 8.2 verbs -------------------------------------------------


def test_sfx_forwards_an_int_id_and_the_auto_channel_sentinel(h):
    da = _core1(h)
    da.sfx("5")
    assert h.na.last("sfx") == ("sfx", 5, -1)
    da.sfx(2, 3)
    assert h.na.last("sfx") == ("sfx", 2, 3)
    da.sfx(2, 0)
    assert h.na.last("sfx") == ("sfx", 2, 0)      # channel 0 is not "auto"


def test_beep_forwards_floats_and_a_default_duration(h):
    da = _core1(h)
    da.beep(440)
    assert h.na.last("beep") == ("beep", 440.0, 0.15)
    da.beep(220, 0.4)
    assert h.na.last("beep") == ("beep", 220.0, 0.4)


def test_music_forwards_the_loop_flag_as_the_C_expects_it(h):
    da = _core1(h)
    da.music(2)
    assert h.na.last("music") == ("music", 2, 1)
    da.music("3", loop=False)
    assert h.na.last("music") == ("music", 3, 0)


def test_music_stop_and_sound_stop_forward_straight_through(h):
    da = _core1(h)
    da.music_stop()
    assert h.na.last("music_stop") == ("music_stop",)
    da.sound_stop()
    assert h.na.last("sound_stop") == ("sound_stop", -1)
    da.sound_stop(2)
    assert h.na.last("sound_stop") == ("sound_stop", 2)


def test_volume_hands_the_CLAMPED_master_to_the_native_module(h):
    """SPEC.md 8.2's level is 0..7. The model clamps; forwarding the raw level
    would hand libmoy a number outside the scale it and the note `vol` column
    share."""
    da = _core1(h)
    da.volume(99)
    assert da.engine.master == 7 and h.na.last("volume") == ("volume", 7)
    da.volume(-4)
    assert da.engine.master == 0 and h.na.last("volume") == ("volume", 0)
    da.volume(3)
    assert h.na.last("volume") == ("volume", 3)


def test_volume_keeps_the_model_in_step_before_it_touches_the_native_side(h):
    """The Settings surface and the store read `engine.master`; the level a
    board plays at and the level it shows must be one number."""
    da = _core1(h)
    da.volume(2)
    assert da.engine.set_calls[-1] == 2
    assert da.engine.master == 2


def test_a_throwing_native_volume_never_reaches_the_frame_loop(h):
    da = _core1(h, raises=("volume",))
    da.volume(4)
    assert da.engine.master == 4


def test_is_active_asks_libmoy_because_libmoy_owns_the_sequencers(h):
    """The redraw gate and the Music editor's preview both ask this; the Python
    engine's idle voices cannot answer it since the twin died."""
    da = _core1(h, active=0)
    assert da.is_active() is False
    h.na.active_mask = 0b0100
    assert da.is_active() is True


def test_a_throwing_active_probe_reads_as_silence(h):
    da = _core1(h, raises=("active",))
    assert da.is_active() is False


# -- the trigger log -----------------------------------------------------------


def test_each_trigger_logs_its_kind_channel_and_the_live_feed(h):
    da = _core1(h)
    da.sfx(7)
    da.beep(440)
    da.music(1)
    assert h.diag.all("AUDIO") == ["sfx=7 chan=auto feed=core1",
                                   "beep=440 chan=auto feed=core1",
                                   "music=1 chan=auto feed=core1"]


def test_the_trigger_log_names_the_channel_and_the_fallback_feed(h):
    da = _legacy(h)
    da.sfx(7, 2)
    assert h.diag.all("AUDIO") == ["sfx=7 chan=2 feed=single"]


def test_the_trigger_log_is_gated_so_a_kid_build_can_drop_it(h, monkeypatch):
    monkeypatch.setattr(DA, "AUDIO_DIAG", False)
    da = _core1(h)
    da.sfx(1)
    assert h.diag.all("AUDIO") == []
    assert h.na.count("sfx") == 1                # ...and the sound still plays


def test_a_broken_diag_never_costs_a_trigger(h, monkeypatch):
    def boom(tag, msg):
        raise OSError("card gone")

    da = _core1(h)
    monkeypatch.setattr(DA, "_diag_note", boom)
    da.sfx(1)
    assert h.na.count("sfx") == 1


# -- diag_state ----------------------------------------------------------------


def test_diag_state_counts_the_four_voices_and_ignores_the_rest(h):
    """`active()` is a bitmask; only the low nibble is the four voices."""
    da = _core1(h, active=0b1011)
    assert da.diag_state() == (da._diag_seq, 1, 0, 0, 3, 0)
    h.na.active_mask = 0xF0
    assert da.diag_state()[4] == 0
    h.na.active_mask = 0xFF
    assert da.diag_state()[4] == 4


def test_diag_state_reports_the_running_task_and_the_reuse_flag(h):
    da = _core1(h, running=True)
    h.na.running_flag = True
    assert da.diag_state()[1:4] == (1, 1, 1)
    h.na.running_flag = False
    assert da.diag_state()[3] == 0


def test_diag_state_reports_minus_one_rather_than_guessing(h):
    """Profiling must never affect playback, so every probe is guarded -- and a
    guard that reported 0 would read as "nothing playing".

    Its OUTER `try` is the one statement in the module this suite cannot
    execute: both probes inside it carry their own guard, including the
    attribute lookups, so nothing is left for it to catch. Said out loud rather
    than left as a gap -- see the module docstring.
    """
    da = _core1(h, raises=("running", "active"))
    seq, core1, reused, running, active, tail = da.diag_state()
    assert (running, active, tail) == (-1, -1, 0)
    assert (core1, reused) == (1, 0)


# -- the legacy feed: the ring top-up ------------------------------------------


def _drive(da, dt, n=1):
    for _ in range(n):
        da.tick(dt)


def test_the_core1_feed_does_no_per_frame_work_at_all(h):
    """"In core-1 mode there is NONE" is the whole crackle fix: the render core
    must not touch I2S.

    `tick`'s `if self._core1: return` is belt AND braces -- a core-1 backend
    never opened a port, so the `self.i2s is None` guard below catches it too.
    Deleting the early-out is therefore an EQUIVALENT mutant here; what keeps
    it honest is `test_the_core1_feeder_is_preferred_and_takes_the_peripheral`,
    which pins the invariant the redundancy rests on.
    """
    da = _core1(h, active=1)
    _drive(da, 0.05, 10)
    assert h.na.count("render") == 0


def test_a_cold_ring_is_filled_to_full_in_one_tick(h):
    """Render exactly rate*dt and the ring hovers near-empty; any 50-60 ms draw
    then under-runs it. So the first tick tops the deep ring UP.

    The ring is AUDIO_IBUF BYTES of 16-bit mono, so a full one is AUDIO_IBUF//2
    FRAMES. Both units are pinned against the number the port was actually
    opened with: a frame/byte slip is the "ratio=2.0" the rate probe exists to
    name, and the derived constant alone would follow the slip.
    """
    da = _legacy(h, active=1)
    da.tick(0.0)
    assert h.i2s.kw["ibuf"] == DA.AUDIO_IBUF
    assert h.na.args_of("render") == [(DA.AUDIO_IBUF // 2,)]
    assert h.i2s.writes[0][0] == DA.AUDIO_IBUF
    assert da._buffered == DA.AUDIO_IBUF // 2


def test_the_top_up_replaces_exactly_what_the_speaker_drained(h):
    da = _legacy(h, active=1)
    da.tick(0.0)
    da._on_done(None)
    h.na.calls.clear()
    da.tick(0.1)                                 # 0.1 s at 22050 = 2205 frames
    assert h.na.args_of("render") == [(2205,)]
    assert da._buffered == DA.AUDIO_IBUF_FRAMES


def test_a_full_ring_is_left_alone(h):
    da = _legacy(h, active=1)
    da.tick(0.0)
    da._on_done(None)
    h.na.calls.clear()
    da.tick(0.0)
    assert h.na.count("render") == 0


def test_a_long_stall_cannot_drive_the_estimate_negative(h):
    """The estimate is deliberately conservative -- it may only UNDER-state
    occupancy. Letting it go negative would make the next top-up over-count and
    the one after it starve."""
    da = _legacy(h, active=1)
    da.tick(10.0)                                # 10 s of drain against 0 buffered
    assert da._buffered == DA.AUDIO_IBUF_FRAMES
    assert h.na.args_of("render") == [(DA.AUDIO_IBUF_FRAMES,)]


def test_a_write_never_overruns_the_persistent_render_buffer(h):
    """The cap is what keeps `memoryview(buf)[:n*2]` inside a buffer sized
    AUDIO_MAX_FRAME*2. (With AUDIO_MAX_FRAME == AUDIO_IBUF_FRAMES today the cap
    itself is unreachable -- see the report's equivalent-mutant note -- so this
    pins the INVARIANT rather than the branch.)"""
    da = _legacy(h, active=1)
    for dt in (0.0, 5.0, 0.02, 1.0, 0.5):
        da._on_done(None)
        da.tick(dt)
    cap = len(da._bufs[0])
    assert all(n <= cap for n, _ in h.i2s.writes)


def test_the_double_buffer_alternates_so_a_copy_in_flight_is_never_touched(h):
    """The port copies the last buffer on a background task; rendering into the
    same bytearray would scribble on a copy in flight."""
    da = _legacy(h, active=1)
    assert da._bufs[0] is not da._bufs[1]        # two bytearrays, not one twice
    da.tick(0.0)
    da._on_done(None)
    da.tick(0.1)
    backing = [obj for _n, obj in h.i2s.writes]
    assert backing[0] is da._bufs[0]
    assert backing[1] is da._bufs[1]
    assert backing[0] is not backing[1]


def test_a_buffer_still_in_flight_is_not_rendered_over(h):
    da = _legacy(h, active=1)
    da.tick(0.0)
    assert da._busy is True
    da.tick(0.1)
    assert h.na.count("render") == 1


def test_the_completion_callback_releases_the_next_render(h):
    """`_on_done` runs via mp_sched between bytecodes, so it only clears the
    flag -- but it must clear the watchdog count with it, or a late irq leaves
    the next stall a tick short."""
    da = _legacy(h, active=1)
    da.tick(0.0)
    da.tick(0.1)                                 # blocked: _busy_ticks -> 1
    assert da._busy_ticks == 1
    da._on_done(None)
    assert da._busy is False and da._busy_ticks == 0
    da.tick(0.1)
    assert h.na.count("render") == 2


def test_a_completion_irq_that_never_fires_is_force_cleared_after_four_ticks(h):
    """The watchdog: by then even a full-ring buffer has long since been
    copied, and a stuck flag is permanent silence."""
    da = _legacy(h, active=1)
    counts = []
    for _ in range(5):
        da.tick(0.05)
        counts.append(h.na.count("render"))
    assert counts == [1, 1, 1, 1, 2]


def test_silence_lets_the_ring_drain_and_resets_the_estimate(h):
    """auto_clear emits silence, not stale DMA, so the next sound must start
    from a known-empty ring."""
    da = _legacy(h, active=1)
    da.tick(0.0)
    da._on_done(None)
    h.na.active_mask = 0
    h.na.calls.clear()
    da.tick(0.01)
    assert h.na.count("render") == 0
    assert da._buffered == 0


def test_the_feed_needs_both_a_port_and_a_native_module(h):
    da = _legacy(h, i2s_fails=True, active=1)
    da.tick(0.05)
    assert h.na.count("render") == 0


def test_a_failing_write_goes_quiet_instead_of_crashing_the_loop(h, capsys):
    """"Audio must never crash the loop": the port is dropped and the console
    keeps running silent."""
    da = _legacy(h, active=1, write_fails=True)
    da.tick(0.0)
    assert "tick failed" in capsys.readouterr().out
    assert da.i2s is None and da._busy is False
    h.na.calls.clear()
    da.tick(0.05)
    assert h.na.count("render") == 0


def test_a_failing_render_also_releases_the_in_flight_flag(h):
    da = _legacy(h, active=1, raises=("render",))
    da.tick(0.0)
    assert da._busy is False and da.i2s is None


# -- the rate probe ------------------------------------------------------------


def _probe(h, da, frames, rate=22050, rend=0, pyr=0, ms=None, dt=2.0):
    if ms is not None:
        h.clock.ms = ms
    h.na.frames = (frames, rate, rend, pyr)
    da.tick(dt)


def test_the_probe_reports_the_rate_the_speaker_really_runs_at(h):
    """The synth is rate-correct by construction, so a uniformly fast or slow
    playback can only be the peripheral consuming at another rate. That is
    invisible from inside the mixer, which is why this exists."""
    da = _core1(h)
    _probe(h, da, frames=0, ms=0)                # first sample: nothing to say
    assert h.diag.all("AUDIORATE") == []
    _probe(h, da, frames=44100, rend=44100, ms=2000)
    assert h.diag.all("AUDIORATE") == [
        "eff=22050 want=22050 ratio=1.000 cum=1.0000 seam=1.0000 pyr=0 "
        "feed=core1"]


def test_a_half_rate_pipe_reads_as_a_ratio_of_one_half(h):
    """"0.5 would be 11025 leaking into the 22050 pipe" -- the docstring's own
    worked example."""
    da = _core1(h)
    _probe(h, da, frames=0, ms=0)
    _probe(h, da, frames=22050, rend=22050, ms=2000)
    line = h.diag.line("AUDIORATE")
    assert "eff=11025" in line and "ratio=0.500" in line and "cum=0.5000" in line


def test_the_probe_prints_only_every_two_seconds(h):
    """"Cheap and quiet when silent": one counter read per frame, one line per
    ~2 s window."""
    da = _core1(h)
    frames = 0
    for i in range(1, 13):                       # 12 x 1 s of wall clock
        frames += 22050
        _probe(h, da, frames=frames, rend=frames, ms=i * 1000, dt=1.0)
    assert len(h.diag.all("AUDIORATE")) == 5     # samples at 2,4,..12; 1st is setup


def test_the_probe_says_nothing_while_nothing_is_playing(h):
    """frames_out stops advancing when the speaker is idle; a line then would
    divide a zero delta by a real window and report a bogus rate."""
    da = _core1(h)
    _probe(h, da, frames=1000, ms=0)
    _probe(h, da, frames=1000, ms=2000)
    _probe(h, da, frames=1000, ms=4000)
    assert h.diag.all("AUDIORATE") == []


def test_the_cumulative_ratio_integrates_across_windows(h):
    """The per-window eff is boundary-sensitive; cum is hardware-clock truth
    after a minute, so a one-off jittery window must not move it far."""
    da = _core1(h)
    _probe(h, da, frames=0, ms=0)
    _probe(h, da, frames=44100, rend=44100, ms=2000)
    _probe(h, da, frames=66150, rend=66150, ms=4000)
    line = h.diag.all("AUDIORATE")[-1]
    assert "eff=11025 " in line                  # the window halved...
    assert "cum=0.7500" in line                  # ...the integral only sagged


def test_the_rendered_versus_written_seam_is_reported(h):
    """rend/out > 1.0 means engine time is consumed that never reaches the
    speaker -- audibly fast playback that both per-side clocks call correct."""
    da = _core1(h)
    _probe(h, da, frames=0, ms=0)
    _probe(h, da, frames=44100, rend=66150, pyr=17, ms=2000)
    line = h.diag.line("AUDIORATE")
    assert "seam=1.5000" in line and "pyr=17" in line


def test_a_window_with_no_wall_time_reports_nothing(h):
    """Two samples inside one clock millisecond: the divisor guard. Without it
    the probe divides by zero inside the frame loop."""
    da = _core1(h)
    _probe(h, da, frames=0, ms=0)
    _probe(h, da, frames=44100, rend=44100, ms=0)
    assert h.diag.all("AUDIORATE") == []


def test_an_older_native_module_with_no_frames_out_says_nothing(h):
    da = _core1(h, missing=("frames_out",))
    da.tick(2.0)
    da.tick(2.0)
    assert h.diag.all("AUDIORATE") == []


def test_the_probe_is_gated_and_costs_nothing_when_off(h, monkeypatch):
    monkeypatch.setattr(DA, "AUDIO_RATE_PROBE", False)
    da = _core1(h)
    _probe(h, da, frames=0, ms=0)
    _probe(h, da, frames=44100, ms=2000)
    assert h.na.count("frames_out") == 0
    assert h.diag.all("AUDIORATE") == []


def test_the_probe_runs_on_the_fallback_feed_too_and_says_so(h):
    da = _legacy(h)
    _probe(h, da, frames=0, ms=0, dt=1.0)
    _probe(h, da, frames=0, ms=1000, dt=1.0)
    _probe(h, da, frames=22050, rend=22050, ms=3000, dt=1.0)
    _probe(h, da, frames=44100, rend=44100, ms=5000, dt=1.0)
    assert "feed=single" in h.diag.line("AUDIORATE")


# -- the one-shot self dump ----------------------------------------------------


def _selfdump_bank():
    return make_bank(nsfx=60, nmusic=6)


def test_the_self_dump_is_off_by_default(h):
    _core1(h, engine=Engine(_selfdump_bank()))
    assert h.na.count("render") == 0


def test_the_self_dump_renders_six_seconds_through_the_devices_own_engine(
        h, monkeypatch, capsys):
    """No mic, no speaker, no I2S in the loop -- the host listener compares the
    base64 against the authored reference. AUDIO_RATE is shrunk here only to
    keep the transcript small; the 6-second span and the 512-frame chunk are
    the real constants under test."""
    da = _core1(h, engine=Engine(_selfdump_bank()))
    monkeypatch.setattr(DA, "AUDIO_SELFDUMP", True)
    monkeypatch.setattr(DA, "AUDIO_RATE", 1024)
    h.na.calls.clear()
    da.engine.bank.touch()
    da._sync_bank()

    assert h.na.args_of("render") == [(512,)] * 12          # 6 * 1024 / 512
    assert h.na.names().index("music") < h.na.names().index("render")
    assert h.na.last("music") == ("music", 4, 1)
    assert h.na.names()[-1] == "music_stop"
    out = capsys.readouterr().out
    assert "AUDIODUMP begin rate=1024 frames=6144" in out
    assert out.count("\nAUDIODUMP end") == 1
    assert out.count("AUDIODUMP ") >= 12


def test_the_self_dump_is_a_one_shot(h, monkeypatch):
    da = _core1(h, engine=Engine(_selfdump_bank()))
    monkeypatch.setattr(DA, "AUDIO_SELFDUMP", True)
    monkeypatch.setattr(DA, "AUDIO_RATE", 1024)
    for _ in range(3):
        da.engine.bank.touch()
        da._sync_bank()
    assert h.na.count("render") == 12


def test_the_self_dump_only_fires_on_a_celeste_sized_bank(h, monkeypatch):
    da = _core1(h, engine=Engine(make_bank(nsfx=59)))
    monkeypatch.setattr(DA, "AUDIO_SELFDUMP", True)
    da.engine.bank.touch()
    da._sync_bank()
    assert h.na.count("render") == 0


def test_a_module_too_old_to_answer_running_still_dumps(h, monkeypatch):
    """The stand-down probe is a guard, not a precondition: an unanswerable
    `running()` must not silently disable the instrument."""
    da = _core1(h, engine=Engine(_selfdump_bank()), raises=("running",))
    monkeypatch.setattr(DA, "AUDIO_SELFDUMP", True)
    monkeypatch.setattr(DA, "AUDIO_RATE", 1024)
    da.engine.bank.touch()
    da._sync_bank()
    assert h.na.count("render") == 12


def test_a_dump_that_throws_mid_render_says_so_and_stops(h, monkeypatch, capsys):
    """Diag only -- it must not take the bank push (or the boot) with it."""
    da = _core1(h, engine=Engine(_selfdump_bank()), raises=("render",))
    monkeypatch.setattr(DA, "AUDIO_SELFDUMP", True)
    monkeypatch.setattr(DA, "AUDIO_RATE", 1024)
    capsys.readouterr()
    da.engine.bank.touch()
    da._sync_bank()
    assert h.na.count("render") == 1
    assert h.na.count("music_stop") == 0
    assert "AUDIODUMP failed" in capsys.readouterr().out
    assert da._bank_rev == da.engine.bank.rev


def test_the_self_dump_stands_down_when_the_core1_task_owns_the_engine(
        h, monkeypatch, capsys):
    """Its render calls must be the engine's ONLY consumer, so it is for a cold
    boot straight into the cart and nothing else."""
    da = _core1(h, engine=Engine(_selfdump_bank()), running=True)
    monkeypatch.setattr(DA, "AUDIO_SELFDUMP", True)
    capsys.readouterr()
    da.engine.bank.touch()
    da._sync_bank()
    assert h.na.count("render") == 0
    assert "AUDIODUMP skipped" in capsys.readouterr().out


# -- the factory ---------------------------------------------------------------


def test_make_audio_wraps_the_engine_it_is_handed(h):
    """`Project._build_audio` builds one engine per run and hands it to the
    injected factory; the backend must keep THAT object, since it is the model
    the Music editor and the master level live on."""
    sys.modules["moy_audio"] = FakeNative()
    sys.modules["machine"] = _fake_machine()
    eng = Engine()
    da = DA.make_audio(eng)
    assert isinstance(da, DA.DeviceAudio)
    assert da.engine is eng


def test_a_stored_master_level_survives_the_per_cart_rebuild(h):
    """CLAUDE.md's audio section: the backend is rebuilt per run, and a level
    set at the launcher used to last exactly until the next cart start. Project
    re-applies it to the engine; what this pins is the backend's half -- the
    level on the engine it is handed reaches libmoy at construction, rather
    than the class default."""
    eng = Engine(master=7)
    eng.set_volume(0)
    da = h.build(engine=eng)
    assert h.na.last("volume") == ("volume", 0)
    assert da.engine.master == 0
