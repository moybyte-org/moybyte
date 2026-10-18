"""The font converter, tools/make_font.py (docs/look_2026-10.md step 3):
TrueType at a fixed size into a font file, version 2.

* The checked-in fixture (tests/fixtures/fonts/inter_subset.ttf, a subset of
  Inter, its licence beside it) rebuilds to the committed sample_v2_*.fnt
  byte for byte under the FreeType and raqm versions the set was built with,
  and to the same bytes twice under any.
* The byte map the converter writes is the one the reader decodes.
* The set: every built-in theme's role faces and the mono, per tier; the
  freezable module a board takes carries exactly its tier's faces and their
  families' licences.
"""

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures", "fonts")
CHARS = "AVTayo.,0123456789Hi−…é"

PIL = pytest.importorskip("PIL")
from tools import make_font  # noqa: E402


def _raqm():
    from PIL import features
    return features.check("raqm")


def _built_with():
    with open(os.path.join(ROOT, "native", "moy_ui", "fonts", "set.json")) as f:
        return json.load(f)["built_with"]


@pytest.mark.parametrize("bpp", [1, 2, 4])
def test_the_fixture_rebuilds(bpp):
    if not _raqm():
        pytest.skip("Pillow without raqm: the converter refuses to run")
    ttf = os.path.join(FIX, "inter_subset.ttf")
    a = make_font.build_face(ttf, 13, 500, bpp, CHARS)
    assert a == make_font.build_face(ttf, 13, 500, bpp, CHARS)
    assert a[:6] == b"MFNT\x02" + bytes([bpp])
    if make_font.versions() == _built_with():
        with open(os.path.join(FIX, "sample_v2_%d.fnt" % bpp), "rb") as f:
            assert a == f.read()
    else:
        pytest.skip("FreeType/raqm %s, not the set's %s: the bytes are only held equal "
                    "under the hinter that made them" % (make_font.versions(), _built_with()))


def test_the_fixture_carries_its_licence():
    with open(os.path.join(FIX, "inter_subset.txt"), "rb") as f:
        assert b"SIL Open Font License" in f.read()


def test_the_byte_map_is_the_readers():
    text = open(os.path.join(ROOT, "native", "moy_gfx", "moy_text.h")).read()
    bmap = make_font.byte_map()
    assert len(bmap) <= 32 and 0x2026 in bmap
    ell = int(re.search(r"#define MOY_FONT_ELLIPSIS (0x[0-9A-Fa-f]+)", text).group(1), 16)
    assert make_font.to_byte(0x2026, bmap) == ell
    assert make_font.to_byte(0xE9, bmap) == 0xE9 and make_font.to_byte(0x41, bmap) == 0x41
    assert make_font.to_byte(0x0100, bmap) is None and make_font.to_byte(0x85, bmap) is None


def test_the_set_is_every_themes_roles_and_the_mono():
    faces, tiers = make_font.faces()
    assert set(tiers) == {"handheld", "desktop"}
    for tier, names in tiers.items():
        assert "jbmono-11-400-2" in names
        for fam in ("daysans", "inter", "atkinson"):
            assert any(n.startswith(fam + "-") for n in names)
        # A theme draws in at most MOY_FONT_SLOTS faces at once.
        assert len([n for n in names if n.startswith("inter-")]) + 1 <= 16
    assert faces["inter-44-600-4"][4] and not faces["inter-15-400-4"][4]   # Calc's subset
    with open(os.path.join(ROOT, "native", "moy_ui", "fonts", "set.json")) as f:
        meta = json.load(f)
    assert meta["tiers"] == tiers and set(meta["faces"]) == set(faces)


def test_every_family_a_seed_names_carries_its_type_roles():
    """A seed's look `fam` (docs/look_2026-10.md step 26a) draws each role of
    `fontset.ROLES`, at the weight steps the look moves (`w_med`, `w_semi`,
    `w_bold`; step 26b), in a face its class's tier carries, with the full
    character set (a Calc subset would draw a name's letters as spaces)."""
    from runtime import fontset
    faces, tiers = make_font.faces()
    named = set()
    themes = os.path.join(ROOT, "native", "moy_ui", "themes")
    for f in os.listdir(themes):
        if f.endswith(".json"):
            with open(os.path.join(themes, f)) as fh:
                look = json.load(fh).get("look", {})
            for mode in ("dark", "light"):
                part = dict(look)
                part.update(look.get(mode, {}))
                if "fam" in part:
                    steps = {w: part[k] for w, k in
                             ((500, "w_med"), (600, "w_semi"), (700, "w_bold")) if k in part}
                    named.add((part["fam"], tuple(sorted(steps.items()))))
    assert {fam for fam, _s in named} >= {"inter", "atkinson"}
    for fam, steps in named:
        for desk, tier in ((False, "handheld"), (True, "desktop")):
            for role in fontset.ROLES:
                face = fontset.role_face(fam, role, desk, dict(steps))
                if face is None:
                    continue
                assert face[:-4] in tiers[tier], (fam, steps, role, tier)
                assert not faces[face[:-4]][4], (face, "a Calc subset")
            for role in fontset.CALC_ROLES:
                face = fontset.role_face(fam, role, desk, dict(steps))
                assert face[:-4] in tiers[tier], (fam, steps, role, tier)
            n = len({fontset.role_face(fam, r, desk, dict(steps))
                     for r in tuple(fontset.ROLES) + tuple(fontset.CALC_ROLES)} - {None})
            assert n + 1 <= 16, "a theme's faces and the mono fit MOY_FONT_SLOTS"


def test_a_board_module_carries_its_tier():
    src = make_font.render_module("handheld")
    ns = {}
    exec(compile(src, "fonts_data", "exec"), ns)
    _faces, tiers = make_font.faces()
    assert ns["TIER"] == "handheld" and sorted(ns["FACES"]) == tiers["handheld"]
    assert set(ns["LICENCES"]) == {"daysans", "inter", "atkinson", "jbmono"}
    assert make_font.board_tier(os.path.join(ROOT, "firmware", "lilygo_t_deck_plus_mainline")) \
        == "handheld"
    assert make_font.board_tier(os.path.join(ROOT, "firmware", "guition_jc8012p4a1c")) \
        == "desktop"
