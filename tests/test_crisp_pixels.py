"""CRISP PIXELS: the capability-gated nearest-neighbour composite toggle.

The P4's PPA scaler is fixed bilinear in silicon, so pixel-art carts composite
smeared; the Settings row routes the game composite nearest-neighbour instead
(the game composite, native/moy_glass/moy_present_game.c: moy_ppa_k_crisp's
SRAM-bounce band pipeline, CPU kernel fallback). The row
shows ONLY where the system canvas exposes set_crisp_scale -- every other tier
composites nearest already and keeps its frozen Settings pixels.
"""

from pathlib import Path

from ws_helpers import build_ws


ROOT = Path(__file__).resolve().parent.parent


def _grant_hook(ws):
    calls = []
    ws.sys_canvas.set_crisp_scale = calls.append
    return calls


def test_row_is_capability_gated(tmp_path):
    ws = build_ws(tmp_path)
    keys = [row[0] for row in ws.settings_layer._settings_rows()]
    assert "crisp_pixels" not in keys      # host canvas: already nearest

    calls = _grant_hook(ws)
    ws.settings_layer._rows_cache = None   # memo keyed on the capability flag
    rows = ws.settings_layer._settings_rows()
    keys = [row[0] for row in rows]
    i = keys.index("crisp_pixels")
    assert keys[i - 1] == "steady"         # sits by its sibling perf trade
    assert rows[i] == ("crisp_pixels", "CRISP PIXELS", "diag")
    assert calls == []                     # showing the row flips nothing


def test_toggle_flips_persists_and_drives_the_canvas_hook(tmp_path):
    ws = build_ws(tmp_path)
    calls = _grant_hook(ws)
    assert ws.crisp_pixels is False        # default OFF = the shipped smooth

    ws.settings_layer._toggle_diag_row("crisp_pixels")
    assert ws.crisp_pixels is True
    assert ws.system.get("crisp_pixels") is True
    assert calls == [True]

    ws.settings_layer._toggle_diag_row("crisp_pixels")
    assert ws.crisp_pixels is False
    assert ws.system.get("crisp_pixels") is False
    assert calls == [True, False]


def test_boot_apply_does_not_persist_but_reaches_the_canvas(tmp_path):
    ws = build_ws(tmp_path)
    calls = _grant_hook(ws)
    ws.system.delete("crisp_pixels")
    ws.toggles.set_crisp_pixels(True, persist=False)
    assert ws.crisp_pixels is True
    assert ws.system.text("crisp_pixels") is None
    assert calls == [True]


def test_device_side_is_wired():
    """Routing the host cannot execute: the P4 canvas hook sets the game
    composite's mode, which takes the PPA's crisp entry, and the bands'
    release stays a verb."""
    p4 = (ROOT / "device/p4_canvas.py").read_text()
    assert "def set_crisp_scale" in p4
    assert "_glass_mod.game_crisp(on)" in p4
    glass = (ROOT / "native/moy_glass/modmoy_glass.c").read_text()
    assert "MP_QSTR_game_crisp" in glass
    body = (ROOT / "native/moy_glass/moy_present_game.c").read_text()
    assert "moy_ppa_k_crisp(dst" in body
    ppa = (ROOT / "native/p4/moy_ppa/modmoy_ppa.c").read_text()
    assert "int moy_ppa_k_crisp(" in ppa
    assert "MP_QSTR_crisp_release" in ppa
    assert "mg_blit565_scale" in ppa       # the ONE expand kernel, not a twin
