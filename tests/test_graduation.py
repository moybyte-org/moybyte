"""Stage 8 (docs/history/shell_ux_technical_plan_v1.md Section 3): blocks<->code
GRADUATION -- the MakeCode model with an honest one-way door (spec Section 8).

This module has three layers, matching the three implementation commits:

  1. the NORMALIZATION primitives in runtime/blocks.py (the fuzzy heart -- what
     counts as a cosmetic vs a diverging edit),
  2. the STORE layer in runtime/moy_carts.py (the stored `graduated` manifest
     fact + the journal `grad` rider that un-graduates on undo),
  3. the console WIRING (a code commit graduates a block cart; the blocks tab goes
     read-only + celebrates; an undo past the graduating commit restores both the
     source AND graduated:false).

The full §8 edge-case matrix lives across `test_normalization_matrix` and the
end-to-end `test_*` below.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import blocks  # noqa: E402
from runtime import moy_carts  # noqa: E402


# ----------------------------------------------------------------------------
# Layer 1: the normalization / round-trip oracle (blocks.py)
# ----------------------------------------------------------------------------

def _block_program():
    """A small but non-trivial block program: a variable + a draw that uses it, so
    the generated source has decls, a lifecycle fn, and real statements to diff."""
    return {
        "vars": ["score"],
        "scripts": [
            blocks.make_block("on_draw", children=[
                blocks.make_block("cls", {"color": "black"}),
                blocks.make_block("set_var", {"var": "score", "value": 7}),
            ]),
        ],
    }


def test_generated_source_roundtrips_with_itself():
    prog = _block_program()
    src = blocks.compile_blocks(prog)
    assert blocks.source_roundtrips(prog, src) is True


def test_inline_comment_scan_respects_strings():
    # a `#` inside a string literal is NOT a comment (must not be cut)
    assert blocks._strip_inline_comment('print("a # b")') == 'print("a # b")'
    assert blocks._strip_inline_comment("x = 1  # note") == "x = 1  "
    assert blocks._strip_inline_comment("# whole line") == ""
    assert blocks._strip_inline_comment("y = '\\''  # esc") == "y = '\\''  "


def test_normalization_matrix():
    """The §8 cosmetic-vs-divergent line, pinned. Each case is (label, mutate) ->
    whether the mutated source should still ROUND-TRIP (True = stays blockifiable)."""
    prog = _block_program()
    base = blocks.compile_blocks(prog)

    # --- cosmetic edits: must STILL round-trip (do NOT graduate) ---
    # trailing whitespace
    assert blocks.source_roundtrips(prog, base.replace("\n", "   \n"))
    # extra blank lines sprinkled in
    assert blocks.source_roundtrips(prog, base.replace("\n", "\n\n"))
    # a kid comment the generator would never emit (full line)
    assert blocks.source_roundtrips(prog, "# my own note\n" + base)
    # the BLOCK_MARKER deleted (content-based, not marker-based): still round-trips
    without_marker = "\n".join(base.split("\n")[1:])
    assert blocks.source_roundtrips(prog, without_marker)
    # an inline comment appended to a real line
    assert blocks.source_roundtrips(prog, base.replace("score = 7", "score = 7  # seven"))

    # --- divergent edits: must NOT round-trip (SHOULD graduate) ---
    # a changed literal (real semantic edit)
    assert not blocks.source_roundtrips(prog, base.replace("score = 7", "score = 999"))
    # a new statement blocks don't express
    assert not blocks.source_roundtrips(prog, base + "\nimport math\n")
    # a re-indent IS structural -> graduate
    assert not blocks.source_roundtrips(prog, base.replace("    score = 7", "        score = 7"))


def test_corrupt_program_never_graduates():
    # a tree that won't compile is treated as STILL round-tripping (conservative:
    # a transient compile error must never lock a kid out of blocks)
    bad = {"scripts": [{"t": "not_a_real_block"}]}
    assert blocks.source_roundtrips(bad, "anything at all") is True


# ----------------------------------------------------------------------------
# Layer 2: the stored graduated fact + journal rider (moy_carts.py)
# ----------------------------------------------------------------------------

def test_load_reads_graduated_flag(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Grad", root, src="def _draw():\n    cls(1)\n")
    assert cart["graduated"] is False           # default: not graduated
    # flip it in the manifest and reload
    assert moy_carts.set_graduated(cart, True) is True
    assert cart["graduated"] is True            # the cart dict is synced in place
    reloaded = moy_carts.load(cart["path"])
    assert reloaded["graduated"] is True
    # the other manifest fields survive the flag write
    assert reloaded["title"] == "Grad"
    assert reloaded["src"] == "def _draw():\n    cls(1)\n"


def test_set_graduated_idempotent_and_reversible(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("Grad2", root, src="def _draw():\n    cls(1)\n")
    assert moy_carts.set_graduated(cart, True) is True
    assert moy_carts.set_graduated(cart, True) is False    # idempotent: no rewrite
    assert moy_carts.load(cart["path"])["graduated"] is True
    assert moy_carts.set_graduated(cart, False) is True    # the journal back-door
    assert moy_carts.load(cart["path"])["graduated"] is False


def test_journal_grad_rider_flips_manifest_on_append_and_walk(tmp_path):
    # A main.py journal entry carries a `grad` rider; the append flips the manifest,
    # and undo/redo re-apply it so graduated rides the same one-way door as the source.
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("J", root, src="v1\n")
    path = cart["path"]
    moy_carts.journal_append(path, "main.py", "v1\n", grad=0)   # baseline, grad=0
    assert moy_carts.load(path)["graduated"] is False
    moy_carts.journal_append(path, "main.py", "v2\n", grad=1)   # graduating, grad=1
    assert moy_carts.load(path)["graduated"] is True            # the append flipped it
    # undo: source back to v1 AND graduated back to false
    assert moy_carts.journal_undo(path) == "main.py"
    assert (Path(path) / "main.py").read_text() == "v1\n"
    assert moy_carts.load(path)["graduated"] is False
    # redo: source to v2 AND graduated back to true
    assert moy_carts.journal_redo(path) == "main.py"
    assert (Path(path) / "main.py").read_text() == "v2\n"
    assert moy_carts.load(path)["graduated"] is True


def test_journal_entry_without_grad_leaves_flag_untouched(tmp_path):
    # a plain (non-main.py / pre-Stage-8) entry has no grad rider -> undo/redo never
    # guess at the graduated flag (they leave whatever the manifest holds).
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    cart = moy_carts.create("K", root, src="a\n")
    path = cart["path"]
    moy_carts.set_graduated(cart, True)
    moy_carts.journal_append(path, "main.py", "a\n")            # no grad rider
    moy_carts.journal_append(path, "main.py", "b\n")            # no grad rider
    assert moy_carts.journal_undo(path) == "main.py"
    assert moy_carts.load(path)["graduated"] is True            # untouched


# ----------------------------------------------------------------------------
# Layer 3: the console WIRING -- a code commit graduates a block cart, and an
# undo past the graduating commit un-graduates it (the full §8 edge matrix,
# driven through the real Workstation).
# ----------------------------------------------------------------------------

def _select(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            return


def _block_cart(tmp_path, title="Grad Cart"):
    """A workstation open on a genuine block-authored cart: blocks.json + a
    round-tripping main.py both on disk (a real program saved through the store),
    reopened so ws.cart['blocks'] is loaded."""
    from runtime import host_app
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    cart = moy_carts.create(title, root, type="game")
    prog = {
        "vars": ["score"],
        "scripts": [
            blocks.make_block("on_draw", children=[
                blocks.make_block("cls", {"color": "black"}),
                blocks.make_block("set_var", {"var": "score", "value": 7}),
            ]),
        ],
    }
    status, _ = moy_carts.save_blocks(cart, prog)
    assert status == moy_carts.SAVE_OK
    ws.launcher.items = moy_carts.scan(root)
    _select(ws, title)
    ws.open()
    assert ws.cart["blocks"] is not None
    return ws, root


def _commit_code(ws, text):
    """Drive a code-tab commit (the graduation trigger point) end to end."""
    ws.set_menu_view("code")
    ws.screen = "menu"
    ws.editor.set_text(text)
    return ws.save_code()


def test_template_block_cart_does_not_graduate(tmp_path):
    """§8 template-only: a block cart whose main.py is the untouched generated source
    stays blockifiable -- committing it does NOT graduate."""
    ws, _ = _block_cart(tmp_path)
    gen = ws.cart["src"]
    assert _commit_code(ws, gen) is True
    assert not ws.cart.get("graduated")
    assert moy_carts.load(ws.cart["path"])["graduated"] is False


def test_code_edit_past_vocabulary_graduates(tmp_path):
    """§8 marker kept + code hand-edited past the vocabulary -> GRADUATES (content-
    based: the BLOCK_MARKER line is left intact, yet the divergence is detected)."""
    ws, _ = _block_cart(tmp_path)
    diverged = ws.cart["src"].replace("score = 7", "score = 999")
    assert "Made with Moybyte blocks" in diverged            # marker deliberately kept
    assert _commit_code(ws, diverged) is True
    assert ws.cart["graduated"] is True                      # RAM synced immediately
    assert moy_carts.load(ws.cart["path"])["graduated"] is True   # persisted
    # blocks.json is left FROZEN as the read-only render source (not deleted)
    assert moy_carts.load_blocks(ws.cart["path"]) is not None


def test_cosmetic_then_reverted_never_graduates(tmp_path):
    """§8 cosmetic-only edit AND code-edited-then-reverted-byte-identical: neither
    graduates (aggressive normalization eats the cosmetics; a revert round-trips)."""
    ws, _ = _block_cart(tmp_path)
    gen = ws.cart["src"]
    cosmetic = gen.replace("\n\n", "\n\n\n", 1) + "# a comment the generator would not emit\n"
    assert _commit_code(ws, cosmetic) is True
    assert not ws.cart.get("graduated")
    # hand-revert to byte-identical generated source: still no graduation
    assert _commit_code(ws, gen) is True
    assert not ws.cart.get("graduated")
    assert moy_carts.load(ws.cart["path"])["graduated"] is False


def test_code_only_cart_never_graduates(tmp_path):
    """§8 a code-only cart (never had blocks) never 'graduates' -- it has no block
    program; today's protected mode, not a one-way door."""
    from runtime import host_app
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    cart = moy_carts.create("Plain", root, src="def _draw():\n    cls(1)\n")
    ws.launcher.items = moy_carts.scan(root)
    _select(ws, "Plain")
    ws.open()
    assert ws.cart["blocks"] is None
    assert _commit_code(ws, "def _draw():\n    cls(2)  # edited freely\n") is True
    assert not ws.cart.get("graduated")
    assert moy_carts.load(cart["path"])["graduated"] is False


def test_undo_past_graduation_restores_source_and_flag(tmp_path):
    """§8 undo past a graduating commit -> source AND graduated:false BOTH restored;
    redo re-graduates (the one honest back-door, riding the Stage-7 journal)."""
    ws, _ = _block_cart(tmp_path)
    path = ws.cart["path"]
    diverged = ws.cart["src"].replace("score = 7", "score = 999")
    assert _commit_code(ws, diverged) is True
    assert ws.cart["graduated"] is True
    assert "score = 999" in (Path(path) / "main.py").read_text()

    # undo the graduating commit: source falls back to the block-generated baseline
    # AND graduated flips false, in ONE step
    assert ws.undo() is True
    restored = (Path(path) / "main.py").read_text()
    assert "score = 999" not in restored and "score = 7" in restored
    assert ws.cart["graduated"] is False
    assert moy_carts.load(path)["graduated"] is False

    # redo re-applies the divergence AND re-graduates
    assert ws.redo() is True
    assert "score = 999" in (Path(path) / "main.py").read_text()
    assert ws.cart["graduated"] is True
    assert moy_carts.load(path)["graduated"] is True


# ----------------------------------------------------------------------------
# Layer 4: the Blocks tab renders a graduated cart read-only + celebrates.
# ----------------------------------------------------------------------------

def _graduate(ws):
    diverged = ws.cart["src"].replace("score = 7", "score = 999")
    assert _commit_code(ws, diverged) is True
    assert ws.cart["graduated"] is True
    return diverged


def test_graduated_blocks_tab_is_read_only_and_celebrates(tmp_path):
    """§8 on graduation: the Blocks tab still opens on the FROZEN program (the read-
    only render source), refuses SAVE (won't overwrite the diverged main.py), and
    renders the celebration banner without error."""
    ws, _ = _block_cart(tmp_path)
    _graduate(ws)
    ws._open_blocks()
    assert ws.menu_view == "blocks"
    assert ws.block_ui.blk_graduated is True
    assert ws.block_ui.blocks_ed is not None            # frozen program still renders
    # SAVE refuses -- the one-way door
    assert ws.block_ui.save_blocks() is False
    assert ws.block_ui.blk_status == "LEVELED UP TO CODE"
    # the diverged main.py is untouched by the refused save
    assert "score = 999" in (Path(ws.cart["path"]) / "main.py").read_text()
    # renders cleanly (the celebration banner draws)
    for _ in range(2):
        ws.frame(1 / 30)
    assert ws.cart_error is None


def test_graduated_graduate_button_opens_diverged_code(tmp_path):
    """§8 the CODE rung on a graduated cart opens the kid's DIVERGED source (never
    recompiles the frozen blocks over it)."""
    ws, _ = _block_cart(tmp_path)
    _graduate(ws)
    ws._open_blocks()
    assert ws.block_ui.blk_graduated is True
    ws.block_ui.graduate_to_code()
    assert ws.menu_view == "code" and ws.editor is not None
    assert "score = 999" in ws.editor.text()            # the kid's code, not a re-gen


def test_non_graduated_block_cart_stays_editable(tmp_path):
    """Regression: a block cart that has NOT graduated is neither protected nor
    graduated -- blocks stay fully editable + saveable (the round trip is intact)."""
    ws, _ = _block_cart(tmp_path)
    ws._open_blocks()
    assert ws.block_ui.blk_graduated is False
    assert ws.block_ui.blk_protect is False
    assert ws.block_ui.save_blocks() is True


# ----------------------------------------------------------------------------
# Layer 3b (#78): a DECK-authored story cart (Storybook) graduates through the
# EXACT SAME manifest-flag + journal-rider + undo mechanism as a block cart --
# the "full graduated-manifest integration" #78 asked for, replacing the old
# v1 hash-compare-only read-only refusal. runtime/project.py's _journal_code
# now recognizes a story cart's deck.json as an origin (mirroring blocks.json)
# via _deck_for_graduation/_journal_code_toward.
# ----------------------------------------------------------------------------

def _story_cart(tmp_path, title="Story Cart"):
    """A workstation open on a genuine deck-authored story cart: deck.json + its
    own regenerated main.py both on disk -- built directly through the store
    (the mirror of _block_cart's real block-authored setup, and of Storybook's
    own _new_story), then reopened so ws.cart carries no blocks.json (the deck
    branch is only reached when `prog` is None)."""
    from runtime import host_app
    from runtime.storybook_app import deck_to_code
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    deck = {"format": "moydeck-v1",
            "pages": [{"bg": "black", "art": None,
                       "text": ["Once upon a time..."]}]}
    src = deck_to_code(deck, title)
    cart = moy_carts.create(title, root, src=src, type="story")
    moy_carts.save_deck(cart, json.dumps(deck))
    ws.launcher.items = moy_carts.scan(root)
    _select(ws, title)
    ws.open()
    assert ws.cart["blocks"] is None
    assert ws.cart["type"] == "story"
    return ws, root


def test_story_template_does_not_graduate(tmp_path):
    """A story cart whose main.py is the deck's own untouched generated source
    stays deck-editable (Storybook) -- committing it does NOT graduate."""
    ws, _ = _story_cart(tmp_path)
    gen = ws.cart["src"]
    assert _commit_code(ws, gen) is True
    assert not ws.cart.get("graduated")
    assert moy_carts.load(ws.cart["path"])["graduated"] is False


def test_story_hand_edit_past_deck_graduates(tmp_path):
    """A story's main.py hand-edited past the deck's page/art/bg vocabulary (in
    the Editor's Code tab) GRADUATES it -- persisted, RAM-synced, deck.json left
    frozen as the read-only render source (not deleted)."""
    ws, _ = _story_cart(tmp_path)
    diverged = ws.cart["src"] + "\nSPEED = 99\n"
    assert _commit_code(ws, diverged) is True
    assert ws.cart["graduated"] is True                      # RAM synced immediately
    assert moy_carts.load(ws.cart["path"])["graduated"] is True   # persisted
    assert moy_carts.load_deck(ws.cart) is not None


def test_story_undo_past_graduation_restores_source_and_flag(tmp_path):
    """Undo past a story's graduating commit -> source AND graduated:false BOTH
    restored; redo re-graduates -- the same one honest back-door blocks get,
    riding the Stage-7 journal generically (no deck-specific undo code needed)."""
    ws, _ = _story_cart(tmp_path)
    path = ws.cart["path"]
    diverged = ws.cart["src"] + "\nSPEED = 99\n"
    assert _commit_code(ws, diverged) is True
    assert ws.cart["graduated"] is True
    assert "SPEED = 99" in (Path(path) / "main.py").read_text()

    assert ws.undo() is True
    restored = (Path(path) / "main.py").read_text()
    assert "SPEED = 99" not in restored
    assert ws.cart["graduated"] is False
    assert moy_carts.load(path)["graduated"] is False

    assert ws.redo() is True
    assert "SPEED = 99" in (Path(path) / "main.py").read_text()
    assert ws.cart["graduated"] is True
    assert moy_carts.load(path)["graduated"] is True


def test_story_already_graduated_stays_sticky(tmp_path):
    """§8's one-way door applies to decks too: once graduated, a FURTHER code
    commit stays graduated even if it happens to match the deck's regenerated
    source again (sticky, not re-derived every commit)."""
    ws, _ = _story_cart(tmp_path)
    gen = ws.cart["src"]
    diverged = gen + "\nSPEED = 99\n"
    assert _commit_code(ws, diverged) is True
    assert ws.cart["graduated"] is True
    # Hand-revert to byte-identical generated source: STILL graduated (sticky).
    assert _commit_code(ws, gen) is True
    assert ws.cart["graduated"] is True
    assert moy_carts.load(ws.cart["path"])["graduated"] is True
