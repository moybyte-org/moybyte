"""Stage 8 (docs/shell_ux_technical_plan_v1.md Section 3): blocks<->code
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
