"""`"fps": "free"` (SPEC.md §5's opt-out, 2026-09-06): a game whose logic is
all `speed * dt` is not paced -- it ticks and draws with the loop at the real
dt -- while every other game keeps the tick the console guarantees."""

import json
from pathlib import Path

from runtime.player import Player, FREE_DT_MAX
from runtime.tick_model import TickScheduler
from tools import gen_device_carts

ROOT = Path(__file__).resolve().parent.parent


class _Input:
    tick_edges = None
    keep_edges = None


class _Ws:
    steady = True
    _uncap = False
    input = _Input()


def _player():
    pl = Player.__new__(Player)
    pl._is_tool = False
    pl._free = False
    pl.sched = TickScheduler()
    pl.tick_ms = 0
    pl._n_ticks = 1
    pl._netplay = None
    pl.ws = _Ws()
    return pl


def test_a_free_cart_is_not_paced_and_draws_every_frame():
    pl = _player()
    pl._arm_pacing({"fps": "free"})
    assert pl.tick_ms == 0 and pl._free is True
    for _ in range(10):
        assert pl.frame_plan(1 / 45) is True     # every loop frame draws
        assert pl._n_ticks == 1                  # one real-dt tick each


def test_a_numbered_cart_keeps_its_tick():
    pl = _player()
    pl._arm_pacing({"fps": 60})
    assert pl.tick_ms == 16 and pl._free is False
    pl = _player()
    pl._arm_pacing({})
    assert pl.tick_ms == 33


def test_free_dt_is_the_real_one_up_to_a_stall():
    pl = _player()
    pl._arm_pacing({"fps": "free"})
    assert pl._loop_dt(1 / 45) == 1 / 45
    assert pl._loop_dt(0.7) == FREE_DT_MAX       # a radio scan slows time, never jumps it
    pl2 = _player()                              # a tool: the loop's own dt, unclamped
    pl2._is_tool = True
    pl2._arm_pacing({})
    assert pl2._loop_dt(0.7) == 0.7


def test_disarming_forgets_free():
    pl = _player()
    pl._arm_pacing({"fps": "free"})
    pl.park = lambda: None
    pl._disarm_pacing()
    assert pl._free is False


def test_the_roster_carries_free_through_to_the_boards():
    carts = gen_device_carts.build_carts(str(ROOT / "system_carts"))
    by_title = {c["title"]: c for c in carts}
    assert by_title["Star Catcher"]["fps"] == "free"
    assert by_title["Sky Run"]["fps"] == 60


# The seed games that declared free, each audited 2026-09-06: every per-frame
# motion in its _update is `* dt`, and its bare `+= 1`s are event counters
# (lives, level, misses, a spawn queue). Adding a cart here is that audit.
FREE_SEEDS = {"Star Catcher", "Brick Siege", "Tiny Runner", "Letter Blitz",
              "Harpoon Pop", "Tap Only Red", "Pixel Pet", "Ray Test"}


def test_only_audited_seed_games_declare_free():
    declared = set()
    for man in (ROOT / "system_carts").glob("*.moy/manifest.json"):
        m = json.loads(man.read_text(encoding="utf-8"))
        if m.get("fps") == "free":
            declared.add(m["title"])
            assert m.get("type") == "game", m["title"]
    assert declared == FREE_SEEDS, sorted(declared ^ FREE_SEEDS)
