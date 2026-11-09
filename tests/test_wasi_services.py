"""A WASI service run by the scheduler (runtime/system/scheduler.py, COMPILED
SERVICES; native/moy_wasm/moy_wasi.h, A SERVICE; docs/os_2026-11/processes.md
section 1, the service model): started under its consent on a thread of its
own with no shell session, listed with the other services, its moybyte.os
`send` to the VM's processes served at the next frame's start, its end by
itself reaped into the restart policy, its stop the descriptor's end of file,
and a wake that never polls again struck by the watchdog, then killed (137)
and terminated once it strikes out. The native tier's twin is
tests/test_native_services.py; the fixture is
tests/fixtures/wasm/wasi_svc.moy, which tests/test_wasi_commands.py runs as a
command."""

import json
import os
import shutil
import time

import pytest

from runtime import native_build

if native_build.cc() is None:
    pytest.skip("no C compiler", allow_module_level=True)

from runtime import moy_evq, moybyte, os_log, scheduler as S  # noqa: E402
from runtime import moy_log as L  # noqa: E402
from test_scheduler import _clear  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "wasm", "wasi_svc.moy")


def _required():
    return bool(os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from runtime import wasm_binding, wasm_host
    from tools import jet_cart, wasm_cart
    if not wasm_host.available():
        why = "no host wasm binding: %s" % (wasm_binding.why_unavailable() or "?")
        if _required():
            pytest.fail(why)
        pytest.skip(why)
    try:
        jet_cart.wasi_sdk(fetch=True)
    except Exception as exc:  # noqa: BLE001
        if _required():
            pytest.fail("no wasi-sdk: %s" % exc)
        pytest.skip("no wasi-sdk: %s" % exc)
    dst = str(tmp_path_factory.mktemp("wasisvc") / "wasi_svc.moy")
    wasm_cart.build(FIXTURE, dst)
    return dst


@pytest.fixture
def svc(tmp_path, built):
    """(ws, the scheduler, the service cart as the shelf holds it, a peer
    process of the service's own app that the VM serves)."""
    from runtime import wasm_binding
    from runtime.host_app import build_workstation
    carts = tmp_path / "carts"
    carts.mkdir()
    shutil.copytree(built, str(carts / "wasi_svc.moy"))
    ws = build_workstation(str(carts))
    assert "wasm" in ws.runtimes
    cart = [c for c in ws.carts.all if c.get("id") == "wasi.svc"][0]
    wakes = moybyte.WAKES
    _clear(wakes)
    s = S.SCHED
    s.reset()
    peer = L.spawn("peer")
    assert moy_evq.open(peer, "wasi.svc", "", True) == moy_evq.OK
    assert moy_evq.listen(peer, "pong", True) == moy_evq.OK
    yield ws, s, cart, peer
    moy_evq.close(peer)
    s.reset()
    _clear(wakes)
    t0 = time.monotonic()
    while wasm_binding.services_live() and time.monotonic() - t0 < 2:
        wasm_binding.reap_services()
        time.sleep(0.01)
    assert wasm_binding.services_live() == 0


def _polled(s, cond, secs=5.0):
    """The scheduler's poll run until cond() holds or `secs` pass."""
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 >= secs:
            return False
        s.poll()
        time.sleep(0.01)
    return True


def _texts(since, pid):
    return [t for _s, _t, _l, q, t in os_log.records(since, 128) if q == pid]


def test_a_wasi_service_is_started_listed_and_stopped_as_any_service(svc):
    ws, s, cart, peer = svc
    since = L.next()
    pid = S.start_cart(ws, cart, s)
    assert pid is not None
    p = s.procs[pid]
    assert p.kind == S.SERVICE and p.state == S.BACKGROUND and p.rt is ws.runtimes["wasm"]
    assert s.services() == [p] and s.find("wasi.svc") is p
    assert s._poll                                  # the poll's wake, while it lives
    assert moy_evq.app(pid) == "wasi.svc"           # its queue, the scheduler's
    assert _polled(s, lambda: moy_evq.send(peer, "wasi.svc", "ping", "[1]")[0] == 1)
    s.wakes.queued = 0
    assert _polled(s, lambda: s.wakes.queued == 1)  # its send to a VM process
    assert moy_evq.take(peer) == (pid, "wasi.svc", "pong", "[1]")
    assert _polled(s, lambda: p.cost_us > 0 and p.mem > 0)  # its wakes and memory, onto its row
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.1:              # its clock ticks meanwhile
        s.poll()
        time.sleep(0.01)
    assert s.stop(pid)
    assert p.state == S.EXITED and p.code == 0 and p.rt is None
    assert not moy_evq.has(pid)
    texts = _texts(since, pid)
    assert texts[0] == "start wasi.svc" and texts[-1] == "exit 0"
    assert "listening" in texts                     # its stdout, the OS log's lines
    assert any(t.startswith("ping ") and t.endswith(" from wasi.svc: 1") for t in texts)
    assert "stopped after 1 pings, ticking" in texts    # the descriptor's end of file
    s.poll()
    assert not s._poll                              # gone with the last service


def test_an_end_by_itself_is_reaped_into_its_restart_policy(svc):
    ws, s, cart, peer = svc
    store = {}

    class Store:
        def get(self, k, d=None):
            return store.get(k, d)

        def set(self, k, v):
            store[k] = v
    pid = S.start_cart(ws, cart, s)
    s.store = Store()
    s.set_policy("wasi.svc", S.ON_CRASH)
    p = s.procs[pid]
    assert _polled(s, lambda: moy_evq.send(peer, "wasi.svc", "quit", "[]")[0] == 1)
    assert _polled(s, lambda: p.state == S.EXITED)
    assert p.code == 7 and p.rt is None
    assert "wasi.svc" in s.pending                  # on-crash: its restart waits
    s.pending.clear()


def test_a_wake_that_never_polls_again_is_struck_out_killed_and_terminated(svc):
    """The watchdog at the poll strikes a wake once for each abort budget it
    runs on, and the third strike, which suspension cannot hold, kills it
    (137): the instance is terminated, and `_start` returns at its next
    import call (its sleep's slice)."""
    from runtime import wasm_binding
    ws, s, cart, peer = svc
    since = L.next()
    pid = S.start_cart(ws, cart, s)
    p = s.procs[pid]
    assert _polled(s, lambda: moy_evq.send(peer, "wasi.svc", "spin", "[]")[0] == 1)
    assert _polled(s, lambda: p.state == S.EXITED, 10)
    assert p.code == L.KILLED
    texts = _texts(since, pid)
    assert "killed: a wake ran past its strikes without a poll" in texts
    assert sum("strike" in t for t in texts) >= 3
    assert wasm_binding.services_live() == 0        # its thread returned, its record freed


def test_a_console_without_the_wasm_tier_refuses_one(svc):
    ws, s, cart, _peer = svc
    since = L.next()
    del ws.runtimes["wasm"]
    assert S.start_cart(ws, cart, s) is None
    p = s.services()[0]
    assert p.state == S.EXITED and p.code == S.EXIT_CRASHED
    assert "a wasm service does not run on this console" in _texts(since, p.pid)
    assert not moy_evq.has(p.pid)


def test_the_pinger_wakes_a_wasi_service_that_receives_its_ping(tmp_path, built):
    """tests/fixtures/pinger_py.moy, the Python service the S3 measurements
    wake a service with (#246): every PERIOD_MS a "ping" from another app,
    which the WASI service takes because its manifest lists it."""
    from runtime.host_app import build_workstation
    carts = tmp_path / "carts"
    carts.mkdir()
    shutil.copytree(built, str(carts / "wasi_svc.moy"))
    man_path = carts / "wasi_svc.moy" / "manifest.json"
    man = json.loads(man_path.read_text())
    man["moybyte"]["receives"] = ["ping"]
    man_path.write_text(json.dumps(man))
    shutil.copytree(os.path.join(ROOT, "tests", "fixtures", "pinger_py.moy"),
                    str(carts / "pinger_py.moy"))
    ws = build_workstation(str(carts))
    by_id = {c.get("id"): c for c in ws.carts.all}
    wakes = moybyte.WAKES
    _clear(wakes)
    s = S.SCHED
    s.reset()
    try:
        since = L.next()
        svc_pid = S.start_cart(ws, by_id["wasi.svc"], s)
        pinger = S.start_cart(ws, by_id["local.pinger"], s)
        assert svc_pid is not None and pinger is not None
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5:
            s.tick(20000)
            s.poll()
            if sum(t.startswith("ping ") and " from local.pinger" in t
                   for t in _texts(since, svc_pid)) >= 2:
                break
            time.sleep(0.01)
        pings = [t for t in _texts(since, svc_pid) if t.startswith("ping ")]
        assert len(pings) >= 2 and all(" from local.pinger: " in t for t in pings)
    finally:
        s.reset()
        _clear(wakes)


def _granted(ws, cart, perms):
    """The service's cart with its manifest asking for `perms`, rescanned."""
    path = os.path.join(cart["path"], "manifest.json")
    with open(path) as f:
        man = json.load(f)
    man["moybyte"]["permissions"] = list(perms)
    with open(path, "w") as f:
        json.dump(man, f)
    ws.carts.rescan()
    return [c for c in ws.carts.all if c.get("id") == "wasi.svc"][0]


@pytest.mark.parametrize("perms", [("notifications",), ()])
def test_a_wasi_service_notifies_under_its_permission_and_writes_the_log(svc, perms):
    """moybyte.os `notify` is the console call a Python service's
    moybyte.notify is, posted on the VM's queue and answered at the poll as
    the service's process; `log` writes the OS log under its pid."""
    from runtime import notifications
    ws, s, cart, peer = svc
    notifications.NOTES.reset()
    since = L.next()
    pid = S.start_cart(ws, _granted(ws, cart, perms), s)
    assert pid is not None
    assert _polled(s, lambda: moy_evq.send(peer, "wasi.svc", "note", '["hi"]')[0] == 1)
    assert _polled(s, lambda: "noted" in _texts(since, pid))
    # its own app declares no service: a `type: service` cart's call is
    # ABSENT, read as WASI's NOENT (44), as an ELF module reads ENOENT
    assert _polled(s, lambda: "service_running -1 errno 44" in _texts(since, pid))
    texts = _texts(since, pid)
    mine = [n for n in notifications.NOTES.list if n.pid == pid]
    if perms:
        assert [(n.summary, n.body) for n in mine] == [("Service Wasi", '["hi"]')]
        assert "note %d errno 0" % mine[0].id in texts, texts
    else:
        assert mine == [] and "note -1 errno 2" in texts, texts     # WASI's ACCES
    assert s.stop(pid)


@pytest.mark.parametrize("perms", [("alarms",), ()])
def test_a_wasi_service_s_timers_and_alarm_are_the_system_s_messages(svc, perms):
    """moybyte.os `after`, `every` and `at` are console calls answered with
    the wake's id, each heard on the event descriptor as the system's message
    carrying it (a timer's "moybyte.timer", the alarm's "moybyte.at"); `cancel` ends its own timer; `at` needs
    `alarms`."""
    ws, s, cart, peer = svc
    since = L.next()
    pid = S.start_cart(ws, _granted(ws, cart, perms), s)
    assert pid is not None
    assert _polled(s, lambda: moy_evq.send(peer, "wasi.svc", "wake", "[]")[0] == 1)
    assert _polled(s, lambda: s.tick() >= 0 and any(t.startswith("cancel ") for t in _texts(since, pid))), \
        _texts(since, pid)
    texts = _texts(since, pid)
    after = [int(t.split()[1]) for t in texts if t.startswith("after ")]
    every = [int(t.split()[1]) for t in texts if t.startswith("every ")]
    assert after and after[0] > 0 and every and every[0] > 0, texts
    assert "moybyte.timer from system [%d]" % after[0] in _texts(since, pid)
    assert "cancel 1" in _texts(since, pid), _texts(since, pid)
    assert every[0] not in moybyte.WAKES.by_id
    at = [t for t in _texts(since, pid) if t.startswith("at ")]
    if perms:
        wid = int(at[0].split()[1])
        assert wid > 0, _texts(since, pid)
        assert _polled(s, lambda: s.tick() >= 0
                       and "moybyte.at from system [%d]" % wid in _texts(since, pid))
    else:
        assert at == ["at -1 errno 2"], at                         # WASI's ACCES
    assert s.stop(pid)


@pytest.mark.parametrize("perms", [("prefs", "clipboard"), ()])
def test_a_wasi_service_s_app_rows_answer_under_its_permissions(svc, perms):
    """moybyte.os `prefs_*` and `clipboard_*` are the role table's rows for
    the service's own grant (the one its start made): a pref written and read
    back, the clipboard's text, a buffer too small RANGE, and each refused
    ACCES without its permission; `open_with` is the console call, 0 where no
    app opens the item."""
    ws, s, cart, peer = svc
    since = L.next()
    pid = S.start_cart(ws, _granted(ws, cart, perms), s)
    assert pid is not None
    assert _polled(s, lambda: moy_evq.send(peer, "wasi.svc", "wake", "[]")[0] == 1)
    assert _polled(s, lambda: any(t.startswith("open_with ") for t in _texts(since, pid))), \
        _texts(since, pid)
    got = [t for t in _texts(since, pid) if t.split(" ")[0] in (
        "prefs.set", "prefs.get", "clipboard.put_text", "clipboard.text", "clipboard.short",
        "open_with")]
    if perms:
        assert got == ["prefs.set 0 errno 0", "prefs.get 42 errno 0",
                       "clipboard.put_text 0 errno 0", "clipboard.text from wasi errno 0",
                       "clipboard.short -1 errno 68", "open_with 0 errno 0"], got
    else:
        assert got == ["prefs.set -1 errno 2", "prefs.get  errno 2",
                       "clipboard.put_text -1 errno 2", "clipboard.text  errno 2",
                       "clipboard.short -1 errno 2", "open_with 0 errno 0"], got
    assert s.stop(pid)


def test_the_fixture_is_a_service():
    with open(os.path.join(FIXTURE, "manifest.json")) as f:
        man = json.load(f)
    assert man["runtime"] == "wasm" and man["moybyte"]["type"] == "service"
