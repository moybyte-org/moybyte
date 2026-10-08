"""A compiled cart's written files in C (native/moy_store/moy_files.c) held to
runtime/cart_files.py, the reference, rule for rule: the key a path is kept
under and back, where a cart's files live, the whole-or-nothing write, the
recovery of what a power loss left, erase, and the listing a cart walks.

Each session runs the same script against two copies of one store, one by
the Python and one by the C, and the two stores must end byte for byte the
same, with every answer on the way equal."""

import os
import random

import pytest

from runtime import cart_files

moy_play = pytest.importorskip("runtime.moy_play")


def _lib_or_skip():
    try:
        moy_play._files_lib()
    except ImportError as exc:
        pytest.skip(str(exc))


PATHS = [b"save", b"Save", b"save.dat", b".hidden", b"trail.", b"a.b.c", b"levels/1",
         b"con", b"CON.txt", b"com1", b"com0", b"lpt9.x", b"nul.", b"aux", b"prn2",
         b"a~b", b"%", b"\x00\xff", "été".encode(), b"-_09az", b"x" * 80]


def test_the_key_and_its_way_back_are_the_reference_s():
    _lib_or_skip()
    rng = random.Random(7)
    paths = list(PATHS) + [bytes(rng.randrange(256) for _ in range(rng.randrange(1, 12)))
                           for _ in range(300)]
    for p in paths:
        k = cart_files.key(p)
        assert moy_play.files_key(p) == k, p
        assert moy_play.files_path_of(k) == cart_files.path_of(k) == p, (p, k)
    for name in ("", "a~b", "save~part", "%4", "%zz", "%4G", "ABC", "a b", "é",
                 "%41%42", "%e9", "x.y", "%2e"):
        assert moy_play.files_path_of(name) == cart_files.path_of(name), name


def test_a_cart_s_files_live_where_the_reference_keeps_them():
    _lib_or_skip()
    for cart in ("/sd/moybyte/carts/x.moy", "/carts/x.moy", "carts/x.moy", "x.moy",
                 "/moy/carts/a.moy/", "/a/b/c/d", "/moy/carts/.moy"):
        assert moy_play.files_folder(cart) == cart_files.folder_of(cart), cart


def _store(root):
    carts = root / "carts"
    cart = carts / "game.moy"
    (cart / "levels").mkdir(parents=True)
    (cart / "manifest.json").write_text("{}")
    (cart / "levels" / "1").write_bytes(b"one")
    (cart / "levels" / "2").write_bytes(b"two")
    return str(cart)


def _snapshot(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


def _script(files):
    """What a session answers, in order."""
    got = []
    got.append(files.where(b"save"))
    got.append(files.write(b"save", b"hello"))
    got.append(files.write(b"Save", b"HELLO"))
    got.append(files.write(b"levels/1", b"mine"))
    got.append(files.write(b"empty", b""))
    got.append(files.erase(b"nothing"))
    got.append(files.erase(b"empty"))
    got.append(files.write(b"save", b"hello again"))
    for prefix in (b"", b"levels/", b"s", b"z"):
        i = 0
        while True:
            n = files.name(prefix, i)
            got.append(n)
            if n is None:
                break
            i += 1
    got.append(files.write(b"\x00\xff", b"bin"))
    got.append(files.name(b"\x00", 0))
    for path, offset, n in ((b"save", 0, 64), (b"save", 6, 3), (b"save", 0, 0),
                            (b"save", 4, 0), (b"save", 11, 4), (b"save", 99, 0),
                            (b"Save", 1, 2), (b"nothing", 0, 4), (b"empty", 0, 0)):
        got.append(files.read(path, offset, n))
    got.append(files.write(b"save", b"third"))
    got.append(files.read(b"save", 0, 64))
    got.append(files.erase(b"save"))
    got.append(files.read(b"save", 0, 64))
    return got


def _where(files, path, root):
    w = files.where(path)
    return None if w is None else os.path.relpath(w, root)


def test_a_session_writes_reads_and_lists_as_the_reference(tmp_path):
    _lib_or_skip()
    py_root, c_root = tmp_path / "py", tmp_path / "c"
    py_cart, c_cart = _store(py_root), _store(c_root)
    py = cart_files.CartFiles(py_cart)
    c = moy_play.Files(c_cart)
    assert _script(py) == _script(c)
    for p in (b"save", b"Save", b"levels/1", b"empty", b"\x00\xff"):
        assert _where(py, p, py_root) == _where(c, p, c_root), p
    c.close()
    assert _snapshot(py_root) == _snapshot(c_root)
    assert moy_play.files_live() == 0, "a closed session left memory held"


def test_a_read_of_the_cart_s_own_folder_streams_through_one_held_file(tmp_path):
    """read on the cart's folder: its bytes from an offset, how many remain
    with a length of 0, 0 past the end or for a file that is not there --
    and a file read in chunks is the file."""
    _lib_or_skip()
    cart = _store(tmp_path)
    blob = bytes(range(256)) * 40
    with open(os.path.join(cart, "data.bin"), "wb") as f:
        f.write(blob)
    c = moy_play.Files(cart)
    assert c.shipped("data.bin", 0, 0) == len(blob)
    assert c.shipped("data.bin", 100, 0) == len(blob) - 100
    assert c.shipped("data.bin", len(blob), 0) == 0
    assert c.shipped("data.bin", len(blob) + 5, 8) == b""
    got = b"".join(c.shipped("data.bin", o, 777) for o in range(0, len(blob), 777))
    assert got == blob
    assert c.shipped("levels/1", 0, 16) == b"one"
    assert c.shipped("data.bin", 5, 3) == blob[5:8]   # back to the first, reopened
    assert c.shipped("missing", 0, 16) == b"" and c.shipped("missing", 0, 0) == 0
    assert c.shipped("levels", 0, 16) == b""          # a folder reads nothing
    c.close()
    assert moy_play.files_live() == 0, "the held file outlived its session"


def test_opening_finishes_what_a_power_loss_left(tmp_path):
    _lib_or_skip()
    roots = []
    for side in ("py", "c"):
        root = tmp_path / side
        cart = _store(root)
        written = root / "written" / "game"
        written.mkdir(parents=True)
        (written / "save").write_bytes(b"old")
        (written / "save~done").write_bytes(b"new")
        (written / "half~part").write_bytes(b"torn")
        (written / "other").write_bytes(b"kept")
        roots.append((root, cart))
    (py_root, py_cart), (c_root, c_cart) = roots
    py = cart_files.CartFiles(py_cart)
    c = moy_play.Files(c_cart)
    assert _snapshot(py_root) == _snapshot(c_root)
    assert (py_root / "written" / "game" / "save").read_bytes() == b"new"
    assert sorted(py.written) == [b"other", b"save"]
    for p in (b"save", b"other", b"half"):
        assert _where(py, p, py_root) == _where(c, p, c_root), p
    c.close()
    assert moy_play.files_live() == 0
