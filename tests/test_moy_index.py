"""The store's index (runtime/moy_index.py): the interface sprint 1a writes in
Rust and in C, pinned once for every binding.

`BINDINGS` names each implementation by a factory for an empty table: the
Python twin, and the native twins over the host's C ABI through ctypes
(tools/moy_index_spike.py: the C twin always, the Rust twin under
MOY_INDEX_IMPL=rust). Every test below runs against each, unchanged -- the
parity the 1b gate asks for. tests/test_moy_index_twins.py runs this same file
on the boards' VM over the native module, and tests/test_semantic_traces.py's
store trace pins the handle VALUES, so a binding that hands out different
numbers fails there too.

What a binding must do, in the order the tests below say it:

  * a handle is an int, never 0, below 2**30: slot | generation << SLOT_BITS,
    the first row slot 0 at generation 1;
  * intern is idempotent per path; find answers 0 for a path with no row;
  * a released row's handle -- and a forged one -- raises StaleHandle (a
    ValueError) from path and release, and valid() says False without raising;
    a handle that is not an int raises TypeError;
  * a freed slot is reused lowest-first, under the next generation, which wraps
    from GEN_MAX to 1 and never reaches 0;
  * SLOTS rows fit and the next intern is OSError(ENOSPC);
  * handles() lists the live rows in slot order; count() counts them.
"""

import pytest

from runtime import moy_index
from runtime.moy_index import GEN_MAX, SLOT_BITS, SLOTS, StaleHandle

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


def test_a_handle_is_slot_and_generation_and_never_zero(make):
    idx = make()
    h = idx.intern("/carts/a.moy")
    assert isinstance(h, int) and h != 0
    assert h == (1 << SLOT_BITS) | 0
    assert idx.intern("/carts/b.moy") == (1 << SLOT_BITS) | 1


def test_intern_is_idempotent_and_find_answers_zero_for_a_stranger(make):
    idx = make()
    h = idx.intern("/carts/a.moy")
    assert idx.intern("/carts/a.moy") == h
    assert idx.find("/carts/a.moy") == h
    assert idx.find("/carts/b.moy") == 0
    assert idx.path(h) == "/carts/a.moy"
    assert idx.valid(h) and idx.count() == 1


def test_a_released_handle_is_stale_everywhere(make):
    idx = make()
    h = idx.intern("/carts/a.moy")
    idx.release(h)
    assert not idx.valid(h)
    with pytest.raises(StaleHandle):
        idx.path(h)
    with pytest.raises(StaleHandle):
        idx.release(h)                      # a double release is loud too
    assert idx.find("/carts/a.moy") == 0 and idx.count() == 0
    assert issubclass(StaleHandle, ValueError)


def test_a_reused_slot_does_not_answer_to_its_old_handle(make):
    idx = make()
    a = idx.intern("/carts/a.moy")
    b = idx.intern("/carts/b.moy")
    idx.release(a)
    c = idx.intern("/carts/c.moy")          # lowest free slot: a's
    assert c & (SLOTS - 1) == a & (SLOTS - 1)
    assert c >> SLOT_BITS == (a >> SLOT_BITS) + 1
    with pytest.raises(StaleHandle):
        idx.path(a)
    assert idx.path(c) == "/carts/c.moy" and idx.path(b) == "/carts/b.moy"


@pytest.mark.parametrize("forged", [0, -1, (2 << SLOT_BITS) | 0, 7,
                                    (1 << SLOT_BITS) | (SLOTS - 1), 1 << 40])
def test_a_forged_handle_is_stale(make, forged):
    idx = make()
    idx.intern("/carts/a.moy")
    assert not idx.valid(forged)
    with pytest.raises(StaleHandle):
        idx.path(forged)


@pytest.mark.parametrize("junk", [None, "4096", 4096.0])
def test_a_handle_that_is_not_an_int_is_a_type_error(make, junk):
    idx = make()
    idx.intern("/carts/a.moy")
    assert not idx.valid(junk)
    with pytest.raises(TypeError):
        idx.path(junk)


def test_the_generation_wraps_to_one_and_never_reaches_zero(make):
    idx = make()
    h = idx.intern("/carts/a.moy")
    for _ in range(GEN_MAX - 1):
        idx.release(h)
        h = idx.intern("/carts/a.moy")
    assert h >> SLOT_BITS == GEN_MAX
    assert h < (1 << 30)                    # the largest handle there is
    idx.release(h)
    h = idx.intern("/carts/a.moy")
    assert h == (1 << SLOT_BITS) | 0


def test_a_full_table_is_a_full_store(make):
    idx = make()
    for i in range(SLOTS):
        idx.intern("/carts/%d.moy" % i)
    with pytest.raises(OSError) as exc:
        idx.intern("/carts/one-more.moy")
    assert exc.value.args[0] == 28          # ENOSPC: what store_full reads
    idx.release(idx.find("/carts/9.moy"))
    assert idx.intern("/carts/one-more.moy") & (SLOTS - 1) == 9


def test_handles_lists_the_live_rows_in_slot_order(make):
    idx = make()
    hs = [idx.intern("/carts/%s.moy" % n) for n in "abcd"]
    idx.release(hs[1])
    assert idx.handles() == [hs[0], hs[2], hs[3]]
    assert idx.count() == 3
