"""`_SceneLayer`/`_MapLayer` key routing.

Both are two-line delegates whose whole job is to hand the frame's keys to
their editor UI and CLAIM the frame. The scene half was reached by nothing in
the suite (verified 2026-08-22 by raising inside it); its pointer half is
covered, which is exactly the asymmetry that lets a delegate rot unnoticed.
"""

from runtime.layers import _MapLayer, _SceneLayer


class _UI:
    def __init__(self):
        self.calls = 0

    def _scene_input(self):
        self.calls += 1

    def _map_input(self):
        self.calls += 1


class _WS:
    def __init__(self):
        self.scene_ui = _UI()
        self.map_ui = _UI()


def _layer(cls):
    ws = _WS()
    lay = cls.__new__(cls)
    lay.ws = ws
    return lay, ws


def test_the_scene_tab_hands_its_keys_to_the_scene_ui():
    lay, ws = _layer(_SceneLayer)
    assert lay.handle_input(object()) is True
    assert ws.scene_ui.calls == 1
    assert ws.map_ui.calls == 0          # and to nobody else's


def test_the_map_tab_hands_its_keys_to_the_map_ui():
    lay, ws = _layer(_MapLayer)
    assert lay.handle_input(object()) is True
    assert ws.map_ui.calls == 1
    assert ws.scene_ui.calls == 0


def test_the_scene_tab_claims_every_frame():
    """Returning False would let the key fall through to the layer beneath --
    the editor is full-screen, so there is nothing below it that should see it.
    One delegation per call, never zero and never two."""
    lay, ws = _layer(_SceneLayer)
    for n in range(1, 4):
        assert lay.handle_input(object()) is True
        assert ws.scene_ui.calls == n
