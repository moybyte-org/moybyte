"""runtime/moy_glass.py, the glass twin (docs/kernel_survival_2026-10.md
section 3.1): owners, loans as BUF rows, the pool keyed by byte size, and the
three verbs the compositors answer."""

import pytest

from runtime import moy_glass, moy_spine
from runtime.moy_glass import Glass, ROLE_BAKE, ROLE_LAYER, ORIGIN_ALLOC


def _kind(h):
    return (h >> moy_spine.KIND_SHIFT) & 0xF


def test_a_loan_is_a_buf_row_owned_by_its_tag():
    g = Glass()
    canvas = object()
    h = g.lend(bytearray(16), 16, ROLE_LAYER, ORIGIN_ALLOC, "cart", canvas)
    assert _kind(h) == moy_spine.KIND_BUF
    assert _kind(g.owner("cart")) == moy_spine.KIND_OWNER
    assert g.loans("cart", ROLE_LAYER) == [h]
    assert g.loans("cart", ROLE_LAYER, canvas) == [h]
    assert g.loans("cart", ROLE_LAYER, object()) == []
    assert g.loans("cart", ROLE_BAKE) == []


def test_giving_back_ends_the_row_and_a_stale_handle_is_refused():
    g = Glass()
    h = g.lend(bytearray(4), 4, ROLE_BAKE, ORIGIN_ALLOC, "paint", "img")
    row = g.give_back(h)
    assert row[moy_glass.HOLDER] == "img" and g.bufs.count() == 0
    with pytest.raises(moy_spine.StaleHandle):
        g.row(h)
    g.owner_end("paint")
    assert g.owners.count() == 0


def test_an_owner_with_loans_cannot_end():
    g = Glass()
    g.lend(bytearray(4), 4, ROLE_BAKE, ORIGIN_ALLOC, "paint", "img")
    with pytest.raises(ValueError):
        g.owner_end("paint")


def test_the_pool_is_keyed_by_byte_size():
    g = Glass()
    a, b = bytearray(8), bytearray(16)
    g.pool_put(8, a)
    g.pool_put(16, b)
    assert g.pool_take(16) is b and g.pool_take(16) is None
    assert g.pool_take(8) is a


def test_a_full_table_is_a_memory_error():
    g = Glass()
    for _ in range(moy_spine.SLOTS):
        g.lend(bytearray(1), 1, ROLE_LAYER, ORIGIN_ALLOC, "cart")
    with pytest.raises(MemoryError):
        g.lend(bytearray(1), 1, ROLE_LAYER, ORIGIN_ALLOC, "cart")


def test_the_three_verbs_reach_the_compositor():
    calls = []

    class Comp:
        def flush(self):
            calls.append("flush")

        def sync(self):
            calls.append("sync")

        def present_pending(self):
            calls.append("pending")

    c = Comp()
    moy_glass.present(c)
    moy_glass.fence(c)
    moy_glass.present_pending(c)
    moy_glass.fence(object())           # nothing asynchronous: no fence
    assert calls == ["flush", "sync", "pending"]
