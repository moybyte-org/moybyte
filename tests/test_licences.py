"""Licences (docs/look_2026-10.md step 32): the texts the console carries and
Settings' page that shows them."""

import subprocess
import sys
from pathlib import Path

from runtime import licences as L
from ws_helpers import build_ws

ROOT = Path(__file__).resolve().parent.parent


def test_the_licences_module_is_its_sources_built():
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "make_licences.py"), "check"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_every_part_opens_its_text():
    names = []
    for _cap, entries in L.GROUPS:
        for e in entries:
            paras = L.text(e)
            assert paras and all(isinstance(p, str) for p in paras), e[0]
            names.append(e[0])
    assert names[:2] == ["The console", "Cart format"]
    for want in ("MicroPython", "Lua 5.4", "WAMR", "Inter", "Day Sans",
                 "Atkinson Hyperlegible", "JetBrains Mono", "Pixelarticons", "PICO-8 palette"):
        assert want in names, want
    mit = L.text(L.GROUPS[1][1][0])
    assert mit[0].startswith("Copyright") and mit[1].startswith("Permission is hereby granted")


def test_the_texts_stay_small():
    """The owner's bar (2026-10-23): the set ships whole while it stays under
    40 KB, each body stored once."""
    stored = sum(len(b) for b in L._BODIES.values())
    assert stored < 40 * 1024, stored


def test_a_board_holds_only_its_own_parts():
    every = [e[0] for _c, es in L.GROUPS for e in es if L.shown(e, None)]
    tdeck = [e[0] for _c, es in L.GROUPS for e in es if L.shown(e, "tdeck")]
    assert "ST7789 panel setup" in tdeck and "EK79007 panel driver" not in tdeck
    assert set(tdeck) < set(every)


def test_the_page_lists_the_parts_and_opens_a_text(tmp_path):
    ws = build_ws(tmp_path)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("licences")
    ws.frame(1 / 30)
    page = sl.licences
    box = sl._sub.box("MicroPython")
    assert box is not None
    page._focus = "MicroPython"
    ws.mark_dirty()
    ws.frame(1 / 30)

    class _I:
        last_key = 0

        def __init__(self, name):
            self.name = name

        def pressed(self, n):
            return n == self.name

    sl._sub.keys(_I("a"))
    ws.mark_dirty()
    ws.frame(1 / 30)
    assert page.entry is not None and page.entry[0] == "MicroPython"
    assert sl.page_title() == "MicroPython"
    ws.mark_dirty()
    ws.frame(1 / 30)
    assert page._lines and page._lines[0].startswith("Copyright")
    assert page.keys(_I("down")) and page._top == 3
    sl.back()
    assert page.entry is None and sl.page == "licences"
    sl.back()
    assert sl.page is None
