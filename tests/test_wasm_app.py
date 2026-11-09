"""A compiled app reaching its roles through imports (docs/history/kernel_appabi_2026-10.md
section 4): the app ABI's import adapter (native/moy_app/moy_app_wasm.h) on
the host's WAMR, the load's grant check and the VM-free verdict's clauses.

  * tests/fixtures/wasm/app_roles.moy imports prefs and theme rows from module
    "moybyte.app" (moy-spec SPEC.md 16.2), keeps a run count in prefs and paints
    the theme's first token, with no upcall of any class;
  * the same module one permission short is refused at load, the import named;
  * tests/fixtures/wasm/app_shell.moy imports a shell-served row, and the
    verdict keeps the VM for it with the import as its `why` (moy_play's
    census, over native/moy_app's table as on a board);
  * tests/fixtures/wasm/app_talk.moy, a C app, makes the console calls a
    Python and a Lua app make (docs/app_api_v1.md sections 8 and 9): it pings
    the Echo service and reads the answer as its event record, notifies,
    awards the badge it declares, hears its alarm as the system's message and
    writes the OS log, each under the permission it is under on every tier,
    and reads an item handed to it by its window event.
"""

import json
import os

import pytest

from runtime import host_app, moy_evq, moy_loop, notifications, os_log, scheduler as S
from runtime import moy_log as L
from tools import wasm_cart
from ws_helpers import open_cart

import moy_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "wasm")
ROLES = os.path.join(FIXTURES, "app_roles.moy")
SHELL = os.path.join(FIXTURES, "app_shell.moy")
TALK = os.path.join(FIXTURES, "app_talk.moy")
ECHO = os.path.join(ROOT, "tests", "fixtures", "apps", "echo_lua.moy")
_DT = 1.0 / 30.0


def _binding_or_skip():
    from runtime import wasm_binding, wasm_host
    if wasm_host.available():
        return
    why = wasm_binding.why_unavailable() or "unknown"
    if os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"):
        pytest.fail("the host wasm binding did not build: %s" % why)
    pytest.skip("no host wasm binding: %s" % why)


def _store(tmp_path, perms=None):
    root = str(tmp_path / "carts")
    host_app.moy_carts.ensure_dirs(root)
    dst = os.path.join(root, "app_roles.moy")
    wasm_cart.build(ROLES, dst)
    if perms is not None:
        p = os.path.join(dst, "manifest.json")
        with open(p) as f:
            man = json.load(f)
        man["moybyte"]["permissions"] = perms
        with open(p, "w") as f:
            json.dump(man, f)
    return root


def _frames(ws, n):
    for _ in range(n):
        ws._dirty = True
        ws.frame(_DT)


def _runs(ws):
    """The count the app kept, read back through a grant of its own id."""
    app = ws.app_abi
    g = app.grant(moy_app.id_for("app_roles", "App Roles Wasm"), ("prefs",), run=True)
    try:
        return moy_app.Prefs(app, g).get("runs", None)
    finally:
        app.end(g)


def test_a_compiled_app_reaches_prefs_and_theme_with_no_upcall(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path))
    t0 = moy_loop.upcalls()[1]
    open_cart(ws, "App Roles Wasm")
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    _frames(ws, 10)
    assert ws.run.player.cart_error is None, ws.run.player.cart_error
    from runtime import moy_play
    books = moy_play.info()[5]
    assert tuple(books) == (0, 0, 0, 0, 0, 0), "the run crossed into Python: %r" % (books,)
    assert moy_loop.upcalls()[1][5] == t0[5], "a C row went through the ROLE door"
    # The band the cart paints is the live theme's first token.
    g = ws.app_abi.grant("probe.theme", ("theme",), run=True)
    token = moy_app.Theme(ws.app_abi, g).token(0)
    ws.app_abi.end(g)
    cv = ws.sys_canvas
    i = 2 * (34 * cv.w + 300)          # the band under the app bar, past the toast
    assert cv._buf[i] | cv._buf[i + 1] << 8 == cv._wire[token & 63]
    ws.exit()
    assert _runs(ws) == 1
    open_cart(ws, "App Roles Wasm")
    _frames(ws, 2)
    ws.exit()
    assert _runs(ws) == 2, "the count kept is the app's, run to run"


def test_the_same_module_one_permission_short_is_refused_naming_the_import(tmp_path):
    _binding_or_skip()
    ws = host_app.build_workstation(_store(tmp_path, perms=[]))
    open_cart(ws, "App Roles Wasm")
    err = ws.run.player.cart_error or ""
    assert "moybyte.app.prefs_get" in err, err
    assert "prefs" in err.split("moybyte.app.prefs_get", 1)[1], err


def test_a_module_that_declares_no_extension_reaches_no_role(tmp_path):
    _binding_or_skip()
    root = _store(tmp_path)
    p = os.path.join(root, "app_roles.moy", "manifest.json")
    with open(p) as f:
        man = json.load(f)
    del man["extensions"]
    with open(p, "w") as f:
        json.dump(man, f)
    ws = host_app.build_workstation(root)
    open_cart(ws, "App Roles Wasm")
    err = ws.run.player.cart_error or ""
    assert "moybyte.app.prefs_get" in err and "extensions it declares" in err, err


def test_the_verdict_keeps_the_vm_for_a_shell_import(tmp_path):
    """moy_play's VM-free rule over the two fixtures, as a board's census
    reads them: every import C-served is free; one shell-served row keeps the
    VM and is named."""
    from runtime import moy_play
    roles = str(tmp_path / "app_roles.moy")
    shell = str(tmp_path / "app_shell.moy")
    wasm_cart.build(ROLES, roles)
    wasm_cart.build(SHELL, shell)
    assert moy_play.census(roles) == ("wasm", True, "free")
    assert moy_play.census(shell) == ("wasm", False,
                                      "import moybyte.app.theme_set_variant")
    # A role whose permission the manifest does not name is no VM-free pass:
    # the native set's rule is unchanged for every other permission.
    p = os.path.join(roles, "manifest.json")
    with open(p) as f:
        man = json.load(f)
    man["moybyte"]["permissions"] = ["prefs", "net"]
    with open(p, "w") as f:
        json.dump(man, f)
    assert moy_play.census(roles) == ("wasm", False, "permission")



def _talk(tmp_path, off=()):
    """App Talk opened beside the running Echo service, its permissions `off`
    turned off in Settings first: its process's OS log lines after its frames
    and the scheduler's serves, and its pid."""
    import shutil
    _binding_or_skip()
    if S._svc() is None:
        pytest.skip("no Lua runtime on this host")
    try:
        built = str(tmp_path / "built" / "app_talk.moy")
        wasm_cart.build(TALK, built)
    except wasm_cart.ToolchainMissing as exc:
        pytest.skip(str(exc))
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    shutil.copytree(built, os.path.join(root, "app_talk.moy"))
    shutil.copytree(ECHO, os.path.join(root, "echo_lua.moy"))
    ws.carts.rescan()
    echo = S.start_cart(ws, next(c for c in ws.carts.all if c["title"] == "Echo"))
    assert echo is not None
    for perm in off:
        ws.consent.turn("local.talk", perm, False)
    since = L.next()
    try:
        open_cart(ws, "Talk")
        assert ws.run.player.cart_error is None, ws.run.player.cart_error
        pid = ws.run.player.pid
        for _ in range(4):
            S.SCHED.mail()
            S.SCHED.poll()
            S.SCHED.tick(0)
            _frames(ws, 1)
        assert ws.run.player.cart_error is None, ws.run.player.cart_error
    finally:
        S.SCHED.stop(echo)
    return ws, pid, [t for _s, _t, _l, q, t in os_log.records(since, 256) if q == pid]


@pytest.fixture
def talk(tmp_path):
    """_talk, its run closed after, so its queue goes with it."""
    held = []

    def run(off=()):
        got = _talk(tmp_path, off)
        held.append(got[:2])
        return got
    yield run
    for ws, pid in held:
        ws.exit()
        assert not moy_evq.has(pid), "the run's queue closes with it"


def test_a_compiled_app_talks_to_a_service_notifies_awards_and_hears_its_alarm(talk):
    ws, pid, lines = talk()
    assert "send 1" in lines, lines
    assert "heard pong from local.echo [42]" in lines, lines
    note = [ln for ln in lines if ln.startswith("notify ")]
    assert note and int(note[0].split()[1]) > 0, lines
    assert [(n.summary, n.body, n.pid) for n in notifications.NOTES.list
            if n.pid == pid] == [("Talk", "Sent a ping", pid)]
    assert "award 1" in lines, lines
    assert ws.ach.has("local.talk", "hello"), "the badge it declares is found"
    at = [ln for ln in lines if ln.startswith("at ")]
    assert at and int(at[0].split()[1]) > 0, lines
    assert "heard moybyte.at from system [%s]" % at[0].split()[1] in lines, lines


def test_a_compiled_app_s_timers_are_the_system_s_messages_and_cancel_its_own(talk):
    """moybyte_after and moybyte_every answer their timer's id and go off as
    the system's message "moybyte.timer" carrying it; moybyte_cancel ends a
    timer of the app's own and no other process's."""
    from runtime import moybyte
    ws, pid, lines = talk()
    once = [ln for ln in lines if ln.startswith("after ")]
    rep = [ln for ln in lines if ln.startswith("every ")]
    assert once and int(once[0].split()[1]) > 0, lines
    assert rep and int(rep[0].split()[1]) > 0, lines
    assert "heard moybyte.timer from system [%s]" % once[0].split()[1] in lines, lines
    ticks = [ln for ln in lines if ln == "heard moybyte.timer from system [%s]"
             % rep[0].split()[1]]
    assert ticks and "cancel 1" in lines, lines
    assert lines.count("cancel 1") == 1, "a tick after the cancel is a cancel of none"
    assert int(rep[0].split()[1]) not in moybyte.WAKES.by_id
    was = L.current()
    L.current(moybyte.SHELL)
    shell = moybyte.WAKES.after(60000, lambda: None)
    try:
        L.current(pid)
        assert moybyte.cancel_message(shell) == 0, "another process's wake is not its own"
        assert shell in moybyte.WAKES.by_id
    finally:
        L.current(was)
        moybyte.WAKES.cancel(shell)


def test_a_compiled_app_hears_an_item_handed_to_it_and_reads_it(talk):
    from runtime import intents
    ws, pid, _lines = talk()
    since = L.next()
    assert ws.run.player.window_event("moy_open", intents.Grant("text/plain", "r", data=b"a gift"))
    _frames(ws, 2)
    lines = [t for _s, _t, _l, q, t in os_log.records(since, 64) if q == pid]
    heard = [ln for ln in lines if ln.startswith("heard moy_open from system [")]
    assert heard and heard[0].endswith(', "text/plain", "r", null]'), lines
    assert "item a gift" in lines, lines


def test_a_compiled_app_is_refused_what_its_permissions_do_not_grant(talk):
    _ws, _pid, lines = talk(off=("notifications", "alarms"))
    assert "notify -4" in lines and "at -4" in lines, lines      # MOY_APP_DENIED
    assert "heard pong from local.echo [42]" in lines, lines     # messages need none
