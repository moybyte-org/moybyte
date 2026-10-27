"""The icon sets (docs/look_2026-10.md step 16, runtime/iconsets.py): the
masks every smooth set fills from its sources, the pixel set and its swap,
Moy's outline, the slots past 0x7f, and the bar, the tabs and a depth
frame's title buttons drawing the theme's set. The Editor's theme mode,
which paints a theme's own tiles over its set, is tests/test_theme_mode.py's."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from ws_helpers import build_ws as _build_ws, install_theme, use_theme  # noqa: E402


def _ws(tmp_path, **kw):
    """A workstation on the `night` fixture (tests/fixtures/themes/) in its
    dark mode, a palette theme with no look; a test of a look sets its theme."""
    ws = _build_ws(tmp_path, **kw)
    use_theme(ws, "night", "dark")
    return ws


# --- the icon sets (docs/look_2026-10.md step 16) ---------------------------------

import importlib  # noqa: E402
import json  # noqa: E402
import struct  # noqa: E402

import pytest  # noqa: E402

from runtime import chrome, iconsets  # noqa: E402
from runtime import ui_draw as _ui  # noqa: E402

ICONS = ROOT / "native" / "moy_ui" / "icons"


def _installed(look, name="iconpkg"):
    rc, _notes = _ui._C.theme_install(json.dumps(
        {"format": 2, "name": name, "inherits": "moy", "look": look}).encode())
    assert rc == 0
    return chrome.live_colors(name, "light")


def _glyph(src, byte):
    """(w, h, x, y, levels) of a mask file's glyph at `byte`, or None."""
    rec = src[16 + byte]
    if rec == 0:
        return None
    at = 16 + 256 + rec * 8
    off = src[at] | src[at + 1] << 8 | src[at + 2] << 16
    w, h, x, y = struct.unpack("<BBbb", src[at + 3:at + 7])
    bpp = src[5]
    base = 16 + 256 + src[9] * 8
    lv = []
    for i in range(w * h):
        bit = i * bpp
        b = src[base + off + (bit >> 3)]
        lv.append((b >> (8 - bpp - (bit & 7))) & ((1 << bpp) - 1))
    return w, h, x, y, [lv[r * w:(r + 1) * w] for r in range(h)]


def test_every_set_fills_the_slots_of_the_sources():
    names = [ln.split()[1] for ln in (ICONS / "pixel" / "slots.txt").read_text().splitlines()
             if ln.strip()]
    assert list(iconsets.SLOTS) == names
    meta = json.loads((ICONS / "set.json").read_text())
    assert meta["slots"] == names and meta["first"] == iconsets.FIRST
    assert not set(iconsets.ALIASES) & set(names), "an alias may not hide a slot"


@pytest.mark.parametrize("name", ["default", "coast", "luna"])
@pytest.mark.parametrize("size", [16, 20, 24])
def test_the_sprite_tools_icons_share_the_sets_grid(name, size):
    """The sprite row's line arrows stand on the set's grid, as large as
    the rotate icon beside them (R3-11: they were 2-4 px smaller), and the
    row's mirror, eraser and vertical flip are slots every set draws."""
    src = (ICONS / ("%s-%d.fnt" % (name, size))).read_bytes()

    def box(slot):
        g = _glyph(src, iconsets.FIRST + iconsets.SLOT[slot])
        assert g is not None, "%s-%d draws no %s" % (name, size, slot)
        w, h, _x, _y, lv = g
        cols = [c for c in range(w) if any(lv[r][c] for r in range(h))]
        rows = [r for r in range(h) if any(lv[r])]
        return cols[-1] - cols[0] + 1, rows[-1] - rows[0] + 1

    rw, rh = box("rotate")
    for arrow in ("arrow_left", "arrow_right", "arrow_up", "arrow_down"):
        aw, ah = box(arrow)
        assert max(aw, ah) >= max(rw, rh), "%s-%d: %s is %dx%d beside rotate's %dx%d" % (
            name, size, arrow, aw, ah, rw, rh)
    for slot in ("eraser", "mirror", "flip_v"):
        box(slot)


def test_masks_build_from_their_sources():
    """The committed masks and pixel set are what tools/make_icons.py builds from
    the SVG sources and pixel_icons.py: a derived artifact, compared byte for byte
    under the rasteriser that made them."""
    pytest.importorskip("resvg_py")
    pytest.importorskip("PIL")
    make_icons = importlib.import_module("tools.make_icons")
    meta = json.loads((ICONS / "set.json").read_text())
    if meta["built_with"] != make_icons.versions():
        pytest.skip("resvg %s, not the set's %s" % (make_icons.versions(), meta["built_with"]))
    files = make_icons.build_all()
    for name, data in files.items():
        assert (ICONS / name).read_bytes() == data, name + " differs from its sources"
    assert {p.name for p in ICONS.glob("*.fnt")} == {n for n in files if n.endswith(".fnt")}


def test_a_tier_freezes_the_seed_sets_at_its_sizes():
    make_icons = importlib.import_module("tools.make_icons")
    meta = json.loads((ICONS / "set.json").read_text())
    assert meta["tiers"]["handheld"] == ["default-16"]
    # the desk's tiles draw the 20-pixel size (docs/look_2026-10.md step 25)
    assert meta["tiers"]["desktop"] == ["default-16", "default-20", "default-24"]
    lines = "\n".join(make_icons.module_lines("handheld"))
    assert "'default-16.fnt'" in lines and "'system_icons.moygfx'" in lines
    assert "default-20" not in lines and "default-24" not in lines


def test_redo_is_undos_mirror_in_both_kinds():
    sheet = iconsets.pixel_sheet()
    u, r = iconsets.SLOT["undo"], iconsets.SLOT["redo"]
    for y in range(16):
        assert [sheet.tget(r, x, y) for x in range(16)] == \
            [sheet.tget(u, 15 - x, y) for x in range(16)]
    for f in ICONS.glob("*.fnt"):
        src = f.read_bytes()
        size = src[6]
        a = _glyph(src, iconsets.FIRST + u)
        b = _glyph(src, iconsets.FIRST + r)
        if a is None:                       # a layer with no undo art (Luna's fill)
            assert b is None, f.name
            continue
        assert (b[0], b[1], b[3]) == (a[0], a[1], a[3]) and b[2] == size - a[2] - a[0], f.name
        assert b[4] == [row[::-1] for row in a[4]], f.name


def test_moy_draws_its_outline():
    """The canonical Moy is outlined in index 0; the pixel set's clear pixel is 16,
    so the outline is drawn, and the sheet's file round-trips its header."""
    src = (ICONS / "pixel" / "system_icons.moygfx").read_text()
    assert src.startswith("clear 16\n")
    sheet = iconsets.pixel_sheet()
    assert sheet.clear == 16
    img = iconsets.pixel_image("moy")
    assert img.transparent == 16
    assert img.pix.count(0) > 30                       # the outline and the face
    assert img.pix[0] == 16 and img.pix[3 + 16] == 0   # clear corner, outlined top
    assert type(sheet).from_hex(src, iconsets.PIXEL_COLS,
                                iconsets.PIXEL_ROWS).to_hex() == src.rstrip("\n")


def test_the_sheet_loader_takes_the_clear_header():
    rows = "clear 16\n" + "\n".join(["." * 16 + "0123456789abcdef"] * 16)
    rc, n = _ui.sheet_check(rows.encode(), 64)
    assert (rc, n) == (0, 16)
    assert _ui.sheet_check(b"clear 17\n00\n", 64)[0] != 0
    assert _ui.sheet_check(b"clear x\n00\n", 64)[0] != 0


def test_the_pixel_set_takes_the_themes_swap():
    live = _installed({"icons": {"kind": "pixel", "swap": "0123456789abd1ef"}})
    try:
        sp = iconsets.spec(live.look)
        assert sp[0] == "pixel" and sp[2] == bytes([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11,
                                                    13, 1, 14, 15])
        plain = iconsets.pixel_image("wifi")
        swapped = iconsets.pixel_image("wifi", sp[2])
        assert 12 in plain.pix and 12 not in swapped.pix
        assert [sp[2][p] if p != 16 else 16 for p in plain.pix] == list(swapped.pix)
    finally:
        _ui._C.theme_uninstall()


def test_a_bad_swap_is_no_swap():
    live = _installed({"icons": {"kind": "pixel", "swap": "123"}}, "iconbad")
    try:
        sp = iconsets.spec(live.look)
        assert sp[0] == "pixel" and sp[2] is None
    finally:
        _ui._C.theme_uninstall()


def test_icons_names_a_set_and_a_theme_without_it_keeps_its_sheet():
    """A look names its set; a theme with no look takes the default's (Moy's
    smooth set), and a look that names none keeps the shell's sheet."""
    for name in ("moy", "contrast"):
        assert iconsets.spec(chrome.live_colors(name, "light").look) == ("smooth", "default", None)
    for name in ("night", "platinum", "win95"):
        install_theme(name)
        assert iconsets.spec(chrome.live_colors(name, "dark").look) == \
            ("smooth", "default", None), name
    _square_look()
    assert iconsets.spec(chrome.live_colors("sq", "dark").look) is None


def _square_look():
    """A package whose look names no icon set, in the install slot."""
    import moy_ui
    assert moy_ui.theme_install(json.dumps(
        {"format": 2, "name": "sq", "look": {"win_r": 4}}).encode())[0] == 0
    chrome.forget_themes()


def _pixels(cv, rect):
    """The RGB565 words of `rect` on canvas `cv`, row by row."""
    x, y, w, h = rect
    buf, stride = bytes(cv._buf), cv._stride
    return [buf[2 * ((y + j) * stride + x):2 * ((y + j) * stride + x + w)] for j in range(h)]


@pytest.mark.parametrize("box", [16, 32])
def test_a_smooth_set_draws_every_slot_and_falls_back_to_the_default(tmp_path, box):
    ws = _ws(tmp_path)
    cv = ws.sys_canvas
    for set_name in ("default", "luna", "coast", "no_such_set"):
        for kind in iconsets.SLOTS:
            cv.rect(0, 0, 40, 40, 0)
            assert iconsets.draw(cv, kind, (0, 0, box, box), 7, ("smooth", set_name, None)), \
                (set_name, kind)
            assert len(set(b"".join(_pixels(cv, (0, 0, box, box))))) > 1, (set_name, kind)
    assert not iconsets.draw(cv, "teapot", (0, 0, 16, 16), 7, ("smooth", "default", None))
    iconsets.reset()                # the sets' masks back out of the font rows


def test_the_bar_draws_the_themes_set(tmp_path):
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    assert ws.icon_spec() == ("smooth", "default", None)
    cv = ws.sys_canvas
    cv.rect(0, 0, 40, 20, 0)
    ws._icon("wifi", 0, 0, cv, 1, ink=7)
    smooth = _pixels(cv, (0, 0, 16, 16))
    cv.rect(0, 0, 40, 20, 0)
    iconsets.draw(cv, "wifi", (0, 0, 16, 16), 7, ("smooth", "default", None))
    assert smooth == _pixels(cv, (0, 0, 16, 16))
    _square_look()
    ws.look.set_theme("sq", persist=False, variant="dark")
    assert ws.icon_spec() is None
    assert not ws.draw_icon("wifi", (0, 0, 16, 16), 7, cv)


def test_a_depth_frames_title_buttons_take_the_set(tmp_path):
    """moy_ui.icons_use hands the C its set: the title buttons of a depth frame
    draw the set's close/maximize/minimize masks, and its glyphs without one."""
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    cv = ws.sys_canvas
    rect, th = (0, 0, 300, 120), 44

    def frame():
        cv.rect(0, 0, 300, 120, 7)
        _ui.frame(cv, ws.theme_colors, rect, th, "T", True, True, 1)
        return _pixels(cv, (150, 0, 150, th))

    iconsets.use_parts(None)
    glyphs = frame()
    iconsets.use_parts(("smooth", "default", None))
    masks = frame()
    assert masks != glyphs
    assert ws.icon_spec() == ("smooth", "default", None)
    iconsets.use_parts(("pixel", None, None))
    assert frame() == glyphs
    iconsets.reset()
    use_theme(ws, "night", "dark")     # the depth is global


def test_one_icon_one_meaning_for_the_round_4_pairs():
    """The gear means Settings alone and `dup` Duplicate alone (D-22):
    Appearance wears the palette and the sprite tab's Copy the clipboard
    (design review R4-15, R4-16)."""
    from runtime import paint_layer
    assert iconsets.slot_of("appearance") == iconsets.SLOT["palette"]
    assert iconsets.slot_of("appearance") != iconsets.slot_of("settings")
    copy = [slot for oid, _label, slot in paint_layer._OPS if oid == "copy"]
    assert copy == ["copy"]


def test_load_and_to_files_wear_two_folders_that_say_their_direction():
    """The sprite map's Load and To Files are inverse operations on one
    object: Load the open folder, To Files the folder with an arrow out of
    it (m21, 2026-10-19), never the same folder twice."""
    import inspect
    from runtime import paint_layer
    src = inspect.getsource(paint_layer.PaintLayer._draw_look)
    assert '"To Files", ("export"' in src and '"Load", ("folder_open"' in src
    assert iconsets.SLOT["export"] != iconsets.SLOT["folder_open"]


@pytest.mark.parametrize("name", sorted(p.name for p in (ICONS / "smooth").iterdir()))
def test_a_sets_line_weight_is_read_off_its_own_mask(name):
    """iconsets.weight measures the set's `new` mask (what a board carries),
    and it agrees with the stroke its source draws: the New card's plus
    keeps each set's weight at any size."""
    import re
    src = (ICONS / "smooth" / name / "new.svg").read_text()
    stroke = float(re.findall(r'stroke-width="([0-9.]+)"', src)[0])
    got = iconsets.weight(("smooth", name, None))
    assert got is not None and abs(got - stroke) <= 0.2, (name, got, stroke)
    assert iconsets.weight(("pixel", None, None)) is None
    assert iconsets.weight(None) is None


@pytest.mark.parametrize("theme,mode", [("moy", "light"), ("moy", "dark"),
                                        ("blocks", "light"), ("contrast", "light"),
                                        ("contrast", "dark")])
def test_the_new_card_is_a_blank_page_with_the_sets_plus(tmp_path, theme, mode):
    """The "+ New" card draws the theme's surface and the plus in its accent
    ink, no IconSheet sprite: its centre is the ink, its corner-in the page."""
    from runtime.launcher_layer import draw_new_art
    ws = _ws(tmp_path)
    ws.look.set_theme(theme, persist=False, variant=mode)
    th = ws.theme_colors
    cv = ws.sys_canvas
    cv.rect(0, 0, 140, 140, th["ink"])
    draw_new_art(cv, th, (4, 4, 128, 128), 12, False, ws.icon_spec())

    def at(x, y):
        return _pixels(cv, (x, y, 1, 1))[0]

    def word(ci):
        cv.rect(0, 136, 1, 1, ci)
        return at(0, 136)

    assert at(68, 68) == word(th["ink_accent"])
    assert at(30, 30) == word(th["surface"])
    assert at(68, 30) == word(th["surface"])      # above the plus's arm


# -- the slots past 0x7f: an icon's glyph by its index, never decoded as text ----------

PAST = [n for n in iconsets.SLOTS if iconsets.FIRST + iconsets.SLOT[n] > 0x7f]
MASKS = sorted(p.name for p in ICONS.glob("*.fnt"))


def test_the_sets_hold_slots_past_0x7f():
    assert PAST and set(PAST) >= {"brush", "resize", "pointer", "grid", "volume", "loop"}
    assert iconsets.FIRST + len(iconsets.SLOTS) <= 256


@pytest.mark.parametrize("name", MASKS)
def test_a_slot_past_0x7f_draws_its_mask_by_index(name):
    """moy_ui.icon_mask draws the glyph at the slot's index byte, as the file
    holds it: full coverage in the ink, none untouched, a part blended between
    -- where a text draw would decode a lone byte past 0x7f as no codepoint."""
    from runtime.host_canvas import make_system_canvas
    import moy_ui
    src = (ICONS / name).read_bytes()
    size, top = src[6], (1 << src[5]) - 1
    rc, fid = moy_ui.font_load(src)
    assert rc == 0
    try:
        for slot in PAST:
            g = _glyph(src, iconsets.FIRST + iconsets.SLOT[slot])
            if g is None:
                assert name.startswith("luna.fill"), (name, slot)   # a line icon's fill
                continue
            w, h, gx, gy, lv = g
            cv = make_system_canvas(40, 40)
            cv.rect(0, 0, 40, 40, 0)
            cv.rect(0, 0, 1, 1, 7)
            ink = _pixels(cv, (0, 0, 1, 1))[0]
            cv.rect(0, 0, 1, 1, 0)
            bg = _pixels(cv, (1, 0, 1, 1))[0]
            _ui._C.icon_mask(cv, fid, iconsets.FIRST + iconsets.SLOT[slot], 4, 4, 7)
            got = _pixels(cv, (4 + gx, 4 + size - gy, w, h))
            for r in range(h):
                for c in range(w):
                    px = got[r][2 * c:2 * c + 2]
                    if lv[r][c] == top:
                        assert px == ink, (name, slot, c, r)
                    elif lv[r][c] == 0:
                        assert px == bg, (name, slot, c, r)
                    else:
                        assert px not in (ink, bg), (name, slot, c, r)
            inked = sum(row.count(ink) for row in [[row[i:i + 2] for i in range(0, len(row), 2)]
                                                   for row in _pixels(cv, (0, 0, 40, 40))])
            assert inked == sum(v == top for row in lv for v in row), (name, slot)
    finally:
        moy_ui.font_free(fid)


@pytest.mark.parametrize("box", [16, 20, 24, 32])
def test_every_set_and_kind_draws_the_slots_past_0x7f(tmp_path, box):
    ws = _ws(tmp_path)
    cv = ws.sys_canvas
    specs = [("smooth", s, None) for s in ("default", "coast", "luna")]
    specs += [("pixel", None, None), ("pixel", None, bytes(range(15, -1, -1)))]
    for spec in specs:
        for slot in PAST:
            cv.rect(0, 0, 40, 40, 0)
            assert iconsets.draw(cv, slot, (0, 0, box, box), 7, spec), (spec, slot)
            assert len(set(b"".join(_pixels(cv, (0, 0, box, box))))) > 1, (spec, slot)
            if spec[0] == "smooth":
                assert iconsets.ink_span(slot, box, box, spec) is not None, (spec, slot)
    iconsets.reset()


def test_the_title_buttons_draw_a_slot_past_0x7f(tmp_path):
    """moy_ui.icons_use takes a slot's byte past 0x7f for a title button and
    the C draws its mask by index."""
    ws = _ws(tmp_path)
    ws.look.set_theme("moy", persist=False, variant="light")
    cv = ws.sys_canvas
    rect, th = (0, 0, 300, 120), 44

    def frame():
        cv.rect(0, 0, 300, 120, 7)
        _ui.frame(cv, ws.theme_colors, rect, th, "T", True, True, 1)
        return _pixels(cv, (150, 0, 150, th))

    iconsets.use_parts(("smooth", "default", None))
    masks = frame()
    f16 = iconsets._pick("default", 16, True)[0]
    f24 = iconsets._pick("default", 24, True)[0]
    codes = [iconsets.FIRST + iconsets.SLOT[n] for n in ("grid", "resize", "loop")]
    assert min(codes) > 0x7f
    _ui._C.icons_use(f16, f24, *codes)
    past = frame()
    _ui._C.icons_use(0, 0, 0, 0, 0)
    glyphs = frame()
    assert past != masks and past != glyphs
    iconsets.reset()
    use_theme(ws, "night", "dark")


# The boards' module: the same masks drawn by the desktop MicroPython's moy_ui.
_MP_ICONS = r'''import sys
sys.path.insert(0, @RUNTIME@)
sys.path.insert(0, @DEVICE@)
sys.path.insert(0, @STAGE@)
import binascii
import moy_ui
import device_canvas as dc


class Comp:
    def __init__(self, w, h):
        self._w, self._h = w, h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def gfx(self):
        import moy_gfx
        return moy_gfx


def make(w, h):
    return dc.SystemCanvas(Comp(w, h))

'''

ICON_CASES = r'''
def icon_cases(make, out, files, codes):
    for name in files:
        f = open(name, "rb")
        src = f.read()
        f.close()
        rc, fid = moy_ui.font_load(src)
        for code in codes:
            cv = make(40, 40)
            cv.rect(0, 0, 40, 40, 1)
            moy_ui.icon_mask(cv, fid, code, 4, 4, 7)
            cv.flush_batch()
            out(rc, code, bytes(cv._buf))
        moy_ui.font_free(fid)
'''


def test_the_boards_module_draws_the_slots_past_0x7f(tmp_path):
    import shutil
    import subprocess
    import zlib
    from unix_mp import require_unix_mp
    exe = require_unix_mp(
        "moy_ui", "moy_gfx", "moy_glass",
        why="Without it the boards' icon_mask, the one every board imports, "
            "never draws a slot past 0x7f on the host.")
    import moy_ui
    from runtime.host_canvas import make_system_canvas
    stage = tmp_path / "stage"
    stage.mkdir()
    shutil.copy(ROOT / "runtime" / "font.py", stage / "moy_font.py")
    shutil.copy(ROOT / "runtime" / "moy_image.py", stage / "moy_image.py")
    files = [str(ICONS / n) for n in MASKS]
    codes = [iconsets.FIRST + iconsets.SLOT[n] for n in PAST]
    script = tmp_path / "icons.py"
    script.write_text(_MP_ICONS.replace("@DEVICE@", repr(str(ROOT / "device")))
                      .replace("@RUNTIME@", repr(str(ROOT / "runtime")))
                      .replace("@STAGE@", repr(str(stage)))
                      + ICON_CASES
                      + "icon_cases(make, lambda rc, c, b: print(rc, c, binascii.crc32(b)), "
                        "%r, %r)\n" % (files, codes))
    run = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-2000:] or run.stdout[-2000:]
    got = [tuple(int(v) for v in ln.split()) for ln in run.stdout.splitlines()]
    want = []
    g = {"moy_ui": moy_ui}
    exec(ICON_CASES, g)
    g["icon_cases"](make_system_canvas,
                    lambda rc, c, b: want.append((rc, c, zlib.crc32(b))), files, codes)
    cv = make_system_canvas(40, 40)
    cv.rect(0, 0, 40, 40, 1)
    blank = zlib.crc32(bytes(cv._buf))
    assert len(got) == len(want) == len(files) * len(codes)
    assert got == want
    held = [_glyph((ICONS / n).read_bytes(), c) is not None for n in MASKS for c in codes]
    assert [w[0] for w in want] == [0] * len(want)
    assert [w[2] != blank for w in want] == held
