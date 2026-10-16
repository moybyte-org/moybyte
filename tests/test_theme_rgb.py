"""RGB colours and the look (docs/look_2026-10.md step 5).

* A role takes "#rrggbb" (RGB | 0xrrggbb in the row) beside a MOY64 index;
  what is no colour is dropped and noted, an index still clamps.
* Each RGB value has a nearest MOY64 index, an exact MOY64 colour its own.
* Placement: an RGB value takes the block slot (64..127) of its RGB565
  word, one slot a word; past 64 words a value folds to its nearest index.
* The live row, the content set, the look's colours and the Appearance
  swatches of every seed in both modes fit the block.
* The cart boundary: inside it a colour index is its index modulo 64 on
  every canvas path, so a cart never draws the block and never reads it.
* The look: the model's defaults, a variant's object before the shared one,
  inheritance, the leaves' clamps, paints, maps, and `custom` carrying it.
* The app ABI: a RUN grant is answered each role's nearest index.
"""

import json

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import chrome

RGB = 0x1000000
NONE = -(1 << 31)
OFF = NONE + 1
OK, NEWER = 0, 3
N_CLAMPED, N_UNKNOWN, N_DROPPED = 1, 2, 4
NAMES, KINDS, BTN = moy_ui.theme_look_fields()
LEAF = {n: i for i, n in enumerate(NAMES)}


def _src(name="t", **kw):
    d = {"format": 2, "name": name}
    d.update(kw)
    return json.dumps(d).encode()


def _install(**kw):
    rc, notes = moy_ui.theme_install(_src("rgb", **kw))
    assert rc == OK
    return notes


def _role(role, light=False, name="rgb"):
    return dict(zip(moy_ui.theme_roles(), moy_ui.theme(name, light)[0]))[role]


def _leaf(name, leaf, light=False):
    return moy_ui.theme_look(name, light)[0][LEAF[leaf]]


# -- RGB roles ----------------------------------------------------------------

def test_a_role_takes_rgb_and_an_index():
    notes = _install(colors={"dark": {"panel": "#0f1127", "accent": "#FFC23D", "ink": 7}})
    assert notes == 0
    assert _role("panel") == RGB | 0x0f1127
    assert _role("accent") == RGB | 0xffc23d
    assert _role("ink") == 7


@pytest.mark.parametrize("bad", ["#12345", "#1234567", "red", "#gg0000", "0f1127",
                                 "#0f112", True, None, [1, 2], 1.5])
def test_what_is_no_colour_is_dropped(bad):
    notes = _install(colors={"dark": {"panel": bad}})
    assert notes == N_DROPPED
    assert _role("panel") == _role("panel", name="night")


def test_an_index_still_clamps():
    assert _install(colors={"dark": {"panel": 99}}) == N_CLAMPED
    assert _role("panel") == 63


def test_a_format_1_package_reads_as_absent():
    assert moy_ui.theme_install(b'{"format": 1, "name": "old"}')[0] == NEWER
    assert moy_ui.theme_resolve(b'{"format": 1, "name": "old"}')[0] == NEWER


# -- nearest and placement --------------------------------------------------------

def test_the_nearest_index_of_a_moy64_colour_is_its_own():
    import ctypes
    m64 = (ctypes.c_uint32 * 64).in_dll(moy_ui.lib, "moy_theme_moy64")
    for i in range(64):
        rgb565, _lum, near = moy_ui.theme_rgb(RGB | m64[i])
        assert near == i or m64[near] == m64[i], i
        assert rgb565 == moy_ui.theme_rgb(i)[0]


def test_placement_takes_one_slot_a_word_and_folds_past_the_block():
    vals = [5, RGB | 0xffffff, RGB | 0xfffffe, RGB | 0x102030, 7]
    placed, near, block, used = moy_ui.theme_place(vals)
    assert used == 2                                  # ffffff and fffffe share a word
    assert placed == (5, 64, 64, 65, 7)
    assert block[0] == 0xffff and block[2:] == (0,) * 62
    assert near[0] == 5 and near[1] == near[2] == moy_ui.theme_rgb(RGB | 0xffffff)[2]
    many = [RGB | (i % 32) << 19 | (i // 32) << 10 for i in range(80)]  # 80 words
    placed, near, block, used = moy_ui.theme_place(many)
    assert used == 64
    assert placed[:64] == tuple(range(64, 128))
    assert placed[64:] == near[64:]


@pytest.mark.parametrize("variant", ["dark", "light"])
@pytest.mark.parametrize("seed", list(moy_ui.themes()))
def test_the_live_row_content_look_and_swatches_fit_the_block(seed, variant):
    live = chrome.live_colors(seed, variant)
    swatches = [a for _n, a, _d in moy_ui.theme_accents()]
    values = list(live.tokens.values()) + list(live.content.values()) + swatches
    look = [live.look[0][n] for n, k in zip(NAMES, KINDS) if k == "colour"]
    assert live.used < 64
    for v in values + look:
        assert v in (NONE, OFF) or 0 <= int(v) < 128, (seed, variant, v)
    # every RGB value holds a slot of its own word, none folded
    row = moy_ui.theme(seed, variant == "light")[0]
    for v, p in zip(row, list(live.tokens.values()) + list(live.content.values())):
        if v >= RGB:
            assert p >= 64 and live.block[p - 64] == moy_ui.theme_rgb(v)[0]


def test_every_seed_reads_in_both_modes():
    for seed in moy_ui.themes():
        for light in (False, True):
            _row, bad, rc = moy_ui.theme(seed, light)
            assert (rc, bad) == (OK, 0), (seed, light)


def test_moy_is_one_theme_with_day_and_night():
    day = dict(zip(moy_ui.theme_roles(), moy_ui.theme("moy", True)[0]))
    night = dict(zip(moy_ui.theme_roles(), moy_ui.theme("moy", False)[0]))
    assert day["surface_light"] == 1 and night["surface_light"] == 0
    assert day["desktop"] == RGB | 0xeceef3 and night["desktop"] == RGB | 0x0f1127
    assert day["value"] == RGB | 0x4c3fd6 and night["value"] == RGB | 0xb6a8f7
    # one accent: focus, current and primary are one colour, the header a bar
    assert day["accent"] == day["focus"] == day["value"]
    assert night["accent"] == night["focus"] == night["value"]
    assert _leaf("moy", "win.header", True) == _leaf("moy", "win.header", False) == 2


# -- the cart boundary -----------------------------------------------------------

def _canvas():
    from runtime.host_canvas import make_system_canvas
    return make_system_canvas(16, 8)


def test_a_cart_colour_is_its_index_modulo_64():
    live = chrome.live_colors("moy", "light")
    cv = _canvas()
    cv.theme_block(live.block)
    slot = live.tokens["desktop"]
    assert slot >= 64

    def word(x, y):
        o = 2 * (y * cv.w + x)
        return cv._buf[o] | cv._buf[o + 1] << 8

    cv.rect(0, 0, 2, 2, slot)
    shell = word(0, 0)
    assert shell == cv._col(slot) and shell != cv._col(slot & 63)
    was = moy_ui.cart_text(True)
    try:
        cv.rect(0, 0, 2, 2, slot)
        assert word(0, 0) == cv._col(slot & 63)
        cv.print("A", 4, 0, slot)
        cv.cls(slot)
        assert word(5, 5) == cv._col(slot & 63)
        assert cv.pix(5, 5) < 64
    finally:
        moy_ui.cart_text(was)
    cv.cls(slot)
    assert word(5, 5) == shell


# -- the look -----------------------------------------------------------------------

def test_the_look_defaults_are_the_model():
    lk = moy_ui.theme_look("night", False)[0]
    assert lk[LEAF["focus.ring.w"]] == 250 and lk[LEAF["win_r"]] == 12
    assert lk[LEAF["win.h"]] == 44 and lk[LEAF["win_corners"]] == 15
    assert lk[LEAF["dock.fill"]] == RGB | 0xffffff and lk[LEAF["dock.alpha"]] == 32
    assert lk[LEAF["shadow.kind"]] == 0 and lk[LEAF["win.icon"]] == 1
    assert lk[LEAF["sel_back"]] == NONE and lk[LEAF["buttons.normal.shape"]] == NONE


def test_focus_and_current_are_the_models_properties():
    light = moy_ui.theme_look("night", True)[0]
    dark = moy_ui.theme_look("night", False)[0]
    styles = KINDS[LEAF["focus.style"]][1]
    assert styles == ("ring", "plate", "lift", "edge", "underline", "invert")
    assert KINDS[LEAF["focus.card"]][1] == styles == KINDS[LEAF["focus.small"]][1]
    assert light[LEAF["focus.style"]] == 0 and light[LEAF["focus.card"]] == NONE
    roles = KINDS[LEAF["focus.color"]][1]
    assert roles[light[LEAF["focus.color"]]] == "accent"
    assert roles[light[LEAF["current.color"]]] == "value"
    assert (light[LEAF["focus.plate.alpha"]], dark[LEAF["focus.plate.alpha"]]) == (14, 22)
    assert (light[LEAF["current.alpha"]], dark[LEAF["current.alpha"]]) == (14, 24)
    assert light[LEAF["focus.lift.grow"]] == 5 and light[LEAF["focus.edge.w"]] == 4
    assert KINDS[LEAF["win.controls"]][1][light[LEAF["win.controls"]]] == "three"
    assert KINDS[LEAF["icons.kind"]][1][light[LEAF["icons.kind"]]] == "smooth"


def test_the_moy_seed_sets_its_marks():
    for light in (True, False):
        lk = moy_ui.theme_look("moy", light)[0]
        styles = KINDS[LEAF["focus.style"]][1]
        assert [styles[lk[LEAF["focus." + k]]] for k in ("style", "card", "small")] == [
            "edge", "lift", "underline"]
        assert KINDS[LEAF["current.style"]][1][lk[LEAF["current.style"]]] == "plate"
        rim = (lk[LEAF["focus.lift.rim.c"]], lk[LEAF["focus.lift.rim.alpha"]])
        assert rim == ((RGB | 0xffffff, 100) if light else (RGB | 0xb6a8f7, 60))
        assert KINDS[LEAF["win.header"]][1][lk[LEAF["win.header"]]] == "bar"


def test_a_set_plate_alpha_holds_in_both_modes():
    _install(look={"focus": {"plate": {"alpha": 0.3}}, "current": {"alpha": 0.5}})
    for light in (True, False):
        assert _leaf("rgb", "focus.plate.alpha", light) == 30
        assert _leaf("rgb", "current.alpha", light) == 50


def test_a_variants_object_comes_before_the_shared_one_and_inherits():
    _install(look={"win_r": 4, "focus": {"ring": {"w": 2}}, "dark": {"win_r": 20},
                   "win": {"header": "fill", "frame": [1, "#2b2f57"]}})
    assert _leaf("rgb", "win_r") == 20 and _leaf("rgb", "win_r", True) == 4
    assert _leaf("rgb", "focus.ring.w") == 200
    assert _leaf("rgb", "win.frame.w") == 100
    assert _leaf("rgb", "win.frame.c") == RGB | 0x2b2f57
    assert _leaf("rgb", "win.header", True) == 1


def test_the_leaves_clamp_and_drop_and_note():
    notes = _install(look={"win_r": 400, "focus": {"ring": {"alpha": 7.5}}, "win": {"header": "zigzag"},
                           "shadow": ["soft", "#000000"], "teapot": 3})
    assert notes == N_CLAMPED | N_DROPPED | N_UNKNOWN
    assert _leaf("rgb", "win_r") == 99 and _leaf("rgb", "focus.ring.alpha") == 100
    assert _leaf("rgb", "win.header") == 0 and _leaf("rgb", "shadow.kind") == 0


def test_null_is_explicitly_none():
    _install(look={"win": {"frame": None}, "dock": {"ring": None}, "home_bg": None})
    assert _leaf("rgb", "win.frame.c") == OFF and _leaf("rgb", "dock.ring.c") == OFF
    assert _leaf("rgb", "home_bg") == OFF


def test_paints_maps_and_texts():
    _install(look={"win": {"grad": [[0, "#4192fa"], [0.5, 12], [1, "#0846bb"]],
                           "fill": ["#ffffff", "#000000"]},
                   "sidebar": "vibrancy", "bar": {"app_title": "Calc"},
                   "tiles": {"play": "#3ca83a"},
                   "app_buttons": {"calc": {"tonal": {"ink": "#c4231b"}}}})
    leaves, paints, tiles, ab, texts, rc = moy_ui.theme_look("rgb", False)
    assert rc == OK
    g = leaves[LEAF["win.grad"]] - 0x2000000
    assert paints[g] == ("smooth", 0, ((0, RGB | 0x4192fa), (500, 12), (1000, RGB | 0x0846bb)))
    f = leaves[LEAF["win.fill"]] - 0x2000000
    assert paints[f][2] == ((0, RGB | 0xffffff), (1000, RGB))
    assert leaves[LEAF["sidebar"]] == 0x3000000
    assert texts[leaves[LEAF["bar.app_title"]] - 1] == "Calc"
    assert tiles == (("play", RGB | 0x3ca83a),)
    (app, kind, v), = ab
    assert (app, kind) == ("calc", "tonal") and v[BTN.index("ink")] == RGB | 0xc4231b


def test_custom_carries_the_look_and_rgb():
    rc, b = moy_ui.theme_custom("moy", None, None)
    assert rc == OK and len(b) <= 4096
    got = moy_ui.theme_resolve(b)
    assert got[0] == OK and got[1] == 0
    for light in (False, True):
        assert got[3 + light][0] == moy_ui.theme("moy", light)[0]
        assert got[8][light][:5] == moy_ui.theme_look("moy", light)[:5]
        assert got[9][light] == tuple(moy_ui.theme_backdrop("moy", light, s)
                                      for s in ("home", "desk"))
        assert got[9][light][1]


# -- the app ABI ----------------------------------------------------------------------

def test_a_run_grant_is_answered_the_nearest_index():
    from tools import moy_app_binding, moy_spine_binding
    sp, m = moy_spine_binding.binding(), moy_app_binding.binding()
    if sp is None or m is None:
        pytest.skip("no C compiler: no host moy_app")
    app = m.App(sp.Settings([].append))
    app.theme_write("moy", "light", {"panel": 70, "surface_light": True},
                    {"panel": 7, "surface_light": True})
    shipped = app.grant("calc", ("theme",))
    run = app.grant("kid", ("theme",), run=True)
    assert m.Theme(app, shipped).colors()["panel"] == 70
    assert m.Theme(app, run).colors()["panel"] == 7
    assert m.Theme(app, run).token(0) == 7
