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


def _problems_for(monkeypatch, text, doc="docs/fixture.md"):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import check_docs
    finally:
        sys.path.remove(str(ROOT / "tools"))
    monkeypatch.setattr(check_docs, "docs", lambda: [doc])
    monkeypatch.setattr(check_docs, "read", lambda rel: text)
    monkeypatch.setattr(check_docs, "PROBLEMS", [])
    check_docs.check_paths()
    return check_docs.PROBLEMS


def test_a_path_with_a_directory_must_exist_there(monkeypatch):
    # runtime/run/player.py is real; runtime/player.py is where it used to be,
    # and a file of that BASENAME exists elsewhere, which must not rescue it.
    assert _problems_for(monkeypatch, "see `runtime/run/player.py`.") == []
    stale = _problems_for(monkeypatch, "see `runtime/player.py`.")
    assert len(stale) == 1 and "runtime/player.py" in stale[0]


def test_a_doc_path_moved_to_history_must_say_so(monkeypatch):
    stale = _problems_for(monkeypatch, "see `docs/kernel_toolkit_2026-10.md`.")
    assert len(stale) == 1
    assert _problems_for(
        monkeypatch, "see `docs/history/kernel_toolkit_2026-10.md`.") == []


def test_a_vendored_doc_may_name_its_upstream_paths(monkeypatch):
    text = "compiled by `runtime/audio_binding.py`."
    assert _problems_for(monkeypatch, text) != []
    assert _problems_for(
        monkeypatch, text, "native/moy_audio/libmoy/UPSTREAM.md") == []
