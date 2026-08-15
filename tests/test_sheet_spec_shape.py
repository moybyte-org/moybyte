"""A cart sprite sheet is SPEC.md 3.2's shape, and nothing else is allowed to be
one silently (#161 tail).

SPEC.md 3.2 fixes a cart sheet at 128 x 256 px -- 16 cols x 32 rows of 8x8 tiles,
512 ids. libmoy bakes that geometry in (it takes no stride argument), so every
sheet-READING verb refuses any other shape and DRAWS NOTHING: blit_map (map),
blit_batch (spr/spr_batch), sspr, tline. Silence is right for a draw verb -- a
throw mid-frame takes the cart down -- but it makes a wrong sheet invisible at the
only place it is used.

SpriteSheet's default was 16x16 until 2026-08-15, i.e. out of spec, i.e. a
default-constructed sheet had ALWAYS drawn nothing through those four verbs on
both boards and in the browser. It was invisible for as long as it was because the
host had a second, permissive Python raster; deleting that (9b0663e) pointed the
host at the same C kernel and the fixtures had to move.

So this file pins BOTH halves of the fix:
  * the default is the spec shape, at the class and at both production seams;
  * a non-spec sheet is LOUD at construction, where a throw is affordable, and
    only buildable by asking for it out loud (spec=False).
Plus the behaviour that made it matter, measured through the real canvas: spec
draws, non-spec draws nothing.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import host_canvas                                    # noqa: E402
from runtime.editors_sheet import (SHEET_COLS, SHEET_H, SHEET_ROWS,  # noqa: E402
                                   SHEET_W, IconSheet, SpriteSheet, TileMap)


# -- the shape itself --------------------------------------------------------

def test_the_spec_shape_is_what_spec_3_2_says():
    assert (SHEET_COLS, SHEET_ROWS) == (16, 32)
    assert (SHEET_W, SHEET_H) == (128, 256)
    assert SHEET_COLS * SHEET_ROWS == 512               # tile ids 0..511


def test_a_default_sheet_is_the_spec_shape():
    sh = SpriteSheet()
    assert (sh.cols, sh.rows) == (SHEET_COLS, SHEET_ROWS)
    assert (sh.w, sh.h) == (SHEET_W, SHEET_H)
    assert sh.count == 512
    assert len(sh.pix) == SHEET_W * SHEET_H
    assert sh.is_spec_shape()


def test_from_hex_defaults_to_the_spec_shape_too():
    # The .moygfx blob does NOT carry its own dimensions -- it is a flat hex grid --
    # so the READER picks the shape, and its default has to agree with __init__'s.
    sh = SpriteSheet.from_hex("")
    assert (sh.cols, sh.rows) == (SHEET_COLS, SHEET_ROWS)
    assert sh.is_spec_shape()


def test_a_short_blob_lands_in_the_top_rows_with_ids_unchanged():
    # Every pre-512 cart and every PICO-8 import stored 128 lines. Those must keep
    # parsing, into the TOP half, with tile ids exactly where they were.
    lines = ["f" * SHEET_W] + ["0" * SHEET_W] * 127     # a 128-line blob
    sh = SpriteSheet.from_hex("\n".join(lines))
    assert sh.is_spec_shape()
    assert sh.tget(0, 0, 0) == 15                       # tile 0 kept its pixel
    assert sh.tget(16, 0, 0) == 0                       # row 1 still blank
    assert sh.pget(0, 128) == 0                         # the added half is blank


# -- loud at construction ----------------------------------------------------

def test_building_a_non_spec_sheet_unasked_raises_and_names_the_spec():
    with pytest.raises(ValueError) as exc:
        SpriteSheet(16, 16)
    msg = str(exc.value)
    assert "SPEC.md 3.2" in msg                         # names the rule...
    assert "16 x 32" in msg and "128 x 256" in msg      # ...and the required shape
    assert "16 x 16" in msg                             # ...and what it got
    assert "spec=False" in msg                          # ...and the way out


@pytest.mark.parametrize("cols,rows", [(16, 16), (4, 4), (8, 8), (16, 31), (32, 32)])
def test_every_wrong_shape_is_refused(cols, rows):
    with pytest.raises(ValueError):
        SpriteSheet(cols, rows)
    with pytest.raises(ValueError):
        SpriteSheet.from_hex("", cols=cols, rows=rows)


def test_spec_false_is_the_opt_out_and_says_so_in_is_spec_shape():
    sh = SpriteSheet(4, 4, spec=False)
    assert (sh.cols, sh.rows) == (4, 4)
    assert not sh.is_spec_shape()
    assert not SpriteSheet.from_hex("", cols=4, rows=4, spec=False).is_spec_shape()


def test_a_sheet_handed_a_short_pix_buffer_is_not_spec_shaped():
    # The C gate checks the BUFFER length too (a short one is an out-of-bounds read
    # on every sprite), so is_spec_shape must mean the same thing it does.
    sh = SpriteSheet(SHEET_COLS, SHEET_ROWS, pix=bytearray(16), spec=False)
    assert not sh.is_spec_shape()


# -- IconSheet is deliberately not a cart sheet ------------------------------

def test_icon_sheet_builds_freely_and_is_never_spec_shaped():
    # The system bar's 16px icon theme. It never reaches libmoy: the bar blits an
    # icon through tile_image() -> spr(), which takes a blittable and does not
    # address the sheet. So the loud constructor must NOT be in its way.
    icons = IconSheet()
    assert (icons.TILE, icons.cols, icons.rows) == (16, 8, 4)
    assert (icons.w, icons.h) == (128, 64)
    assert not icons.is_spec_shape()
    assert not IconSheet.from_hex("").is_spec_shape()
    # ...and not even an icon sheet of the cart sheet's TILE COUNT is spec-shaped,
    # because its tiles are 16px: geometry, not arithmetic, is what libmoy bakes in.
    assert not IconSheet(SHEET_COLS, SHEET_ROWS).is_spec_shape()
    # The opt-out is a default here, not a swallow: insist on spec=True and you get
    # the same refusal as anyone else, since an icon sheet can never satisfy it.
    with pytest.raises(ValueError):
        IconSheet(SHEET_COLS, SHEET_ROWS, spec=True)


# -- the behaviour that made the default a bug -------------------------------

def _painted(cols, rows):
    sh = SpriteSheet(cols, rows, spec=(cols, rows) == (SHEET_COLS, SHEET_ROWS))
    for y in range(8):
        for x in range(8):
            sh.pset(x, y, 8)                            # tile 0, solid
    return sh


def _filled_map():
    tm = TileMap(4, 4)
    for y in range(4):
        for x in range(4):
            tm.mset(x, y, 0)
    return tm


def _draws(sheet, verb):
    cv = host_canvas.make_canvas(64, 64)
    cv.cls(0)
    before = bytes(cv._buf)
    tm = _filled_map()
    if verb == "map":
        cv.map(tm, sheet, 0, 0, 4, 4, 0, 0)
    elif verb == "spr_batch":
        cv.spr_batch(sheet, [(0, 0, 0), (0, 8, 0)])
    elif verb == "sspr":
        cv.sspr(sheet, 0, 0, 8, 8, 0, 0, 32, 32)
    elif verb == "tline":
        cv.tline(tm, sheet, 0, 0, 60, 0, 0.0, 0.0, 0.1, 0.0)
    else:                                                # pragma: no cover
        raise AssertionError(verb)
    return bytes(cv._buf) != before


SHEET_VERBS = ["map", "spr_batch", "sspr", "tline"]


@pytest.mark.parametrize("verb", SHEET_VERBS)
def test_a_spec_sheet_draws_through_every_sheet_reading_verb(verb):
    assert _draws(_painted(SHEET_COLS, SHEET_ROWS), verb)


@pytest.mark.parametrize("verb", SHEET_VERBS)
def test_a_non_spec_sheet_draws_nothing_through_them(verb):
    # THE BUG, pinned as behaviour: libmoy declines the sheet and the frame stays
    # blank. Nothing here is a regression to fix in the raster -- silence is the
    # right mid-frame answer -- which is exactly why the constructor is loud.
    assert not _draws(_painted(16, 16), verb)


def test_spr_is_the_verb_that_does_not_care():
    # spr() takes a BLITTABLE (tile_image), not the sheet, so it never addresses
    # sheet geometry -- which is how a 16x16 sheet looked fine for so long: the
    # console's own icon/tile drawing all goes this way.
    cv = host_canvas.make_canvas(64, 64)
    cv.cls(0)
    before = bytes(cv._buf)
    cv.spr(_painted(16, 16).tile_image(0), 0, 0)
    assert bytes(cv._buf) != before


# -- the two production seams ------------------------------------------------

def test_the_open_carts_sheet_is_spec_shaped():
    from runtime.project import Project

    class _NoCart:
        cart = None

    sh = Project._build_sheet(_NoCart())
    assert sh.is_spec_shape()


def test_the_cross_cart_shared_sheet_is_spec_shaped():
    # The #18 shared-sprite clipboard. It has to span all 512 ids or copy_tile()
    # refuses a PUT/GET of anything past id 255 -- which the old 16x16 default did,
    # reported to the kid as a bare "CAN'T PUT".
    from runtime import console

    class _Store:
        @staticmethod
        def load_shared_sheet(root):
            return None

    class _WS:
        carts_root = "/nonexistent"
        carts_store = _Store

        def _with_sd(self, fn):
            return fn()

    shared = console.Workstation._load_shared_sheet(_WS())
    assert shared.is_spec_shape()
    assert shared.count == 512
    cart = SpriteSheet()
    cart.tset(511, 0, 0, 9)                              # a tile only a 16x32 sheet has
    assert shared.copy_tile(cart, 511) == 511            # ...and the clipboard takes it


# -- Python and C must agree on the geometry ---------------------------------

def test_libmoy_and_the_c_gate_pin_the_same_shape():
    # If libmoy's sheet geometry ever moves upstream, this is what says so -- the
    # Python side is a mirror of moy.h, not an independent choice.
    moy_h = (ROOT / "firmware/lilygo_t_deck_plus_micropython/native/moy_gfx"
             / "libmoy/moy.h").read_text(encoding="utf-8")
    got = dict(re.findall(r"#define\s+(MOY_SHEET_COLS|MOY_SHEET_ROWS)\s+(\d+)", moy_h))
    assert got == {"MOY_SHEET_COLS": str(SHEET_COLS), "MOY_SHEET_ROWS": str(SHEET_ROWS)}


def test_both_c_twins_still_gate_every_sheet_reading_verb():
    # The PREDICATE is single-source now (mg_is_moy_sheet in
    # native/moy_gfx/moy_gfx_kernels.h, which both surfaces include), so the two
    # can no longer disagree about what a moy sheet IS. What is still per-file is
    # the CALL SITES -- each sheet-reading verb marshals its own arguments and
    # has to remember to ask -- and losing the gate on one tier is how that stays
    # undetectable.
    dev = (ROOT / "firmware/lilygo_t_deck_plus_micropython/native/moy_gfx"
           / "modmoy_gfx.c").read_text(encoding="utf-8")
    host = (ROOT / "runtime/moyhost_gfx.c").read_text(encoding="utf-8")
    # blit_map, blit_batch, sspr, tline -- plus set_batch_src on the device, which
    # is a REGISTRATION (not mid-frame) and therefore raises instead of declining.
    assert dev.count("if (!mg_is_moy_sheet(") == 5
    assert "set_batch_src: not a moy sheet" in dev
    assert host.count("if (!mg_is_moy_sheet(") == 4
