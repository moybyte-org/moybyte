"""Licences (docs/history/look_2026-10.md step 32): the texts the console carries and
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


class _I:
    """A key press as Settings hands it on."""
    last_key = 0
    typed = 0

    def __init__(self, name):
        self.name = name

    def steps(self, n):
        return int(bool(self.pressed(n)))

    def pressed(self, n):
        return n == self.name


def test_the_page_lists_the_parts_and_opens_a_text(tmp_path):
    """A handheld: the list's rows open their text as a page over the list,
    whose title is the part's; the back closes it onto the list, then
    Settings' page."""
    ws = build_ws(tmp_path)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("licences")
    ws.frame(1 / 30)
    page = sl.licences
    assert sl._sub.box("MicroPython/read") is not None
    page.read("MicroPython")
    ws.mark_dirty()
    ws.frame(1 / 30)
    assert page.text_open() and sl.page_title() == "MicroPython"
    text = page.page.s._v["text"]
    assert text.startswith("Copyright") and "Permission is hereby granted" in text
    assert sl.back() is not False and not page.text_open() and sl.page == "licences"
    sl.back()
    assert sl.page is None


def test_the_texts_arrows_scroll_it_once_it_has_focus(tmp_path):
    """L7: the text is its own stop; Down there scrolls it a line."""
    from runtime import ui, ui_host
    ws = build_ws(tmp_path)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("licences")
    ws.frame(1 / 30)
    sl.licences.read("MicroPython")
    for _ in range(2):
        ws.mark_dirty()
        ws.frame(1 / 30)
    assert ui.focus() == "text"
    x, y, w, h = sl._sub.box("text")
    off = lambda: ui_host._C.api_offset(sl._sub.ctx, x + 4, y + 4)   # noqa: E731
    assert off() == 0
    sl.licences.keys(_I("down"))
    for _ in range(3):
        ws.mark_dirty()
        ws.frame(1 / 30)
    assert off() > 0


def test_the_desk_text_moves_its_own_cells_gen_not_the_pages(tmp_path):
    """The desk's list and text are a split's two cells, each a region with
    its own gen over the states only its rows read: a row focused writes the
    text, which moves the text cell's gen and leaves the page's and the
    list's, so the list's step stays a frame of rows (test_wm_records)."""
    from ws_helpers import build_desktop_ws
    ws = build_desktop_ws(tmp_path, panel_diagonal_in=7.0, font_scale=1)
    ws.home.open_desk()
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("licences")
    for _ in range(2):
        ws.mark_dirty()
        ws.frame(1 / 30)
    eng = sl.licences.page
    gens = lambda: (eng._page_o["gen"],) + tuple(c[0]["gen"] for c in eng._cells.values())  # noqa: E731
    assert eng._gen_ok and len(eng._cells) == 2
    before = gens()
    sl.licences.read(eng.s._v["parts"][1]._f["id"])
    ws.mark_dirty()
    ws.frame(1 / 30)
    after = gens()
    assert after[0] == before[0] and after[1] == before[1] and after[2] != before[2], (before, after)


def test_every_body_inflates_on_micropython(tmp_path):
    """The boards and the browser inflate through `deflate.DeflateIO`, whose
    raw window defaults to 256 bytes: a body deflated with a wider one must
    name it, or a far back-reference is EINVAL there and never on CPython."""
    from unix_mp import require_unix_mp, runtime_tree
    exe = require_unix_mp(why="Without it no body is inflated the way a board does.")
    script = tmp_path / "inflate.py"
    script.write_text(
        "import sys\nsys.path.insert(0, %r)\nimport licences as L\n"
        "for k in sorted(L._BODIES):\n"
        "    print(k, len(L._inflate(L._BODIES[k])))\n" % runtime_tree())
    run = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr[-2000:] or run.stdout[-2000:]
    import zlib
    want = ["%s %d" % (k, len(zlib.decompress(L._BODIES[k], -15))) for k in sorted(L._BODIES)]
    assert run.stdout.split("\n")[:-1] == want
