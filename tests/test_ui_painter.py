"""The surface painter (native/moy_ui/moy_ui_paint.c, docs/theming_2026-09.md
§4.2-§4.4) on the host's canvas, pixel for pixel.

A theme with surfaces is installed beside the seeds and its shape made the
toolkit's (`ui_draw.use_shape`); the colours are the default theme's (night,
dark), whose shade roles derive from its hilite and dim. Each edge, fill,
radius and text effect is drawn into a small canvas and read back as palette
indices against a picture: `.` is the background left untouched, the other
letters the colours in LEGEND.

The goldens hold the other half: no seed names a surface, so every primitive
draws as it did.
"""

import json

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import ui_draw
from runtime.chrome import theme_colors
from runtime.host_canvas import indices_of
from runtime.host_canvas import make_system_canvas as SystemCanvas

BG = 9
W, H = 16, 12


def _legend(th):
    return {".": BG, "F": 7, "B": 12, "D": th["shade_dark"], "H": th["shade_hi"],
            "L": th["shade_lo"]}


@pytest.fixture
def theme():
    """Install a package and make its shape the toolkit's; the empty shape
    again afterwards, so no other test draws with it."""
    def use(**kw):
        d = {"format": 2, "name": "painted"}
        d.update(kw)
        rc, _notes = moy_ui.theme_install(json.dumps(d).encode())
        assert rc == 0
        ui_draw.use_shape("painted")
        return theme_colors("night")
    yield use
    ui_draw.use_shape(None)


def _canvas(w=W, h=H):
    cv = SystemCanvas(w, h)
    cv.cls(BG)
    return cv


def _picture(cv, th, x, y, w, h):
    px = indices_of(cv)
    inv = {v: k for k, v in reversed(list(_legend(th).items()))}
    return ["".join(inv.get(px[(y + j) * cv.w + x + i], "?") for i in range(w))
            for j in range(h)]


def _paint(th, text, w, h, x=2, y=2, metrics=None, use=None):
    use(surfaces={"desk": {"rest": text}}, metrics=metrics or {})
    cv = _canvas()
    assert ui_draw.paint(cv, th, (x, y, w, h), "desk")
    return _picture(cv, th, x, y, w, h), cv


@pytest.mark.parametrize("text,want", [
    ("flat, fill 7", ["FFFFFF", "FFFFFF", "FFFFFF", "FFFFFF", "FFFFFF"]),
    ("raised, fill 7", ["HHHHHL", "HFFFFL", "HFFFFL", "HFFFFL", "LLLLLL"]),
    ("sunken, fill 7", ["LLLLLH", "LFFFFH", "LFFFFH", "LFFFFH", "HHHHHH"]),
    ("etched, fill 7", ["LLLLLH", "LHHHLH", "LHFFLH", "LLLLLH", "HHHHHH"]),
    ("bevel2, fill 7", ["FFFFFD", "FHHHLD", "FHFFLD", "FLLLLD", "DDDDDD"]),
    ("raised, fill 7, border 12", ["BBBBBB", "BHHHLB", "BHFFLB", "BLLLLB", "BBBBBB"]),
    ("fill 7, shadow", ["FFFF..", "FFFFD.", "FFFFDD", ".DDDDD", "..DDDD"]),
])
def test_each_edge(theme, text, want):
    th = theme()
    got, _cv = _paint(th, text, 6, 5, use=theme)
    assert got == want


@pytest.mark.parametrize("radius,w,h,want", [
    (1, 8, 6, [".BBBBBB.", "BFFFFFFB", "BFFFFFFB", "BFFFFFFB", "BFFFFFFB", ".BBBBBB."]),
    (2, 8, 6, ["..BBBB..", ".BFFFFB.", "BFFFFFFB", "BFFFFFFB", ".BFFFFB.", "..BBBB.."]),
    (3, 8, 8, ["...BB...", ".BBFFBB.", ".BFFFFB.", "BFFFFFFB", "BFFFFFFB", ".BFFFFB.",
               ".BBFFBB.", "...BB..."]),
])
def test_the_corner_masks(theme, radius, w, h, want):
    th = theme()
    got, _cv = _paint(th, "fill 7, border 12", w, h, metrics={"radius": radius}, use=theme)
    assert got == want


def test_a_radius_rounds_every_level_of_an_edge(theme):
    th = theme()
    got, _cv = _paint(th, "raised, fill 7, border 12", 8, 8, metrics={"radius": 3},
                      use=theme)
    assert got == ["...BB...", ".BBHHBB.", ".BHFFHB.", "BHFFFFLB", "BHFFFFLB",
                   ".BLFFLB.", ".BBLLBB.", "...BB..."]


def test_patterns_are_anchored_to_the_screen_and_opaque(theme):
    th = theme()
    a, _cv = _paint(th, "dither 7/0", 4, 4, x=2, y=2, use=theme)
    assert a == ["FDFD", "DFDF", "FDFD", "DFDF"]
    b, _cv = _paint(th, "dither 7/0", 4, 4, x=3, y=2, use=theme)
    assert b == ["DFDF", "FDFD", "DFDF", "FDFD"]
    p, _cv = _paint(th, "pinstripe 7/0", 4, 4, x=2, y=2, use=theme)
    assert p == ["FFFF", "DDDD", "FFFF", "DDDD"]


def test_a_pattern_is_one_fill(theme):
    th = theme()
    theme(surfaces={"desk": {"rest": "dither 7/0"}})
    cv = SystemCanvas(64, 48)
    cv.gate_counts_reset()
    ui_draw.paint(cv, th, (0, 0, 64, 48), "desk")
    assert cv.gate_counts()[0] == 1


def test_a_gradient_runs_from_its_first_colour_to_its_second(theme):
    th = theme(surfaces={"desk": {"rest": "gradient 7/0"}})
    cv = SystemCanvas(8, 33)
    ui_draw.paint(cv, th, (0, 0, 8, 33), "desk")
    px = indices_of(cv)
    rows = [px[j * 8:(j + 1) * 8] for j in range(33)]
    assert set(rows[0]) == {7} and set(rows[-1]) == {0}
    dark = [sum(r.count(0) for r in rows[j:j + 4]) for j in range(0, 32, 4)]
    assert dark == sorted(dark) and dark[0] < dark[-1]


def test_a_part_without_a_surface_paints_nothing(theme):
    th = theme(surfaces={"desk": {"rest": "fill 7"}})
    cv = _canvas()
    assert not ui_draw.paint(cv, th, (0, 0, 8, 8), "bar")
    assert set(indices_of(cv)) == {BG}


def test_a_state_follows_the_chain(theme):
    th = theme(surfaces={"chip": {"rest": "fill 7", "hot": "fill 12"},
                         "window": {"active": "fill 3"}})

    def one(part, state):
        cv = _canvas()
        assert ui_draw.paint(cv, th, (0, 0, 4, 4), part, state)
        return indices_of(cv)[0]
    assert one("chip", "pressed") == 12       # pressed -> hot
    assert one("chip", "hover") == 7          # hover -> rest
    assert one("window", "inactive") == 3     # inactive -> active


def test_a_primitive_paints_its_surface_in_place_of_its_frame(theme):
    th = theme(surfaces={"button": {"rest": "bevel2, border"}})
    a, b = _canvas(), _canvas()
    ui_draw.button(a, th, (2, 2, 12, 8), "")
    assert ui_draw.paint(b, th, (2, 2, 12, 8), "button")
    assert indices_of(a) == indices_of(b)
    # the style's own field and edge stand where the surface names none
    f, _i, e = ui_draw.state_colors(th, "button", ui_draw.REST)
    pic = indices_of(a)
    assert pic[2 * W + 2] == e and pic[5 * W + 6] == f


def _label_pixels(th, label, **kw):
    full, bare = _canvas(48, 16), _canvas(48, 16)
    ui_draw.button(full, th, (0, 0, 48, 16), label, **kw)
    ui_draw.button(bare, th, (0, 0, 48, 16), "", **kw)
    a, b = indices_of(full), indices_of(bare)
    return {(i % 48, i // 48): a[i] for i in range(len(a)) if a[i] != b[i]}


def test_the_pressed_label_shifts(theme):
    plain = _label_pixels(theme(), "AB", state=ui_draw.PRESSED)
    th = theme(metrics={"press_dx": 1, "press_dy": 2})
    moved = _label_pixels(th, "AB", state=ui_draw.PRESSED)
    assert plain and moved == {(x + 1, y + 2): c for (x, y), c in plain.items()}
    # only the pressed state moves
    assert _label_pixels(th, "AB") == _label_pixels(theme(), "AB")


def test_text_effects(theme):
    plain = _label_pixels(theme(), "AB")
    ink = set(plain.values())
    assert len(ink) == 1
    ink = ink.pop()
    th = theme(text={"button": {"rest": {"effect": "shadow", "color": "shade_dark"}}})
    got = _label_pixels(th, "AB")
    extra = {p: c for p, c in got.items() if p not in plain}
    assert extra and all(c == th["shade_dark"] and (p[0] - 1, p[1] - 1) in plain
                         for p, c in extra.items())
    assert all(got[p] == ink for p in plain)
    th = theme(text={"button": {"rest": {"effect": "emboss", "color": "shade_hi"}}})
    got = _label_pixels(th, "AB")
    assert all(got[p] == ink for p in plain)
    for p, c in got.items():
        if p in plain:
            continue
        up = (p[0] + 1, p[1] + 1) in plain and c == th["shade_hi"]
        down = (p[0] - 1, p[1] - 1) in plain and c == ink
        assert up or down, (p, c)


def test_an_effect_follows_the_chain_and_a_named_none_stops_it(theme):
    plain = theme()
    hover0 = _label_pixels(plain, "AB", state=ui_draw.HOVER)
    pressed0 = _label_pixels(plain, "AB", state=ui_draw.PRESSED)
    th = theme(text={"button": {"rest": {"effect": "shadow", "color": "shade_dark"},
                                "hover": {"effect": "none"}}})
    assert _label_pixels(th, "AB", state=ui_draw.HOVER) == hover0
    assert _label_pixels(th, "AB", state=ui_draw.PRESSED) != pressed0    # pressed -> hot -> rest


def test_the_on_glass_painter_scenes_paint_on_the_host():
    """tests/on_glass.py's painter_matches_host compares a board with these."""
    import on_glass
    frames = on_glass.painter_host_frames()
    assert len(frames) == len(on_glass.PAINTER_SCENES)
    assert all(len(f) == 16 * 12 and set(f) - {9} for f in frames)
