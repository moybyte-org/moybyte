"""Headless tests for the v0.4 audio core (#16): the shared sound data model +
synth/mixer (runtime/audio.py), the host audio API surface (host_app.make_api +
FakeAudio), the .moy sounds.json store (moy_carts), and the Beeper demo cart
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


def test_voice_gen_bumps_on_every_trigger_and_stop():
    # The device core-1 feed (#41) commits a voice to the C mixer only when its
    # _Voice.gen counter changes -- this is the Battle City fix. The old detector
    # used (id(steps), active), but the GC can reuse a freed list's address, so a
    # rapid retrigger of the SAME sfx on the SAME channel read as "unchanged" and was
    # never committed (silent). gen must increment on EVERY play()/stop(), so every
    # trigger -- even an identical one onto a channel it already owns -- is detected.
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    v = eng.voices[0]
    assert v.gen == 0
    # Rapid retriggers of the same SFX on the same forced channel: gen strictly climbs.
    seen = [v.gen]
    for _ in range(10):
        eng.play_sfx(0, chan=0)
        assert v.gen > seen[-1], "play() must bump gen every time (Battle City fix)"
        seen.append(v.gen)
    # A stop is also a committable state change.
    g = v.gen
    eng.stop(0)
    assert v.gen > g
    # The gen sequence has no duplicates -> every trigger is distinguishable.
    assert len(set(seen)) == len(seen)


def test_voice_gen_independent_per_channel():
    # Each channel's gen advances independently, so committing one voice never looks
    # like a change on another (the core-1 dirty scan is per-channel).
    eng = audio.AudioEngine(audio.AudioBank.default(), rate=8000)
    g_before = [v.gen for v in eng.voices]
    eng.play_sfx(0, chan=1)
    for c, v in enumerate(eng.voices):
        if c == 1:
            assert v.gen > g_before[c]
        else:
            assert v.gen == g_before[c]


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


# -- .moy store: sounds.json round-trip ----------------------------------

def test_moy_carts_saves_and_loads_sounds(tmp_path):
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("Tune Cart", root, src="def _draw():\n    cls(1)\n")
    assert c["sounds"] is None                     # new cart has no bank yet
    bank = audio.AudioBank.default().to_dict()
    moy_carts.save_sounds(c, bank)
    reloaded = moy_carts.load(c["path"])
    assert reloaded["sounds"] == bank              # persisted + reloaded intact
    # a corrupt sounds.json degrades to None, never a crash
    with open(c["path"] + "/sounds.json", "w") as f:
        f.write("{not json")
    assert moy_carts.load(c["path"])["sounds"] is None


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
    # Enable the cart's attract mode (off by default since it auto-cycles the pads)
    # so the demo audibly exercises sfx + beep through the console + mixer.
    ws.config["autoplay"] = 1
    for _ in range(120):
        ws.frame(1 / 30)
    kinds = [c[0] for c in ws.audio.calls]
    assert "sfx" in kinds and "beep" in kinds
    assert ws.audio.rendered > 0                    # tick() pulled PCM each frame


def test_cart_without_audio_backend_still_runs(tmp_path):
    # A Workstation with no make_audio injected falls back to _SilentAudio so a
    # cart's sfx()/beep() are harmless no-ops (and make_api stays callable).
    from runtime import console, moy_carts
    from runtime.canvas import Canvas
    from runtime.input import InputState
    from runtime import host_app
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    moy_carts.create("Noisy", root,
                     src="def _draw():\n    sfx(0)\n    beep(440)\n    cls(1)\n")
    ws = console.Workstation(host_app._NullComp(), Canvas(320, 240), InputState(),
                             moy_carts.scan(root))
    ws.make_api = host_app.make_api          # API present ...
    ws.make_audio = None                     # ... but no audio backend
    ws.open()
    ws.frame(1 / 30)
    assert ws.cart_error is None             # silent no-op, not a crash
    assert isinstance(ws.audio, console._SilentAudio)


# -- music / sound editor core (#50) ----------------------------------------

def _music_editor(bank=None):
    from runtime.editors import MusicEditor
    b = bank if bank is not None else audio.AudioBank.default()
    return MusicEditor(b, sfx_factory=audio.SFX, track_factory=audio.MusicTrack), b


def test_music_editor_never_faces_empty_bank():
    # An empty bank seeds a minimal SFX + track so the grid is never blank.
    me, b = _music_editor(audio.AudioBank())
    assert b.sfx and b.music
    assert me.cur_sfx() is not None and me.cur_track() is not None
    assert me.step_count() >= 1 and me.slot_count() >= 1


def test_music_editor_sfx_step_edits_and_clamps():
    me, b = _music_editor()
    st = me.cur_step()
    assert st is not None
    # pitch nudges clamp into 0..95
    me.set_pitch(94)
    me.nudge_pitch(5)
    assert me.cur_step()[0] == 95
    me.set_pitch(1)
    me.nudge_pitch(-5)
    assert me.cur_step()[0] == 0
    # wave cycles square->triangle->saw->noise->square (0..3 wrap)
    me.set_pitch(57)
    waves = []
    for _ in range(5):
        waves.append(me.cur_step()[1])
        me.cycle_wave(1)
    assert waves == [waves[0], (waves[0] + 1) % 4, (waves[0] + 2) % 4,
                     (waves[0] + 3) % 4, waves[0]]
    # volume cycles with wrap (7 -> 0); nudge clamps at the ends
    me.cur_step()[2] = 7
    me.cycle_vol(1)
    assert me.cur_step()[2] == 0
    me.nudge_vol(-1)
    assert me.cur_step()[2] == 0           # clamped, no underflow
    me.nudge_vol(99)
    assert me.cur_step()[2] == 7           # clamped at max
    assert me.dirty


def test_music_editor_rest_toggle_roundtrip():
    me, b = _music_editor()
    me.set_pitch(57)
    me.toggle_rest()
    assert me.cur_step()[0] < 0            # now a rest
    me.toggle_rest(default_pitch=60)
    assert me.cur_step()[0] == 60          # restored to the default note
    # nudging a rest is a no-op (you must un-rest it first)
    me.set_pitch(-1)
    me.nudge_pitch(3)
    assert me.cur_step()[0] < 0


def test_music_editor_add_del_steps_keeps_at_least_one():
    me, b = _music_editor()
    n0 = me.step_count()
    me.add_step()
    assert me.step_count() == n0 + 1 and me.step == 1
    # delete down to a single step; the last one can't be removed
    while me.step_count() > 1:
        me.del_step()
    assert me.step_count() == 1
    me.del_step()
    assert me.step_count() == 1


def test_music_editor_select_sfx_grows_bank_past_end():
    me, b = _music_editor()
    n = len(b.sfx)
    me.select_sfx(n)                       # walk past the last SFX
    assert len(b.sfx) > n                  # a fresh SFX was appended
    assert me.sfx_idx == len(b.sfx) - 1 and me.step == 0
    me.select_sfx(-99)                     # clamps at 0
    assert me.sfx_idx == 0


def test_music_editor_song_view_edits():
    me, b = _music_editor()
    me.toggle_view()
    assert me.view == me.SONG_VIEW
    m0 = me.slot_count()
    me.add_slot()
    assert me.slot_count() == m0 + 1 and me.slot == 1
    # nudge a slot's SFX id, clamped to the bank's SFX range
    me.set_slot(0)
    me.nudge_slot(99)
    assert me.cur_slot_value() == len(b.sfx) - 1
    me.nudge_slot(-99)
    assert me.cur_slot_value() == 0
    while me.slot_count() > 1:
        me.del_slot()
    me.del_slot()
    assert me.slot_count() == 1            # a track always keeps one slot


def test_music_editor_speed_and_loop_target_active_view():
    me, b = _music_editor()
    sfx_spd = me.cur_sfx().speed
    me.nudge_speed(2)
    assert me.cur_sfx().speed == sfx_spd + 2          # sfx view -> SFX tempo
    me.toggle_view()
    trk_spd = me.cur_track().speed
    me.nudge_speed(-1)
    assert me.cur_track().speed == trk_spd - 1        # song view -> track tempo
    lo = me.cur_track().loop
    me.toggle_loop()
    assert me.cur_track().loop is (not lo)


def test_music_editor_cursor_move_clamps():
    me, b = _music_editor()
    me.add_step(); me.add_step()                      # >= 3 steps
    me.move_cursor(-99)
    assert me.step == 0
    me.move_cursor(99)
    assert me.step == me.step_count() - 1


def test_music_editor_edits_roundtrip_through_sounds_json(tmp_path):
    # The editor mutates the bank in place; saving + reloading the cart preserves it.
    from runtime import moy_carts
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    c = moy_carts.create("Tune", root, src="def _draw():\n    cls(1)\n")
    bank = audio.AudioBank.default()
    me, _ = _music_editor(bank)
    me.set_pitch(72)
    me.cycle_wave(1)
    me.nudge_vol(-2)
    me.add_step()
    me.toggle_view()
    me.add_slot()
    moy_carts.save_sounds(c, me.bank.to_dict())
    reloaded = moy_carts.load(c["path"])
    again = audio.AudioBank.from_dict(reloaded["sounds"])
    assert again.to_dict() == me.bank.to_dict()       # full round-trip, byte-stable


def test_audio_bank_backward_compat_schema_unchanged():
    # A bank authored before #50 (the existing sfx/music schema) still loads, and a
    # freshly-edited bank still serializes to that same shape (no new keys).
    legacy = {"sfx": [{"speed": 8, "loop": False, "steps": [[60, 0, 6]]}],
              "music": [{"speed": 4, "loop": True, "pattern": [0]}]}
    b = audio.AudioBank.from_dict(legacy)
    me, _ = _music_editor(b)
    me.nudge_pitch(1)
    out = b.to_dict()
    assert set(out.keys()) == {"sfx", "music"}
    assert set(out["sfx"][0].keys()) == {"speed", "loop", "steps"}
    assert set(out["music"][0].keys()) == {"speed", "loop", "pattern"}


# -- music editor wired into the shared console (host == device) ------------

def test_music_editor_opens_edits_previews_and_saves_on_console(tmp_path):
    from runtime import host_app
    from runtime import console as C
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    assert ws.cart_error is None and ws.audio is not None

    ws._open_music()
    assert ws.screen == "menu" and ws.menu_view == "music"
    me = ws.music_ui.musicedit
    assert me is not None
    # The editor edits the SAME bank the running cart plays through.
    assert me.bank is ws.audio.engine.bank

    # A frame in the music view draws without error.
    ws.frame(1 / 30)
    assert len(set(ws.canvas.buf)) > 1

    # Tap NOTE+ on the edit pad -> the current step's pitch rises + bank goes dirty.
    p0 = me.cur_step()[0]
    r = C._mu_pad_rect(1, 0)                  # (col 1, row 0) = NOTE+
    ws.music_ui._music_click(r[0] + 2, r[1] + 2)
    assert me.cur_step()[0] == p0 + 1 and me.dirty

    # PLAY starts a preview through the live engine; tapping again STOPS it (toggle).
    ws.music_ui._music_click(C._MU_PLAY[0] + 2, C._MU_PLAY[1] + 2)
    assert ws.music_ui.music_preview is not None
    rendered0 = ws.audio.rendered
    ws.frame(1 / 30)                          # a frame ticks the mixer (renders PCM)
    assert ws.audio.rendered > rendered0
    ws.music_ui._music_click(C._MU_PLAY[0] + 2, C._MU_PLAY[1] + 2)
    assert ws.music_ui.music_preview is None           # toggled off while still sounding

    # SAVE persists to sounds.json; reload proves it stuck. SAVE moved to the unified
    # bar (Stage-4 rollout): the music tab's bar SAVE dispatches through save_current.
    ws.editor_app.save_current()
    assert ws.save_status == "SAVED" and not me.dirty
    from runtime import moy_carts
    reloaded = moy_carts.load(ws.cart["path"])
    assert reloaded["sounds"] is not None
    assert audio.AudioBank.from_dict(reloaded["sounds"]).to_dict() == me.bank.to_dict()

    # Leaving the editor stops any preview and returns to the cart.
    ws.music_ui._music_click(C._MU_PLAY[0] + 2, C._MU_PLAY[1] + 2)   # start a preview again
    ws._leave_menu()
    assert ws.music_ui.music_preview is None and ws.screen == "desktop"


def test_music_editor_view_toggle_and_song_path_on_console(tmp_path):
    from runtime import host_app
    from runtime import console as C
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    ws._open_music()
    me = ws.music_ui.musicedit
    # Toggle to SONG view via the view button; a frame draws it.
    ws.music_ui._music_click(C._MU_VIEW[0] + 2, C._MU_VIEW[1] + 2)
    assert me.view == me.SONG_VIEW
    ws.frame(1 / 30)
    assert len(set(ws.canvas.buf)) > 1
    # SFX+ pad button (song view, row 0 col 1) bumps the slot's SFX id.
    v0 = me.cur_slot_value()
    r = C._mu_pad_rect(1, 0)
    ws.music_ui._music_click(r[0] + 2, r[1] + 2)
    assert me.cur_slot_value() == min(v0 + 1, len(me.bank.sfx) - 1)
