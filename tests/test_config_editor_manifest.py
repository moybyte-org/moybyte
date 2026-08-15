"""Tests for #94's host-side gaps in the Config ("Make it mine" cards) tab:

  1. a cart manifest/metadata editing surface -- title + author, through
     moy_carts.save_manifest_meta / Project.commit_manifest / the CardsLayer
     "CART INFO" modal (cards_layer.py's _open_meta/_meta_key/_commit_meta).
  2. validation feedback for a bad/out-of-range `edit` field definition --
     covered in test_preliterate_ux.py alongside the existing malformed-card
     tests, not here.

`permissions` stays read-only (see the comment over moy_carts.save_manifest_meta)
-- the tracker leaves its editability an open, security-sensitive question, so
this only covers the unambiguous title/author half of the gap.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import moy_carts  # noqa: E402
from runtime import host_app  # noqa: E402

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)


# ----------------------------------------------------------------------------
# Layer 1: the store (moy_carts.save_manifest_meta)
# ----------------------------------------------------------------------------

def test_load_defaults_author_to_empty_string(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("No Author", root, src="def _draw():\n    cls(1)\n")
    assert cart["author"] == ""                 # absent in the manifest -> ""


def test_duplicate_carries_the_author_over(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Original", root, src="def _draw():\n    cls(1)\n")
    assert moy_carts.save_manifest_meta(cart["path"], author="Ada Lovelace")
    cart = moy_carts.load(cart["path"])
    copy = moy_carts.duplicate(cart, root)
    assert copy["author"] == "Ada Lovelace"
    assert copy["title"] == "Original copy"


def test_save_manifest_meta_round_trip_preserves_other_fields(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create(
        "Info Cart", root, src="def _draw():\n    cls(1)\n", type="game",
        edit=[{"key": "n", "type": "int", "min": 0, "max": 9, "card": "N"}])
    moy_carts.set_graduated(cart, True)          # a field that must survive untouched

    assert moy_carts.save_manifest_meta(cart["path"], title="New Title",
                                        author="Ada Lovelace") is True
    reloaded = moy_carts.load(cart["path"])
    assert reloaded["title"] == "New Title"
    assert reloaded["author"] == "Ada Lovelace"
    # every other manifest fact rode along untouched
    assert reloaded["type"] == "game"
    assert reloaded["graduated"] is True
    assert reloaded["edit"] == cart["edit"]
    assert reloaded["src"] == cart["src"]


def test_save_manifest_meta_noop_when_unchanged(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Same", root, src="def _draw():\n    cls(1)\n")
    assert moy_carts.save_manifest_meta(cart["path"], title="Same") is False
    assert moy_carts.save_manifest_meta(cart["path"]) is False   # both None -> no-op
    assert moy_carts.save_manifest_meta(cart["path"], author="") is False  # matches default


def test_save_manifest_meta_bad_manifest_is_a_safe_false(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    assert moy_carts.save_manifest_meta(root + "/nonexistent.moy", title="X") is False


# ----------------------------------------------------------------------------
# Layer 2: Project.commit_manifest (RAM sync + store write + blank-title guard)
# ----------------------------------------------------------------------------

def _cart_with_edit_schema(root):
    return moy_carts.create(
        "Tune Me", root, src="def _draw():\n    cls(1)\n", type="app",
        edit=[{"key": "spd", "type": "int", "min": 0, "max": 10, "card": "SPD {value}"}])


def test_project_commit_manifest_persists_and_syncs_ram(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    _cart_with_edit_schema(root)
    ws = host_app.build_workstation(root)
    ws.launcher.set_items(moy_carts.scan(root))
    ws.launcher.sel = next(i for i, c in enumerate(ws.launcher.items)
                           if c["title"] == "Tune Me")
    ws.open_in_editor()
    assert ws.menu_view == "cards"

    ok = ws.project.commit_manifest(title="Renamed", author="Kid Coder")
    assert ok is True
    assert ws.project.cart["title"] == "Renamed"       # in-RAM sync
    assert ws.project.cart["author"] == "Kid Coder"
    reloaded = moy_carts.load(ws.project.cart["path"])
    assert reloaded["title"] == "Renamed"
    assert reloaded["author"] == "Kid Coder"


def test_project_commit_manifest_rejects_blank_title(tmp_path):
    # A blank/whitespace title never overwrites the cart's real title -- but this
    # low-level store call still applies whichever OTHER field IS valid (the
    # all-or-nothing guard lives one layer up, in the modal's _commit_meta,
    # which never calls commit_manifest at all on a blank title -- see
    # test_meta_modal_blank_title_stays_open_with_message).
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = _cart_with_edit_schema(root)
    ws = host_app.build_workstation(root)
    ws.launcher.set_items(moy_carts.scan(root))
    ws.launcher.sel = next(i for i, c in enumerate(ws.launcher.items)
                           if c["title"] == "Tune Me")
    ws.open_in_editor()

    assert ws.project.commit_manifest(title="   ", author="Someone") is True
    assert ws.project.cart["title"] == "Tune Me"        # title left alone in RAM
    assert ws.project.cart["author"] == "Someone"       # author still applied
    reloaded = moy_carts.load(cart["path"])
    assert reloaded["title"] == "Tune Me"               # title left alone on disk
    assert reloaded["author"] == "Someone"

    # blank BOTH -> nothing to do, a clean no-op False
    assert ws.project.commit_manifest(title="  ", author=None) is False


def test_project_commit_manifest_noop_without_a_real_cart():
    ws = host_app.build_workstation(None)
    ws.project.cart = None
    assert ws.project.commit_manifest(title="X") is False


# ----------------------------------------------------------------------------
# Layer 3: the CardsLayer "CART INFO" modal (open/type/commit/cancel)
# ----------------------------------------------------------------------------

def _open_cards_for(ws, title):
    ws.launcher.sel = next(i for i, c in enumerate(ws.launcher.items)
                           if c["title"] == title)
    ws.open_in_editor()
    assert ws.menu_view == "cards"


def test_info_button_tap_opens_the_modal(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    _cart_with_edit_schema(root)
    ws = host_app.build_workstation(root)
    ws.launcher.set_items(moy_carts.scan(root))
    _open_cards_for(ws, "Tune Me")
    ws.input.begin_frame()
    ws.frame(1 / 30)                                    # draw once (lays out layout)
    assert ws.cards_layer.meta is None

    rect = ws.cards_layer.layout.info_btn
    cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
    assert ws.cards_layer.handle_pointer(cx, cy, True) is True
    assert ws.cards_layer.meta is not None
    assert ws.input.text_mode is True                   # typing mode armed


def _type(cards, text):
    for ch in text:
        cards._meta_key(ord(ch))


def test_meta_modal_edits_title_and_author_and_commits(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = _cart_with_edit_schema(root)
    ws = host_app.build_workstation(root)
    ws.launcher.set_items(moy_carts.scan(root))
    _open_cards_for(ws, "Tune Me")

    cl = ws.cards_layer
    cl._open_meta()
    assert cl.meta["field"] == 0                         # starts on TITLE
    assert cl.meta["title"] == "Tune Me"                  # pre-filled from the cart
    # clear the pre-filled title, type a new one
    for _ in range(len("Tune Me")):
        cl._meta_key(8)
    _type(cl, "Star Racer")
    cl._meta_key(9)                                       # Tab -> AUTHOR
    assert cl.meta["field"] == 1
    _type(cl, "Ada")
    cl._meta_key(13)                                       # Enter -> commit

    assert cl.meta is None                                 # modal closed
    assert ws.input.text_mode is False                     # keyboard restored
    assert ws.project.cart["title"] == "Star Racer"
    assert ws.project.cart["author"] == "Ada"
    reloaded = moy_carts.load(cart["path"])
    assert reloaded["title"] == "Star Racer"
    assert reloaded["author"] == "Ada"


def test_meta_modal_blank_title_stays_open_with_message(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    _cart_with_edit_schema(root)
    ws = host_app.build_workstation(root)
    ws.launcher.set_items(moy_carts.scan(root))
    _open_cards_for(ws, "Tune Me")

    cl = ws.cards_layer
    cl._open_meta()
    for _ in range(len(cl.meta["title"])):
        cl._meta_key(8)                                     # backspace to empty
    assert cl.meta["title"] == ""
    cl._meta_key(13)                                        # Enter -> try to commit

    assert cl.meta is not None                              # stayed open
    assert cl.meta["msg"] == "TITLE CAN'T BE BLANK"
    assert ws.project.cart["title"] == "Tune Me"             # unchanged


def test_meta_modal_cancel_discards_edits(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = _cart_with_edit_schema(root)
    ws = host_app.build_workstation(root)
    ws.launcher.set_items(moy_carts.scan(root))
    _open_cards_for(ws, "Tune Me")

    cl = ws.cards_layer
    cl._open_meta()
    _type(cl, "XXXXX")
    cl._meta_key(27)                                        # Esc -> cancel

    assert cl.meta is None
    assert ws.input.text_mode is False
    assert ws.project.cart["title"] == "Tune Me"             # discarded
    reloaded = moy_carts.load(cart["path"])
    assert reloaded["title"] == "Tune Me"


def test_meta_modal_draws_without_crashing_and_reset_closes_it(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    _cart_with_edit_schema(root)
    ws = host_app.build_workstation(root)
    ws.launcher.set_items(moy_carts.scan(root))
    _open_cards_for(ws, "Tune Me")

    ws.cards_layer._open_meta()
    ws.input.begin_frame()
    ws.frame(1 / 30)                                        # draws the modal, must not raise
    assert ws.cart_error is None
    assert probe.drew_something(ws.canvas)

    ws.cards_layer.reset()                                  # switching cart must not leak it
    assert ws.cards_layer.meta is None
    assert ws.input.text_mode is False
