"""CRISP PIXELS: the capability-gated nearest-neighbour composite toggle.

The P4's PPA scaler is fixed bilinear in silicon, so pixel-art carts composite
smeared; the Settings row routes the game composite nearest-neighbour instead
(moy_ppa.blit_crisp's SRAM-bounce band pipeline, CPU kernel fallback). The row
shows ONLY where the system canvas exposes set_crisp_scale -- every other tier
composites nearest already and keeps its frozen Settings pixels.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _ws(tmp_path, **kwargs):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"), **kwargs)


def _grant_hook(ws):
    calls = []
    ws.sys_canvas.set_crisp_scale = calls.append
    return calls


def test_row_is_capability_gated(tmp_path):
    ws = _ws(tmp_path)
    keys = [row[0] for row in ws.settings_layer._settings_rows()]
    assert "crisp_pixels" not in keys      # host canvas: already nearest

    calls = _grant_hook(ws)
    ws.settings_layer._rows_cache = None   # memo keyed on the capability flag
    rows = ws.settings_layer._settings_rows()
    keys = [row[0] for row in rows]
    i = keys.index("crisp_pixels")
    assert keys[i - 1] == "frameskip"      # sits by its sibling perf trade
    assert rows[i] == ("crisp_pixels", "CRISP PIXELS", "diag")
    assert calls == []                     # showing the row flips nothing


def test_toggle_flips_persists_and_drives_the_canvas_hook(tmp_path):
    ws = _ws(tmp_path)
    calls = _grant_hook(ws)
    assert ws.crisp_pixels is False        # default OFF = the shipped smooth

    ws.settings_layer._toggle_diag_row("crisp_pixels")
    assert ws.crisp_pixels is True
    assert ws.system["crisp_pixels"] is True
    assert calls == [True]

    ws.settings_layer._toggle_diag_row("crisp_pixels")
    assert ws.crisp_pixels is False
    assert ws.system["crisp_pixels"] is False
    assert calls == [True, False]


def test_boot_apply_does_not_persist_but_reaches_the_canvas(tmp_path):
    ws = _ws(tmp_path)
    calls = _grant_hook(ws)
    ws.system.pop("crisp_pixels", None)
    ws.set_crisp_pixels(True, persist=False)
    assert ws.crisp_pixels is True
    assert "crisp_pixels" not in ws.system
    assert calls == [True]


def test_device_side_is_wired():
    """Source ratchet for the halves host tests cannot execute: the P4 canvas
    hook + its blit_crisp routing, and the native verb's registration."""
    p4 = (ROOT / "firmware/esp32_p4_wifi6_touch_lcd_7b/modules/moy_runtime.py"
          ).read_text()
    assert "def set_crisp_scale" in p4
    assert "blit_crisp" in p4
    ppa = (ROOT / "firmware/esp32_p4_wifi6_touch_lcd_7b/native/moy_ppa/"
                  "modmoy_ppa.c").read_text()
    assert "MP_QSTR_blit_crisp" in ppa
    assert "MP_QSTR_crisp_release" in ppa
    assert "mg_blit565_scale" in ppa       # the ONE expand kernel, not a twin
