"""Accent and tint: every colour role derived (docs/theming_2026-09.md §4.10),
in C (`moy_theme_derive`, native/moy_ui/moy_theme.c) and written as
`custom`'s theme.json by `moy_ui.theme_custom`.

* The data: style.json's accents and tints are the ones the Appearance mockups
  draw (owner, 2026-10-14: the set as drawn), every accent one black clears,
  and the C's luminance table is the mockups'.
* Every seed x accent (and the source's own) x tint (and none) x variant: the
  C answers the mockups' `derive()` role for role (its code roles held to
  theme.code's floor), no pair of theme.pairs is under theme.contrast and no
  code role under theme.code's, and the bytes parse with no note and resolve to
  the rows the derivation answered; no tint with the source's own accent
  answers the source's rows but for the dim inks and content colours the
  source itself leaves under the floor.
* The flattening: custom resolves to its source's metrics, text effects,
  surfaces, frame, fonts and layout, and carries a stored source's wallpaper
  keys.
* The cap: a maximal source (every role, 32 surfaces, 32 effects, a layout)
  stays under theme.bytes; a source whose whole rows would not leaves out the
  roles its resolution gives back.
* Refusals, and the boards' module (the desktop MicroPython's `moy_ui`)
  answering as the host's does.
"""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from tools import gen_ui_tables

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from unix_mp import require_unix_mp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "native", "moy_ui")
with open(os.path.join(UI, "style.json")) as _f:
    STYLE = json.load(_f)
TH = STYLE["theme"]
ROLES = STYLE["tokens"] + TH["content"]
PAIRS = len(TH["pairs"])          # the current plate's bit is the one past the pairs
OK, BIG, SYNTAX, NEWER, NAME, TAKEN, CHAIN = range(7)
SEEDS = list(moy_ui.themes())
ACCENTS = [None] + [a for _n, a, _d in TH["accents"]]
TINTS = [None] + [n for n, _r in TH["tints"]]


def _mockups():
    path = os.path.join(ROOT, "docs", "media", "theming_2026-09", "appearance", "mockups.py")
    spec = importlib.util.spec_from_file_location("appearance_mockups", path)
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, sys.argv[:1]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    return mod


MK = _mockups()


WHITE = 0x1ffffff                 # RGB white: MOY64's (9000) misses 4.5:1 on a mid grey


def _code_reads(a, b):
    """A code role on the code surface at theme.code's floor."""
    la, lb = MK.lum(a), MK.lum(b)
    return (max(la, lb) + 500) * 10 >= TH["code"]["contrast"] * (min(la, lb) + 500)


def _reference(name, variant, accent, tint):
    """The mockups' derivation, its code roles then held to theme.code's
    floor (docs/look_2026-10.md step 23): the source's where it reaches it,
    else the derive set's for the code surface's side, else black or RGB
    white, the larger ratio."""
    entry = None if accent is None else next(a for a in MK.ACCENTS if a[0] == accent)
    base = MK.base_row(name, variant)
    row, _why = MK.derive_variant(base, entry, "NONE" if tint is None else tint.upper())
    s = row[TH["code"]["bg"]]
    light = MK.light_bg(s)
    for r in TH["code"]["roles"]:
        row[r] = base[r]
        if not _code_reads(row[r], s):
            alt = (MK.SYNTAX_LIGHT if light else MK.SYNTAX_DARK).get(r)
            row[r] = alt if alt is not None and _code_reads(alt, s) else \
                (0 if MK.ratio(s, 0) >= MK.ratio(s, WHITE) else WHITE)
    return row


def _val(x):
    """A theme.json colour: an index, or "#rrggbb" as RGB | 0xrrggbb."""
    return 0x1000000 | int(x[1:], 16) if isinstance(x, str) else int(x)


def _row(d):
    return tuple(_val(d[r]) for r in ROLES)


def _custom(source, accent=None, tint=None):
    rc, b = moy_ui.theme_custom(source, accent, tint)
    assert rc == OK, (source if isinstance(source, str) else "bytes", accent, tint, rc)
    assert len(b) <= TH["bytes"]
    return b


def _src(name, **kw):
    d = {"format": 2, "name": name}
    d.update(kw)
    return json.dumps(d).encode()


# -- the data ------------------------------------------------------------------

def test_the_accents_and_tints_are_the_mockups():
    assert [(n.upper(), a, d) for n, a, d in TH["accents"]] == \
        [(n, a, d) for a, d, n in MK.ACCENTS]
    assert [(n.upper(), r) for n, r in TH["tints"]] == list(MK.TINTS[1:])
    assert moy_ui.theme_accents() == tuple(tuple(e) for e in TH["accents"])
    assert moy_ui.theme_tints() == tuple((n, tuple(r)) for n, r in TH["tints"])


def test_every_accent_is_one_black_clears_and_the_tables_agree():
    import ctypes
    lum = (ctypes.c_uint16 * 64).in_dll(moy_ui.lib, "moy_theme_lum")
    assert list(lum) == MK.LUM
    assert MK.FLOOR == TH["contrast"]
    for name, a, _deep in TH["accents"]:
        assert MK.clears(a, 0), name
    for name, ramp in TH["tints"]:
        assert 0 not in ramp and 7 not in ramp, name


# -- every combination ---------------------------------------------------------

@pytest.mark.parametrize("name", SEEDS)
def test_every_combination_is_the_reference_and_reads(name):
    for accent in ACCENTS:
        for tint in TINTS:
            b = _custom(name, accent, tint)
            pkg = json.loads(b)
            assert pkg["name"] == "custom" and "inherits" not in pkg
            rc, notes, got_name, dark, light, _fonts, _lay, _sh, _look, _bd = moy_ui.theme_resolve(b)
            assert (rc, notes, got_name) == (OK, 0, "custom"), (name, accent, tint)
            for v, resolved in (("dark", dark), ("light", light)):
                want = _reference(name, v, accent, tint)
                assert _row(pkg["colors"][v]) == _row(want), (name, v, accent, tint)
                row, unreadable, rc = resolved
                assert rc == OK and tuple(row) == _row(want), (name, v, accent, tint)
                # the pairs; the current plate's bit (past them) is a report
                # the derivation does not answer: a picked accent on its plate
                assert unreadable & ~(1 << PAIRS) == 0, (name, v, accent, tint)


# Appearance's accents on Moy (its own and seven of the sixteen) whose focus
# colour, the `accent` role, misses 3:1 on Moy's current plate in that mode
# (Day light, Night dark) -- the readability report's plate bit, measured
# 2026-10-18. Each is the owner's call; none is changed here. A fix takes
# its entry off; a new failure is a regression.
PLATE_UNDER = {("orange", "Day"), ("pink", "Day"), ("blue", "Day"), ("mint", "Day"),
               ("green", "Day"), ("red", "Night"), ("violet", "Night")}


def test_moys_appearance_accents_read_in_day_and_night():
    """Every accent Appearance offers on Moy, in Day and Night: every pair
    of theme.pairs reaches theme.contrast, and the focus colour on the
    current plate does, but for the pinned PLATE_UNDER."""
    from runtime.appearance_app import ACCENTS as OFFERED
    by_name = {n: a for n, a, _d in TH["accents"]}
    assert len(OFFERED) + 1 == 8, "Appearance's swatches are the theme's own and seven"
    under = set()
    for name in (None,) + tuple(OFFERED):
        b = _custom("moy", None if name is None else by_name[name])
        rc, _notes, _n, dark, light = moy_ui.theme_resolve(b)[:5]
        assert rc == OK
        for mode, (_row, unreadable, rcv) in (("Day", light), ("Night", dark)):
            assert rcv == OK
            assert unreadable & ((1 << PAIRS) - 1) == 0, (name, mode)
            if unreadable >> PAIRS & 1:
                under.add((name, mode))
    assert (None, "Day") not in under and (None, "Night") not in under, "Moy's own accent"
    assert under == PLATE_UNDER


REPAIRED = {r: bgs for r, _f, bgs in TH["recolour"]["dim_ink"]}
REPAIRED.update((r, ["surface"]) for r in TH["recolour"]["content"])
CODE = set(TH["code"]["roles"])


@pytest.mark.parametrize("name", SEEDS)
def test_no_tint_and_the_own_accent_answer_the_source(name):
    """Every role but a dim ink or a content colour the source itself leaves
    under the floor against its own backgrounds, which steps 4 and 5 repair."""
    pkg = json.loads(_custom(name))
    for v, light in (("dark", False), ("light", True)):
        row = dict(zip(ROLES, moy_ui.theme(name, light)[0]))
        got = dict(zip(ROLES, _row(pkg["colors"][v])))
        for r in ROLES:
            if got[r] != row[r]:
                assert r in REPAIRED, (name, v, r)
                if r in CODE:
                    assert not _code_reads(row[r], row[TH["code"]["bg"]]), (name, v, r)
                else:
                    assert not all(MK.clears(row[r], row[b]) for b in REPAIRED[r]), (name, v, r)


# -- the flattening ------------------------------------------------------------

def _resolved(name):
    return (moy_ui.theme_shape(name), moy_ui.theme_fonts(name), moy_ui.theme_layout(name),
            moy_ui.theme_home(name))


def _installed(src):
    rc, notes = moy_ui.theme_install(src)
    assert (rc, notes) == (OK, 0)
    return moy_ui.theme_installed()


@pytest.mark.parametrize("name", SEEDS)
def test_custom_resolves_to_its_seeds_shape(name):
    b = _custom(name, TH["accents"][3][1], "teal")
    try:
        assert _resolved(_installed(b)) == _resolved(name)
    finally:
        moy_ui.theme_uninstall()
    with open(os.path.join(UI, "themes", name + ".json")) as f:
        seed = json.load(f)
    if "frame" in seed:
        assert json.loads(b)["frame"] == seed["frame"]


STORED = _src(
    "sunset",
    colors={"dark": {"panel": 2, "title": 8, "accent": 9, "ink_note": 14},
            "light": {"panel": 31, "title": 8, "accent": 9}},
    metrics={"radius": 2, "press_dx": 1},
    frame={"style": "win9x", "title_align": "center", "buttons": "T|ZMC"},
    fonts={"ui": "sun.fnt"},
    text={"button": {"rest": {"effect": "shadow", "color": "ink_dim"},
                     "pressed": {"effect": "none"}},
          "window.title": {"active": {"effect": "emboss", "color": "title_ink"}}},
    surfaces={"button": {"rest": "raised,  fill 3, border, shadow",
                         "hot": "dither surface/12, border focus"},
              "desk": {"rest": "gradient desktop/48"},
              "bar.item": {"active": "flat"}},
    layout={"bars": [{"edge": "bottom", "size": 22, "left": ["menu", "text"],
                      "center": ["chips"], "right": ["text", "zone", "clock", "close"],
                      "text": ["a \"quoted\" one", "back\\slash"]}],
            "home": TH["layout"]["homes"][-1]},
    wallpapers={"desk": "moybyte.sakura"},
    images={"desk": "desk.moyimg", "list": [1, 2, {"k": "v w"}]})


def test_a_stored_source_flattens_whole():
    b = _custom(STORED, 40, "plum")
    pkg = json.loads(b)
    src = json.loads(STORED)
    assert pkg["wallpapers"] == src["wallpapers"] and pkg["images"] == src["images"]
    assert pkg["fonts"] == src["fonts"]
    assert pkg["metrics"] == {"radius": 2, "press_dx": 1}
    assert pkg["frame"] == {"style": "win9x", "title_align": "center", "buttons": "TZMC"}
    assert pkg["surfaces"]["button"]["rest"] == "raised, fill 3, border, shadow"
    try:
        want = _resolved(_installed(STORED))
        moy_ui.theme_uninstall()
        assert _resolved(_installed(b)) == want
    finally:
        moy_ui.theme_uninstall()


def test_a_source_layout_the_rule_refused_flattens_to_none():
    src = _src("broken", layout={"bars": [{"edge": "top", "left": ["clock"]}]})
    pkg = json.loads(_custom(src))
    assert "layout" not in pkg


# -- the cap ---------------------------------------------------------------------

def _maximal(name="maximal", colors=True):
    """A package at every cap: 32 surfaces, 32 text effects and a layout, and
    with `colors` every role in both variants, the entries as short as the
    grammar allows so it fits theme.bytes; without, the entries as long."""
    parts = sorted(gen_ui_tables.Tables(STYLE).kinds + TH["parts"], key=len if colors else None)
    states = ("on", "hot") if colors else moy_ui.theme_states()
    surfaces, text = {}, {}
    for i in range(32):
        part, state = parts[i % 16], states[i // 16]
        if colors:
            surfaces.setdefault(part, {})[state] = ("shadow", "raised")[i % 2]
            text.setdefault(part, {})[state] = ({"effect": "none"},
                                                {"effect": "shadow", "color": "ink"})[i % 2]
        else:
            surfaces.setdefault(part, {})[state] = "etched, dither shade_dark/63, border 63"
            text.setdefault(part, {})[state] = {"effect": "emboss", "color": "shade_dark"}
    flags = {r for v in ("dark", "light", "both") for r, x in TH["derive"][v]
             if isinstance(x, bool)}
    rows = {v: {r: (True if r in flags else 13) for r in ROLES} for v in ("dark", "light")}
    bars = [{"edge": "top", "left": ["menu", "zone"], "right": ["clock", "close"]}]
    if not colors:
        bars[0]["center"] = ["text"]
        bars[0]["text"] = ["x" * 24]
        bars.append({"edge": "left", "left": ["text", "spacer"], "text": ["y" * 24]})
    kw = dict(metrics={"radius": 3}, frame={"style": "platinum"}, surfaces=surfaces,
              text=text, layout={"bars": bars})
    if colors:
        kw["colors"] = rows
    return json.dumps(dict({"format": 2, "name": name}, **kw), separators=(",", ":")).encode()


def test_a_maximal_source_stays_under_the_cap():
    src = _maximal()
    assert len(src) <= TH["bytes"]
    rc, notes, *_ = moy_ui.theme_resolve(src)
    assert (rc, notes) == (OK, 0)
    for accent in ACCENTS:
        for tint in TINTS:
            b = _custom(src, accent, tint)
            assert len(json.loads(b)["colors"]["dark"]) == len(ROLES)
    try:
        want = _resolved(_installed(src))
        moy_ui.theme_uninstall()
        assert _resolved(_installed(_custom(src, 14, "rose"))) == want
    finally:
        moy_ui.theme_uninstall()


def _half_effects(src):
    pkg = json.loads(src)
    pkg["name"] = "half"
    pkg["text"] = dict(list(pkg["text"].items())[:8])
    return json.dumps(pkg, separators=(",", ":")).encode()


def test_over_the_cap_the_roles_resolution_gives_back_are_left_out():
    """Sources with no colours and long surfaces and effects, whose whole rows
    would not fit: custom carries only the roles that differ from what its
    resolution gives back, and still resolves to the derived rows; where even
    that does not fit, the answer is MOY_THEME_BIG."""
    full = _maximal("no-colors", colors=False)
    half = _half_effects(full)
    for src in (full, half):
        assert len(src) <= TH["bytes"] < len(src) + len(_custom("night"))
    # what the resolution gives back is left out: night's own rows, but for
    # the code roles its syntax colours leave under theme.code's floor
    cols = json.loads(_custom(half))["colors"]
    assert cols and set(cols["dark"]) | set(cols["light"]) <= set(TH["code"]["roles"])
    fits = 0
    for src in (full, half):
        for accent in ACCENTS:
            for tint in TINTS:
                rc, b = moy_ui.theme_custom(src, accent, tint)
                if rc == BIG:
                    continue
                assert rc == OK and len(b) <= TH["bytes"]
                cols = json.loads(b).get("colors", {})
                assert all(len(c) < len(ROLES) for c in cols.values())
                _rc, notes, _name, dark, light, _f, _l, _s, _lk, _bd = moy_ui.theme_resolve(b)
                assert notes == 0
                for v, resolved in (("dark", dark), ("light", light)):
                    want = _reference("night", v, accent, tint)
                    assert tuple(resolved[0]) == _row(want), (accent, tint, v)
                fits += bool(cols)
    assert fits > 0


# -- refusals ----------------------------------------------------------------------

def test_what_theme_custom_refuses():
    with open(os.path.join(UI, "themes", "forest.json"), "rb") as f:
        forest = f.read()
    assert moy_ui.theme_custom(forest, None, None)[0] == OK
    assert moy_ui.theme_custom(forest.replace(b"\n", b"\n "), None, None) == (TAKEN, None)
    assert moy_ui.theme_custom(_src("kid", inherits="forest"), None, None) == (CHAIN, None)
    assert moy_ui.theme_custom("sunset", None, None) == (CHAIN, None)
    assert moy_ui.theme_custom(b"{", None, None) == (SYNTAX, None)
    with pytest.raises(ValueError):
        moy_ui.theme_custom("night", 7, None)
    with pytest.raises(ValueError):
        moy_ui.theme_custom("night", None, "plaid")


# -- the boards' module ----------------------------------------------------------

_MP_DRIVER = """
import moy_ui
srcs = @SRCS@
print(repr(moy_ui.theme_accents()))
print(repr(moy_ui.theme_tints()))
for s in srcs:
    for a in @ACCENTS@:
        for t in @TINTS@:
            print(repr(moy_ui.theme_custom(s, a, t)))
"""


def test_the_boards_module_answers_as_the_host_does(tmp_path):
    exe = require_unix_mp(
        "moy_ui",
        why="Without it the derivation's MicroPython face, the one every board "
            "imports, never meets the host's answers.")
    full = _maximal("no-colors", colors=False)
    srcs = SEEDS + [STORED, _maximal(), full, _half_effects(full)]
    script = tmp_path / "driver.py"
    script.write_text(_MP_DRIVER.replace("@SRCS@", repr(srcs))
                      .replace("@ACCENTS@", repr(ACCENTS)).replace("@TINTS@", repr(TINTS)))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=300)
    lines = out.stdout.splitlines()
    want = [repr(moy_ui.theme_accents()), repr(moy_ui.theme_tints())]
    want += [repr(moy_ui.theme_custom(s, a, t)) for s in srcs for a in ACCENTS for t in TINTS]
    assert len(lines) == len(want), out.stdout[-500:] + out.stderr
    for got, w in zip(lines, want):
        assert got == w
