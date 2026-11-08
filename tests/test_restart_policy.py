"""Init restart policies (runtime/system/scheduler.py's RESTART POLICIES,
docs/os_2026-11/processes.md section 5): `never` leaves a service down,
`on-crash` starts it again after a nonzero end, `always` after any end, each
after a back-off that doubles, and the strike ledger's cap leaves a service
that ends that many times in a row down; a stop asked for never restarts, a
start by hand forgives, and the policy lives in the settings, which the
`service policy` face sets and shows."""

import time

import pytest

from runtime import moybyte, os_log, scheduler as S
from runtime import moy_log as L
from test_scheduler import Guard, _clear


class Store:
    """The settings' face: get and set."""

    def __init__(self):
        self.rows = {}

    def get(self, key, default=None):
        return self.rows.get(key, default)

    def set(self, key, value):
        self.rows[key] = value


@pytest.fixture
def sched(monkeypatch):
    monkeypatch.setattr(S, "MOY_RESTART_BACKOFF_MS", 4)
    wakes = moybyte.WAKES
    _clear(wakes)
    s = S.Scheduler(wakes, Guard())
    s.store = Store()
    yield s
    s.reset()
    _clear(wakes)
    wakes.ended = S.SCHED._ended
    wakes.opener = S.SCHED._open
    wakes.goal = S.SCHED._goal


CRASHER = '''
import moybyte
def boom():
    raise ValueError("bad")
moybyte.after(0, boom)
'''

QUITTER = '''
import moybyte
moybyte.after(0, lambda: None)
'''


def _cart(src, cid="t.svc"):
    return {"id": cid, "title": cid, "type": "service", "src": src,
            "runtime": "python", "permissions": [], "required": [],
            "path": "/carts/%s.moy" % cid}


def _run(s, ms):
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000 < ms:
        time.sleep(0.001)
        s.tick(0)


def _starts(s, name):
    return sorted(p.pid for p in s.procs.values() if p.name == name)


def test_never_is_the_default_and_leaves_it_down(sched):
    assert sched.policy("t.svc") == S.NEVER
    pid = sched.start(_cart(CRASHER))
    _run(sched, 60)
    assert sched.procs[pid].code == S.EXIT_CRASHED
    assert sched.find("t.svc") is None and not sched.pending


def test_on_crash_restarts_a_crash_after_its_back_off(sched):
    sched.set_policy("t.svc", S.ON_CRASH)
    assert sched.store.rows[S.RESTART_KEY] == {"t.svc": "on-crash"}
    since = L.next()
    first = sched.start(_cart(CRASHER))
    sched.tick(0)
    assert sched.procs[first].code == S.EXIT_CRASHED
    assert "t.svc" in sched.pending
    _run(sched, 15)
    again = [p for p in sched.procs.values() if p.name == "t.svc"]
    assert first not in sched.procs                 # the old row went at the restart
    assert again and again[0].pid != first
    texts = [t for _s, _t, _l, q, t in os_log.records(since, 64) if q == first]
    assert "on-crash: restart in 4 ms (end 1 of 3)" in texts


def test_on_crash_leaves_a_clean_exit_down(sched):
    sched.set_policy("t.svc", S.ON_CRASH)
    pid = sched.start(_cart(QUITTER))
    _run(sched, 30)
    assert sched.procs[pid].code == S.EXIT_OK and not sched.pending
    assert _starts(sched, "t.svc") == [pid]


def test_always_restarts_a_clean_exit(sched):
    sched.set_policy("t.svc", S.ALWAYS)
    pid = sched.start(_cart(QUITTER))
    sched.tick(0)
    assert sched.procs[pid].code == S.EXIT_OK and "t.svc" in sched.pending
    _run(sched, 20)
    assert pid not in sched.procs and _starts(sched, "t.svc")


def test_a_service_that_crashes_cap_times_in_a_row_stays_down(sched):
    sched.set_policy("t.svc", S.ON_CRASH)
    since = L.next()
    sched.start(_cart(CRASHER))
    _run(sched, 200)                                # 4 + 8 ms of back-off, and more
    rows = [p for p in sched.procs.values() if p.name == "t.svc"]
    assert len(rows) == 1 and rows[0].code == S.EXIT_CRASHED
    assert sched.down["t.svc"] == S.RESTART_CAP and not sched.pending
    starts = [t for _s, _t, _l, _q, t in os_log.records(since, 128) if t == "start t.svc"]
    assert len(starts) == S.RESTART_CAP
    last = [t for _s, _t, _l, q, t in os_log.records(since, 128) if q == rows[0].pid]
    assert last[-1] == "on-crash: 3 ends in a row, it stays down"


def test_the_back_off_doubles(sched):
    sched.set_policy("t.svc", S.ALWAYS)
    since = L.next()
    sched.start(_cart(CRASHER))
    _run(sched, 200)
    waits = [t for _s, _t, _l, _q, t in os_log.records(since, 128) if "restart in" in t]
    assert waits == ["always: restart in 4 ms (end 1 of 3)",
                     "always: restart in 8 ms (end 2 of 3)"]


def test_a_long_run_starts_the_count_afresh(sched, monkeypatch):
    sched.set_policy("t.svc", S.ON_CRASH)
    sched.down["t.svc"] = S.RESTART_CAP - 1
    monkeypatch.setattr(S, "MOY_RESTART_HEAL_MS", 0)
    sched.start(_cart(CRASHER))
    sched.tick(0)
    assert sched.down["t.svc"] == 1 and "t.svc" in sched.pending


def test_a_stop_asked_for_never_restarts(sched):
    sched.set_policy("t.svc", S.ALWAYS)
    pid = sched.start(_cart("import moybyte\nmoybyte.every(5, lambda: None)\n"))
    assert sched.stop(pid)
    _run(sched, 20)
    assert not sched.pending and sched.find("t.svc") is None


def test_a_start_by_hand_forgives(sched):
    sched.set_policy("t.svc", S.ON_CRASH)
    sched.start(_cart(CRASHER))
    sched.tick(0)
    assert sched.down["t.svc"] == 1 and "t.svc" in sched.pending
    assert sched.forgive("t.svc")
    assert "t.svc" not in sched.down and not sched.pending
    assert not sched.forgive("t.svc")


def test_never_drops_the_row_and_a_bad_policy_is_refused(sched):
    sched.set_policy("t.svc", S.ALWAYS)
    sched.set_policy("t.svc", S.NEVER)
    assert sched.store.rows[S.RESTART_KEY] == {}
    with pytest.raises(ValueError):
        sched.set_policy("t.svc", "sometimes")
    sched.store.rows[S.RESTART_KEY] = {"t.svc": "sometimes"}   # by hand: absent
    assert sched.policy("t.svc") == S.NEVER
