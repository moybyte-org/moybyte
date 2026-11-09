"""`moy_seed.seed_folders`: what a store of RECORD (the browser's on a static
host) takes from the served cart folders, decided in the VM -- the browser's
worker writes what it reports into OPFS and decides nothing itself
(docs/moycore_direction.md section 3).

Run by CPython over the twin store and by the desktop MicroPython over the
native one, which is what the wasm links: the same report and the same tree.
"""

import json
import os
import subprocess
import sys

from unix_mp import require_unix_mp, runtime_tree

DRIVER = r'''
import sys, json
sys.path.insert(0, @RUNTIME@)
import moy_carts
print("native", getattr(moy_carts, "_store", None) is not None)
print(json.dumps(moy_carts.seed_folders(@SRC@, @ROOT@)))
'''

SYS = {"system": True}


def _man(title, version=None, ven=None, **top):
    m = {"title": title}
    if version is not None:
        m["version"] = version
    if ven is not None:
        m["moybyte"] = ven
    m.update(top)
    return json.dumps(m)


LOCAL = {
    "star.moy/manifest.json": _man("Star", ven=SYS),
    "star.moy/main.py": "old code",
    "star.moy/old.lua": "gone",
    "star.moy/pmem.json": "[7]",
    "star.moy/journal/journal.jsonl": "{}",
    "same.moy/manifest.json": _man("Same", 3, SYS),
    "same.moy/main.py": "kid's edit",
    "mine.moy/manifest.json": _man("Mine"),
    "torn.moy/main.py": "half a seed",
}

BUNDLE = {
    "star.moy/manifest.json": _man("Star", 9, SYS),
    "star.moy/main.py": "new code",
    "star.moy/pmem.json": "[]",
    "star.moy/config.json": "{}",
    "same.moy/manifest.json": _man("Same", 3, SYS),
    "same.moy/main.py": "seed",
    "moybyte.get_carts.moy/manifest.json": _man("Get Carts", ven=SYS),
    "moybyte.get_carts.moy/main.py": "pass",
    "moybyte.get_carts.moy/cover.png": b"\x89PNG\x00\xff",
    "moybyte.get_carts.moy/images/a.moyimg": "img",
    "demo.moy/manifest.json": _man("Demo"),
    "demo.moy/main.py": "x",
    "loose.moy/manifest.json": _man("Loose", system=True),
    "loose.moy/main.py": "x",
    "torn.moy/manifest.json": _man("Torn"),
    "torn.moy/main.py": "whole",
    "bad.moy/manifest.json": "not json",
}


def _write(root, files):
    for rel, data in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode())


def _tree(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root).replace(os.sep, "/")] = fh.read()
    return out


def _seed(cmd, tmp, local):
    src, root = str(tmp / "bundle"), str(tmp / "carts")
    _write(src, BUNDLE)
    os.makedirs(root, exist_ok=True)
    _write(root, local)
    script = (DRIVER.replace("@RUNTIME@", repr(runtime_tree()))
              .replace("@SRC@", repr(src)).replace("@ROOT@", repr(root)))
    out = subprocess.run(cmd + ["-c", script], capture_output=True, text=True,
                         timeout=300)
    assert out.returncode == 0, (out.stdout + out.stderr)[-3000:]
    lines = [ln for ln in out.stdout.splitlines()
             if not ln.startswith("Moybyte cart ")]
    return lines[0], json.loads(lines[1]), _tree(root)


def _check_returning(got, tree):
    wrote, removed = got["wrote"], got["removed"]
    assert got["first"] is False
    assert {"moybyte.get_carts.moy/cover.png", "moybyte.get_carts.moy/main.py",
            "moybyte.get_carts.moy/images/a.moyimg",
            "moybyte.get_carts.moy/manifest.json"} <= set(wrote), wrote
    assert tree["moybyte.get_carts.moy/cover.png"] == BUNDLE[
        "moybyte.get_carts.moy/cover.png"]
    # A newer system cart is replaced, its manifest last; its saves stay,
    # tuning it never had comes with it, its other files go.
    star = [w for w in wrote if w.startswith("star.moy/")]
    assert star[-1] == "star.moy/manifest.json"
    assert tree["star.moy/main.py"] == b"new code"
    assert "star.moy/pmem.json" not in wrote and tree["star.moy/pmem.json"] == b"[7]"
    assert "star.moy/config.json" in wrote
    assert sorted(r for r in removed if r.startswith("star.moy/")) == [
        "star.moy/journal/journal.jsonl", "star.moy/old.lua"]
    for r in removed:
        assert r not in tree
    # Same version: untouched. A non-system cart: never added. A top-level
    # "system" is not Moybyte's field. A seed a reload cut: written whole.
    assert not [w for w in wrote if w.startswith(("same.moy/", "demo.moy/",
                                                   "loose.moy/", "bad.moy/"))]
    assert tree["same.moy/main.py"] == b"kid's edit"
    assert "mine.moy/manifest.json" in tree
    assert tree["torn.moy/main.py"] == b"whole" and "torn.moy/manifest.json" in tree
    for w in wrote:
        assert w in tree


def test_a_returning_store_takes_only_its_system_carts(tmp_path):
    native, got, tree = _seed([sys.executable], tmp_path, LOCAL)
    assert native == "native False"
    _check_returning(got, tree)


def test_a_first_visit_takes_every_cart(tmp_path):
    _native, got, tree = _seed([sys.executable], tmp_path, {})
    assert got["first"] is True and got["removed"] == []
    for rel in BUNDLE:
        if not rel.startswith("bad.moy/"):
            assert rel in got["wrote"] and tree[rel] == (
                BUNDLE[rel] if isinstance(BUNDLE[rel], bytes) else BUNDLE[rel].encode())
    assert "bad.moy/manifest.json" not in tree


def test_the_native_store_seeds_the_same(tmp_path):
    exe = require_unix_mp(
        "moy_store", why="seed_folders over the native store the wasm links.")
    for local, tag in ((LOCAL, "back"), ({}, "first")):
        vm = _seed([exe, "-X", "heapsize=16M"], tmp_path / ("vm-" + tag), local)
        cp = _seed([sys.executable], tmp_path / ("cp-" + tag), local)
        assert vm[0] == "native True"
        assert vm[1] == cp[1] and vm[2] == cp[2], tag
