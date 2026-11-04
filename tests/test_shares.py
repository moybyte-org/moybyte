"""THE SHARES' ROWS (native/moy_play/moy_play.h, P2.1): each console board's
line, the fit check a taker of each row asks, and the kill threshold the rows
derive, on the host's build of the C (runtime/host/moy_play.py) standing in
for each board's line."""

import pytest

from runtime import moy_play as mp
from runtime.run import player_fit as fit

KIB, MIB = 1024, 1 << 20
CONSOLES = ("tdeck", "guition_s3", "p4", "guition_p4")
ROWS = {"procs": mp.SHARE_PROCS, "media": mp.SHARE_MEDIA, "rings": mp.SHARE_RINGS,
        "elf": mp.SHARE_ELF, "lanes": mp.SHARE_LANES}


@pytest.fixture
def board():
    yield lambda name: mp.share_use(name)
    mp.share_use(None)


def test_the_host_has_no_line_and_counts_nothing(board):
    assert mp.shares() is None
    assert mp.kill_below(0) is None
    for row in ROWS.values():
        assert mp.share_take(row, 1 << 30)
    assert mp.share_held(mp.SHARE_MEDIA) == 0
    assert fit.board_share() is None


def test_each_console_has_a_line_and_nothing_else_does():
    for name in CONSOLES:
        assert mp.shares(name) is not None
    for name in ("xiao_zero", "web", "host", "nope"):
        assert mp.shares(name) is None
    assert not mp.share_use("xiao_zero")


def test_the_s3s_have_only_the_cart_floor_and_the_rings():
    """The cart floor, the native share and the Python heap's share spend
    the S3s' 8 MiB: media, ELF and lanes are ABSENT (None), never 0."""
    for name in ("tdeck", "guition_s3"):
        procs, media, rings, elf, lanes = mp.shares(name)
        assert procs == 4 * MIB
        assert media is None and elf is None and lanes is None
        assert rings is not None and 0 < rings <= 64 * KIB


def test_the_p4s_have_every_row():
    for name in ("p4", "guition_p4"):
        assert all(v is not None and v > 0 for v in mp.shares(name))
        assert mp.shares(name)[mp.SHARE_PROCS] == 7 * MIB


@pytest.mark.parametrize("name", CONSOLES)
@pytest.mark.parametrize("row", sorted(ROWS))
def test_the_fit_check_holds_each_row_to_its_cap(board, name, row):
    """A take fits up to the row's cap and not one byte past it; a give
    makes room again; an absent row refuses every take, the smallest too."""
    board(name)
    r = ROWS[row]
    cap = mp.shares(name)[r]
    if cap is None:
        assert not mp.share_take(r, 1)
        assert mp.share_held(r) == 0
        return
    assert mp.share_take(r, cap - 100)
    assert mp.share_take(r, 100)
    assert mp.share_held(r) == cap
    assert not mp.share_take(r, 1)
    mp.share_give(r, 100)
    assert not mp.share_take(r, 101)
    assert mp.share_take(r, 100)
    mp.share_give(r, cap + 5)                   # never below nothing
    assert mp.share_held(r) == 0


@pytest.mark.parametrize("name", CONSOLES)
def test_the_kill_threshold_is_what_the_present_rows_still_owe(board, name):
    board(name)
    rows = mp.shares(name)
    present = [v for v in rows if v is not None]
    assert mp.kill_below(0) == sum(present)
    held = 3 * MIB
    assert mp.kill_below(held) == sum(present) - held
    assert mp.kill_below(rows[0] + MIB) == sum(present) - rows[0]
    if rows[mp.SHARE_RINGS]:
        assert mp.share_take(mp.SHARE_RINGS, 4 * KIB)
        assert mp.kill_below(0) == sum(present) - 4 * KIB


@pytest.mark.parametrize("name", CONSOLES)
def test_admission_counts_the_procs_row(name):
    assert fit.board_share(name) == mp.shares(name)[mp.SHARE_PROCS]


def test_an_event_ring_is_a_take_from_the_rings_row(board):
    """native/moy_input's rings a task writes take their bytes from RINGS:
    a source gets a ring while the row has room and none past it, and its
    bytes go back when the table is freed."""
    import gc
    from runtime import moy_input as mi
    gc.disable()                # no other table's give lands mid-count
    try:
        mi.InputTable().__del__()   # a first free makes the kernel's table
        board("tdeck")
        t = mi.InputTable()     # its "local" source's ring
        one = mp.share_held(mp.SHARE_RINGS)
        assert one > 0
        t.source("probe")
        assert mp.share_held(mp.SHARE_RINGS) == 2 * one
        cap = mp.shares("tdeck")[mp.SHARE_RINGS]
        assert mp.share_take(mp.SHARE_RINGS, cap - 2 * one)    # the row full
        t.source("starved")                     # a source with no ring
        assert mp.share_held(mp.SHARE_RINGS) == cap
        mp.share_give(mp.SHARE_RINGS, cap - 2 * one)
        t.__del__()
        assert mp.share_held(mp.SHARE_RINGS) == 0
    finally:
        gc.enable()
