"""The index's root table (docs/kernel_store_2026-10.md section 4), pinned once
for every binding: the Python twin, the C twin through ctypes, and -- by
test_the_roots_hold_on_the_vm -- this same file on the boards' VM over the
native module and the Python one.

  * root(path) is the root's id, 1 .. ROOTS: the lowest free slot the first
    time a path is named, the same id every time after;
  * root_path(rid) is the path, None for an id that names no root;
  * rows(rid) lists the live rows whose key starts with chr(rid), slot order;
  * with every slot taken, a new root takes the slot of the root named longest
    ago, and that root's rows are released -- their handles go stale -- while
    every other root's stay.
"""

import pytest

from runtime import moy_index
from runtime.moy_index import ROOTS, StaleHandle

BINDINGS = {"python": moy_index.Index}

try:
    from tools import moy_index_spike
except ImportError:         # the VM's run of this file has no tools/
    moy_index_spike = None
if moy_index_spike is not None:
    BINDINGS.update(moy_index_spike.host_bindings())


@pytest.fixture(params=sorted(BINDINGS))
def make(request):
    return BINDINGS[request.param]


def test_a_root_takes_the_lowest_free_id_once(make):
    idx = make()
    assert idx.root("/sd/carts") == 1
    assert idx.root("/moy/carts") == 2
    assert idx.root("/sd/carts") == 1
    assert idx.root_path(1) == "/sd/carts" and idx.root_path(2) == "/moy/carts"
    for rid in (0, 3, ROOTS, ROOTS + 1, -1, 1 << 40):
        assert idx.root_path(rid) is None


def test_rows_are_the_keys_that_start_with_the_id(make):
    idx = make()
    a, b = idx.root("/a"), idx.root("/b")
    ha = [idx.intern(chr(a) + n) for n in ("x.moy", "y.moy")]
    hb = idx.intern(chr(b) + "x.moy")
    other = idx.intern("/carts/x.moy")
    idx.intern("")
    assert idx.rows(a) == ha and idx.rows(b) == [hb]
    assert other not in idx.rows(ord("/")) and idx.rows(0) == []
    assert idx.rows(ROOTS + 1) == []


def test_a_ninth_root_evicts_the_one_named_longest_ago(make):
    idx = make()
    keep = {}
    for i in range(ROOTS):
        rid = idx.root("/r%d" % i)
        keep[rid] = idx.intern(chr(rid) + "c.moy")
    idx.root("/r0")                         # named again: now the newest
    rid = idx.root("/r-new")
    assert rid == 2 and idx.root_path(2) == "/r-new"
    assert not idx.valid(keep[2])
    with pytest.raises(StaleHandle):
        idx.path(keep[2])
    assert idx.rows(2) == []
    for r, h in keep.items():
        if r != 2:
            assert idx.valid(h) and idx.rows(r) == [h]
    assert idx.root("/r1") == 3             # /r1 was next oldest: id 3 goes
    assert idx.root_path(3) == "/r1" and not idx.valid(keep[3])


def test_the_roots_hold_on_the_vm(tmp_path):
    import vm_suite
    from unix_mp import require_unix_mp
    exe = require_unix_mp(
        "moy_index", why="the root table on the VM a board runs, over the "
                         "native store index.")
    for native in (True, False):
        passed, failed, text = vm_suite.run(
            exe, tmp_path / str(native), "moy_index", "test_store_roots.py",
            native, "moy_index.Index", skip=("test_the_roots_hold_on_the_vm",))
        assert failed == 0 and passed == 3, text
