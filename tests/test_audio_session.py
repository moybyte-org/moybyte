"""runtime/audio_session.py's AudioSessions, the audio session twin
(docs/kernel_survival_2026-10.md section 5.1): a session per owner, rows of
kind AUDIO, the six verbs addressed to a session, and `focus` naming the
audible one."""

import pytest

from runtime import moy_spine
from runtime.audio_session import AudioSessions


class Backend:
    def __init__(self):
        self.calls = []

    def __getattr__(self, verb):
        return lambda *a: self.calls.append((verb,) + a)


def test_a_session_per_owner_and_the_newest_is_focused():
    s = AudioSessions()
    a, b = Backend(), Backend()
    ha = s.open("cart", a)
    assert (ha >> moy_spine.KIND_SHIFT) & 0xF == moy_spine.KIND_AUDIO
    hb = s.open("music_editor", b)
    assert s.focused == hb and s.of("cart") == ha
    s.focus(ha)
    assert s.focused == ha


def test_the_six_verbs_reach_only_their_session():
    s = AudioSessions()
    a, b = Backend(), Backend()
    ha, hb = s.open("cart", a), s.open("preview", b)
    s.sfx(ha, 3)
    s.beep(ha, 440, 0.1)
    s.music(hb, 1, False)
    s.music_stop(hb)
    s.sound_stop(ha, 2)
    s.volume(hb, 5)
    assert a.calls == [("sfx", 3, None), ("beep", 440, 0.1), ("sound_stop", 2)]
    assert b.calls == [("music", 1, False), ("music_stop",), ("volume", 5)]


def test_an_owner_reopening_ends_its_old_session():
    s = AudioSessions()
    old = s.open("cart", Backend())
    new = s.open("cart", Backend())
    assert s.of("cart") == new and new != old
    with pytest.raises(moy_spine.StaleHandle):
        s.sfx(old, 0)
    s.end(new)
    assert s.focused == 0 and s.of("cart") == 0
    s.end(new)                      # already gone: a no-op


def test_a_run_holds_the_cart_session(tmp_path):
    from ws_helpers import build_ws, open_cart
    ws = build_ws(tmp_path)
    open_cart(ws, "Coin Quest")
    sess = ws.project.audio_sessions
    h = sess.of("cart")
    assert h and sess.focused == h and sess.backend(h) is ws.audio
