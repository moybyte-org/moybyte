"""The store's cart interface (runtime/moy_catalogue.py): carts by handle.

What the native store exposes in sprint 1b, call for call, so these are the
host half of its parity tests: the boot, the shelf, the roles and the undo
walk reach a cart only through these calls, and each says here what it does
with a handle -- including the stale one, which must fail loudly rather than
name another cart. tests/test_semantic_traces.py replays the same vocabulary
on the boards' VM.

The last test is the line itself: no module outside the store reads, makes,
copies or removes a cart through moy_carts' path-level bodies. A caller that
did would hold a cart the index does not name, which is the dict a native
store cannot give back.
"""

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from runtime import moy_carts
from runtime import moy_catalogue as cat
from runtime.boot_carts import BootCarts
from runtime.moy_index import StaleHandle

ROOT = Path(__file__).resolve().parent.parent
SRC = "def _draw():\n    cls(1)\n"


def _store(tmp_path, titles=("Beta", "Alpha")):
    root = str(tmp_path / "carts")
    cat.ensure_dirs(root)
    made = [cat.create(t, root, src=SRC) for t in titles]
    return root, made


def test_every_cart_the_interface_returns_carries_its_handle(tmp_path):
    root, made = _store(tmp_path)
    assert all(c["h"] == cat.handle(c["path"]) for c in made)
    entries = cat.catalogue(root)
    assert [e["title"] for e in entries] == ["Alpha", "Beta"]   # folder order
    for e in entries:
        assert cat.valid(e["h"]) and cat.path(e["h"]) == e["path"]
        assert cat.entry(e["h"])["h"] == e["h"]
        whole = cat.load(e["h"])
        assert whole["h"] == e["h"] and whole["src"] == SRC
        assert "src" not in e                        # an entry has no payloads
    assert cat.new(root)["h"] == cat.handle(root + "/new_cart.moy")


def test_a_rescan_keeps_a_folders_handle(tmp_path):
    root, made = _store(tmp_path)
    first = {e["path"]: e["h"] for e in cat.catalogue(root)}
    again = {e["path"]: e["h"] for e in cat.catalogue(root)}
    assert first == again == {c["path"]: c["h"] for c in made}


def test_a_deleted_cart_is_a_stale_handle(tmp_path):
    root, made = _store(tmp_path)
    h = made[0]["h"]
    cat.delete(h)
    assert not os.path.exists(made[0]["path"])
    assert not cat.valid(h)
    for call in (cat.load, cat.entry, cat.path, cat.delete):
        with pytest.raises(StaleHandle):
            call(h)
    with pytest.raises(StaleHandle):
        cat.duplicate(h, root)
    assert [e["title"] for e in cat.catalogue(root)] == ["Alpha"]


def test_a_folder_removed_behind_the_store_goes_stale_at_the_next_scan(tmp_path):
    """The sync wire or a card swap removes a folder the index names: the row
    answers None (no cart there) until a scan reconciles it away."""
    root, made = _store(tmp_path)
    h = made[1]["h"]
    shutil.rmtree(made[1]["path"])
    assert cat.valid(h) and cat.load(h) is None and cat.entry(h) is None
    cat.catalogue(root)
    assert not cat.valid(h)
    with pytest.raises(StaleHandle):
        cat.load(h)


def test_a_root_that_will_not_list_changes_nothing(tmp_path):
    root, made = _store(tmp_path)
    assert cat.catalogue(str(tmp_path / "no-such-root")) == []
    assert all(cat.valid(c["h"]) for c in made)


def test_a_catalogue_reconciles_its_own_root_alone(tmp_path):
    """A row's key is its root and its folder: a second store's catalogue
    leaves the first one's rows, and the same folder name in each is two
    rows."""
    root, made = _store(tmp_path)
    other = str(tmp_path / "other" / "carts")
    cat.ensure_dirs(other)
    twin = cat.create("Alpha", other, src=SRC)
    assert [e["title"] for e in cat.catalogue(other)] == ["Alpha"]
    assert all(cat.valid(c["h"]) for c in made)
    assert twin["h"] != made[1]["h"]
    assert cat.path(twin["h"]) == twin["path"] == other + "/alpha.moy"
    assert cat.path(made[1]["h"]) == root + "/alpha.moy"
    assert cat.handle(other + "/alpha.moy") == twin["h"]
    back = cat.catalogue(root)
    assert [e["h"] for e in back] == [made[1]["h"], made[0]["h"]]
    assert cat.valid(twin["h"])


def test_a_full_root_table_gives_up_the_root_named_longest_ago(tmp_path):
    roots = []
    for i in range(cat.ROOTS + 1):
        r = str(tmp_path / ("s%d" % i) / "carts")
        cat.ensure_dirs(r)
        roots.append((r, cat.create("Cart", r, src=SRC)["h"]))
    first, rest = roots[0], roots[1:]
    assert not cat.valid(first[1])
    with pytest.raises(StaleHandle):
        cat.load(first[1])
    assert all(cat.valid(h) for _r, h in rest)
    assert [e["title"] for e in cat.catalogue(first[0])] == ["Cart"]
    assert not cat.valid(rest[0][1]) and cat.valid(rest[1][1])


def test_duplicate_copies_the_cart_the_store_holds(tmp_path):
    """The copy is read from the card under the handle, not from a dict a
    caller holds -- a native store has no dict to read."""
    root, made = _store(tmp_path)
    src = made[1]                                    # Alpha
    src["src"] = "an edit that never reached the card"
    dup = cat.duplicate(src["h"], root)
    assert dup["title"] == "Alpha copy" and dup["src"] == SRC
    assert cat.valid(dup["h"]) and dup["h"] != src["h"]


def test_a_handle_is_never_a_type_the_store_guesses_at(tmp_path):
    _store(tmp_path)
    for junk in (None, "1", 4096.0):
        assert not cat.valid(junk)
        with pytest.raises(TypeError):
            cat.load(junk)
    with pytest.raises(StaleHandle):
        cat.load(0)


def test_the_boot_reads_the_shelf_through_the_interface(tmp_path):
    """BootCarts is handed the interface on every board (desktop_spine), and the
    shelf it returns is handle-carrying entries."""

    class Boot(BootCarts):
        def say(self, msg):
            pass

        def note(self, msg, frac=None):
            pass

    root = str(tmp_path / "carts")
    seed = [{"title": "Seeded", "type": "game", "src": SRC, "cfg": {},
             "edit": []}]
    carts, got_root = Boot().load_carts(cat, seed, root=root)
    assert got_root == root and [c["title"] for c in carts] == ["Seeded"]
    assert cat.path(carts[0]["h"]) == carts[0]["path"]


# -- the line -----------------------------------------------------------------

# moy_carts' path-level bodies a cart passes through, the journal's included:
# the shell walks and appends to a cart's journal by its handle. Reading a
# cart's FILES by path (save_*, load_pmem, load_deck) is not on this list: those
# leave nothing in the store between calls. The sync wire and Files' project
# files start from a path and reach moy_journal itself.
_CART_BODIES = {"catalogue", "entry", "load", "scan", "create",
                "new_from_template", "duplicate", "delete", "journal_append",
                "journal_undo", "journal_redo", "journal_can_undo",
                "journal_can_redo", "journal_compact"}
# The store itself; and moy_seed, which writes the seed roster's folders.
_STORE = {"runtime/moy_carts.py", "runtime/moy_catalogue.py",
          "runtime/moy_seed.py"}


def _production_sources():
    """Every tracked Python file a console, the Zero, the browser or a host
    tool runs -- never a build-staged copy."""
    out = subprocess.run(
        ["git", "ls-files", "--", "runtime/*.py", "device/*.py", "tools/*.py",
         "firmware/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.splitlines() if line]


def _names_the_store(node):
    """True when `node` is `moy_carts`, `<x>.moy_carts` or `<x>.carts_store`."""
    if isinstance(node, ast.Name):
        return node.id == "moy_carts"
    if isinstance(node, ast.Attribute):
        return node.attr in ("moy_carts", "carts_store")
    return False


def test_no_caller_reaches_a_cart_around_the_interface():
    bad = []
    for path in _production_sources():
        rel = path.relative_to(ROOT).as_posix()
        if rel in _STORE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr in _CART_BODIES
                    and _names_the_store(node.value)):
                bad.append("%s:%d %s" % (rel, node.lineno, node.attr))
            elif (isinstance(node, ast.ImportFrom) and node.module
                    and node.module.split(".")[-1] == "moy_carts"):
                bad += ["%s:%d %s" % (rel, node.lineno, a.name)
                        for a in node.names if a.name in _CART_BODIES]
    assert not bad, ("a cart reached around moy_catalogue (use its handle "
                     "calls): " + ", ".join(bad))


def test_the_line_sees_a_caller_that_crosses_it():
    """The guard above is only worth its silence if it can fire."""
    tree = ast.parse("ws.carts_store.load(p)\nmoy_carts.catalogue(r)\n"
                     "host_app.moy_carts.scan(r)\n")
    hits = [n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in _CART_BODIES
            and _names_the_store(n.value)]
    assert sorted(hits) == ["catalogue", "load", "scan"]
