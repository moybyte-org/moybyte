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


def _src(name="t", **kw):
    d = {"format": 1, "name": name}
    d.update(kw)
    return json.dumps(d).encode()


def _row(name, light=False):
    row, bad, rc = moy_ui.theme(name, light)
    return list(row), bad, rc


def _check_row(row):
    for role, v in zip(ROLES, row):
        if role in FLAGS:
            assert v in (0, 1), (role, v)
        else:
            assert 0 <= v <= 63, (role, v)


def test_the_seeds_are_style_jsons_and_the_default_comes_first():
    assert list(moy_ui.themes()) == TH["seeds"]
    assert moy_ui.themes()[0] == "night"
    for name in TH["seeds"]:
        with open(os.path.join(UI, "themes", name + ".json"), "rb") as f:
            assert len(f.read()) <= TH["bytes"]


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("seed", TH["seeds"])
def test_a_flattened_seed_round_trips_as_a_package(seed, light):
    row, bad, rc = _row(seed, light)
    assert rc == OK and bad == 0
    _check_row(row)
    variant = "light" if light else "dark"
    roles = {r: (bool(v) if r in FLAGS else v) for r, v in zip(ROLES, row)}
    rc, notes = moy_ui.theme_install(_src("dump", colors={variant: roles}))
    assert (rc, notes) == (OK, 0)
    assert _row("dump", light)[0] == row


def test_an_unknown_name_resolves_as_the_default():
    assert _row("no-such-theme") == _row("night")


def test_a_recolour_inherits_the_rest():
    rc, _n = moy_ui.theme_install(_src("mine", inherits="berry",
                                       colors={"dark": {"accent": 12}}))
    assert rc == OK and moy_ui.theme_installed() == "mine"
    mine, berry = _row("mine")[0], _row("berry")[0]
    i = ROLES.index("accent")
    assert mine[i] == 12
    # focus and ink_accent derive from accent, so they follow the recolour
    follow = ("accent", "focus", "ink_accent")
    assert mine[ROLES.index("focus")] == mine[ROLES.index("ink_accent")] == 12
    assert [v for k, v in enumerate(mine) if ROLES[k] not in follow] \
        == [v for k, v in enumerate(berry) if ROLES[k] not in follow]
    # no `inherits`: the default is the parent
    moy_ui.theme_install(_src("mine2", colors={"dark": {"accent": 12}}))
    night = _row("night")[0]
    assert _row("mine2")[0][ROLES.index("panel")] == night[ROLES.index("panel")]


@pytest.mark.parametrize("src,rc", [
    (b"", SYNTAX),
    (b"[]", SYNTAX),
    (b'{"format": 1, "name": "t"', SYNTAX),
    (b'{"format": 1, "name": "t"} x', SYNTAX),
    (b'{"format": 1, "name": "t", "x": [[[[[[[[[[1]]]]]]]]]]}', SYNTAX),
    (b'{"format": 1, "name": "t\\q"}', SYNTAX),
    (b'{"name": "t"}', NEWER),
    (b'{"format": 2, "name": "t"}', NEWER),
    (b'{"format": "1", "name": "t"}', NEWER),
    (b'{"format": 1.0, "name": "t"}', NEWER),
    (b'{"format": 1}', NAME),
    (b'{"format": 1, "name": "Has Caps"}', NAME),
    (b'{"format": 1, "name": "' + b"x" * 25 + b'"}', NAME),
    (b'{"format": 1, "name": 7}', NAME),
    (b'{"format": 1, "name": "night"}', TAKEN),
    (b'{"format": 1, "name": "t", "inherits": "nowhere"}', CHAIN),
    (b'{"format": 1, "name": "t", "inherits": "t"}', CHAIN),
    (b'{"format": 1, "name": "t", "inherits": 3}', CHAIN),
])
def test_what_the_reader_refuses(src, rc):
    assert moy_ui.theme_install(_src("kept"))[0] == OK
    assert moy_ui.theme_install(src)[0] == rc
    assert moy_ui.theme_installed() == "kept"


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
    assert bad == (1 << pairs.index(("title", "title_ink"))
                   | 1 << pairs.index(("title_active", "title_ink")))


def test_every_block_category_and_syntax_class_has_a_role():
    from runtime import blocks
    content = set(TH["content"])
    for cat in blocks.categories():
        assert "block_" + cat in content, cat
    for cls in ("text", "keyword", "string", "number", "comment", "builtin"):
        assert "code_" + cls in content, cls


def test_metrics_and_text_effects():
    names = list(TH["metrics"])
    assert moy_ui.theme_shape("night") == ((0,) * len(names), (), ())
    rc, notes = moy_ui.theme_install(_src("w", inherits="berry",
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
    _m, _fx, got = moy_ui.theme_shape("s1")
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
    assert len(moy_ui.theme_shape("s4")[2]) == TH["surfaces_cap"]
    # a later key for the same part and state replaces the earlier
    rc, notes = moy_ui.theme_install(
        b'{"format": 1, "name": "s5", "surfaces": {"chip": {"rest": "raised"},'
        b' "chip": {"rest": "sunken"}}}')
    assert (rc, notes) == (OK, 0)
    assert moy_ui.theme_shape("s5")[2] == (
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
    links = {"c1": "night", "c2": "c1", "c3": "c2", "c4": "c3", "c5": "c4",
             "loop_a": "loop_b", "loop_b": "loop_a"}
    surfaces = {"c1": {"button": {"rest": "raised", "pressed": "sunken"},
                       "chip": {"rest": "raised"}},
                "c2": {"button": {"rest": "etched"}}}
    for name, parent in links.items():
        (d / "themes" / (name + ".json")).write_text(json.dumps(
            {"format": 1, "name": name, "inherits": parent,
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
    night = resolve("night")[1]
    rc, row = resolve("c4")            # c4 -> c3 -> c2 -> c1 -> night: depth 4
    assert rc == OK and row[dim] == 4
    for name in ("c5", "loop_a"):      # one too deep; a cycle
        rc, row = resolve(name)
        assert rc == CHAIN and row == night


def _corpus(n):
    rng = random.Random(224)
    seeds = []
    for name in TH["seeds"]:
        with open(os.path.join(UI, "themes", name + ".json"), "rb") as f:
            src = f.read()
        seeds.append(src.replace(b'"name": "%s"' % name.encode(), b'"name": "fz"'))
    seeds.append(_src("fz", inherits="slate", metrics={"radius": 2, "press_dy": 1},
                      text={"button": {"disabled": {"effect": "emboss", "color": "ink"},
                                       "pressed": {"effect": "shadow", "color": "dim"}},
                            "tab": {"on": {"effect": "shadow", "color": "code_text"}}},
                      colors={"dark": {"block_sound": 3, "accent": 9}},
                      surfaces={"button": {"rest": "bevel2, fill surface, shadow",
                                           "pressed": "sunken, dither shade_lo/7"},
                                "window.title": {"active": "pinstripe shade_lo/shade_hi"}}))
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
    return rc, notes, mod.theme(mod.theme_installed(), False), \
        mod.theme_shape(mod.theme_installed())


def test_mutated_themes_never_break_the_reader():
    for src in _corpus(3000):
        rc, notes, row, _shape = _answer(moy_ui, src)
        assert 0 <= rc <= 7 and 0 <= notes <= 7
        if rc == OK:
            assert row[2] == OK
            _check_row(row[0])


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
    rc, notes = moy_ui.theme_install(src)
    if rc == 0:
        n = moy_ui.theme_installed()
        print(repr((rc, notes, moy_ui.theme(n, False), moy_ui.theme_shape(n))))
    else:
        print(repr((rc, notes, None, None)))
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
    assert len(lines) == len(corpus), out.stdout[-500:] + out.stderr
    for src, line in zip(corpus, lines):
        rc, notes, row, shape = _answer(moy_ui, src)
        want = (rc, notes, None if row is None else (tuple(row[0]), row[1], row[2]), shape)
        assert repr(want) == line, src
