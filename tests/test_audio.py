"""Headless tests for the v0.4 audio core (#16): the shared sound data model +
synth/mixer (runtime/audio.py), the host audio API surface (host_app.make_api +
FakeAudio), the .kcart sounds.json store (kid_carts), and the Beeper demo cart
making sound through the fake backend on the shared console (host == device).

No sound hardware: FakeAudio records calls AND drives the real AudioEngine, so the
mixer is exercised under SDL_VIDEODRIVER=dummy.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import audio  # noqa: E402


# -- note math -------------------------------------------------------------

def test_note_to_freq_and_name_to_pitch():
    a4 = audio.name_to_pitch("A4")
    assert audio.note_to_freq(a4) == 440.0
    # an octave up doubles the frequency
    a5 = audio.name_to_pitch("A5")
    assert abs(audio.note_to_freq(a5) - 880.0) < 1e-6
    # C4 is 9 semitones below A4
    assert audio.name_to_pitch("C4") == a4 - 9
    # sharps + rests
    assert audio.name_to_pitch("C#4") == audio.name_to_pitch("C4") + 1
    assert audio.note_to_freq(audio.REST) == 0.0
    assert audio.name_to_pitch("nonsense") == audio.REST


def test_freq_to_pitch_roundtrips_through_note_to_freq():
    for name in ("C3", "E4", "A4", "G5"):
        p = audio.name_to_pitch(name)
        assert audio.AudioEngine.freq_to_pitch(audio.note_to_freq(p)) == p


# -- data model ------------------------------------------------------------

def test_sfx_normalizes_and_roundtrips():
    s = audio.SFX([[60, 0, 6], [62], [-1, 9, 99]], speed=12, loop=True)
    # short steps fill defaults; wave/vol clamp into range; rest stays -1
    assert s.steps[1] == [62, audio.WAVE_SQUARE, 6]
    assert s.steps[2] == [audio.REST, 3, 7]
    assert s.speed == 12 and s.loop is True
    s2 = audio.SFX.from_dict(s.to_dict())
    assert s2.to_dict() == s.to_dict()


def test_audio_bank_default_and_roundtrip():
    bank = audio.AudioBank.default()
    assert len(bank.sfx) == 3 and len(bank.music) == 1
    assert bank.get_sfx(0) is not None and bank.get_sfx(99) is None
    assert bank.get_music(0).pattern == [0, 1, 0, 2]
    bank2 = audio.AudioBank.from_dict(bank.to_dict())
    assert bank2.to_dict() == bank.to_dict()
    # empty / missing data degrades to empty, not a crash
    assert audio.AudioBank.from_dict(None).to_dict() == {"sfx": [], "music": []}


# -- mixer -----------------------------------------------------------------

def test_render_produces_pcm_and_silence_when_idle():
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    # nothing playing -> all-zero PCM of the right length (16-bit mono)
    pcm = eng.render(100)
    assert len(pcm) == 200
    assert set(pcm) == {0}
    assert not eng.is_active()


def test_render_into_matches_render_and_reuses_buffer():
    # The device I2S backend (#16) feeds I2S from render_into() into ONE persistent
    # buffer per frame (so the non-blocking write's held pointer never sees a GC'd /
    # reallocated buffer). render_into must produce byte-identical output to render()
    # and report the frames written, so the two seams stay equivalent.
    a = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    b = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    a.play_sfx(0)
    b.play_sfx(0)
    buf = bytearray(400)                       # 200 frames * 2 bytes, reused below
    n = b.render_into(buf, 200)
    assert n == 200
    assert bytes(buf) == a.render(200)         # same samples from the same start
    # a second call reuses the SAME buffer object (no per-frame allocation)
    before = id(buf)
    n2 = b.render_into(buf, 200)
    assert n2 == 200 and id(buf) == before
    assert bytes(buf) == a.render(200)         # both engines advanced in lockstep


def test_render_into_idle_is_silent_and_returns_zero_for_empty():
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    buf = bytearray(100)
    assert eng.render_into(buf, 0) == 0        # nframes<=0 -> no work
    assert eng.render_into(buf, 50) == 50      # idle engine -> silence written
    assert set(buf) == {0}


def test_play_sfx_makes_nonzero_audio_then_finishes():
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    eng.play_sfx(0)
    assert eng.is_active()
    pcm = eng.render(400)            # render across the whole short SFX
    assert any(b != 0 for b in pcm)  # actual sound came out
    # a non-looping SFX eventually stops
    for _ in range(50):
        eng.render(400)
    assert not eng.is_active()


def test_out_of_range_sfx_is_silent_noop():
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    eng.play_sfx(99)                 # no such SFX
    assert not eng.is_active()
    assert set(eng.render(50)) == {0}


def test_beep_plays_a_tone_without_a_bank_entry():
    eng = audio.AudioEngine(audio.AudioBank(), rate=8000)   # empty bank
    eng.play_beep(440, 0.05)
    assert eng.is_active()
    assert any(b != 0 for b in eng.render(400))


def test_volume_zero_silences_output():
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    eng.set_volume(0.0)
    eng.play_sfx(0)
    assert set(eng.render(400)) == {0}    # muted
    eng.set_volume(1.0)
    eng.play_sfx(0)
    assert any(b != 0 for b in eng.render(400))


def test_music_loops_and_stops():
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    eng.play_music(0)
    assert eng.is_active()
    # advance well past the 4-slot phrase; looping keeps it active
    for _ in range(20):
        eng.render(800)
    assert eng.is_active()
    eng.stop_music()
    assert eng._music is None


def test_stop_all_silences_everything():
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    eng.play_sfx(0)
    eng.play_music(0)
    eng.stop()
    assert not eng.is_active()


# -- host API surface (host_app.make_api + FakeAudio) ----------------------

class _Input:
    def held(self, n):
        return False

    def pressed(self, n):
        return False


def test_make_api_exposes_audio_and_drives_engine():
    from runtime import host_app
    from runtime.canvas import Canvas
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    fake = host_app.FakeAudio(eng)
    api = host_app.make_api(Canvas(32, 32), _Input(), {}, None, fake)
    for name in ("sfx", "beep", "music", "music_stop", "sound_stop", "volume"):
        assert name in api
    api["sfx"](1)
    api["beep"](440)
    api["music"](0)
    api["volume"](0.5)
    api["music_stop"]()
    api["sound_stop"]()
    # the fake recorded every call ...
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["sfx", "beep", "music", "volume", "music_stop", "sound_stop"]
    # ... and routed them through the real engine (volume took effect)
    assert eng.volume == 0.5


def test_fake_audio_tick_renders_frames():
    from runtime import host_app
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    fake = host_app.FakeAudio(eng)
    fake.sfx(0)
    fake.tick(1 / 30)
    assert fake.rendered == int(8000 / 30)


# -- .kcart store: sounds.json round-trip ----------------------------------

def test_kid_carts_saves_and_loads_sounds(tmp_path):
    from runtime import kid_carts
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    c = kid_carts.create("Tune Cart", root, src="def _draw():\n    cls(1)\n")
    assert c["sounds"] is None                     # new cart has no bank yet
    bank = audio.AudioBank.default().to_dict()
    kid_carts.save_sounds(c, bank)
    reloaded = kid_carts.load(c["path"])
    assert reloaded["sounds"] == bank              # persisted + reloaded intact
    # a corrupt sounds.json degrades to None, never a crash
    with open(c["path"] + "/sounds.json", "w") as f:
        f.write("{not json")
    assert kid_carts.load(c["path"])["sounds"] is None


# -- the Beeper demo cart, on the shared console ----------------------------

def test_beeper_demo_cart_makes_sound(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "Beeper":
            ws.launcher.sel = i
            break
    else:
        raise AssertionError("Beeper demo cart was not seeded")
    ws.open()
    assert ws.screen == "desktop" and ws.cart_error is None
    assert isinstance(ws.audio, host_app.FakeAudio)
    # _init() should have started the looping music (music_on defaults to 1)
    assert ("music", 0, True) in ws.audio.calls
    # run attract mode; it auto-taps pads -> sfx/beep calls accrue, mixer renders
    for _ in range(120):
        ws.frame(1 / 30)
    kinds = [c[0] for c in ws.audio.calls]
    assert "sfx" in kinds and "beep" in kinds
    assert ws.audio.rendered > 0                    # tick() pulled PCM each frame


def test_cart_without_audio_backend_still_runs(tmp_path):
    # A Workstation with no make_audio injected falls back to _SilentAudio so a
    # cart's sfx()/beep() are harmless no-ops (and make_api stays callable).
    from runtime import console, kid_carts
    from runtime.canvas import Canvas
    from runtime.input import InputState
    from runtime import host_app
    root = str(tmp_path / "carts")
    kid_carts.ensure_dirs(root)
    kid_carts.create("Noisy", root,
                     src="def _draw():\n    sfx(0)\n    beep(440)\n    cls(1)\n")
    ws = console.Workstation(host_app._NullComp(), Canvas(320, 240), InputState(),
                             kid_carts.scan(root))
    ws.make_api = host_app.make_api          # API present ...
    ws.make_audio = None                     # ... but no audio backend
    ws.open()
    ws.frame(1 / 30)
    assert ws.cart_error is None             # silent no-op, not a crash
    assert isinstance(ws.audio, console._SilentAudio)
