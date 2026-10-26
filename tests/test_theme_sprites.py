"""A theme's sprites (docs/theming_2026-09.md §4.6, §9 phase 6 step 9).

* The sheet: the cart sheet's hex format read strictly as a ROLE sheet, each
  nibble a slot of style.json's `theme.sprite_slots`; the caps and the
  refusals; the fuzz corpus answered the same by the boards' module.
* The package: `sprites` and `icons` name a `.moygfx` of the folder, read
  along `inherits`, carried by `custom`'s flattening.
* The blit (native/moy_ui/moy_ui_draw.c): a glyph cell centred, a 9-slice
  with whole corners and tiled edges, at the chrome scale, never outside its
  box, slot 0 leaving the target, one op a sprite -- against a Python model.
* The parts: `button` and `desk.icon` through their surfaces, the window's
  9-slice and its title buttons through the frame; an empty cell is no
  sprite and a state falls back along the chain.
* The look: a theme's sheet loads with it and goes with it, an accent
  recolours a sprite with no new file, the slots its look's
  `icons.pixel_slots` names draw from its own tiles, and the sheets travel
  with `custom` and NAME….
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import chrome, ui_draw
from runtime.chrome import theme_colors
from runtime.host_canvas import indices_of, make_system_canvas

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import on_glass  # noqa: E402
from unix_mp import require_unix_mp  # noqa: E402
from ws_helpers import build_desktop_ws, build_ws, fixture_theme  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / "native" / "moy_ui" / "style.json") as _f:
    TH = json.load(_f)["theme"]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "themes" / "sprig.moy"
SHEET = (FIXTURE / "parts.moygfx").read_bytes()
OK, BIG, SYNTAX = 0, 1, 2
W = 128
BG = 9


@pytest.fixture(autouse=True)
def _clean():
    yield
    ui_draw.sheet_clear()
    ui_draw.use_shape(None)
    moy_ui.theme_uninstall()
    chrome.adopt_packages((), None)


def _sheet(cells=None, slot=None):
    """Sheet bytes: the fixture, or a blank sheet with `cells` (indices of
    sheet_cells) each filled with `slot`."""
    if cells is None:
        return SHEET
    rows = [[0] * W for _ in range(TH_ROWS)]
    for i in cells:
        _p, _s, _b, x, y, w, h, _n = moy_ui.sheet_cells()[i]
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                rows[yy][xx] = slot
    return "\n".join("".join("%x" % c for c in r) for r in rows).encode()


TH_ROWS = max(c[4] + c[6] for c in moy_ui.sheet_cells())


def _cell(part, state, sub=0):
    for i, c in enumerate(moy_ui.sheet_cells()):
        if (c[0], c[1], c[2]) == (part, state, sub):
            return i
    raise KeyError((part, state, sub))


def _install(name="spr", **kw):
    """Install `name`: the `night` fixture's colours (a palette theme,
    tests/fixtures/themes/) under `kw`; its dark colours."""
    pkg = json.loads(fixture_theme("night"))
    for k in ("surfaces", "text", "metrics", "frame"):
        pkg.pop(k, None)
    pkg["name"] = name
    for v, roles in kw.pop("colors", {}).items():
        pkg["colors"][v].update(roles)
    pkg.update(kw)
    assert moy_ui.theme_install(json.dumps(pkg).encode())[0] == OK
    chrome.forget_themes()
    ui_draw.use_shape(name)
    return theme_colors(name, "dark")


# -- the sheet ------------------------------------------------------------------------


def test_the_cells_fit_one_128_wide_sheet_and_never_overlap():
    taken = set()
    for part, state, sub, x, y, w, h, nine in moy_ui.sheet_cells():
        assert part in moy_ui.parts() and state in moy_ui.theme_states()
        assert x % 8 == y % 8 == w % 8 == h % 8 == 0 and x + w <= W
        assert nine == (part in TH["sprite_nine"])
        assert not nine or (w >= 24 and h >= 24)
        assert (sub != 0) <= (part == "window.button")
        px = {(x + i, y + j) for i in range(w) for j in range(h)}
        assert not px & taken
        taken |= px
    # the sheet's title-button cells; the back chevron has none (a glyph or the icon set's)
    assert ui_draw.FRAME_BUTTONS == tuple(TH["frame_button_names"]) + ("back",)
    assert len(TH["sprite_slots"]) == 16 and TH["sprite_slots"][0] is None


def test_the_sheet_is_the_cart_sheets_hex_read_strictly():
    caps = ui_draw.sheet_caps()
    assert caps == (W, TH["sheet_rows"]["sprites"], TH["sheet_rows"]["icons"],
                    TH["sheet_rows"]["sprites"] * (W + 2), TH["sheet_rows"]["icons"] * (W + 2))
    # what a cart's sprites.moygfx may hold: short rows, blank lines, CRLF,
    # either case, a trailing newline, nothing at all
    assert ui_draw.sheet_check(b"0123\n\nABCdef\r\n  7  \n", 64) == (OK, 3)
    assert ui_draw.sheet_check(b"", 64) == (OK, 0)
    assert ui_draw.sheet_check(SHEET, caps[1]) == (OK, TH_ROWS)
    assert ui_draw.sheet_check(b"0" * W, 1) == (OK, 1)
    for bad in (b"012g", b"0 1", b"0" * (W + 1), b"\x00", b"0,1", "é".encode()):
        assert ui_draw.sheet_check(bad, 64)[0] == SYNTAX, bad
    assert ui_draw.sheet_check(b"0\n" * 65, 64)[0] == BIG
    assert ui_draw.sheet_check(b" " * (64 * (W + 2) + 1), 64)[0] == BIG
    # a refusal loads nothing and leaves no sheet
    assert ui_draw.sheet_load(SHEET) == OK and any(moy_ui.sheet_set())
    assert ui_draw.sheet_load(b"zz") == SYNTAX and not any(moy_ui.sheet_set())


def test_a_cell_all_slot_0_is_no_sprite_and_a_state_follows_the_chain():
    rest = _cell("button", "rest")
    assert ui_draw.sheet_load(_sheet([rest], 2)) == OK
    got = dict(zip(range(len(moy_ui.sheet_cells())), moy_ui.sheet_set()))
    assert got[rest] and sum(got.values()) == 1
    th = _install()
    for state in ("rest", "hot", "pressed", "disabled", "on", "hover"):
        cv = make_system_canvas(30, 30)
        cv.cls(BG)
        assert ui_draw.paint(cv, th, (2, 2, 26, 26), "button", state), state
        assert indices_of(cv)[10 * 30 + 10] == th["surface"]
    # desk.icon's cell is empty: it draws its surface, as with no sheet
    th = _install(surfaces={"desk.icon": {"rest": "fill 8"}})
    frames = []
    for sheet in (_sheet([rest], 2), None):
        if sheet is None:
            ui_draw.sheet_clear()
        else:
            ui_draw.sheet_load(sheet)
        cv = make_system_canvas(30, 30)
        cv.cls(BG)
        assert ui_draw.paint(cv, th, (2, 2, 26, 26), "desk.icon")
        frames.append(indices_of(cv))
    assert frames[0] == frames[1] and frames[0][10 * 30 + 10] == 8


# -- the package -----------------------------------------------------------------------


def test_a_package_names_its_sheets_and_custom_carries_them():
    src = json.dumps({"format": 2, "name": "spr", "sprites": "parts.moygfx",
                      "icons": "icons.moygfx"}).encode()
    got = moy_ui.theme_resolve(src)
    assert got[0] == OK and got[1] == 0 and got[7] == ("parts.moygfx", "icons.moygfx")
    assert moy_ui.theme_install(src)[0] == OK
    assert moy_ui.theme_sheets("spr") == ("parts.moygfx", "icons.moygfx")
    assert moy_ui.theme_sheets("moy") == (None, None)
    rc, b = moy_ui.theme_custom(src, 12, None)
    assert rc == OK
    d = json.loads(b)
    assert (d["sprites"], d["icons"]) == ("parts.moygfx", "icons.moygfx")
    # a name that is no .moygfx of the folder is dropped and noted
    for bad in ("parts.png", ".moygfx", "../x.moygfx", "A.moygfx", 3, {"a": 1},
                "x" * 30 + ".moygfx"):
        got = moy_ui.theme_resolve(json.dumps({"format": 2, "name": "spr",
                                               "sprites": bad}).encode())
        assert got[0] == OK and got[1] & 4 and got[7] == (None, None), bad
    assert "sprites" not in TH["reserved"] and "icons" not in TH["reserved"]


# -- the blit --------------------------------------------------------------------------


def _model(cell, box, scale, slots, clip=None, size=(64, 48)):
    """The index frame `cell` drawn into `box` over BG, from the rule in
    moy_ui_draw.c's header, pixel by pixel."""
    part, state, sub, cx, cy, cw, ch, nine = moy_ui.sheet_cells()[cell]
    rows = [r.strip() for r in SHEET.decode().split("\n") if r.strip()]

    def src(at, size_, n, off):
        if not nine:
            return (at - off) // scale
        c = 8 * scale
        lo = c if size_ >= 2 * c else (size_ + 1) // 2
        hi = min(c, size_ - lo)
        if at < lo:
            return at // scale
        if at >= size_ - hi:
            return n - 1 - (size_ - 1 - at) // scale
        return 8 + ((at - lo) // scale) % (n - 16)

    bx, by, bw, bh = box
    ox, oy = (bw - cw * scale) // 2, (bh - ch * scale) // 2
    w, h = size
    out = [BG] * (w * h)
    for y in range(bh):
        for x in range(bw):
            X, Y = bx + x, by + y
            if not (0 <= X < w and 0 <= Y < h):
                continue
            if clip and not (clip[0] <= X < clip[0] + clip[2] and clip[1] <= Y < clip[1] + clip[3]):
                continue
            if not nine and not (0 <= x - ox < cw * scale and 0 <= y - oy < ch * scale):
                continue
            sx, sy = src(x, bw, cw, ox), src(y, bh, ch, oy)
            v = slots[int(rows[cy + sy][cx + sx], 16)]
            if v >= 0:
                out[Y * w + X] = v
    return out


def _slots(th):
    out = [-1]
    for s in TH["sprite_slots"][1:]:
        out.append(s if isinstance(s, int) else th[s])
    return out


BOXES = ((2, 3, 40, 30), (0, 0, 64, 48), (5, 5, 12, 9), (1, 1, 17, 40), (-6, -4, 30, 20),
         (40, 30, 30, 30), (3, 3, 1, 1), (10, 2, 24, 24))


@pytest.mark.parametrize("scale", (1, 2, 3))
def test_a_9_slice_and_a_glyph_cell_draw_as_the_model_does(scale):
    th = _install(colors={"dark": {"accent": 12}})
    assert ui_draw.sheet_load(SHEET) == OK
    slots = _slots(th)
    for part, state, sub in (("button", "rest", 0), ("desk.icon", "rest", 0),
                             ("button", "on", 0)):
        cell = _cell(part, state, sub)
        for box in BOXES:
            cv = make_system_canvas(64, 48, font_scale=scale)
            cv.cls(BG)
            cv.gate_counts_reset()
            assert ui_draw.paint(cv, th, box, part, state)
            assert cv.gate_counts()[0] == 1, (part, box)
            assert list(indices_of(cv)) == _model(cell, box, scale, slots), (part, box, scale)
    # under a clip, the clipped part of the same frame
    cell = _cell("button", "rest")
    cv = make_system_canvas(64, 48, font_scale=scale)
    cv.cls(BG)
    cv.clip(10, 8, 20, 9)
    ui_draw.paint(cv, th, (2, 3, 40, 30), "button")
    cv.clip()
    assert list(indices_of(cv)) == _model(cell, (2, 3, 40, 30), scale, slots,
                                          clip=(10, 8, 20, 9))


def test_a_sprite_recolours_with_the_theme_with_no_new_file():
    assert ui_draw.sheet_load(SHEET) == OK
    frames = []
    for accent in (12, 8):
        th = _install(colors={"dark": {"accent": accent, "title": 3}})
        cv = make_system_canvas(64, 48)
        cv.cls(BG)
        ui_draw.paint(cv, th, (0, 0, 64, 48), "button", "on")      # filled with `accent`
        frames.append(list(indices_of(cv)))
        assert frames[-1][24 * 64 + 32] == accent
    assert [a == b or {a, b} == {12, 8} for a, b in zip(*frames)] == [True] * (64 * 48)


def test_the_frame_takes_the_windows_9_slice_and_the_title_buttons():
    th = _install(colors={"dark": {"accent": 12}})
    fs, th_h = 1, 14
    win = (20, 10, 160, 80)

    def frame(active):
        cv = make_system_canvas(200, 100)
        cv.cls(BG)
        ui_draw.frame(cv, th, win, th_h, "SPRIG", active, True, fs)
        return indices_of(cv)

    ui_draw.sheet_clear()
    plain = frame(True)
    assert ui_draw.sheet_load(SHEET) == OK
    slots = _slots(th)
    for active in (True, False):
        px = frame(active)
        head = _model(_cell("window", "active" if active else "inactive"),
                      (20, 10, 160, th_h + 1), fs, slots, size=(200, 100))
        # the head is the sprite where no title or button draws over it
        x0, x1 = 20 + 30, 20 + 60
        for y in range(10, 10 + th_h + 1):
            for x in (21, 22, 178, 179):
                assert px[y * 200 + x] == head[y * 200 + x], (x, y)
        assert x0 < x1
        # the ring's sides and foot and the shadow are the style's
        for y in range(10 + th_h + 1, 90):
            assert px[y * 200 + 20] == plain[y * 200 + 20]
            assert px[y * 200 + 181] == plain[y * 200 + 181]
        assert px[91 * 200 + 100] == plain[91 * 200 + 100]
        for name, (bx, by, bw, bh), _right in ui_draw.frame_buttons(win, th_h, "SPRIG", True, fs):
            sub = ui_draw.FRAME_BUTTONS.index(name)
            want = _model(_cell("window.button", "active" if active else "inactive", sub),
                          (bx, by, bw, bh), fs, slots, size=(200, 100))
            want_head = head
            for y in range(by, by + bh):
                for x in range(bx, bx + bw):
                    w = want[y * 200 + x]
                    assert px[y * 200 + x] == (w if w != BG else want_head[y * 200 + x]), \
                        (name, x, y)


def test_one_op_a_sprite_and_never_outside_its_box():
    th = _install()
    assert ui_draw.sheet_load(SHEET) == OK
    cv = make_system_canvas(64, 48, font_scale=2)
    for box in BOXES:
        cv.cls(BG)
        ui_draw.paint(cv, th, box, "desk.icon")
        px = indices_of(cv)
        for y in range(48):
            for x in range(64):
                if not (box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]):
                    assert px[y * 64 + x] == BG, (box, x, y)


# -- the fuzz: the boards' module reads every sheet as the host does ---------------------


_MP_DRIVER = """
import moy_ui
f = open(@CORPUS@, "rb")
data = f.read()
f.close()
i = 0
while i < len(data):
    n = data[i] | data[i + 1] << 8 | data[i + 2] << 16
    src = data[i + 3:i + 3 + n]
    i += 3 + n
    print(repr((moy_ui.sheet_check(src, 64), moy_ui.sheet_load(src), moy_ui.sheet_set())))
"""


def test_the_boards_module_reads_the_sheet_corpus_as_the_host_does(tmp_path):
    exe = require_unix_mp(
        "moy_ui",
        why="Without it the sheet reader's MicroPython face, the one every board "
            "imports, never meets an untrusted sheet on the host.")
    corpus = on_glass.sheet_corpus(160)
    blob = b"".join(len(s).to_bytes(3, "little") + s for s in corpus)
    (tmp_path / "corpus.bin").write_bytes(blob)
    script = tmp_path / "driver.py"
    script.write_text(_MP_DRIVER.replace("@CORPUS@", repr(str(tmp_path / "corpus.bin"))))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=120)
    lines = out.stdout.splitlines()
    assert len(lines) == len(corpus), out.stdout[-500:] + out.stderr
    for src, line in zip(corpus, lines):
        want = (ui_draw.sheet_check(src, 64), ui_draw.sheet_load(src), moy_ui.sheet_set())
        assert repr(want) == line, src[:80]


def test_a_mutated_sheet_never_breaks_the_reader():
    for src in on_glass.sheet_corpus(600):
        rc = ui_draw.sheet_load(src)
        assert rc in (OK, BIG, SYNTAX)
        assert any(moy_ui.sheet_set()) <= (rc == OK)


def test_the_host_paints_the_fixture_as_the_boards_will():
    frames = on_glass.sprite_host_frames()
    assert len(frames) == len(on_glass.SPRITE_SCENES)
    assert all(any(v != BG for v in f) for f in frames)


# -- the look ---------------------------------------------------------------------------


def _put(ws, name="sprig"):
    dst = Path(ws.carts_root) / (name + ".moy")
    shutil.copytree(FIXTURE, dst)
    ws.carts.rescan()
    return dst


def test_the_theme_brings_its_sheets_and_takes_them_away(tmp_path):
    ws = build_desktop_ws(tmp_path)
    default = ws.look.icon_sheet.to_hex()
    _put(ws)
    ws.look.set_theme("sprig")
    assert ws.look.theme_name == "sprig" and all(moy_ui.sheet_set())
    assert ws.look.pixel_sheet is not None and ws.look.pixel_slots is None
    assert ws.look.icon_sheet.to_hex() == default, "the shell's own sheet is its own"
    ws.look.set_theme("moy")
    assert not any(moy_ui.sheet_set())
    assert ws.look.pixel_sheet is None
    assert ws.look.icon_sheet.to_hex() == default
    # a sheet that will not load leaves the parts on their surfaces
    (Path(ws.carts_root) / "sprig.moy" / "parts.moygfx").write_bytes(b"not hex")
    ws.carts.rescan()
    ws.look.set_theme("sprig")
    assert ws.look.theme_name == "sprig" and not any(moy_ui.sheet_set())


def test_the_slots_its_look_names_draw_from_its_tiles(tmp_path):
    """`icons.pixel_slots` names the slots a theme draws from its own tiles
    (its `icons` sheet in the pixel set's shape), over its icon set: those
    alone, in its colours, at a whole scale."""
    from runtime import iconsets
    ws = build_desktop_ws(tmp_path)
    dst = Path(ws.carts_root) / "tiles.moy"
    shutil.copytree(FIXTURE, dst)
    (dst / "theme.json").write_text(json.dumps({
        "format": 2, "name": "tiles", "icons": "icons.moygfx",
        "look": {"icons": {"kind": "smooth", "set": "default", "pixel_slots": ["home"]}}}))
    sheet = iconsets.blank_sheet()
    ox, oy = sheet.tile_origin(iconsets.SLOT["home"])
    for y in range(16):
        for x in range(16):
            sheet.pset(ox + x, oy + y, 8)
    (dst / "icons.moygfx").write_text(sheet.to_hex())
    ws.carts.rescan()
    ws.look.set_theme("tiles")
    assert ws.look.theme_name == "tiles"
    flags = ws.look.pixel_slots
    assert flags is not None and [i for i in range(len(flags)) if flags[i]] == [
        iconsets.SLOT["home"]]
    assert ws.look.pixel_image("home") is not None
    assert ws.look.pixel_image("wifi") is None
    cv = make_system_canvas(40, 40)
    cv.cls(BG)
    assert ws.draw_icon("home", (4, 4, 32, 32), 7, cv)
    got = indices_of(cv)
    assert got[4 * 40 + 4] == 8 and got[35 * 40 + 35] == 8, "a whole 2x tile"
    ws.look.set_theme("moy")
    assert ws.look.pixel_image("home") is None


def test_the_sheets_travel_with_custom_and_name(tmp_path):
    ws = build_desktop_ws(tmp_path)
    _put(ws)
    ws.look.set_theme("sprig")
    assert ws.look.change("accent", 12) == 0
    ws.look.tick(1.0)
    assert ws.look.theme_name == "custom" and all(moy_ui.sheet_set())
    th = ws.theme_colors
    assert th["accent"] == 12
    assert ws.look.commit()
    folder = Path(ws.carts_root) / "custom.moy"
    assert (folder / "parts.moygfx").read_bytes() == SHEET
    assert (folder / "icons.moygfx").read_bytes() == (FIXTURE / "icons.moygfx").read_bytes()
    before = ws.look.package_rows()["custom"]
    assert ws.look.name_custom("leafy") is None
    ws.carts.rescan()
    assert ws.look.theme_name == "leafy" and all(moy_ui.sheet_set())
    leafy = Path(ws.carts_root) / "leafy.moy"
    assert (leafy / "parts.moygfx").read_bytes() == SHEET
    rows = ws.look.package_rows()
    assert rows["leafy"][0] == 0
    # the digest covers the sheets: the same rows with another sheet differ
    (leafy / "parts.moygfx").write_bytes(SHEET.replace(b"6", b"5", 1))
    ws.carts.rescan()
    assert ws.look.package_rows()["leafy"] != rows["leafy"]
    assert before[0] == 0


def test_a_console_tier_draws_the_themes_buttons(tmp_path):
    ws = build_ws(tmp_path)
    _put(ws)
    ws.look.set_theme("sprig")
    th = ws.theme_colors
    cv = make_system_canvas(48, 32)
    cv.cls(BG)
    ui_draw.button(cv, th, (4, 4, 40, 24), "OK")
    px = indices_of(cv)
    assert px[4 * 48 + 4] == BG                      # the fixture's rounded corner
    assert px[4 * 48 + 20] == th["edge"]             # its border
