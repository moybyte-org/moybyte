"""A KEYS_IDLE surface in a desk window (Layer.KEYS_IDLE, Settings) is handed
the first frame of no input after input and none after it: its window's
route skips the call and the install of its layout context round it
(WindowRoute._keys_idle). A press, the frame that lets it go, a typed key and
a held one each reach it, and a surface that is not KEYS_IDLE is handed
every frame."""

from runtime import host_app
from ws_helpers import build_desktop_ws, quiesce

DT = 1.0 / 30.0


def _desk(tmp_path):
    ws = build_desktop_ws(tmp_path)
    ws.look.set_theme("moy", persist=False)
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    for _ in range(3):
        quiesce(ws)
        drv.frame(DT)
    return ws, drv


def _count(monkeypatch, layer):
    calls = []
    real = type(layer).handle_input

    def spy(self, i):
        calls.append(1)
        return real(self, i)

    monkeypatch.setattr(type(layer), "handle_input", spy)
    return calls


def _frames(ws, drv, n):
    for _ in range(n):
        quiesce(ws)
        drv.frame(DT)


def test_settings_is_handed_one_frame_of_no_input_then_none(tmp_path, monkeypatch):
    ws, drv = _desk(tmp_path)
    layer = ws.wm.content_for("settings")
    assert ws.wm.focus_key == "settings" and layer.KEYS_IDLE
    calls = _count(monkeypatch, layer)
    _frames(ws, drv, 6)
    assert len(calls) <= 1
    del calls[:]
    drv.press("down")                     # the press, then the frame after it
    _frames(ws, drv, 1)
    assert calls == [1]
    _frames(ws, drv, 5)
    assert len(calls) == 2
    del calls[:]
    drv.hold("down", True)                # a held button: every frame
    _frames(ws, drv, 4)
    assert len(calls) == 4
    drv.hold("down", False)               # the frame that lets it go is the first idle
    _frames(ws, drv, 4)
    assert len(calls) == 5
    del calls[:]
    drv.type_char(ord("a"))               # a typed key
    _frames(ws, drv, 3)
    assert calls[:1] == [1] and len(calls) == 2


def test_a_surface_that_is_not_keys_idle_is_handed_every_frame(tmp_path, monkeypatch):
    ws, drv = _desk(tmp_path)
    layer = ws.wm.content_for("settings")
    monkeypatch.setattr(type(layer), "KEYS_IDLE", False)
    calls = _count(monkeypatch, layer)
    _frames(ws, drv, 6)
    assert len(calls) == 6
