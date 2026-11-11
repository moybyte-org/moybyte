"""WASI commands (native/moy_wasm/moy_wasi.h): a module built by wasi-sdk or
by rustc for wasm32-wasip1, unchanged, run as an installed command in the
Terminal's session over the console's own door (runtime/shell/sh_door.py),
on the host's WAMR (runtime/host/moyhost_wasm.c). Its args, env, stdio
through the session's pipes and redirections, exit code, files under its
permissions, Ctrl-C, budget and trap, and the one moybyte.term import.

The fixtures carry their source (tests/fixtures/wasm/wasi_*.moy) and are
built here by the stock toolchains (tools/wasm_cart.py): wasi-sdk is the
tree's own (tools/jet_cart.py fetches it by sha256), Rust's hello needs
rustc with the wasm32-wasip1 target and skips without it."""

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
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "wasm")


def _required():
    return bool(os.environ.get("CI") or os.environ.get("MOYBYTE_REQUIRE_HOST_WASM"))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """{fixture: built cart folder} for the WASI fixtures this machine can
    build; the C ones skip (or fail under CI) without wasi-sdk."""
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
    out = tmp_path_factory.mktemp("wasi")
    got = {}
    for name in ("wasi_cat", "wasi_probe", "wasi_hello", "wasi_svc", "wasi_crypto"):
        if name == "wasi_hello" and not wasm_cart.rust_wasip1():
            continue
        dst = str(out / (name + ".moy"))
        wasm_cart.build(os.path.join(FIXTURES, name + ".moy"), dst)
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
    bare = carts / "wasi_bare.moy"
    shutil.copytree(built["wasi_probe"], str(bare))
    man = json.loads((bare / "manifest.json").read_text())
    man["id"], man["title"] = "wasi.bare", "Bare Wasi"
    man["moybyte"]["permissions"] = []
    (bare / "manifest.json").write_text(json.dumps(man))
    svc = carts / "wasi_svc.moy"
    if svc.exists():                    # the service fixture, run here as a command
        man = json.loads((svc / "manifest.json").read_text())
        man["moybyte"]["type"] = "command"
        man["moybyte"]["permissions"] = ["prefs", "clipboard"]
        (svc / "manifest.json").write_text(json.dumps(man))
    ws = build_workstation(str(carts))
    app = ws.terminal_app
    app.open()
    app.draw(0)
    app.draw(0)                         # the grid takes the view's width
    run(app, "cd /files/Documents")     # `files` is Documents: the command's one root
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
    """The lines `text` printed, its own echoed line (wrapped to the view's
    width) left out."""
    app.face.take()
    before = app.face.lines()
    n0 = 0 if before == [""] else len(before)
    run(app, text)
    app.face.take()
    lines = [re.sub("\x1b\\[[0-9;]*m", "", ln) for ln in app.face.lines()]
    echo, at = "", n0
    want = ("$" + text).replace(" ", "")      # a wrap at a space drops it
    while at < len(lines) and not echo.replace(" ", "").endswith(want):
        echo += lines[at]
        at += 1
    return lines[at:]


def test_the_commands_are_on_the_path(term, built):
    _ws, app = term
    want = ["bare", "cat", "crypto"] + (["hello"] if "wasi_hello" in built else []) + [
        "probe", "svc"]
    assert " ".join(out(app, "ls /bin")).split() == want


def test_args_reach_main_and_the_name_is_argv0(term):
    _ws, app = term
    assert out(app, 'wasi.probe/probe args one "two words"') == [
        "0=probe", "1=args", "2=one", "3=two words"]


def test_the_session_s_exported_variables_are_the_environment(term):
    _ws, app = term
    assert out(app, "export GREETING=hi; LOCAL=no; wasi.probe/probe env GREETING") == ["hi"]
    assert out(app, "wasi.probe/probe env LOCAL") == ["(unset)"]


def test_the_exit_code_and_stderr_are_the_shell_s(term):
    _ws, app = term
    assert out(app, "wasi.probe/probe exit 7; echo status $?") == ["leaving with 7", "status 7"]
    assert out(app, "wasi.probe/probe nothing; echo status $?") == [
        "probe: no verb nothing", "status 64"]


def test_stdio_runs_through_the_session_s_pipes_and_redirections(term, tmp_path):
    _ws, app = term
    assert out(app, "printf 'a\\nbc\\n' | wasi.probe/probe stdin") == ["5 bytes 2 lines"]
    assert out(app, "wasi.probe/probe args x | wasi.cat/cat") == ["0=probe", "1=args", "2=x"]
    assert out(app, "printf 'hello\\nworld\\n' > n.txt; wasi.cat/cat n.txt > m.txt; cat m.txt") \
        == ["hello", "world"]
    assert (tmp_path / "files" / "Documents" / "m.txt").read_text() == "hello\nworld\n"
    assert out(app, "wasi.cat/cat < n.txt") == ["hello", "world"]


def test_cat_reads_the_files_its_permission_grants(term):
    _ws, app = term
    run(app, "printf 'one\\n' > a.txt; printf 'two\\n' > b.txt")
    assert out(app, "wasi.cat/cat a.txt - b.txt < a.txt") == ["one", "one", "two"]
    assert out(app, "wasi.cat/cat missing.txt; echo status $?") == [
        "cat: missing.txt: No such file or directory", "status 1"]


def test_files_are_made_written_listed_moved_and_removed(term, tmp_path):
    _ws, app = term
    assert out(app, "wasi.probe/probe files") == [
        "size 14", "entry b.txt", "read two", "read three", "gone yes"]
    assert not (tmp_path / "files" / "Documents" / "probe.d").exists()


def test_the_files_rows_save_list_load_rename_and_delete_in_its_folder(term, tmp_path):
    """moybyte.os `files_*` (wasi_probe's `app`) are the files role's rows for
    the command's own grant, the folder its `files` permission names: a
    document saved, listed, loaded, renamed and deleted into the trash, a
    kind outside its folder ACCES; with no permission every row is ACCES."""
    _ws, app = term
    files = tmp_path / "files"
    assert out(app, "wasi.probe/probe app") == [
        "files.ready 1", "files.save probe", "files.count 1", "files.list probe,",
        "files.load # probe,", "files.load.other -1 errno 2", "files.rename moved",
        "files.load.moved # probe,", "files.delete moved", "files.count.after 0"]
    assert not (files / "Documents" / "moved.md").exists()
    assert (files / ".Trash" / "docs" / "moved.md").read_text() == "# probe\n"
    rows = ("ready", "save", "count", "list", "load", "load.other", "rename", "load.moved",
            "delete", "count.after")
    assert out(app, "wasi.bare/bare app") == ["files.%s -1 errno 2" % r for r in rows]


def test_a_path_outside_its_folders_is_not_capable(term, tmp_path):
    _ws, app = term
    (tmp_path / "secret.txt").write_text("no")
    run(app, "printf 'x\\n' > a.txt")
    assert out(app, "wasi.probe/probe open /files/Documents/a.txt") == ["opened"]
    assert out(app, "wasi.probe/probe open /secret.txt; echo $?") == [
        "open: No such file or directory", "2"]
    assert out(app, "wasi.probe/probe open ../secret.txt") == ["open: Capabilities insufficient"]
    assert out(app, "cd /; wasi.probe/probe open secret.txt") == [
        "open: Capabilities insufficient"]


def test_with_no_files_permission_it_reaches_no_files(term):
    _ws, app = term
    run(app, "printf 'x\\n' > a.txt")
    assert out(app, "wasi.bare/bare open a.txt") == ["open: Capabilities insufficient"]
    assert out(app, "wasi.bare/bare files") == ["mkdir: Capabilities insufficient"]


def test_clocks_random_and_the_terminal_s_size(term):
    _ws, app = term
    assert out(app, "wasi.probe/probe clock") == ["since 2000 yes, slept yes"]
    assert out(app, "wasi.probe/probe random") == ["random yes"]
    cols, rows = app.face.size()
    assert out(app, "wasi.probe/probe term") == ["0 %d %d" % (cols, rows)]


def test_the_crypto_rows_answer_a_wasi_command_with_no_permission(term):
    """The native fixture's program built for WASI (tests/fixtures/wasm/
    wasi_crypto.moy): its moybyte.os imports answer what the ELF tier's rows
    answer, errno for errno."""
    import crypto_vectors
    _ws, app = term
    assert out(app, "wasi.crypto/crypto; echo status $?") == crypto_vectors.PROGRAM + ["status 0"]


def test_ctrl_c_stops_a_command_at_its_next_import(term):
    _ws, app = term
    app.face.interrupt()
    t0 = time.monotonic()
    got = out(app, "wasi.probe/probe spin; echo not-after")
    assert time.monotonic() - t0 < 5
    assert "not-after" not in got and app.face.status() == 130


def test_a_long_command_is_a_job_the_door_steps(term, monkeypatch):
    """A foreground run is a job the door steps (sh_door._compiled_steps):
    the shell's face is busy while its `_start` sleeps on a thread of its
    own, each step comes back, and Ctrl-C mid-run closes the job with 130,
    the run's record freed, its process gone and the next command running."""
    from runtime import moy_sh, scheduler, sh_door, wasm_binding
    ws, app = term
    rt = ws.runtimes["wasm"]
    steps = []
    real = rt.step
    monkeypatch.setattr(rt, "step", lambda pid, ms: steps.append(ms) or real(pid, ms))
    app.face.set_line("wasi.probe/probe sleep; echo not-after")
    app.face.submit()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.5:
        moy_sh.jobs()
        time.sleep(0.002)
    assert app.face.busy() and wasm_binding.command_live()
    assert len(steps) > 3 and set(steps) == {sh_door.STEP_MS}
    assert any(p.name == "probe" for p in scheduler.SCHED.procs.values())
    app.face.interrupt()
    t0 = time.monotonic()
    while app.face.busy() and time.monotonic() - t0 < 10:
        moy_sh.jobs()
        time.sleep(0.002)
    assert not app.face.busy() and app.face.status() == 130
    assert not wasm_binding.command_live()
    assert not any(p.name == "probe" for p in scheduler.SCHED.procs.values())
    assert "not-after" not in "".join(app.face.lines())
    assert out(app, "wasi.probe/probe exit 4; echo status $?") == [
        "leaving with 4", "status 4"]


def _steps(cond, s=10.0):
    """The shell's jobs stepped until cond() holds or `s` seconds pass:
    whether it held."""
    from runtime import moy_sh
    t0 = time.monotonic()
    while True:
        if cond():
            return True
        if time.monotonic() - t0 >= s:
            return False
        moy_sh.jobs()
        time.sleep(0.002)


def test_a_program_blocks_in_poll_on_its_event_descriptor(term):
    """The service model's WASI face (moy_wasi.h, THE EVENT DESCRIPTOR): the
    run's process has an event queue under its app id; its poll_oneoff on
    MOY_EVQ_FD blocks in the one kernel wait between its clock's deadlines,
    a ping from its own app is read as one record and answered with its
    moybyte.os send, its notify is answered between the job's steps, and its
    end closes the queue."""
    from runtime import moy_evq, moy_log, scheduler
    _ws, app = term
    peer = moy_log.spawn("peer")
    assert moy_evq.open(peer, "wasi.svc", "", False) == moy_evq.OK
    try:
        assert moy_evq.listen(peer, "pong", True) == moy_evq.OK
        app.face.set_line("wasi.svc/svc")
        app.face.submit()
        assert _steps(lambda: app.face.take() or "listening" in app.face.lines()), \
            app.face.lines()
        pid = next(p.pid for p in scheduler.SCHED.procs.values() if p.name == "svc")
        assert moy_evq.app(pid) == "wasi.svc"
        assert moy_evq.send(peer, "wasi.svc", "ping", "[7]")[0] == 1
        assert _steps(lambda: moy_evq.queued(peer) == 1)
        assert moy_evq.take(peer) == (pid, "wasi.svc", "pong", "[7]")
        t0 = time.monotonic()
        _steps(lambda: time.monotonic() - t0 > 0.1)           # its clock ticks meanwhile
        # a console call, answered between the job's steps: refused, as the
        # command asks for no notifications
        assert moy_evq.send(peer, "wasi.svc", "note", '["x"]')[0] == 1
        assert _steps(lambda: (app.face.take(), "note -1 errno 2" in app.face.lines())[1]), \
            app.face.lines()
        # its app rows, answered for the grant its run was made: the prefs and
        # the clipboard it asks for
        assert moy_evq.send(peer, "wasi.svc", "wake", "[]")[0] == 1
        assert _steps(lambda: (app.face.take(), "open_with 0 errno 0" in app.face.lines())[1]), \
            app.face.lines()
        assert "prefs.get 42 errno 0" in app.face.lines()
        assert "clipboard.text from wasi errno 0" in app.face.lines()
        assert moy_evq.send(peer, "wasi.svc", "bye", "[]")[0] == 1
        assert _steps(lambda: not app.face.busy())
        assert app.face.status() == 0
        app.face.take()
        lines = app.face.lines()
        assert "ping 3 from wasi.svc: 1" in lines
        assert "bye after 1 pings, ticking" in lines
        assert not moy_evq.has(pid)
    finally:
        moy_evq.close(peer)


def test_a_sleeping_command_past_its_budget_is_killed(term, monkeypatch):
    from runtime import sh_door
    monkeypatch.setattr(sh_door, "COMMAND_MS", 300)
    _ws, app = term
    assert out(app, "wasi.probe/probe sleep; echo status $?") == [
        "killed: ran past 0 s", "status 137"]


def test_a_command_past_its_budget_is_killed(term, monkeypatch):
    from runtime import sh_door
    monkeypatch.setattr(sh_door, "COMMAND_MS", 300)
    _ws, app = term
    assert out(app, "wasi.probe/probe spin; echo status $?") == [
        "killed: ran past 0 s", "status 137"]


def test_a_trap_is_the_command_s_crash(term):
    _ws, app = term
    got = out(app, "wasi.probe/probe trap; echo status $?")
    assert got[-1] == "status 134" and "unreachable" in got[0], got


def test_a_module_that_imports_what_a_command_cannot_is_refused(term, tmp_path):
    ws, app = term
    hello = os.path.join(FIXTURES, "hello.moy")
    from tools import wasm_cart
    dst = str(tmp_path / "carts" / "wasi_game.moy")
    wasm_cart.build(hello, dst)
    man = json.loads(open(os.path.join(dst, "manifest.json")).read())
    man.update(id="wasi.game", moybyte={"type": "command"})
    open(os.path.join(dst, "manifest.json"), "w").write(json.dumps(man))
    ws.rescan_carts()
    got = out(app, "wasi.game/game; echo status $?")
    assert got[-1] == "status 126" and "acommandonthisconsolecannot" in "".join(got).replace(" ", ""), got


def test_a_background_command_logs_and_its_status_waits(term):
    _ws, app = term
    assert out(app, "wasi.probe/probe exit 5 & wait $!; echo status $?")[0] == "status 5"


def test_rust_s_hello_runs_unchanged(term, built):
    if "wasi_hello" not in built:
        pytest.skip("no rustc with the wasm32-wasip1 target")
    _ws, app = term
    assert out(app, "wasi.hello/hello moy byte") == ["hello from rust, 3 args", "moy", "byte"]


def test_a_built_command_keeps_everything_but_its_dwarf():
    from tools import wasm_cart, wat
    blob = wat.assemble('(module (func (export "_start")))')

    def custom(name, body):
        payload = bytes([len(name)]) + name + body
        return bytes([0, len(payload)]) + payload
    dwarf = custom(b".debug_info", b"\1\2\3")
    names = custom(b"name", b"\0")
    assert wasm_cart.strip_debug(blob + dwarf + names + dwarf) == blob + names


def test_hostile_arguments_answer_an_errno_or_trap(term):
    """preview1's imports called raw with what a hostile module passes
    (wasi_probe's `hostile`): an absolute path is the bytes its length says
    and no more, a pointer at the end of memory or a range off it is FAULT
    or the engine's trap, a descriptor never opened BADF -- and the console
    reads nothing outside the module's memory."""
    _ws, app = term
    run(app, "printf 'x\\n' > a.txt")
    assert out(app, "wasi.probe/probe hostile abs") == ["abs 0"]
    assert out(app, "wasi.probe/probe hostile end") == ["end answered"]
    got = out(app, "wasi.probe/probe hostile wrap; echo status $?")
    assert got[-1] == "status 134" and "out of bounds memory access" in "".join(got), got
    assert out(app, "wasi.probe/probe hostile iov") == ["iov 21"]
    assert out(app, "wasi.probe/probe hostile iovlen") == ["iovlen 21"]
    assert out(app, "wasi.probe/probe hostile badfd") == ["badfd 8 8"]
    assert out(app, "wasi.probe/probe hostile prestat0") == ["prestat0 37"]
    assert out(app, "wasi.probe/probe hostile poll0") == ["poll0 28"]
    assert out(app, "wasi.probe/probe hostile renum") == ["renum 8"]
