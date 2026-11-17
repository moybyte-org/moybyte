"""A KEYS_IDLE surface in a desk window (Layer.KEYS_IDLE, Settings) is handed
the first frame of no input after input and none after it: its window's
route skips the call and the install of its layout context round it
(WindowRoute._keys_idle). A press, the frame that lets it go, a typed key and
a held one each reach it, and a surface that is not KEYS_IDLE is handed
every frame."""

import pytest

from runtime import host_app
from ws_helpers import build_desktop_ws, open_cart, quiesce

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


# -- every KEYS_IDLE surface: a frame of no input changes nothing ------------------
#
# A window's route hands such a surface no second idle frame, which is only
# right where the one it is handed does nothing: no dirty mark, no deferred
# work, none of its own state moved, and it still answers True.


def _idle_launcher(ws):
    return ws.launcher_layer


def _idle_projects(ws):
    return ws.projects_layer


def _idle_search(ws):
    ws.search_typing = True
    return ws.launcher_layer


def _idle_tab_row(ws):
    ws.launcher_layer._tab_focus = True
    return ws.launcher_layer


def _idle_update(ws):
    ws.update_ui._upd_phase = "downloading"
    return ws.layer_of("update")


def _idle_player(ws):
    open_cart(ws, "Star Catcher")
    for _ in range(3):
        ws.input.begin_frame()
        ws.frame(DT)
    return ws.layer_of("desktop")


def _idle_sprites(ws):
    assert ws.look.edit_file()
    ws.input.begin_frame()
    ws.frame(DT)
    ws.editor_app.theme_tab("theme_sprites")
    for _ in range(2):
        ws.input.begin_frame()
        ws.frame(DT)
    return ws.theme_sprites_layer


def _idle_settings(ws):
    ws.open_settings()
    for _ in range(3):
        ws.input.begin_frame()
        ws.frame(DT)
    return ws.settings_layer


_IDLE = {
    "launcher": _idle_launcher,
    "projects": _idle_projects,
    "launcher in a typed search": _idle_search,
    "launcher on its tab row": _idle_tab_row,
    "update": _idle_update,
    "desktop": _idle_player,
    "theme_sprites": _idle_sprites,
    "settings": _idle_settings,
}


@pytest.mark.parametrize("name", list(_IDLE))
def test_a_keys_idle_surface_does_nothing_with_a_frame_of_no_input(tmp_path, name):
    ws = build_desktop_ws(tmp_path)
    layer = _IDLE[name](ws)
    assert layer.KEYS_IDLE
    for _ in range(2):
        ws.input.begin_frame()
        assert not (ws.input.any_held() or ws.input.any_pressed() or ws.input.typed)
        ws.frame_state.dirty = False
        deferred = len(ws.deferred)
        state = dict(vars(layer))
        konami = ws.ach_ui._konami_pos
        assert layer.handle_input(ws.input) is True
        assert not ws.frame_state.dirty
        assert len(ws.deferred) == deferred
        assert dict(vars(layer)) == state
        assert ws.ach_ui._konami_pos == konami


def test_every_keys_idle_surface_is_in_the_table(tmp_path):
    ws = build_desktop_ws(tmp_path)
    marked = {id(l): l for l in ws._content_layers.values() if l.KEYS_IDLE}
    marked[id(ws.settings_layer)] = ws.settings_layer
    marked[id(ws.theme_sprites_layer)] = ws.theme_sprites_layer
    ids = {l.id for l in marked.values()}
    assert ids == {"launcher", "projects", "update", "desktop", "theme_sprites", "settings"}
