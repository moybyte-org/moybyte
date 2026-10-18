"""The theme loader (native/moy_ui/moy_theme.c, docs/theming_2026-09.md §3-§5,
§7): format 1, read by C every image runs, from bytes a kid may have typed.

* Round trip: every seed in both variants, flattened, dumped as a package that
  sets every role and installed beside the seeds, flattens to the same row.
* Refusals and notes: what the reader refuses, and what it clamps, drops or
  ignores and reports.
* The chain: `inherits`, the depth cap and cycle refusal, over a copy of
  native/moy_ui whose seeds chain (built the way tests/test_ui_tables.py
  builds an edited style).
* Readability: the seeds clear the floor; a pair under it sets its bit.
* Metrics and text effects: the default sets every metric; a theme's are
  clamped, inherited and noted like its colours.
* Surfaces: the grammar (docs/theming_2026-09.md §4.4) compiles what it
  takes into the record the painter reads and refuses the rest; a package's
  surfaces are keyed by part and state, capped, inherited and noted.
* A fuzz: mutated seed bytes never crash the reader and never yield a row
  outside MOY64 or a flag outside 0/1; and the boards' module (the desktop
  MicroPython's `moy_ui`) answers the corpus exactly as the host's does.
"""

import ctypes
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import glass_binding
from tools import gen_ui_tables, moy_ui_binding

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from unix_mp import require_unix_mp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "native", "moy_ui")
with open(os.path.join(UI, "style.json")) as _f:
    STYLE = json.load(_f)
TH = STYLE["theme"]
ROLES = STYLE["tokens"] + TH["content"]   # the row: tokens, then content
FLAGS = {r for v in ("dark", "light", "both") for r, x in TH["derive"][v]
         if isinstance(x, bool)}
OK, BIG, SYNTAX, NEWER, NAME, TAKEN, CHAIN = range(7)
N_CLAMPED, N_UNKNOWN, N_DROPPED = 1, 2, 4
# The readability report: a bit per pair, the current plate's past them, then
# one per code role (theme.code, docs/look_2026-10.md step 23).
PAIRS = len(TH["pairs"])
CODE_BITS = ((1 << len(TH["code"]["roles"])) - 1) << (PAIRS + 1)
# The seeds that hold the code roles to theme.code's floor: the 2026-10
# look's. The older seeds keep their syntax colours, and their pixels, until
# step 27 retires them; the check reports theirs.
CODE_READ = ("moy", "blocks", "contrast")


def _src(name="t", **kw):
    d = {"format": 2, "name": name}
    d.update(kw)
    return json.dumps(d).encode()


def _row(name, light=False):
    row, bad, rc = moy_ui.theme(name, light)
    return list(row), bad, rc


RGB = 0x1000000


def _check_row(row):
    for role, v in zip(ROLES, row):
        if role in FLAGS:
            assert v in (0, 1), (role, v)
        else:
            assert 0 <= v <= 63 or RGB <= v <= RGB | 0xFFFFFF, (role, v)


def _json_colour(v):
    """A row's colour as a theme.json takes it."""
    return "#%06x" % (v & 0xFFFFFF) if v >= RGB else v


def test_the_seeds_are_style_jsons_and_the_default_comes_first():
    assert list(moy_ui.themes()) == TH["seeds"]
    assert moy_ui.themes()[0] == "moy"
    for name in TH["seeds"]:
        with open(os.path.join(UI, "themes", name + ".json"), "rb") as f:
            assert len(f.read()) <= TH["bytes"]


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("seed", TH["seeds"])
def test_a_flattened_seed_round_trips_as_a_package(seed, light):
    row, bad, rc = _row(seed, light)
    assert rc == OK and bad & ~CODE_BITS == 0
    _check_row(row)
    variant = "light" if light else "dark"
    roles = {r: (bool(v) if r in FLAGS else _json_colour(v)) for r, v in zip(ROLES, row)}
    rc, notes = moy_ui.theme_install(_src("dump", colors={variant: roles}))
    assert (rc, notes) == (OK, 0)
    assert _row("dump", light)[0] == row


def test_an_unknown_name_resolves_as_the_default():
    assert _row("no-such-theme") == _row("moy")


def test_a_recolour_inherits_the_rest():
    rc, _n = moy_ui.theme_install(_src("mine", inherits="blocks",
                                       colors={"dark": {"accent": 12}}))
    assert rc == OK and moy_ui.theme_installed() == "mine"
    mine, blocks = _row("mine")[0], _row("blocks")[0]
    i = ROLES.index("accent")
    assert mine[i] == 12
    # Blocks sets focus and ink_accent itself, so only accent moves (a role
    # derived from accent follows it only where the chain leaves it unset)
    assert [v for k, v in enumerate(mine) if k != i] \
        == [v for k, v in enumerate(blocks) if k != i]
    # no `inherits`: the default is the parent
    moy_ui.theme_install(_src("mine2", colors={"dark": {"accent": 12}}))
    moy = _row("moy")[0]
    assert _row("mine2")[0][ROLES.index("panel")] == moy[ROLES.index("panel")]


@pytest.mark.parametrize("src,rc", [
    (b"", SYNTAX),
    (b"[]", SYNTAX),
    (b'{"format": 2, "name": "t"', SYNTAX),
    (b'{"format": 2, "name": "t"} x', SYNTAX),
    (b'{"format": 2, "name": "t", "x": [[[[[[[[[[1]]]]]]]]]]}', SYNTAX),
    (b'{"format": 2, "name": "t\\q"}', SYNTAX),
    (b'{"name": "t"}', NEWER),
    (b'{"format": 3, "name": "t"}', NEWER),
    (b'{"format": 1, "name": "t"}', NEWER),     # a format-1 package reads as absent
    (b'{"format": "1", "name": "t"}', NEWER),
    (b'{"format": 2.0, "name": "t"}', NEWER),
    (b'{"format": 2}', NAME),
    (b'{"format": 2, "name": "Has Caps"}', NAME),
    (b'{"format": 2, "name": "' + b"x" * 25 + b'"}', NAME),
    (b'{"format": 2, "name": 7}', NAME),
    (b'{"format": 2, "name": "_t"}', NAME),
    (b'{"format": 2, "name": "-t"}', NAME),
    (b'{"format": 2, "name": "moy"}', TAKEN),
    (b'{"format": 2, "name": "t", "inherits": "nowhere"}', CHAIN),
    (b'{"format": 2, "name": "t", "inherits": "t"}', CHAIN),
    (b'{"format": 2, "name": "t", "inherits": 3}', CHAIN),
])
def test_what_the_reader_refuses(src, rc):
    assert moy_ui.theme_install(_src("kept"))[0] == OK
    assert moy_ui.theme_install(src)[0] == rc
    assert moy_ui.theme_installed() == "kept"


def test_a_name_is_the_carts_repositorys_theme_id():
    """1 to 24 of [a-z0-9_-], starting with a letter or a digit: what
    moybyte-org/carts' validator takes as a theme's id, so any theme the
    loader takes can be published."""
    for name in ("9", "a-b_c", "0" + "x" * 23):
        assert moy_ui.theme_install(_src(name))[0] == OK, name
        assert moy_ui.theme_installed() == name
    for name in ("_a", "-a", "a.b", "a b", "\u00e9", "x" * 25):
        assert moy_ui.theme_install(_src(name))[0] == NAME, name


def test_a_theme_over_the_byte_cap_is_refused():
    pad = b" " * TH["bytes"]
    assert moy_ui.theme_install(_src("t")[:-1] + pad + b"}")[0] == BIG


def test_what_the_reader_gets_past_and_notes():
    rc, notes = moy_ui.theme_install(_src("t", colors={"dark": {"accent": 99}}))
    assert (rc, notes) == (OK, N_CLAMPED)
    assert _row("t")[0][ROLES.index("accent")] == 63
    rc, notes = moy_ui.theme_install(_src("t", colors={"dark": {"glow": 3}},
                                          extra={"a": [1, {"b": None}]}))
    assert (rc, notes) == (OK, N_UNKNOWN)
    rc, notes = moy_ui.theme_install(_src("t", colors={
        "dark": {"accent": "red", "surface_light": 1, "panel": 2.5},
        "light": 4}))
    assert (rc, notes) == (OK, N_DROPPED)
    # every reserved key is accepted and not read
    rc, notes = moy_ui.theme_install(_src("t", **{k: {} for k in TH["reserved"]}))
    assert (rc, notes) == (OK, 0)


def test_readability_reports_and_never_refuses():
    rc, _n = moy_ui.theme_install(_src("t", colors={"dark": {"title": 7,
                                                              "title_ink": 7}}))
    assert rc == OK
    _row_, bad, rc = _row("t")
    pairs = [tuple(p) for p in TH["pairs"]]
    assert rc == OK
    assert bad & ~CODE_BITS == (1 << pairs.index(("title", "title_ink"))
                                | 1 << pairs.index(("title_active", "title_ink")))
    # a code role under theme.code's floor is reported too, its own bit
    code = TH["code"]["roles"]
    rc, _n = moy_ui.theme_install(_src("t", inherits="moy", colors={"light": {
        "code_comment": "#c0c0c0"}}))
    assert rc == OK
    assert _row("t", True)[1] == 1 << (PAIRS + 1 + code.index("code_comment"))
    assert _row("t", False)[1] == 0


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("seed", CODE_READ)
def test_the_looks_seeds_hold_the_code_roles_to_their_floor(seed, light):
    """Code syntax colours are theme roles, each 4.5:1 or better on the code
    surface (docs/look_2026-10.md, the decisions)."""
    assert TH["code"]["contrast"] == 45 and TH["code"]["bg"] == "surface"
    assert set(TH["code"]["roles"]) <= set(TH["content"])
    _row_, bad, rc = _row(seed, light)
    assert rc == OK and bad & CODE_BITS == 0


def test_every_block_category_and_syntax_class_has_a_role():
    from runtime import blocks
    content = set(TH["content"])
    for cat in blocks.categories():
        assert "block_" + cat in content, cat
    for cls in ("text", "keyword", "string", "number", "comment", "builtin"):
        assert "code_" + cls in content, cls


def test_metrics_and_text_effects():
    names = list(TH["metrics"])
    assert moy_ui.theme_shape("moy")[:2] == ((0,) * len(names), ())
    rc, notes = moy_ui.theme_install(_src("w", inherits="blocks",
        metrics={"radius": 9, "press_dx": 1, "glow": 2},
        text={"button": {"disabled": {"effect": "emboss", "color": "ink"},
                         "rest": {"effect": "shadow"}},
              "nope": {"rest": {"effect": "none"}},
              "row": {"hover": {"effect": "sparkle", "color": "ink"}}}))
    assert rc == OK and notes == N_CLAMPED | N_UNKNOWN | N_DROPPED
    mets, fx, _sf = moy_ui.theme_shape("w")
    want = dict.fromkeys(names, 0)
    want.update(radius=3, press_dx=1)
    assert mets == tuple(want[n] for n in names)
    assert fx == (("button", "disabled", "emboss", "ink"),)
    # an effect of `none` is no effect: nothing is listed
    rc, _n = moy_ui.theme_install(_src("w2", text={"chip": {"on": {"effect": "none"}}}))
    assert rc == OK and moy_ui.theme_shape("w2")[1] == ()


# -- surfaces ---------------------------------------------------------------------

STYLE_TERM = -128


def _tok(name):
    return -1 - STYLE["tokens"].index(name)


def _own(name):
    """Theme `name`'s surfaces less those it inherits unchanged from the
    default seed, whose surfaces every chain ends at."""
    base = set(moy_ui.theme_shape("moy")[2])
    return tuple(r for r in moy_ui.theme_shape(name)[2] if r not in base)


@pytest.mark.parametrize("text,rec,notes", [
    ("flat", (0, 0, 0, STYLE_TERM, STYLE_TERM, STYLE_TERM), 0),
    ("raised, fill surface, border", (1, 1, 1, _tok("surface"), STYLE_TERM, STYLE_TERM), 0),
    ("pinstripe shade_lo/shade_hi, border",
     (0, 3, 1, _tok("shade_lo"), _tok("shade_hi"), STYLE_TERM), 0),
    ("dither desktop/23", (0, 2, 0, _tok("desktop"), 23, STYLE_TERM), 0),
    ("gradient 1/12, shadow, bevel2", (4, 4, 2, 1, 12, STYLE_TERM), 0),
    ("border edge", (0, 0, 1, STYLE_TERM, STYLE_TERM, _tok("edge")), 0),
    ("etched,border 0,shadow", (3, 0, 3, STYLE_TERM, STYLE_TERM, 0), 0),
    ("  sunken ,\tfill 7  ", (2, 1, 0, 7, STYLE_TERM, STYLE_TERM), 0),
    ("fill 99", (0, 1, 0, 63, STYLE_TERM, STYLE_TERM), N_CLAMPED),
])
def test_the_surface_grammar_compiles_what_it_takes(text, rec, notes):
    assert moy_ui.surface(text) == (rec, notes)


@pytest.mark.parametrize("text", [
    "", " ", ",", "raised,", ",raised", "raised,,border", "raised raised",
    "raised, sunken", "fill 1, fill 2", "border, border", "shadow, shadow",
    "fill", "fill nope", "fill surface_light", "fill code_text", "fill 1/2",
    "fill -1", "fill 1234", "fill 1 2", "dither 1", "dither 1/", "dither /2",
    "dither 1 / 2", "gradient a/b", "border 1 2", "shadow 1", "Raised", "raised;",
    "bevel", "fill  ", "raised " + "x" * 60, "fill " + "panel," * 8,
])
def test_the_surface_grammar_refuses_the_rest(text):
    assert moy_ui.surface(text) is None


def test_the_surface_compiler_survives_any_words():
    rng = random.Random(4)
    words = (STYLE["theme"]["edges"] + STYLE["theme"]["fills"]
             + ["border", "shadow", "panel", "ink", "7", "63", "64", "/", ",", " ", "a/b",
                "shade_hi/3", "surface_light", "", "x"])
    tokens = len(STYLE["tokens"])
    for _i in range(4000):
        text = " ".join(rng.choice(words) for _w in range(rng.randint(0, 7)))
        got = moy_ui.surface(text)
        if got is None:
            continue
        (edge, fill, flags, a, b, line), notes = got
        assert edge < len(STYLE["theme"]["edges"]) and fill <= len(STYLE["theme"]["fills"])
        assert flags < 4 and notes in (0, N_CLAMPED)
        for t in (a, b, line):
            assert t == STYLE_TERM or 0 <= t <= 63 or -tokens <= t <= -1, (text, t)


def test_a_packages_surfaces_are_keyed_capped_inherited_and_noted():
    sf = {"button": {"rest": "raised, border", "pressed": "sunken",
                     "glow": "flat", "hot": "sparkly", "on": 3},
          "window.title": {"active": "pinstripe shade_lo/shade_hi"},
          "nope": {"rest": "flat"}}
    rc, notes = moy_ui.theme_install(_src("s1", surfaces=sf))
    assert rc == OK and notes == N_UNKNOWN | N_DROPPED
    got = _own("s1")
    assert got == (
        ("button", "pressed", (2, 0, 0, STYLE_TERM, STYLE_TERM, STYLE_TERM)),
        ("button", "rest", (1, 0, 1, STYLE_TERM, STYLE_TERM, STYLE_TERM)),
        ("window.title", "active",
         (0, 3, 0, _tok("shade_lo"), _tok("shade_hi"), STYLE_TERM)))
    # the cap: a package holds at most surfaces_cap
    many = {k: {"rest": "raised", "hover": "sunken"} for k in moy_ui.parts()}
    assert 2 * len(many) > TH["surfaces_cap"]
    rc, notes = moy_ui.theme_install(_src("s4", surfaces=many))
    assert rc == OK and notes == N_DROPPED
    assert len(_own("s4")) == TH["surfaces_cap"]
    # a later key for the same part and state replaces the earlier
    rc, notes = moy_ui.theme_install(
        b'{"format": 2, "name": "s5", "surfaces": {"chip": {"rest": "raised"},'
        b' "chip": {"rest": "sunken"}}}')
    assert (rc, notes) == (OK, 0)
    assert _own("s5") == (
        ("chip", "rest", (2, 0, 0, STYLE_TERM, STYLE_TERM, STYLE_TERM)),)


def test_the_part_list_is_the_kinds_then_the_chrome_and_the_doc_lists_it():
    kinds = list(STYLE["kinds"]) + [k for k in STYLE["metrics"] if k not in STYLE["kinds"]]
    assert list(moy_ui.parts()) == kinds + TH["parts"]
    assert list(moy_ui.theme_states()) == STYLE["states"] + TH["states"]
    with open(os.path.join(ROOT, "docs", "theming_2026-09.md")) as f:
        doc = f.read()
    sec = doc[doc.index("### 4.3 Parts"):doc.index("### 4.4")]
    listed = set(re.findall(r"`([a-z_.]+)`", sec))
    missing = [p for p in moy_ui.parts() if p not in listed]
    assert not missing, "docs/theming_2026-09.md §4.3 does not list %s" % missing


# -- the chain, over a copy whose seeds chain -----------------------------------

def _chained_lib(tmp_path):
    d = tmp_path / "ui"
    shutil.copytree(UI, d)
    style = json.loads((d / "style.json").read_text())
    # The chain's own surfaces only: a default seed with none of its own.
    night = json.loads((d / "themes" / "moy.json").read_text())
    night.pop("surfaces", None)
    (d / "themes" / "moy.json").write_text(json.dumps(night))
    links = {"c1": "moy", "c2": "c1", "c3": "c2", "c4": "c3", "c5": "c4",
             "loop_a": "loop_b", "loop_b": "loop_a"}
    surfaces = {"c1": {"button": {"rest": "raised", "pressed": "sunken"},
                       "chip": {"rest": "raised"}},
                "c2": {"button": {"rest": "etched"}}}
    for name, parent in links.items():
        (d / "themes" / (name + ".json")).write_text(json.dumps(
            {"format": 2, "name": name, "inherits": parent,
             "colors": {"dark": {"dim": int(name[-1]) if name[-1].isdigit() else 0}},
             "surfaces": surfaces.get(name, {})}))
    style["theme"]["seeds"] += list(links)
    (d / "style.json").write_text(json.dumps(style))
    h, c = gen_ui_tables.generate(gen_ui_tables.load(str(d)), str(d))
    (d / "moy_ui_tables.h").write_text(h)
    (d / "moy_ui_tables.c").write_text(c)
    lib = ctypes.CDLL(glass_binding.build(ui_dir=str(d), cache=str(tmp_path / "cache")))
    lib.moy_theme_resolve.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int,
                                      ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]

    def resolve(name):
        row = (ctypes.c_int32 * len(ROLES))()
        bad = ctypes.c_uint32()
        rc = lib.moy_theme_resolve(name.encode(), len(name), 0, row, ctypes.byref(bad))
        return rc, list(row)

    def shape(name):
        sh = Shape()
        lib.moy_theme_shape(name.encode(), len(name), ctypes.byref(sh))
        parts, states = moy_ui.parts(), moy_ui.theme_states()
        return {(parts[p], states[st]): sh.surf[sh.stid[p][st] - 1].edge
                for p in range(len(parts)) for st in range(len(states))
                if sh.stid[p][st]}, sh.n_surf
    Shape = moy_ui_binding._shape_type(len(TH["metrics"]), len(moy_ui.parts()),
                                       len(moy_ui.theme_states()), 64)
    resolve.shape = shape
    return resolve


def test_surfaces_inherit_nearest_first(tmp_path):
    shape = _chained_lib(tmp_path).shape
    edges = TH["edges"]
    got, n = shape("c3")                # c3 -> c2 -> c1
    assert {k: edges[v] for k, v in got.items()} == {
        ("button", "rest"): "etched", ("button", "pressed"): "sunken",
        ("chip", "rest"): "raised"}
    assert n == 3
    got, n = shape("c1")
    assert n == 2                       # chip and button share raised's record


def test_the_chain_is_capped_and_a_cycle_is_refused(tmp_path):
    resolve = _chained_lib(tmp_path)
    dim = ROLES.index("dim")
    night = resolve("moy")[1]
    rc, row = resolve("c4")            # c4 -> c3 -> c2 -> c1 -> night: depth 4
    assert rc == OK and row[dim] == 4
    for name in ("c5", "loop_a"):      # one too deep; a cycle
        rc, row = resolve(name)
        assert rc == CHAIN and row == night


# -- the layout (docs/shell_layout_2026-10.md §5, §9) -----------------------------

LAY = TH["layout"]
# The default seed's (Moy's) bar.
TODAY = ("top", 18, ("menu", "chips", "zone"), (), ("close", "wifi", "battery", "clock"), ())


def _bar(edge="top", **kw):
    b = {"edge": edge}
    b.update(kw)
    return b


def _lay(name, bars, **kw):
    rc, notes = moy_ui.theme_install(_src(name, layout={"bars": bars}, **kw))
    assert rc == OK
    return notes, moy_ui.theme_layout(name)


def test_the_home_rides_the_layout_carousel_by_default():
    # `home` names the home's arrangement (docs/shell_layout_2026-10.md
    # §12): every seed's is the carousel; a package sets it with its bars;
    # an unknown name is noted and the default stands; a non-string dropped.
    assert LAY["homes"][0] == "carousel"
    for name in TH["seeds"]:
        assert moy_ui.theme_home(name) == "carousel", name
    req = ["menu", "zone", "close"]
    for home, want, note in (("grid", "grid", 0), ("carousel", "carousel", 0),
                             ("icons", "icons", 0),
                             ("tiles", "carousel", N_UNKNOWN),
                             (7, "carousel", N_DROPPED)):
        rc, notes = moy_ui.theme_install(_src("hm", layout={"bars": [{"edge": "top", "left": req}],
                                                            "home": home}))
        assert rc == OK and (notes, moy_ui.theme_home("hm")) == (note, want), home


def test_the_default_seed_sets_todays_bar_and_every_seed_inherits_it():
    """...but Moy, Blocks and Contrast, whose looks carry the 2026-10 bar
    (docs/look_2026-10.md steps 14, 26b and 26c): the Moy button first,
    the status at the right."""
    for name in TH["seeds"]:
        if name not in ("moy", "blocks", "contrast"):
            assert moy_ui.theme_layout(name) == (False, (TODAY,)), name
    for name in ("moy", "blocks", "contrast"):
        assert moy_ui.theme_layout(name) == (False, (
            ("top", 18, ("menu", "chips", "zone"), (), ("close", "wifi", "battery", "clock"),
             ()),)), name
    assert "layout" not in TH["reserved"]
    assert set(LAY["required"]) <= set(LAY["widgets"])
    from runtime import shell_layout
    assert shell_layout.WIDGETS == tuple(LAY["widgets"])
    assert shell_layout.EDGES == tuple(LAY["edges"])
    assert shell_layout.REQUIRED == tuple(LAY["required"])


def test_a_layout_is_read_whole_and_inherited_whole():
    notes, got = _lay("dock", [_bar("bottom", size=22, left=["menu", "separator", "chips", "zone"],
                                    right=["close", "wifi", "battery", "clock"])])
    assert notes == 0
    assert got == (False, (("bottom", 22, ("menu", "separator", "chips", "zone"), (),
                            ("close", "wifi", "battery", "clock"), ()),))
    # A child that sets no layout takes its parent's, and one that sets bars
    # replaces the list whole: a bar list is never merged by position.
    assert moy_ui.theme_install(_src("dock2", inherits="contrast"))[0] == OK
    assert moy_ui.theme_layout("dock2") == (False, (TODAY,))
    notes, got = _lay("two", [_bar("top", left=["title"], right=["clock", "close", "menu"]),
                              _bar("bottom", left=["zone"])])
    assert got[1][1] == ("bottom", 18, ("zone",), (), (), ())


def test_the_caps_and_clamps_drop_and_note():
    # size: absent 18, clamped to 16..32 (every widget is compact), a
    # non-integer dropped
    for size, want, note in ((0, 16, N_CLAMPED), (10000, 32, N_CLAMPED), (40, 32, N_CLAMPED),
                             (24, 24, 0),
                             ("big", 18, N_DROPPED), (2.5, 18, N_DROPPED)):
        notes, got = _lay("t", [_bar(size=size, left=["menu", "zone", "close"])])
        assert (notes, got[1][0][1]) == (note, want), size
    # two bars on one edge, an unknown edge and a bar that is not an object
    notes, got = _lay("t", [_bar(left=["menu", "zone", "close"]), _bar(left=["clock"]),
                            _bar("middle"), "bar", {"left": ["wifi"]}])
    assert notes == N_DROPPED and [b[0] for b in got[1]] == ["top"]
    # five bars: one per edge at most
    notes, got = _lay("t", [_bar(e, left=["menu", "zone", "close"] if e == "top" else [])
                            for e in ("top", "bottom", "left", "right", "top")])
    assert notes == N_DROPPED and [b[0] for b in got[1]] == ["top", "bottom", "left", "right"]
    # an unknown widget, a slot over its cap, a widget placed twice
    notes, got = _lay("t", [_bar(left=["menu", "zone", "close", "teapot"])])
    assert notes == N_UNKNOWN and got[1][0][2] == ("menu", "zone", "close")
    notes, got = _lay("t", [_bar(left=["menu", "zone", "close", "spacer", "spacer",
                                       "spacer", "spacer"])])
    assert notes == N_DROPPED and len(got[1][0][2]) == LAY["slot_cap"]
    notes, got = _lay("t", [_bar(left=["menu", "zone"], right=["close", "menu", "spacer",
                                                               "separator", "spacer"])])
    assert notes == N_DROPPED and got[1][0][4] == ("close", "spacer", "separator", "spacer")
    # at most widget_cap per layout, in placement order
    full = ["menu", "zone", "close", "spacer", "spacer", "spacer"]
    notes, got = _lay("t", [_bar("top", left=full, right=["spacer"] * 6),
                            _bar("bottom", left=["clock", "wifi"])])
    assert notes == N_DROPPED and sum(len(s) for b in got[1] for s in b[2:5]) == LAY["widget_cap"]
    assert got[1][1][2] == ()
    # slots and bars of the wrong type
    notes, got = _lay("t", [_bar(left="menu", right=["menu", "zone", "close", 7])])
    assert notes == N_DROPPED and got[1][0][2:5] == ((), (), ("menu", "zone", "close"))
    rc, notes = moy_ui.theme_install(_src("t", layout={"bars": "top"}))
    assert (rc, notes) == (OK, N_DROPPED) and moy_ui.theme_layout("t") == (False, (TODAY,))
    rc, notes = moy_ui.theme_install(_src("t", layout=[1], extra=1))
    assert (rc, notes) == (OK, N_DROPPED | N_UNKNOWN)
    rc, notes = moy_ui.theme_install(_src("t", layout={"windows": "max"}))
    assert (rc, notes) == (OK, N_UNKNOWN) and moy_ui.theme_layout("t") == (False, (TODAY,))


def test_a_text_widget_takes_its_string_by_position():
    req = ["menu", "zone", "close"]
    notes, got = _lay("t", [_bar(left=req + ["text"], right=["text"], text=["hi", "there"])])
    assert notes == 0 and got[1][0][2:] == (tuple(req) + ("text",), (), ("text",), ("hi", "there"))
    # 200 characters are cut to text_max; an empty or non-printable string
    # drops its widget, a string with no widget is dropped
    notes, got = _lay("t", [_bar(left=req + ["text"], text=["x" * 200])])
    assert notes == N_CLAMPED and got[1][0][5] == ("x" * LAY["text_max"],)
    notes, got = _lay("t", [_bar(left=req + ["text", "text"], text=["", "ok", "spare"])])
    assert notes == N_DROPPED and got[1][0][2] == tuple(req) + ("text",)
    assert got[1][0][5] == ("ok",)
    notes, got = _lay("t", [_bar(left=req + ["text"], text=["tab\there"])])
    assert notes == N_DROPPED and got[1][0][5] == ()
    notes, got = _lay("t", [_bar(left=req + ["text"])])
    assert notes == N_DROPPED and got[1][0][2] == tuple(req)


def test_the_required_set_rule_runs_on_the_data():
    for missing in LAY["required"]:
        left = [w for w in LAY["required"] if w != missing]
        notes, got = _lay("t", [_bar(left=left + ["clock"])])
        assert notes == N_DROPPED and got == (True, (TODAY,)), missing
    notes, got = _lay("t", [])
    assert notes == N_DROPPED and got == (True, (TODAY,))
    # a widget dropped by a cap counts as never placed
    notes, got = _lay("t", [_bar(left=["spacer"] * 6, center=["spacer"] * 6,
                                 right=["menu", "zone", "close"])])
    assert got == (True, (TODAY,))
    # a `zone` in a vertical bar passes the loader: the composer drops it
    notes, got = _lay("t", [_bar("top", left=["menu", "close"]), _bar("left", left=["zone"])])
    assert notes == 0 and got[0] is False


def test_a_refused_layout_in_the_chain_stands_in_with_the_default():
    rc, _n = moy_ui.theme_install(_src("t", inherits="blocks",
                                       layout={"bars": [_bar(left=["clock"])]}))
    assert rc == OK and moy_ui.theme_layout("t") == (True, (TODAY,))


LOOK_SAMPLE = {
    "render": {"text": "crisp", "shapes": "stepped", "scale": 2},
    "focus": {"style": "invert", "card": "ring", "small": "plate", "color": "value",
              "ink": "accent", "ring": {"w": 3.5, "gap": 0, "alpha": 0.6}, "outline": [2, "#000000"],
              "plate": {"alpha": 0.3}, "lift": {"grow": 99, "rim": ["#ffffff", 0.6]},
              "edge": {"w": 2}, "underline": {"w": 1}},
    "win_r": 8, "key_r": 99,
    "win_corners": [True, True, False, False], "shadow": ["hard", "#000000", 2, 2],
    "edges": "bevel", "home_bg": "#ffd23f", "dim_neighbours": 0.3,
    "on_backdrop": {"ink": "#ffffff", "shadow": ["#00213f", 0.9, 1, 1]},
    "win": {"header": "grad", "h": 40, "align": "center", "controls": "xp",
            "grad": [[0, "#4192fa"], [0.35, "#0b5fe3"], [1, "#0846bb"]],
            "grad_back": {"stops": ["#b0c6f2", "#7a96df"], "mode": "steps:4"},
            "frame": [4, "#0846bb"], "icon": False, "tshadow": ["#0a1f6d", 0.85, 1, 1]},
    "current": {"style": "fill", "color": "teapot", "alpha": 0.2}, "sel_back": "#d6d2c2",
    "icons": {"kind": "pixel", "set": "system_icons", "swap": {"3": 7}},
    "bar": {"edge": "bottom", "style": "grad", "sysbtn": "start", "chips": "button",
            "app_title": "Calc", "sys": ["#23274b", "#b6a8f7", "#23274b"],
            "tray": ["#1aa0f0", "#0e78d6"], "clock": "center", "alpha": 0.85},
    "hhbar": {"style": "glass", "h": 22, "line": [1, "#e2e5ec"]},
    "tabs": {"style": "pill", "pill": "#1b4ab0"},
    "launcher": "dock", "dock": {"frost": False, "ring": None, "r": 22},
    "tiles": {"play": "#3ca83a", "calc": 13, "bad name": "#000000"},
    "sidebar": "vibrancy", "zebra": "#f4f4f7", "colhead": {"fill": "#ebeadb"},
    "buttons": {"normal": {"fill": {"stops": [[0, "#ffffff"], [1, "#d9d5c7"]], "mode": "dither"},
                           "border": [1, "#003c74"], "shape": "circle"},
                "tonal": {"ink": "#c4231b", "font": "op", "bevel": True}},
    "app_buttons": {"calc": {"normal": {"ink": "#1a3fcf"}, "primary": {"shape": "circle"}}},
    "lcd_bevel": "in", "keycap": {"alpha": 0.28, "r": 5},
    "backdrop": {"desk": [{"k": "circle", "at": [1, 2, 3], "p": "#191c3e"},
                          {"k": "band", "at": [0, 720.5], "p": ["#1b5ad3", 4, "#86b9ef"]},
                          {"k": "pie", "at": [996, 433, 233, 180, 270], "p": "#7cc8ff"},
                          {"k": "rrect", "at": [586, 73, 234, 150, 75], "p": 12, "a": 0.4}],
                 "home": [{"k": "fill", "p": [[0, "#1d2b53"], [0.6, "#7e2553"]],
                           "mode": "dither"},
                          {"k": "wave", "at": [[0, 600], [560, 610], [1000, 580]],
                           "p": ["#8fd04e", "#5aa12a"], "mode": "steps:4"}]},
    "dark": {"shadow": ["soft", "#000000", 0.55, 14, 6], "win": {"header": "fill"},
             "backdrop": {"desk": [{"k": "ellipse", "at": [270, 170, 120, 48],
                                    "p": "#ffffff", "a": 0.85}]}},
    "light": {"bar": {"fill": "#ffffff"}, "teapot": 1},
}


def _corpus(n):
    rng = random.Random(224)
    seeds = []
    for name in TH["seeds"]:
        with open(os.path.join(UI, "themes", name + ".json"), "rb") as f:
            src = f.read()
        seeds.append(src.replace(b'"name": "%s"' % name.encode(), b'"name": "fz"'))
    # ...and the deleted seeds' fixture packages (tests/fixtures/themes/)
    for d in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "fixtures", "themes",
                                           "*.moy"))):
        pkg = json.loads(open(os.path.join(d, "theme.json"), "rb").read())
        pkg["name"] = "fz"
        seeds.append(json.dumps(pkg, indent=1).encode())
    seeds.append(_src("fz", inherits="contrast", metrics={"radius": 2, "press_dy": 1},
                      text={"button": {"disabled": {"effect": "emboss", "color": "ink"},
                                       "pressed": {"effect": "shadow", "color": "dim"}},
                            "tab": {"on": {"effect": "shadow", "color": "code_text"}}},
                      colors={"dark": {"block_sound": 3, "accent": 9}},
                      fonts={"ui": "ui_draw.fnt", "mono": "mono-8.fnt"},
                      sprites="parts.moygfx", icons="bar-icons.moygfx",
                      surfaces={"button": {"rest": "bevel2, fill surface, shadow",
                                           "pressed": "sunken, dither shade_lo/7"},
                                "window.title": {"active": "pinstripe shade_lo/shade_hi"}}))
    # The look (docs/look_2026-10.md step 5): every field type, paints, maps,
    # both variants' objects.
    seeds.append(_src("fz", colors={"dark": {"panel": "#102030", "accent": "#ffc23d"},
                                    "light": {"surface": "#FFFFFF", "ink": 0}},
                      look=LOOK_SAMPLE))
    # The layout cases (docs/shell_layout_2026-10.md §5): a string for bars,
    # five bars, two on one edge, a size of 0 and of 10000, an unknown
    # widget, a 200-character text, no menu, `zone` in a vertical bar only.
    req = ["menu", "zone", "close"]
    for bars in ("top",
                 [{"edge": e, "left": req} for e in ("top", "bottom", "left", "right", "top")],
                 [{"edge": "top", "left": req}, {"edge": "top", "right": ["clock"]}],
                 [{"edge": "bottom", "size": 0, "left": req, "right": ["wifi", "battery"]}],
                 [{"edge": "right", "size": 10000, "left": ["clock"] + req}],
                 [{"edge": "top", "left": req + ["teapot", "text"], "text": ["x" * 200]}],
                 [{"edge": "top", "left": ["zone", "close"], "center": ["clock"]}],
                 [{"edge": "top", "left": ["menu", "close"]}, {"edge": "left", "left": ["zone"]}],
                 [{"edge": "bottom", "size": 22,
                   "left": ["menu", "separator", "chips", "zone", "text", "spacer"],
                   "right": ["close", "wifi", "battery", "clock"], "text": ["moy", "!"]}]):
        seeds.append(_src("fz", inherits="blocks", layout={"bars": bars}))
    alphabet = b'{}[]":,0123456789-.eE truefalsnl\\/ '
    out = list(seeds)
    while len(out) < n:
        b = bytearray(rng.choice(seeds))
        for _m in range(rng.randint(1, 4)):
            op = rng.randrange(4)
            i = rng.randrange(len(b) + 1)
            if op == 0 and b:
                del b[min(i, len(b) - 1)]
            elif op == 1:
                b[i:i] = bytes([rng.choice(alphabet)])
            elif op == 2 and b:
                b[min(i, len(b) - 1)] = rng.randrange(256)
            else:
                b = b[:i]
        out.append(bytes(b))
    return out


def _answer(mod, src):
    rc, notes = mod.theme_install(src)
    if rc != 0:
        return rc, notes, None, None
    n = mod.theme_installed()
    return rc, notes, mod.theme(n, False), \
        (mod.theme_shape(n), mod.theme_fonts(n), mod.theme_layout(n), mod.theme_sheets(n),
         mod.theme_backdrop(n, False, "desk"), mod.theme_backdrop(n, True, "home"))


def test_mutated_themes_never_break_the_reader():
    for src in _corpus(3000):
        rc, notes, row, _shape = _answer(moy_ui, src)
        assert 0 <= rc <= 7 and 0 <= notes <= 7
        if rc == OK:
            assert row[2] == OK
            _check_row(row[0])


def _installed_answer(name):
    """What the install slot answers for `name`, in theme_resolve's shape."""
    fonts = moy_ui.theme_fonts(name)
    return (moy_ui.theme(name, False), moy_ui.theme(name, True),
            moy_ui.theme_layout(name) + (moy_ui.theme_home(name),), fonts)


def test_a_package_resolved_from_its_bytes_is_its_row_after_install():
    """`theme_resolve` (phase 6 step 2) answers what an install would, and
    leaves the slot as it was; a package naming `inherits` is refused, as a
    stored package is standalone, and so is a seed's name."""
    assert moy_ui.theme_install(_src("kept"))[0] == OK
    seen = 0
    for src in _corpus(600):
        got = moy_ui.theme_resolve(src)
        assert moy_ui.theme_installed() == "kept"
        parsed = moy_ui.theme_install(src)
        if got[0] != OK:
            assert got[2:] == (None,) * 8
            assert got[0] == parsed[0] or (parsed[0] == OK and got[0] == CHAIN
                                           and b"inherits" in src)
        else:
            seen += 1
            assert parsed == (OK, got[1])
            dark, light, layout, fonts = _installed_answer(got[2])
            assert (got[3], got[4], got[6]) == (dark, light, layout)
            assert set(got[5]) <= set(fonts)
            assert got[9] == tuple(tuple(moy_ui.theme_backdrop(got[2], lt, s)
                                         for s in ("home", "desk")) for lt in (False, True))
        moy_ui.theme_install(_src("kept"))
    assert seen > 40
    assert moy_ui.theme_resolve(_src("t", inherits="blocks"))[0] == CHAIN
    assert moy_ui.theme_resolve(_src("moy"))[0] == TAKEN
    assert moy_ui.theme_installed() == "kept"


_MP_DRIVER = """
import moy_ui
f = open(@CORPUS@, "rb")
data = f.read()
f.close()
i = 0
while i < len(data):
    n = data[i] | data[i + 1] << 8
    src = data[i + 2:i + 2 + n]
    i += 2 + n
    got = moy_ui.theme_resolve(src)
    rc, notes = moy_ui.theme_install(src)
    if rc == 0:
        n = moy_ui.theme_installed()
        print(repr((rc, notes, moy_ui.theme(n, False),
                    (moy_ui.theme_shape(n), moy_ui.theme_fonts(n), moy_ui.theme_layout(n),
                     moy_ui.theme_sheets(n), moy_ui.theme_backdrop(n, False, "desk"),
                     moy_ui.theme_backdrop(n, True, "home")))))
    else:
        print(repr((rc, notes, None, None)))
    print(repr(got))
"""


def test_the_boards_module_answers_as_the_host_does(tmp_path):
    exe = require_unix_mp(
        "moy_ui",
        why="Without it the loader's MicroPython face, the one every board "
            "imports, never meets an untrusted theme on the host.")
    corpus = _corpus(400)
    blob = b"".join(len(s).to_bytes(2, "little") + s for s in corpus)
    (tmp_path / "corpus.bin").write_bytes(blob)
    script = tmp_path / "driver.py"
    script.write_text(_MP_DRIVER.replace("@CORPUS@", repr(str(tmp_path / "corpus.bin"))))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=120)
    lines = out.stdout.splitlines()
    assert len(lines) == 2 * len(corpus), out.stdout[-500:] + out.stderr
    for i, src in enumerate(corpus):
        got = moy_ui.theme_resolve(src)
        rc, notes, row, shape = _answer(moy_ui, src)
        want = (rc, notes, None if row is None else (tuple(row[0]), row[1], row[2]), shape)
        assert repr(want) == lines[2 * i], src
        assert repr(got) == lines[2 * i + 1], src


# -- the readability check of a pattern fill (§7) ---------------------------------

def test_every_seeds_pattern_fills_read_in_both_variants():
    from runtime import ui_draw
    from runtime.chrome import THEME_VARIANTS, theme_colors
    try:
        for name in moy_ui.themes():
            ui_draw.use_shape(name)
            for variant in THEME_VARIANTS:
                assert ui_draw.pattern_unreadable(theme_colors(name, variant)) == [], (
                    name, variant)
    finally:
        ui_draw.use_shape(None)


def test_a_pattern_fill_is_checked_against_both_colours():
    """A dither of black and white under a black label misses on the black
    half, and so does its pinstripe; a solid fill is the pairs' business; a
    part that paints no label is not checked."""
    from runtime import ui_draw
    from runtime.chrome import theme_colors
    rc, _n = moy_ui.theme_install(_src("dz", surfaces={
        "button": {"rest": "dither 0/7", "pressed": "pinstripe 7/0",
                   "hover": "dither 7/6"},
        "chip": {"rest": "fill 0"},
        "desk.icon": {"rest": "dither 0/7"}}))
    assert rc == OK
    th = dict(theme_colors("moy", "light"))
    th["ink"] = 0                      # the button's label ink at rest
    try:
        ui_draw.use_shape("dz")
        bad = ui_draw.pattern_unreadable(th)
    finally:
        ui_draw.use_shape(None)
    assert ("button", "rest") in bad and ("button", "pressed") in bad
    assert ("button", "hover") not in bad
    assert not [b for b in bad if b[0] in ("chip", "desk.icon")]
