"""The documents still describe this tree.

`tools/check_docs.py` is the whole check; this is the wrapper that puts it in
`make test`, so a rename that strands a path in CLAUDE.md fails on the same push
as the rename rather than at the next session that trusts it.

Why it exists: on 2026-08-08 a review found CLAUDE.md claiming six `moy_gfx`
verbs were libmoy's when nine were -- the reversal had been recorded in
`native/moy_gfx/libmoy/UPSTREAM.md` the same day -- and pointing at
`tools/command_canvas.py` four commits after that file was deleted. CLAUDE.md is
the first thing every session reads, so a stale sentence in it does not mislead
one reader; it misleads every future session until somebody happens to notice.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_docs_match_the_tree():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check_docs.py")],
                       cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, "\n" + r.stdout + r.stderr


def test_archived_firmware_runtime_contract_names_the_lilygo_target():
    # (Merged from the one-test test_docs.py, 2026-08-18.)
    text = (ROOT / "docs" / "history" / "firmware_runtime_contract.md").read_text(
        encoding="utf-8")
    assert "LilyGO T-Deck Plus" in text
    assert "moybyte check-portable" in text
    assert "lilygo_t_deck_plus" in text
