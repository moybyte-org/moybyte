"""A rebuild of an unchanged tree compiles nothing, and a changed input is
always rebuilt.

Every board build and the browser console's stage their inputs by copying,
and what compiles them (ninja, MicroPython's makemanifest, make) judges by
mtime. So the copies are content-keyed (`board_config.put`/`mirror`): an
unchanged file keeps its mtime, and a changed one is written NOW, newer than
anything compiled from its old text, wherever its source's mtime stands. The
tests here hold both halves; tests/test_esp32_build_lib.py holds the build
library's guards (the frozen set, the submodules step, the configure lock).
"""

import gzip
import importlib.util
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import board_config  # noqa: E402
import zopfli_gz  # noqa: E402

OLD = 1_000_000                   # an mtime long before any build


def _age(path):
    os.utime(path, (OLD, OLD))


def test_put_leaves_an_unchanged_file_alone(tmp_path):
    src, dest = tmp_path / "a.py", tmp_path / "out" / "a.py"
    src.write_text("A = 1\n")
    assert board_config.put(src, dest) is True
    _age(dest)
    assert board_config.put(src, dest) is False
    assert dest.stat().st_mtime == OLD


def test_put_writes_a_changed_file_newer_than_anything_built_before(tmp_path):
    """The source's own mtime is OLDER than the object built from the old
    text (a checkout, a copy that kept its time): the staged copy is still
    newer, so the rebuild happens."""
    src, dest = tmp_path / "a.py", tmp_path / "a_staged.py"
    src.write_text("A = 1\n")
    board_config.put(src, dest)
    built = time.time() - 5
    src.write_text("A = 2\n")
    _age(src)
    assert board_config.put(src, dest) is True
    assert dest.read_text() == "A = 2\n"
    assert dest.stat().st_mtime > built


def test_put_carries_the_mode(tmp_path):
    src, dest = tmp_path / "run.sh", tmp_path / "out.sh"
    src.write_text("#!/bin/sh\n")
    src.chmod(0o755)
    board_config.put(src, dest)
    assert dest.stat().st_mode & 0o777 == 0o755


def _tree(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_mirror_makes_dest_the_source_and_keeps_what_did_not_change(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _tree(src, {"a.c": "a", "sub/b.c": "b", "gone.c": "x",
                "__pycache__/c.pyc": "junk"})
    board_config.mirror(src, dest, ignore=("__pycache__",))
    for p in dest.rglob("*"):
        _age(p)
    (src / "gone.c").unlink()
    shutil.rmtree(src / "sub")
    _tree(src, {"a.c": "a", "new/d.c": "d"})
    board_config.mirror(src, dest, ignore=("__pycache__",))
    got = sorted(str(p.relative_to(dest)) for p in dest.rglob("*"))
    assert got == ["a.c", "new", "new/d.c"]
    assert (dest / "a.c").stat().st_mtime == OLD


def test_mirror_keeps_the_files_a_build_generates_into_the_copy(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _tree(src, {"m.c": "m", "blob.gen.c": "another build's text"})
    gen = board_config.GENERATED
    board_config.mirror(src, dest, ignore=(gen,), keep=(gen,))
    assert not (dest / "blob.gen.c").exists()
    (dest / "blob.gen.c").write_text("this build's text")
    board_config.mirror(src, dest, ignore=(gen,), keep=(gen,))
    assert (dest / "blob.gen.c").read_text() == "this build's text"


def test_restaging_an_unchanged_board_rewrites_nothing(tmp_path):
    """The no-change rebuild: stage-native and stage twice, and every staged
    file -- native sources, the cmake include list, the Python modules --
    keeps the mtime the first run gave it."""
    tdeck = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
    work = tmp_path / "board"
    work.mkdir()
    shutil.copyfile(tdeck / "board.toml", work / "board.toml")
    board_config.stage_native(work, ROOT, quiet=True)
    board_config.stage(work, ROOT, quiet=True)
    files = [p for p in work.rglob("*") if p.is_file() and p.name != "board.toml"]
    assert len(files) > 100
    for p in files:
        _age(p)
    board_config.stage_native(work, ROOT, quiet=True)
    board_config.stage(work, ROOT, quiet=True)
    moved = [str(p.relative_to(work)) for p in files
             if p.stat().st_mtime != OLD]
    assert moved == []


def test_the_mirror_verb_copies_a_tree_or_one_file(tmp_path):
    src = tmp_path / "src"
    _tree(src, {"a.h": "a", "x.pyc": "junk"})
    assert board_config.main(["board_config.py", "mirror", str(src),
                              str(tmp_path / "d"), "--ignore", "*.pyc"]) == 0
    assert sorted(os.listdir(tmp_path / "d")) == ["a.h"]
    assert board_config.main(["board_config.py", "mirror", str(src / "a.h"),
                              str(tmp_path / "one.h")]) == 0
    assert (tmp_path / "one.h").read_text() == "a"


# -- the browser console's compressed assets ------------------------------------

needs_zopfli = pytest.mark.skipif(
    importlib.util.find_spec("zopfli") is None,
    reason="pip install 'zopfli==0.4.3' (the dev extra)")


@needs_zopfli
def test_an_unchanged_asset_comes_from_the_cache_and_a_changed_one_does_not(
        tmp_path, monkeypatch):
    cache = tmp_path / "gz"
    a = tmp_path / "a.mjs"
    a.write_bytes(b"export const a = 1;\n" * 200)
    zopfli_gz.main([str(a)], str(cache))
    calls = []
    real = zopfli_gz.gz
    monkeypatch.setattr(zopfli_gz, "gz", lambda raw: calls.append(raw) or real(raw))
    zopfli_gz.main([str(a)], str(cache))
    assert calls == []
    a.write_bytes(b"export const a = 2;\n" * 200)
    zopfli_gz.main([str(a)], str(cache))
    assert len(calls) == 1
    assert gzip.decompress((tmp_path / "a.mjs.gz").read_bytes()) == a.read_bytes()
    assert len(os.listdir(cache)) == 1          # the last run's, nothing older


@needs_zopfli
def test_a_cache_entry_that_does_not_inflate_to_the_input_is_recompressed(
        tmp_path):
    cache = tmp_path / "gz"
    a = tmp_path / "a.mjs"
    a.write_bytes(b"export const a = 1;\n" * 200)
    zopfli_gz.main([str(a)], str(cache))
    (entry,) = cache.iterdir()
    entry.write_bytes(gzip.compress(b"something else"))
    zopfli_gz.main([str(a)], str(cache))
    assert gzip.decompress((tmp_path / "a.mjs.gz").read_bytes()) == a.read_bytes()


def test_the_cache_key_names_the_passes_and_the_input():
    k = zopfli_gz.cache_key(b"x")
    assert k == zopfli_gz.cache_key(b"x") != zopfli_gz.cache_key(b"y")
