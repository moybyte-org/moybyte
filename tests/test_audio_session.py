"""The kernel's audio sessions (native/moy_audio/moy_aud.h;
docs/kernel_survival_2026-10.md section 5.1) through `moy_audio` as CPython
reaches it: a session per owner, rows of kind AUDIO, the verbs addressed to a
session, `focus` naming the audible one, the muted one's verbs leaving the mix
untouched, a bank swap that never holds the lock for a parse, `hush`, and the
sample voice (#70)."""

import json

import pytest

from runtime import moy_spine
from runtime.audio import AudioBank, SFX
import audio_synth

na = None


@pytest.fixture(autouse=True)
def _module():
    global na
    if not audio_synth.available():
        pytest.skip("no C compiler: the host has no audio module")
    na = audio_synth.audio_session.native()
    na.set_rate(8000)
    yield
    for h in list(_OPEN):
        try:
            na.close(h)
        except ValueError:
            pass
    _OPEN.clear()


_OPEN = []
_OWNER = [500]


def _open(bank=None):
    _OWNER[0] += 1
    text = json.dumps((bank or AudioBank.default()).to_dict())
    h = na.open(_OWNER[0], text)
    _OPEN.append(h)
    return h


def _render(n=400):
    buf = bytearray(2 * n)
    na.render(buf, n)
    return bytes(buf)


def test_a_session_is_a_row_of_kind_audio():
    h = _open()
    assert (h >> moy_spine.KIND_SHIFT) & 0xF == moy_spine.KIND_AUDIO
    assert na.focused() != h                # open does not focus


def test_an_owner_reopening_closes_its_old_session():
    old = na.open(9000)
    new = na.open(9000)
    _OPEN.append(new)
    assert new != old
    with pytest.raises(ValueError):
        na.sfx(old, 0)
    na.close(new)
    _OPEN.remove(new)
    with pytest.raises(ValueError):
        na.close(new)


def test_only_the_focused_session_reaches_the_mix():
    a, b = _open(), _open()
    na.focus(a)
    _render(4000)                           # a quiet start
    silent = _render()
    na.sfx(b, 0)                            # the muted session's verb ...
    na.music(b, 0)
    assert _render() == silent              # ... leaves the mix unchanged
    assert na.active(b) != 0                # though its state moved
    na.focus(b)
    assert any(_render())                   # and it plays once focused


def test_focus_none_is_silence_and_close_unfocuses():
    a = _open()
    na.focus(a)
    na.music(a, 0)
    assert any(_render())
    na.focus(0)
    assert not any(_render())
    na.focus(a)
    na.close(a)
    _OPEN.remove(a)
    assert na.focused() == 0


def test_a_refused_bank_leaves_a_live_silent_session():
    h = _open()
    assert na.bank(h, "{not json") is False
    na.focus(h)
    na.sfx(h, 0)
    assert not any(_render())
    assert na.bank(h, json.dumps(AudioBank.default().to_dict())) is True


def test_a_bank_swap_holds_the_lock_for_microseconds_whatever_its_size():
    """The parse runs outside the lock; the lock covers a pointer swap and a
    voice reset. A board's feeder never waits on a big bank."""
    big = AudioBank([SFX([[40 + i % 30, i % 8, 5]] * 64, speed=8)
                     for i in range(64)], [])
    h = _open()
    na.stats(True)                          # reset the maxima
    for _ in range(5):
        na.bank(h, json.dumps(big.to_dict()))
    st = na.stats()
    parse_us, hold_us = st[9], st[8]
    assert parse_us > hold_us * 4, (parse_us, hold_us)


def test_hush_silences_every_session_and_closes_none():
    a, b = _open(), _open()
    na.music(a, 0)
    na.music(b, 0)
    na.focus(a)
    assert any(_render())
    na.hush()
    assert not any(_render())
    assert na.active(a) & 0x3F == 0 and na.active(b) & 0x3F == 0
    na.sfx(a, 0)                            # still open
    assert any(_render())


def test_the_console_level_caps_every_session():
    a = _open()
    na.focus(a)
    na.volume(0)
    na.sfx(a, 0)
    assert not any(_render())
    na.volume(7)
    na.sfx(a, 0)
    assert any(_render())


def test_the_sample_voice_plays_a_clip_resampled_at_load():
    a = _open(AudioBank())
    na.focus(a)
    pcm = b"".join(int(8000 if (i // 4) % 2 else -8000).to_bytes(2, "little", signed=True)
                   for i in range(400))                 # 400 frames at 4000 Hz
    clip = na.sample_load(pcm, 4000)
    na.sample_play(a, clip)
    assert na.active(a) & (1 << 7)
    out = _render(800)                                  # 800 frames at 8000 Hz
    assert any(out)
    assert not any(_render(100))                        # it ended on time
    assert na.active(a) & (1 << 7) == 0
    na.sample_play(a, clip, 2)
    na.sample_free(clip)                                # freeing stops it
    assert not any(_render(100))
    with pytest.raises(ValueError):
        na.sample_play(a, clip)


def test_the_trace_counts_one_call_per_verb():
    a = _open()
    na.trace(True)
    na.sfx(a, 3, 1)
    na.music(a, 0)
    na.stop(a)
    rows = na.trace()
    na.trace(False)
    assert [(r[1], r[2], r[3]) for r in rows] == [(4, 3, 1), (6, 0, 1), (8, -1, 0)]
    assert len({r[0] for r in rows}) == 1


def test_a_run_holds_the_focused_cart_session(tmp_path):
    from ws_helpers import build_ws, open_cart
    ws = build_ws(tmp_path)
    open_cart(ws, "Coin Quest")
    assert ws.audio.h and na.focused() == ws.audio.h
    first = ws.audio.h
    open_cart(ws, "Brick Siege")
    assert ws.audio.h != first
    with pytest.raises(ValueError):
        na.sfx(first, 0)


def test_the_self_dump_renders_an_unfocused_session_alone():
    """The board's digital self-dump: a session's synth rendered straight from
    its state, no speaker, no mix -- equal to what the mix makes of it."""
    a, b = _open(), _open()
    na.focus(a)
    for h in (a, b):
        na.music(h, 0)
    dumped = bytearray(800)
    na.dump(b, dumped, 400)
    with pytest.raises(ValueError):
        na.dump(a, dumped, 400)             # the focused one is the feeder's
    assert any(dumped) and bytes(dumped) == _render()


def test_a_hush_is_measured_by_where_the_last_loud_chunk_ended():
    a = _open()
    na.focus(a)
    na.music(a, 0)
    _render(400)
    na.hush()
    _render(400)
    st = na.stats()
    hush_at, loud_at = st[10], st[11]
    assert loud_at <= hush_at              # nothing audible after the hush
