"""A site-mode browser keeps its theme, its Settings and its badges across a
reload, as a board keeps them across a reboot (owner, 2026-10-20: "Why do they
reset? They should use the store, achievements as well").

Two page loads of the REAL console (dist/worker.js and its wasm, through
`system_persist_test.mjs`) on a static host, sharing one fake OPFS the way two
loads share an origin's: the first boots on a stored theme and SHOW FPS off,
opens a cart -- which earns First Steps and records the run in system.json --
and the sweep carries both documents into the store; the second boots from
that store alone. The store's own crash-safety and path rules are
`store_test.mjs`'s (tests/test_web_store.py runs it); the watcher's are
tests/test_moy_sync.py's.

Needs a built dist/ (firmware/web_runner/build.sh) and node; skipped without
them, like the worker protocol suite.
"""

import base64
import json
import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_RUNNER = os.path.abspath(os.path.join(_HERE, os.pardir, "firmware", "web_runner"))
_CART = "moybyte.star_catcher.moy"


def _have_dist():
    return all(os.path.isfile(os.path.join(_RUNNER, "dist", n))
               for n in ("worker.js", "carts.json", "micropython.mjs"))


pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None, reason="node not installed"),
    pytest.mark.skipif(not _have_dist(), reason="web_runner dist/ not built"),
]


def _load(search, tree):
    """One page load over the store in `tree`: (its state, its persist lines)."""
    p = subprocess.run(["node", "system_persist_test.mjs", search, str(tree)],
                       cwd=_RUNNER, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, "the load %s failed:\n%s\n%s" % (
        search, p.stdout[-3000:], p.stderr[-2000:])
    tagged = {}
    for line in p.stdout.splitlines():
        tag, _, rest = line.partition(" ")
        if tag in ("STATE", "PERSIST", "BATCHES"):
            tagged[tag] = json.loads(rest)
    return tagged


def _doc(tree, rel):
    raw = json.loads(tree.read_text()).get(rel)
    return None if raw is None else json.loads(base64.b64decode(raw))


def test_theme_settings_and_badges_survive_a_reload(tmp_path):
    tree = tmp_path / "opfs.json"
    stored = {"theme": "blocks", "theme_variant": "dark", "show_fps": False}
    tree.write_text(json.dumps({"system/system.json": base64.b64encode(
        json.dumps(stored).encode()).decode()}))

    first = _load("?desktop=1&cart=" + _CART, tree)
    st = first["STATE"]
    assert st["theme"]["name"] == "blocks" and st["theme"]["variant"] == "dark", \
        "the stored theme was not the one the console booted on: %r" % st["theme"]
    assert st["show_fps"] is False, "the stored SHOW FPS was overridden at boot"
    assert "first_open" in st["ach"], st["ach"]

    # The first load's own writes reached the store, the stored rows with them.
    sysdoc = _doc(tree, "system/system.json")
    assert sysdoc["theme"] == "blocks" and sysdoc["show_fps"] is False, sysdoc
    assert sysdoc.get("desk_mru"), \
        "the run this load recorded never reached the store: %r\n%r" % (
            sysdoc, first["BATCHES"])
    assert "first_open" in _doc(tree, "system/achievements.json")["unlocked"]
    names = json.loads(tree.read_text())
    assert not [n for n in names if n.startswith("system/") and "~" in n], \
        "a committed write left its staging behind: %r" % sorted(names)
    assert not [n for n in names if n.startswith("carts/") and n.count("/") == 1], \
        "a console document landed in the carts store: %r" % sorted(names)

    second = _load("?desktop=1", tree)
    assert second["PERSIST"]["d"].startswith("loaded"), second["PERSIST"]
    st = second["STATE"]
    assert st["theme"]["name"] == "blocks" and st["theme"]["variant"] == "dark", st["theme"]
    assert st["show_fps"] is False
    assert "first_open" in st["ach"], st["ach"]
    assert st["system"].get("desk_mru") == sysdoc["desk_mru"]
