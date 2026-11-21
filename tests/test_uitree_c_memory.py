"""The C engine's memory on the boards' MicroPython (the board model `make
unix-micropython` builds): every block the engine holds is counted by its
allocator (`moy_uitree.mem()`, native/moy_uitree/moy_uitree.c), which a board
points at PSRAM. A page made at boot holds nothing until it opens; a page
left keeps only its lowered tree and its state's values (`Engine.close`), so
opening and leaving every page again and again ends where the first round
ended but for what the values hold (the log's lines grow with the log), and letting
each go (`forget`) ends at the bytes the desk started with. A page opened
again draws the tree it kept: no build, no lowering.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_frame_alloc as FA  # noqa: E402

# The engine bytes at the T-Deck's desk before any page opens: every page the
# console makes at boot (Settings' rows, Wi-Fi, the log) holds no store yet.
DESK_BYTES = 0
# The lowered trees and state values of the pages below, kept after each is
# left (52,333 B when this was written, 9.7 KB of it values; the rows page is
# 17 KB of it).
KEPT_MAX = 56 * 1024
ROUNDS = 20

DRIVER = r'''
ALL = []


def _pre():
    """Before the console boots: every page on the C engine, each engine
    made kept in ALL (they live as long as the console anyway)."""
    import uitree_app, uitree_c, uitree as S
    uitree_app.use_engine("c")
    made = uitree_c.CEngine._c_init
    def keep(self):
        ALL.append(self)
        return made(self)
    uitree_c.CEngine._c_init = keep
    for cls, name in ((S.Builder, "_build"), (uitree_c.CEngine, "lower")):
        f = getattr(cls, name)
        def counted(self, *a, f=f, name=name):
            CALLS[name] = CALLS.get(name, 0) + 1
            return f(self, *a)
        setattr(cls, name, counted)
    return None


CALLS = {}


def mem(loop):
    import gc, moy_uitree
    ws = loop.ws

    def steps(n):
        for _ in range(n):
            loop.step()

    def live():
        gc.collect()
        return moy_uitree.mem()[0]

    sl = lambda: ws.settings_layer
    def page(p):
        return lambda: sl().open_page(p)
    pages = [("settings_rows", None, ws.open_settings), ("tz", ws.open_settings, page("tz")),
             ("log", ws.open_settings, page("log")), ("permissions", ws.open_settings, page("permissions")),
             ("licences", ws.open_settings, page("licences")),
             ("achievements", ws.open_settings, page("achievements")),
             ("wifi", ws.open_settings, page("wifi")), ("tasks", None, ws.open_tasks)]
    steps(40)
    out = {"desk": live(), "engines_at_desk": len(ALL), "rounds": [], "kept": {}, "calls": []}
    for r in range(ROUNDS):
        for name, prep, opener in pages:
            before = live()
            if prep is not None:
                prep()
                steps(2)
            CALLS.clear()
            opener()
            steps(3)
            out["calls"].append((r, name, CALLS.get("_build", 0), CALLS.get("lower", 0)))
            ws.home.go_home()
            steps(2)
            if r == 0:
                out["kept"][name] = live() - before
        out["rounds"].append((live(), sum(e._x.stats()[6] for e in ALL if e._x is not None)))
    for e in ALL:
        e.forget()
    out["forgot"] = live()
    out["engines"] = len(ALL)
    out["peak"] = moy_uitree.mem()[1]
    print("RESULT " + __import__("json").dumps(out))
'''


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    info, _ = FA._run(tmp_path_factory.mktemp("uitree_c_mem"), entry="(_pre() or mem)",
                      more="ROUNDS = %d\n" % ROUNDS + DRIVER,
                      why="the C engine's memory on the boards' MicroPython: this is its check there")
    print(info)
    return info


def test_the_desk_holds_no_engine_bytes_before_a_page_opens(result):
    assert result["engines_at_desk"] > 0, "the console made no page at boot: the pin measures nothing"
    assert result["desk"] == DESK_BYTES, result["desk"]


def test_leaving_a_page_keeps_its_tree_and_nothing_grows(result):
    """Each round opens and leaves every page; the engine bytes at every
    round's end less the state values the stores hold are the same, and
    they are the lowered trees and values the pages keep."""
    rounds = result["rounds"]
    held = [b - v for b, v in rounds]
    assert len(set(held)) == 1, rounds
    assert sum(result["kept"].values()) == rounds[0][0] - result["desk"], result["kept"]
    assert rounds[0][0] <= KEPT_MAX, (rounds[0], result["kept"])


def test_letting_every_page_go_returns_every_byte(result):
    assert result["forgot"] == result["desk"], result["forgot"]


def test_a_page_opened_again_neither_builds_nor_lowers(result):
    first = [c for c in result["calls"] if c[0] == 0]
    again = [c for c in result["calls"] if c[0] > 0]
    assert all(c[2] >= 1 and c[3] >= 1 for c in first), first
    assert all(c[2] == 0 and c[3] == 0 for c in again), [c for c in again if c[2] or c[3]]
