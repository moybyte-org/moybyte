"""Clock and alarms (docs/os_2026-11/between_apps.md section 3): the
crontab's reader and its minutes (runtime/system/cron.py), `moybyte.at`
under the `alarms` permission, and the shell's `cron` and `at`, whose jobs
run a line in a faceless session (native/moy_sh/moy_sh_job.h) with their
output in the OS log -- all on a fake wall clock."""

import calendar
import time

import pytest

from runtime import cron as C
from runtime import devices
from runtime import moy_log as L
from runtime import moybyte
from runtime import scheduler as S
from runtime import wallclock as wc
from test_scheduler import _clear

BELGRADE = "CET-1CEST,M3.5.0,M10.5.0/3"


def _at(*ymdhms):
    return calendar.timegm(ymdhms + (0,) * (6 - len(ymdhms)))


class _Clock:
    """The wall clock the alarms read: Unix seconds a test moves."""

    def __init__(self, monkeypatch, t):
        self.t = t
        monkeypatch.setattr(wc, "_now", lambda: self.t)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # The tests name their own pids (`_pid`), so they start from an empty
    # registry: a wake another test left on a pid would be counted as theirs.
    _clear(moybyte.WAKES)
    monkeypatch.setattr(wc, "_kernel", [None, True])        # synced past 2020
    before = set(moybyte.WAKES.by_id)
    yield
    for wid in [w for w in list(moybyte.WAKES.by_id) if w not in before]:
        S.SCHED.plain_cancel(wid)
    moybyte.WAKES.woke = None
    wc.set_zone(wc.UTC)
    S.SCHED.reset()


def _tick():
    S.SCHED._n = S.SCHED._i = 0
    S.SCHED.tick(1_000_000)


def _logged(pid):
    return [(r[2], r[4]) for r in L.read(0, 1 << 30) if r[3] == pid]


def _sh():
    try:
        from runtime import moy_sh
        moy_sh._lib()
    except (ImportError, RuntimeError) as e:
        pytest.skip("no C shell on this host: %s" % e)
    return moy_sh


def _settle(tab, limit=5.0):
    """The frames' tails until the running job ends (a parked run's turn,
    then the job's poll)."""
    moy_sh = _sh()
    t0 = time.monotonic()
    while (tab.job is not None or tab.queue) and time.monotonic() - t0 < limit:
        moy_sh.jobs()
        _tick()
        time.sleep(0.002)


class _WS:
    def __init__(self, root):
        (root / "sd" / "moybyte" / "carts").mkdir(parents=True)
        (root / "sd" / "moybyte" / "system").mkdir(parents=True)
        (root / "sd" / "moybyte" / "files").mkdir(parents=True)
        self.carts_root = str(root / "sd" / "moybyte" / "carts")
        self.system = root / "sd" / "moybyte" / "system"


# -- the crontab's reader ------------------------------------------------------------

def test_a_line_is_five_fields_and_a_command():
    spec, cmd = C.parse_line("*/15 9-17 * * 1-5 notify send stand up")
    assert spec[0] == (0, 15, 30, 45) and spec[1] == tuple(range(9, 18))
    assert spec[4] == (1, 2, 3, 4, 5) and spec[5] and not spec[6]
    assert cmd == "notify send stand up"
    assert C.parse_line("0,30 8 1 1,7 0,7 echo hi")[0][4] == (0,)     # 7 is Sunday
    assert C.parse_line("5-20/5 * * * * x")[0][0] == (5, 10, 15, 20)
    assert C.parse_line("@daily echo night")[0][:2] == ((0,), (0,))
    for bad in ("* * * * echo", "60 * * * * x", "* 24 * * * x", "* * 0 * * x",
                "* * * 13 * x", "* * * * 8 x", "*/0 * * * * x", "5-2 * * * * x",
                "a * * * * x", "* * * * *", "@daily", "@often x"):
        assert C.parse_line(bad) is None, bad


def test_the_reader_is_strict_and_passes_bad_lines_over():
    lines, passed = C.parse("# a comment\n\n0 7 * * * alarm\nnonsense here\n@hourly tick\n")
    assert [c for _s, c, _l in lines] == ["alarm", "tick"]
    assert passed == [(4, "nonsense here")]


# -- its minutes, local --------------------------------------------------------------

def test_next_minute_in_the_console_s_zone():
    wc.set_zone(BELGRADE)
    spec = C.parse_line("30 7 * * * x")[0]
    t = _at(2026, 11, 8, 6, 31)                           # 07:31 Belgrade (CET)
    assert C.next_time(spec, t) == _at(2026, 11, 9, 6, 30)
    assert C.next_time(spec, _at(2026, 11, 8, 6, 0)) == _at(2026, 11, 8, 6, 30)
    assert C.next_time(spec, _at(2026, 7, 1, 4, 0)) == _at(2026, 7, 1, 5, 30)   # CEST
    assert C.when(_at(2026, 7, 1, 5, 30)) == "2026-07-01 07:30"


def test_either_day_field_runs_a_line_when_both_are_given():
    spec = C.parse_line("0 12 13 * 5 x")[0]                # the 13th, or a Friday
    t = C.next_time(spec, _at(2026, 11, 8))                # a Sunday
    assert t == _at(2026, 11, 13, 12)                      # Friday the 13th
    t = C.next_time(spec, t)
    assert t == _at(2026, 11, 20, 12)                      # the next Friday
    only_dow = C.parse_line("0 12 * * 1 x")[0]
    assert C.next_time(only_dow, _at(2026, 11, 8)) == _at(2026, 11, 9, 12)
    assert C.next_time(C.parse_line("0 0 30 2 * x")[0], _at(2026, 1, 1)) is None


# -- at's times ------------------------------------------------------------------------

def test_at_reads_now_relative_and_a_time_of_day():
    wc.set_zone(BELGRADE)
    now = _at(2026, 11, 8, 9, 0)                           # 10:00 local
    assert C.at_time(["now", "echo"], now) == (now, 1)
    assert C.at_time(["now", "+", "5", "minutes", "echo"], now) == (now + 300, 4)
    assert C.at_time(["now", "+2", "hours"], now) == (now + 7200, 3)
    assert C.at_time(["now+1", "day"], now) == (now + 86400, 2)
    assert C.at_time(["now", "+", "30", "second"], now) == (now + 30, 4)
    assert C.at_time(["11:15", "echo"], now) == (_at(2026, 11, 8, 10, 15), 1)
    assert C.at_time(["09:00"], now) == (_at(2026, 11, 9, 8, 0), 1)     # tomorrow's
    assert C.at_time(["09:00", "tomorrow"], now) == (_at(2026, 11, 9, 8, 0), 2)
    assert C.at_time(["08:00", "2026-12-24"], now) == (_at(2026, 12, 24, 7, 0), 2)
    assert C.at_time(["08:00", "2026-01-01"], now)[0] is None
    for bad in (["noon"], ["now", "+", "5"], ["now", "+", "x", "minutes"],
                ["now", "+", "5", "fortnights"], ["25:00"], ["7:5"]):
        assert C.at_time(bad, now)[0] is None, bad


def test_a_time_of_day_needs_the_clock(monkeypatch):
    monkeypatch.setattr(wc, "_kernel", [lambda: 0, True])
    assert C.at_time(["07:00"], _at(2026, 1, 1)) == (None, "the clock is not set")
    assert C.at_time(["now", "+", "1", "minute"], 100) == (160, 4)


def test_the_command_words_become_one_line():
    assert C.command_line(["echo hi > /tmp/x"]) == "echo hi > /tmp/x"
    assert C.command_line(["notify", "send", "tea time"]) == "notify send 'tea time'"
    assert C.command_line(["echo", "it's"]) == "echo 'it'\\''s'"


# -- moybyte.at ------------------------------------------------------------------------

def test_moybyte_at_is_an_alarm_on_the_wall_clock_that_wakes(monkeypatch):
    clock = _Clock(monkeypatch, _at(2026, 11, 8, 7, 0))
    woke, fired = [], []
    moybyte.WAKES.woke = lambda pid: woke.append((pid, clock.t))
    monkeypatch.setattr(devices, "grants", lambda pid: ("alarms",))
    monkeypatch.setattr(moybyte, "_pid", lambda: 5)
    wid = moybyte.at(clock.t + 60.5, lambda: fired.append(clock.t))
    assert moybyte.WAKES.count(5) == 1
    out = [0] * 8
    clock.t += 60
    assert moybyte.WAKES.due(0, out) == 0                 # no earlier than t
    clock.t += 1
    assert moybyte.WAKES.due(0, out) == 1 and out[0] == wid and woke == [(5, clock.t)]
    moybyte.WAKES.call(wid)
    assert fired == [clock.t] and moybyte.WAKES.count(5) == 0


def test_moybyte_at_needs_alarms(monkeypatch):
    _Clock(monkeypatch, _at(2026, 11, 8))
    monkeypatch.setattr(devices, "grants", lambda pid: ("notifications",))
    monkeypatch.setattr(moybyte, "_pid", lambda: 5)
    with pytest.raises(OSError) as e:
        moybyte.at(0, lambda: None)
    assert e.value.args == (1, 'needs "alarms"')
    monkeypatch.setattr(moybyte, "_pid", lambda: 1)          # the shell is not gated
    assert moybyte.at(0, lambda: None) > 0


def test_the_console_lights_the_screen_on_a_waking_alarm():
    from runtime.console import Workstation
    seen = []

    class _Ladder:
        def wake(self):
            seen.append(True)
            return len(seen) == 1

    ws = Workstation.__new__(Workstation)
    Workstation.alarm_woke(ws, 7)                  # no ladder: the host
    ws.idle_ladder = _Ladder()
    seq = L.next()
    Workstation.alarm_woke(ws, 7)
    Workstation.alarm_woke(ws, 7)                  # awake already: nothing said
    assert seen == [True, True]
    assert [(r[3], r[4]) for r in L.read(seq, 9)] == [(7, "an alarm woke the screen")]


def test_alarms_is_every_tier_s_permission_with_a_prompt():
    from runtime import app_perms
    r = app_perms.row("alarms")
    assert r[app_perms.TIERS] == ("python", "lua", "wasm", "elf") and r[app_perms.PROMPT]


# -- the crontab firing, and at's job in the log ---------------------------------------

def test_a_crontab_line_fires_and_its_output_reaches_the_log(monkeypatch, tmp_path):
    _sh()
    clock = _Clock(monkeypatch, _at(2026, 11, 8, 9, 58, 30))
    ws = _WS(tmp_path)
    (ws.system / "crontab").write_text("# every minute\n* * * * * echo tick; echo oops >&2\n"
                                       "0 3 * * * echo night\nbad line\n")
    tab = C.Cron()
    tab.bind(ws)
    assert tab.reload() == (2, 1)
    assert tab.due_at == _at(2026, 11, 8, 9, 59)
    _tick()
    assert tab.job is None and not tab.queue              # not yet
    clock.t = _at(2026, 11, 8, 9, 59, 1)
    seq = L.next()
    _tick()
    _settle(tab)
    pids = sorted({r[3] for r in L.read(seq, 1 << 30) if L.name(r[3]) == "cron"})
    assert len(pids) == 1
    assert _logged(pids[0]) == [(L.INFO, "echo tick; echo oops >&2"), (L.INFO, "tick"),
                                (L.ERR, "oops"), (L.INFO, "exit 0")]
    assert tab.due_at == _at(2026, 11, 8, 10, 0)           # the next minute armed
    assert pids[0] not in S.SCHED.procs                    # the job's row went


def test_a_late_alarm_runs_its_minute_once_and_a_zone_change_skips_it(monkeypatch, tmp_path):
    _sh()
    clock = _Clock(monkeypatch, _at(2026, 11, 8, 6, 0))
    tab = C.Cron()
    tab.bind(_WS(tmp_path))
    tab.load("30 6 * * * echo once\n")
    clock.t = _at(2026, 11, 8, 6, 45)                      # the console was away
    seq = L.next()
    _tick()
    _settle(tab)
    _tick()
    ran = [r[4] for r in L.read(seq, 1 << 30) if L.name(r[3]) == "cron"]
    assert ran == ["echo once", "once", "exit 0"]
    assert tab.due_at == _at(2026, 11, 9, 6, 30)
    wc.set_zone(BELGRADE)                                  # 06:30 UTC is 07:30 now
    clock.t = _at(2026, 11, 9, 6, 31)
    seq = L.next()
    _tick()
    _settle(tab)
    assert not [r for r in L.read(seq, 1 << 30) if L.name(r[3]) == "cron"]
    assert tab.due_at == _at(2026, 11, 10, 5, 30)


def test_an_unsynced_clock_arms_nothing_until_it_is_set(monkeypatch, tmp_path):
    state = [0]
    monkeypatch.setattr(wc, "_kernel", [lambda: state[0], True])
    clock = _Clock(monkeypatch, 3600)
    tab = C.Cron()
    tab.bind(_WS(tmp_path))
    tab.load("* * * * * echo x\n")
    assert tab.wid is None and tab.watch is not None
    state[0] = wc.SYNCED
    clock.t = _at(2026, 11, 8, 12, 0, 10)
    tab._watch()
    assert tab.watch is None and tab.due_at == _at(2026, 11, 8, 12, 1)


def test_the_shell_s_at_and_cron(monkeypatch, tmp_path):
    from runtime.sh_door import ShellDoor
    from runtime import sh_roles
    _sh()
    clock = _Clock(monkeypatch, _at(2026, 11, 8, 9, 0))
    ws = _WS(tmp_path)
    tab = C.Cron()
    tab.bind(ws)
    monkeypatch.setattr(C, "CRON", tab)

    class _Io:
        def __init__(self):
            self.got = {1: b"", 2: b""}

        def write(self, fd, b):
            self.got[fd] += bytes(b)

    io = _Io()
    d = ShellDoor(ws, io)
    assert sh_roles.r_at(d, ["now", "+", "2", "minutes", "echo", "tea", "time"]) == 0
    assert sh_roles.r_at(d, ["10:30", "echo later"]) == 0
    assert io.got[1] == b"job 1 at 2026-11-08 09:02\njob 2 at 2026-11-08 10:30\n"
    io.got[1] = b""
    assert sh_roles.r_at(d, ["-l"]) == 0
    assert io.got[1] == (b"1  2026-11-08 09:02  echo tea time\n"
                         b"2  2026-11-08 10:30  echo later\n")
    assert sh_roles.r_at(d, ["-r", "2", "9"]) == 1 and b"9: no such job" in io.got[2]
    assert sorted(tab.ats) == [1]
    assert sh_roles.r_at(d, ["sometime", "echo"]) == 2
    clock.t += 125
    seq = L.next()
    _tick()
    _settle(tab)
    pids = sorted({r[3] for r in L.read(seq, 1 << 30) if L.name(r[3]) == "at"})
    assert [_logged(p) for p in pids] == [[(L.INFO, "echo tea time"), (L.INFO, "tea time"),
                                            (L.INFO, "exit 0")]]
    assert tab.ats == {}
    (ws.system / "crontab").write_text("15 10 * * * echo a\nnope\n")
    io.got = {1: b"", 2: b""}
    assert sh_roles.r_cron(d, ["reload"]) == 1
    assert io.got[1] == b"1 line\n" and b"line 2 passed over: nope" in io.got[2]
    io.got[1] = b""
    assert sh_roles.r_cron(d, []) == 0
    assert io.got[1] == b"2026-11-08 10:15  15 10 * * * echo a\n"


def test_kill_interrupts_a_parked_job(monkeypatch, tmp_path):
    from runtime.sh_door import ShellDoor, ESRCH
    moy_sh = _sh()
    _Clock(monkeypatch, _at(2026, 11, 8, 9, 0))
    ws = _WS(tmp_path)
    ws.procs = type("P", (), {"rows": lambda self: []})()
    tab = C.Cron()
    tab.bind(ws)
    monkeypatch.setattr(C, "CRON", tab)
    tab.queue.append(("at", "sleep 5; echo never"))
    tab.pump()
    assert tab.job is not None and tab.poll is not None
    pid = tab.job[0]
    assert S.SCHED.procs[pid].kind == C.JOB
    door = ShellDoor(ws)
    assert door(b"kill\0%d\x0015" % pid) == 0
    assert door(b"kill\0%d\x0015" % (pid + 1000)) == ESRCH
    t0 = time.monotonic()
    _settle(tab)
    assert time.monotonic() - t0 < 2
    assert _logged(pid)[-1] == (L.INFO, "exit 130")
    moy_sh.jobs()


def test_a_vm_stop_carries_the_at_jobs_and_runs_the_minute_it_held(monkeypatch, tmp_path):
    """The resume record carries what the stop would lose (cron.held): the at
    jobs, armed again after it, one whose time passed meanwhile run at the
    next frame, and the crontab minute the stop held, run once."""
    _sh()
    clock = _Clock(monkeypatch, _at(2026, 11, 8, 6, 29, 30))
    ws = _WS(tmp_path)
    (ws.system / "crontab").write_text("30 6 * * * echo minute\n")
    tab = C.Cron()
    tab.bind(ws)
    tab.reload()
    tab.add_at(_at(2026, 11, 8, 6, 40), "echo late")
    tab.add_at(_at(2026, 11, 9), "echo later")
    held = tab.held()
    assert held == {"due": _at(2026, 11, 8, 6, 30),
                    "at": [[_at(2026, 11, 8, 6, 40), "echo late"], [_at(2026, 11, 9), "echo later"]]}
    for wid in [tab.wid] + [a[2] for a in tab.ats.values()]:
        S.SCHED.plain_cancel(wid)                          # the VM goes, its alarms with it
    clock.t = _at(2026, 11, 8, 6, 45)
    after = C.Cron()
    after.bind(ws)
    after.carry(held)
    seq = L.next()
    after.reload()
    after.resume()
    _settle(after)
    _tick()
    _settle(after)
    ran = [r[4] for r in L.read(seq, 1 << 30) if L.name(r[3]) == "cron"]
    late = [r[4] for r in L.read(seq, 1 << 30) if L.name(r[3]) == "at"]
    assert ran == ["echo minute", "minute", "exit 0"]
    assert late == ["echo late", "late", "exit 0"]
    assert [a[:2] for a in after.ats.values()] == [(_at(2026, 11, 9), "echo later")]
    assert after.due_at == _at(2026, 11, 9, 6, 30)


def test_the_resume_record_keeps_the_at_jobs_that_fit(monkeypatch):
    from runtime import console_home
    import json
    monkeypatch.setattr(C.CRON, "ats", {i: (1000 + i, "echo %s" % ("x" * 60), 0)
                                         for i in range(20)})
    monkeypatch.setattr(C.CRON, "due_at", 60)
    text = console_home._with_cron({"tab": "games"}, json)
    rec = json.loads(text)
    assert len(text.encode()) <= console_home.RESUME_MAX
    assert rec["cron"]["due"] == 60 and 0 < len(rec["cron"]["at"]) < 20
    assert rec["cron"]["at"][0] == [1000, "echo " + "x" * 60]
