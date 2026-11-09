"""The native (ELF) tier on the host (native/moy_elf/README.md): a module built
by the SDK (tools/elf_cart.py) run as an installed command in the Terminal's
session over the console's own door (runtime/shell/sh_door.py), and as a
service, through the boards' C (native/moy_elf/moy_os.c) over the host's
loader (runtime/host/moyhost_elf.c). Its args, env, stdio through the
session's pipes and redirections, exit codes, files under its permissions,
threads, Ctrl-C and budget; the sweep of what it held; a module that needs a
newer console refused; the "full_access" consent."""

import json
import os
import re
import shutil
import time

import pytest

from runtime import native_build

if native_build.cc() is None:
    pytest.skip("no C compiler", allow_module_level=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "elf")
NAMES = ("elf_hello", "elf_cat", "elf_probe", "elf_svc", "elf_nc", "elf_head", "elf_libc",
         "elf_app", "elf_crypto")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from tools import elf_cart
    out = tmp_path_factory.mktemp("elf")
    got = {}
    for name in NAMES:
        dst = str(out / (name + ".moy"))
        elf_cart.build(os.path.join(FIXTURES, name + ".moy"), dst, ["host"], signed=False)
        got[name] = dst
    return got


@pytest.fixture
def term(tmp_path, built):
    from runtime.host_app import build_workstation
    carts = tmp_path / "carts"
    carts.mkdir()
    (tmp_path / "files" / "Documents").mkdir(parents=True)
    for name, path in built.items():
        shutil.copytree(path, str(carts / (name + ".moy")))
    bare = carts / "elf_bare.moy"
    shutil.copytree(built["elf_probe"], str(bare))
    man = json.loads((bare / "manifest.json").read_text())
    man["id"], man["title"] = "elf.bare", "Bare Native"
    man["moybyte"]["permissions"] = ["full_access"]
    (bare / "manifest.json").write_text(json.dumps(man))
    blind = carts / "elf_blind.moy"
    shutil.copytree(built["elf_hello"], str(blind))
    man = json.loads((blind / "manifest.json").read_text())
    man["id"], man["title"] = "elf.blind", "Blind Native"
    man["moybyte"]["permissions"] = []
    (blind / "manifest.json").write_text(json.dumps(man))
    ncblind = carts / "elf_ncblind.moy"
    shutil.copytree(built["elf_nc"], str(ncblind))
    man = json.loads((ncblind / "manifest.json").read_text())
    man["id"], man["title"] = "elf.ncblind", "Netcat Blind"
    man["moybyte"]["permissions"] = ["full_access"]
    (ncblind / "manifest.json").write_text(json.dumps(man))
    ws = build_workstation(str(carts))
    assert "elf" in ws.runtimes
    app = ws.terminal_app
    app.open()
    app.draw(0)
    app.draw(0)
    yield ws, app
    app.close_session()


def run(app, text, limit=20.0):
    from runtime import moy_sh
    app.face.set_line(text)
    app.face.submit()
    t0 = time.monotonic()
    while app.face.busy() and time.monotonic() - t0 < limit:
        moy_sh.jobs()
        time.sleep(0.002)


def out(app, text):
    """The lines `text` printed, its own echoed line left out."""
    app.face.take()
    before = app.face.lines()
    n0 = 0 if before == [""] else len(before)
    run(app, text)
    app.face.take()
    lines = [re.sub("\x1b\\[[0-9;]*m", "", ln) for ln in app.face.lines()]
    echo, at = "", n0
    want = ("$" + text).replace(" ", "")
    while at < len(lines) and not echo.replace(" ", "").endswith(want):
        echo += lines[at]
        at += 1
    return lines[at:]


def test_app_calls_answer_under_the_command_s_permissions(term):
    """The app calls a native command makes (sdk/include/moybyte/os.h, the
    rows native/os_calls.json gives the ELF tier) reach the console's role
    table for its own grant: prefs, the clipboard, its files folder and a
    notification under the permissions it was granted (a document
    duplicated, renamed and a free name found), a buffer too small
    ERANGE, a folder it was not granted and a role it lacks EACCES; award and
    progress reach the achievements process, which finds the badge it
    declares; a timer it makes is its own to cancel, once, and an alarm needs
    "alarms"."""
    from runtime.notifications import NOTES
    _ws, app = term
    assert out(app, "elf.app/app; echo status $?") == [
        "prefs.set 0", "prefs.get 42", "clipboard.put_text 0", "clipboard.text from C",
        "clipboard.short -1 ERANGE", "files.save note", "files.load # hi", "",
        "files.list note,", "files.load.other -1 EACCES", "files.duplicate note_2",
        "files.rename kept", "files.new_name note_2", "files.new_name.untitled doc_1", "notify 1",
        "nav.open_app -1 EACCES", "award 1", "progress 1", "after 1", "every 1", "cancel 1",
        "cancel.again 0", "cancel.every 1", "at -1 EACCES", "status 0"]
    assert [(n.app, n.summary, n.body) for n in NOTES.list if n.summary == "Hello"] == [
        ("App Calls", "Hello", "from C")]
    ws = _ws
    ws.frame(1 / 30)
    assert ws.ach.has("elf.app", "first"), "a badge it declares, awarded from C"
    assert not ws.ach.has("elf.app", "far")


def test_a_native_command_picks_an_item_and_hears_it_as_a_message(term):
    """`moy_pick` from C (tests/fixtures/elf/elf_app.moy, `app pick`): a
    command holds no `front`, so its pick waits as a notification, whose
    act puts the console's picker up; the document picked reaches the
    process as the message `moy_open` on its event descriptor
    (moybyte.hand_item), its handle what moy_item_read and moy_item_write
    take -- a short buffer ERANGE, a write the store keeps -- and a cancelled
    pick hands handle 0, which reads ENOENT."""
    from runtime import moy_sh
    from runtime import intents as I
    from runtime.notifications import NOTES
    ws, app = term
    ws.carts_store.save_file("docs", "picked", "# picked\n", ws.carts_root)
    app.face.take()
    before = app.face.lines()
    app.face.set_line("elf.app/app pick; echo status $?")
    app.face.submit()
    answers = [lambda: I.INTENTS.picked(I.INTENTS.picking[3].index("Documents/picked.md")),
               ws.confirm.cancel]
    t0 = time.monotonic()
    while app.face.busy() and time.monotonic() - t0 < 20:
        moy_sh.jobs()
        asked = [n for n in NOTES.list if n.then is not None and n.app == "App Calls"]
        if asked:
            assert asked[0].summary == "Wants you to pick a file"
            assert NOTES.act(asked[0].id)
            assert I.INTENTS.picking is not None and ws.confirm.open
            answers.pop(0)()
        time.sleep(0.002)
    assert not answers, "both picks reached the picker"
    app.face.take()
    lines = [re.sub("\x1b\\[[0-9;]*m", "", ln) for ln in app.face.lines()][len(before):]
    at = next(i for i, ln in enumerate(lines) if ln.startswith("on "))
    assert lines[at:] == [
        "on 0", "pick 1", 'open "text/markdown", "rw", "picked.md"]',
        "item_read # picked", "", "item_read.short -1 ERANGE", "item_write 1",
        "item_read.again # kept", "", "pick.again 1",
        "cancelled [0, null, null, null]", "item_read.none -1 ENOENT", "status 0"]
    assert ws.carts_store.load_file("docs", "picked", ws.carts_root) == "# kept\n"


def test_the_crypto_rows_answer_a_native_command(term):
    """The crypto rows from C (tests/fixtures/elf/elf_crypto.moy): the known
    answers, and each length refused an errno, never a crash."""
    import crypto_vectors
    _ws, app = term
    assert out(app, "elf.crypto/crypto; echo status $?") == crypto_vectors.PROGRAM + ["status 0"]


def test_hello_runs_and_takes_its_args(term):
    _ws, app = term
    assert out(app, "elf.hello/hello one two; echo status $?") == [
        "hello from a native module one two", "status 0"]


def test_the_environment_folder_terminal_and_clocks(term):
    _ws, app = term
    assert out(app, "export GREETING=hi; elf.probe/probe env GREETING") == ["hi"]
    assert out(app, "elf.probe/probe env NOPE") == ["(unset)"]
    assert out(app, "cd /files/Documents; elf.probe/probe cwd") == ["/files/Documents"]
    assert out(app, "elf.probe/probe term") == ["term 1"]
    assert out(app, "elf.probe/probe time") == ["mono 1", "wall 1"]


def test_cat_through_pipes_redirections_and_files(term, tmp_path):
    _ws, app = term
    assert out(app, "cd /files/Documents; printf 'one\\ntwo\\n' > a.txt; elf.cat/cat a.txt") == ["one", "two"]
    assert out(app, "printf 'x\\ny\\n' | elf.cat/cat") == ["x", "y"]
    assert out(app, "elf.cat/cat < a.txt") == ["one", "two"]
    run(app, "elf.cat/cat a.txt - a.txt < a.txt > b.txt")
    assert (tmp_path / "files" / "Documents" / "b.txt").read_text() == "one\ntwo\n" * 3
    assert out(app, "elf.cat/cat missing.txt; echo status $?") == [
        "cat: missing.txt: No such file or directory", "status 1"]


def test_files_are_made_written_listed_moved_and_removed(term, tmp_path):
    _ws, app = term
    assert out(app, "elf.probe/probe files /files/Documents") == [
        "mkdir 0", "open 0", "write 7", "stat 7", "isdir 1", "rename 0", "entry b",
        "read 7", "native", "unlink 0", "rmdir 0"]
    assert not (tmp_path / "files" / "Documents" / "x").exists()


def test_a_path_outside_its_folders_is_refused(term, tmp_path):
    _ws, app = term
    (tmp_path / "secret.txt").write_text("no")
    assert out(app, "elf.probe/probe deny /secret.txt") == ["open -1"]
    assert out(app, "cd /files/Documents; elf.probe/probe deny ../secret.txt") == ["open -1"]
    (tmp_path / "files" / ".Trash").mkdir()
    (tmp_path / "files" / ".Trash" / "t.txt").write_text("gone")
    assert out(app, "elf.probe/probe deny /files/.Trash/t.txt") == ["open -1"]
    assert out(app, "cd /files/Documents; elf.probe/probe deny ../.Trash/t.txt") == ["open -1"]
    run(app, "printf 'x\\n' > /files/Documents/a.txt")
    assert out(app, "elf.bare/bare deny /files/Documents/a.txt") == ["open -1"]


def test_a_command_s_grant_ends_with_it(term):
    """Each native command's run grant (sh_door._grant) ends with the
    process: run after run, the grant table holds what it held."""
    ws, app = term
    out(app, "elf.hello/hello")
    held = ws.app_abi.count()
    for _ in range(5):
        out(app, "elf.hello/hello")
        out(app, "elf.hello/hello & wait")
    assert ws.app_abi.count() == held


def test_exit_codes_from_main_and_from_a_thread(term):
    _ws, app = term
    assert out(app, "elf.probe/probe exit 7; echo status $?") == ["status 7"]
    assert out(app, "elf.probe/probe texit 9; echo status $?") == ["status 9"]
    assert out(app, "elf.probe/probe nothing; echo status $?") == [
        "probe: no mode", "status 2"]


def test_threads_and_a_mutex(term):
    _ws, app = term
    assert out(app, "elf.probe/probe threads") == ["count 4000", "joined 6"]


def test_what_a_command_left_held_is_swept(term):
    from runtime import elf_binding
    _ws, app = term
    before = elf_binding.stats()
    assert out(app, "cd /files/Documents; elf.probe/probe leak l.txt; echo status $?") == [
        "held 1", "status 3"]
    after = elf_binding.stats()
    # the file, the folder, the mutex
    assert after["swept"] - before["swept"] == 3
    assert after["leaked"] == before["leaked"]


def test_ctrl_c_and_the_budget_end_a_run(term, monkeypatch):
    """A foreground run is a job the door steps (sh_door._compiled_steps), so
    the console goes on between its steps; Ctrl-C closes the job."""
    from runtime import sh_door
    ws, app = term
    rt = ws.runtimes["elf"]
    steps = []
    real = rt.step
    monkeypatch.setattr(rt, "step", lambda pid, ms: steps.append(pid) or real(pid, ms))
    app.face.interrupt()
    t0 = time.monotonic()
    got = out(app, "elf.probe/probe sleep; echo not-after")
    assert time.monotonic() - t0 < 5
    assert "not-after" not in got and app.face.status() == 130
    monkeypatch.setattr(sh_door, "COMMAND_MS", 300)
    assert out(app, "elf.probe/probe sleep; echo status $?") == [
        "killed: ran past 0 s", "status 137"]
    assert len(steps) > 1


def test_ctrl_c_mid_run_closes_its_job(term):
    """Ctrl-C while the door steps a running command stops its job, which
    ends the process with 130 and sweeps it."""
    from runtime import elf_binding, moy_sh
    _ws, app = term
    before = elf_binding.stats()
    app.face.set_line("elf.probe/probe sleep")
    app.face.submit()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.4:
        moy_sh.jobs()
        time.sleep(0.002)
    assert app.face.busy()
    app.face.interrupt()
    t0 = time.monotonic()
    while app.face.busy() and time.monotonic() - t0 < 10:
        moy_sh.jobs()
        time.sleep(0.002)
    assert not app.face.busy() and app.face.status() == 130
    after = elf_binding.stats()
    assert after["started"] == before["started"] + 1 and after["leaked"] == before["leaked"]


def log_text():
    from runtime import moy_log
    return "\n".join(r[4] for r in moy_log.read(0, 100000))


def test_in_the_background_its_output_is_the_log_s(term):
    _ws, app = term
    got = out(app, "elf.hello/hello in the background & wait $!; echo status $?")
    assert "status 0" in got
    assert not any("hello from" in ln for ln in got)
    assert "hello from a native module in the background" in log_text()


def test_a_native_cart_must_ask_for_full_access(term):
    _ws, app = term
    got = out(app, "elf.blind/blind; echo status $?")
    assert got[-1] == "status 126" and 'must ask for "full_acc' in got[0]


def test_a_module_needing_a_newer_console_is_refused(tmp_path):
    from runtime import elf_binding
    from tools import elf_cart
    src = tmp_path / "newer.c"
    src.write_text('__asm__(".pushsection .moy.rows,\\"\\",@progbits\\n.4byte 4000\\n.popsection");\n'
                   'int main(void) { return 0; }\n')
    mod = elf_cart.build_module([str(src)], "host", str(tmp_path / "newer.elf"), signed=False)
    why = elf_binding.check(mod, elf_binding.UNSIGNED)
    assert why is not None and "needs a console update" in why
    ok = tmp_path / "ok.c"
    ok.write_text("int main(void) { return 0; }\n")
    unsigned = elf_cart.build_module([str(ok)], "host", str(tmp_path / "ok.elf"), signed=False)
    assert elf_binding.check(unsigned, elf_binding.UNSIGNED) is None
    # without its cart's consent to "full_access", a module with no signature is not admitted
    assert elf_binding.check(unsigned) == "refused: unsigned module"


def _until(cond, s=5):
    """cond() polled until it holds or `s` seconds pass: whether it held."""
    t0 = time.monotonic()
    while True:
        if cond():
            return True
        if time.monotonic() - t0 >= s:
            return False
        time.sleep(0.01)


def test_a_service_is_a_loop_over_its_event_descriptor(built):
    """The service model's native face (moy_os.h, A SERVICE): main blocks in
    poll on MOY_EVQ_FD with its tick's deadline, a ping from its own app is
    read as one record and answered with moy_send, its stop is the
    descriptor's end of file, and what it held is swept."""
    from runtime import elf_binding, moy_evq, moy_log
    path = os.path.join(built["elf_svc"], "main.host.elf")
    pid = moy_log.spawn("svc")
    peer = moy_log.spawn("peer")
    assert moy_evq.open(peer, "elf.svc", "", True) == moy_evq.OK
    try:
        assert moy_evq.listen(peer, "pong", True) == moy_evq.OK
        assert elf_binding.service(path, pid, "elf.svc", elf_binding.UNSIGNED) is None
        assert moy_evq.app(pid) == "elf.svc"            # its queue, opened by its start
        assert _until(lambda: (elf_binding.info(pid) or {}).get("wakes", 0) >= 3)
        info = elf_binding.info(pid)
        assert info["service"] == 1
        assert info["threads"] == 2                     # its task and the thread it made
        assert info["handles"] == 0                     # a deadline is no registration
        assert _until(lambda: moy_evq.send(peer, "elf.svc", "ping", "[42, \"x\"]")[0] == 1)
        assert _until(lambda: moy_evq.queued(peer) == 1)
        assert moy_evq.take(peer) == (pid, "elf.svc", "pong", '[42, "x"]')
        assert elf_binding.info(pid)["events"] == 1
        before = elf_binding.stats()
        assert elf_binding.stop(pid) == 0
        assert elf_binding.info(pid) is None
        assert not moy_evq.has(pid)                     # its queue closed with it
        after = elf_binding.stats()
        assert after["leaked"] == before["leaked"]
        text = log_text()
        for line in ("start 0", "tick 1", "tick 2", "ping 9", "stop 1", "started"):
            assert line in text, line
    finally:
        moy_evq.close(peer)


def test_a_service_that_returns_is_reaped_and_one_that_will_not_is_killed(tmp_path):
    """main returning ends a service by itself (moy_os_reap); one that
    ignores its stop is killed at MOY_OS_STOP_US with 137."""
    from runtime import elf_binding, moy_log
    from tools import elf_cart
    quick = tmp_path / "quick.c"
    quick.write_text('#include "moybyte/os.h"\nint main(void) { return 7; }\n')
    stubborn = tmp_path / "stubborn.c"
    stubborn.write_text('#include <poll.h>\n'
                        "int main(void) { for (;;) { poll(0, 0, 1000); } }\n")
    q = elf_cart.build_module([str(quick)], "host", str(tmp_path / "q.elf"), signed=False)
    pid = moy_log.spawn("quick")
    assert elf_binding.service(q, pid, "elf.quick", elf_binding.UNSIGNED) is None
    got = []
    assert _until(lambda: got.extend(elf_binding.reap()) or (pid, 7) in got)
    s = elf_cart.build_module([str(stubborn)], "host", str(tmp_path / "s.elf"), signed=False)
    pid = moy_log.spawn("stubborn")
    assert elf_binding.service(s, pid, "elf.stubborn", elf_binding.UNSIGNED) is None
    time.sleep(0.05)
    assert elf_binding.stop(pid) == 137


def test_a_service_s_wakes_are_charged_to_the_one_supervisor(tmp_path):
    """Each wake, from a poll of the event descriptor's return to the next,
    is a callback of the one supervisor's (moy_super.h): a wake over its
    slice is an overrun, the strikes suspend the service, and a suspended
    service's poll waits unserved until it is resumed."""
    from runtime import elf_binding, moy_evq, moy_log
    from tools import elf_cart
    slow = tmp_path / "slow.c"
    slow.write_text('#include <poll.h>\n#include <unistd.h>\n#include "moybyte/os.h"\n'
                    "int main(void) { for (;;) {\n"
                    "    struct pollfd p = { MOY_EVQ_FD, POLLIN, 0 };\n"
                    "    if (poll(&p, 1, 0) < 0 || (p.revents & POLLHUP)) { return 0; }\n"
                    "    usleep(30000); } }\n")
    m = elf_cart.build_module([str(slow)], "host", str(tmp_path / "slow.elf"), signed=False)
    pid = moy_log.spawn("slow")
    assert elf_binding.service(m, pid, "elf.slow", elf_binding.UNSIGNED) is None
    try:
        assert _until(lambda: moy_evq.held(pid))
        assert moy_evq.rungs(pid)[1] >= 3              # its strikes
        held = elf_binding.info(pid)["wakes"]
        time.sleep(0.15)
        assert elf_binding.info(pid)["wakes"] == held  # nothing served while suspended
        assert moy_evq.resume(pid)
        assert _until(lambda: elf_binding.info(pid)["wakes"] > held)
    finally:
        assert elf_binding.stop(pid) == 0


def test_the_install_prompt_says_full_access():
    from runtime import app_perms
    cart = {"id": "elf.x", "native": ["host"], "permissions": ["full_access", "files"]}
    assert app_perms.tier_of(cart) == "elf"
    assert app_perms.prompt(cart) == ["Full access to the console", "Read and save your documents"]
    assert app_perms.check(dict(cart, permissions=["files"]), None, ())[2] == \
        'a native cart must ask for "full_access"'


def test_the_install_check_reads_a_manifest_s_chips():
    from runtime import app_perms
    man = {"id": "elf.x", "moybyte": {"native": ["esp32s3"], "permissions": ["full_access"]}}
    cart = app_perms.manifest_cart(man, "elf_x.moy")
    assert cart["native"] == ["esp32s3"] and app_perms.tier_of(cart) == "elf"
    assert app_perms.manifest_cart({"id": "a.b"}, "a_b.moy")["native"] is None


def test_a_cart_with_no_module_for_this_console_does_not_run(built):
    from runtime import elf_binding
    from runtime.elf_run import ElfRuntime, REFUSED
    rt = ElfRuntime(None, elf_binding)
    cart = {"path": built["elf_hello"], "id": "elf.hello", "native": ["host"]}
    assert rt.module(cart) == os.path.join(built["elf_hello"], "main.host.elf")
    other = dict(cart, native=["esp32s3"])
    assert rt.module(other) is None
    code, why = rt.command(other, ["hello"])
    assert code == REFUSED and "no module for this console (host)" in why


def test_the_player_opens_no_native_app(term):
    from runtime import player_fit
    ws, _app = term
    cart = next(c for c in ws.carts.all if c.get("id") == "elf.hello")
    assert cart["native"] == ["host"] and cart["main"] == "main.host.elf"
    assert player_fit._compiled(cart)
    from ws_helpers import open_cart
    open_cart(ws, "Hello Native")
    assert "runs as a command in the Terminal" in (ws.run.player.cart_error or "")


def test_a_signature_that_is_present_must_verify(built, tmp_path):
    from runtime import elf_binding
    from tools import elf_cart, wasm_module
    data = open(os.path.join(built["elf_hello"], "main.host.elf"), "rb").read()
    forged = tmp_path / "forged.elf"
    forged.write_bytes(wasm_module.sign(data, "host", lambda text: b"\x01" * 256,
                                        scheme=elf_cart.sig_scheme()))
    assert elf_binding.check(str(forged)) == "refused: bad signature"
    assert elf_binding.check(str(forged), elf_binding.UNSIGNED) == "refused: bad signature"
    cut = tmp_path / "cut.elf"
    cut.write_bytes(data + b"\x00\x00\x00\x00moybyte-sig1")
    assert elf_binding.check(str(cut)) == "refused: malformed signature"
