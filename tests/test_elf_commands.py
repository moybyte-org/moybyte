"""The native (ELF) tier on the host (native/moy_elf/README.md): a module built
by the SDK (tools/elf_cart.py) run as an installed command in the Terminal's
session over the console's own door (runtime/shell/sh_door.py), and as a
service, through the boards' C (native/moy_elf/moy_os.c) over the host's
loader (runtime/host/moyhost_elf.c). Its args, env, stdio through the
session's pipes and redirections, exit codes, files under its permissions,
threads, Ctrl-C and budget; the sweep of what it held; a module that needs a
newer console refused; the "native" consent."""

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
NAMES = ("elf_hello", "elf_cat", "elf_probe", "elf_svc", "elf_nc", "elf_head", "elf_libc")


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
    man["moybyte"]["permissions"] = ["native"]
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
    man["moybyte"]["permissions"] = ["native"]
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


def test_a_native_cart_must_ask_for_native(term):
    _ws, app = term
    got = out(app, "elf.blind/blind; echo status $?")
    assert got[-1] == "status 126" and "native" in got[0]


def test_a_module_needing_a_newer_console_is_refused(tmp_path):
    from runtime import elf_binding
    from tools import elf_cart
    src = tmp_path / "newer.c"
    src.write_text('__asm__(".pushsection .moy.rows,\\"\\",@progbits\\n.4byte 4000\\n.popsection");\n'
                   'int main(void) { return 0; }\n')
    mod = elf_cart.build_module([str(src)], "host", str(tmp_path / "newer.elf"), signed=False)
    why = elf_binding.check(mod)
    assert why is not None and "needs a console update" in why
    ok = tmp_path / "ok.c"
    ok.write_text("int main(void) { return 0; }\n")
    assert elf_binding.check(elf_cart.build_module([str(ok)], "host", str(tmp_path / "ok.elf"),
                                                   signed=False)) is None


def test_a_service_s_hooks_on_its_task_and_its_sweep(built):
    from runtime import elf_binding, moy_log
    path = os.path.join(built["elf_svc"], "main.host.elf")
    pid = moy_log.spawn("svc")
    assert elf_binding.service(path, pid, "elf.svc") is None
    t0 = time.monotonic()
    while (elf_binding.info(pid) or {}).get("timers", 0) < 3 and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    info = elf_binding.info(pid)
    assert info["service"] == 1 and info["timers"] >= 3
    assert info["threads"] == 2                 # its task and the thread it made
    assert info["handles"] == 2                 # the timer and the reader
    elf_binding.post_event(1, a=42)             # MOY_EV_KEY_DOWN
    t0 = time.monotonic()
    while elf_binding.info(pid)["events"] < 1 and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    before = elf_binding.stats()
    assert elf_binding.stop(pid) == 0
    assert elf_binding.info(pid) is None
    after = elf_binding.stats()
    assert after["swept"] - before["swept"] == 2
    assert after["leaked"] == before["leaked"]
    text = log_text()
    for line in ("start 0", "tick 1", "tick 2", "key 42", "stop 1", "started"):
        assert line in text, line


def test_the_install_prompt_says_full_access():
    from runtime import app_perms
    cart = {"id": "elf.x", "native": ["host"], "permissions": ["native", "files"]}
    assert app_perms.tier_of(cart) == "elf"
    assert app_perms.prompt(cart) == ["Full access to the console", "Read and save your documents"]
    assert app_perms.check(dict(cart, permissions=["files"]), None, ())[2] == \
        'a native cart must ask for "native"'


def test_the_install_check_reads_a_manifest_s_chips():
    from runtime import app_perms
    man = {"id": "elf.x", "moybyte": {"native": ["esp32s3"], "permissions": ["native"]}}
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
    assert "runs as a command in the Terminal" in (ws.player.cart_error or "")


def test_a_signature_that_is_present_must_verify(built, tmp_path):
    from runtime import elf_binding
    from tools import elf_cart, wasm_module
    data = open(os.path.join(built["elf_hello"], "main.host.elf"), "rb").read()
    forged = tmp_path / "forged.elf"
    forged.write_bytes(wasm_module.sign(data, "host", lambda text: b"\x01" * 256,
                                        scheme=elf_cart.sig_scheme()))
    assert elf_binding.check(str(forged)) == "refused: bad signature"
    cut = tmp_path / "cut.elf"
    cut.write_bytes(data + b"\x00\x00\x00\x00moybyte-sig1")
    assert elf_binding.check(str(cut)) == "refused: malformed signature"
