"""The Terminal in a real browser, in both web modes.

BOARD-SERVED: the page a board serves opens its Terminal straight into
`remote`, a face of the board's shell (native/moy_sh/moy_sh_link.h), over the
worker's WebSocket to GET /term. Here the board is a twin: serve.py --carts
--term, passing /term to the web terminal's host harness (the kernel's
webhost, the bridge, the face and the board's shell, tests/test_moy_tty.py).
Keys typed in the page reach the board's shell, its bytes draw the page's
grid; a wrong pin, a wait and a board that does not answer each say so in the
grid.

SITE: the site's console (dist_site/, `build.sh --site`) runs the shell
itself in the wasm on the browser's store; a command past its run's budget
gives the tab back.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_term_e2e.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

import web_e2e
from web_e2e import RUNNER, ROOT

sys.path.insert(0, os.path.dirname(__file__))

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome)")

PIN = "4321"


@pytest.fixture(scope="module")
def tty_exe(tmp_path_factory):
    web_e2e.require("term")
    import test_moy_tty
    return test_moy_tty._build(tmp_path_factory.mktemp("tty"))


def _twin(tmp_path, term_port, pin=PIN, dist="dist"):
    web_e2e.warm_host_library()
    store = tmp_path / "board" / "carts"
    store.mkdir(parents=True)
    for cart in sorted((ROOT / "system_carts").glob("*.moy")):
        shutil.copytree(cart, store / cart.name)
    port = web_e2e.free_port()
    log = tmp_path / "twin.log"
    args = [sys.executable, "serve.py", str(port), dist, "--carts", str(store)]
    if pin:
        args += ["--pin", pin]
    if term_port:
        args += ["--term", "127.0.0.1:%d" % term_port]
    p = subprocess.Popen(args, cwd=RUNNER, stdout=open(log, "w"), stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/index.html", timeout=1).read(64)
            return p, base
        except OSError:
            if p.poll() is not None:
                pytest.fail("serve.py died on startup:\n" + log.read_text())
            time.sleep(0.1)
    p.terminate()
    pytest.fail("serve.py never answered")


def _lines(tag):
    return {"js": "window.__moyState().then(s => '%s ' + JSON.stringify(s.term))" % tag}


def _until(test, timeout=30000):
    """A step that holds until a grid line passes `test` (a JS predicate over
    one line), so each check waits on what it checks, never on a guess."""
    return {"waitFor": "window.__moyState().then(s => s.term.some(%s))" % test,
            "timeout": timeout}


def _says(text):
    return _until("ln => ln.includes(%s)" % json.dumps(text))


def _tagged(js, tag):
    for j in js:
        if isinstance(j, str) and j.startswith(tag + " "):
            return json.loads(j[len(tag) + 1:])
    raise AssertionError("no %s step in %r" % (tag, js))


def _typed(text):
    return [{"key": c} for c in text]


def _sides(host, tmp_path, out):
    """Each side's own account, for a failure to say which one stuck: the
    shell's host (its STATS: sessions, live, bytes each way), the twin's log
    and the page's run."""
    return "\n".join(["", "-- the shell's host:", host.log()[-1500:],
                      "-- the twin:", (tmp_path / "twin.log").read_text()[-1500:],
                      "-- the page:", out[-2500:]])


def _play(tmp_path, name, base, steps, pin=PIN):
    q = "?dev=1&desktop=1&cart=Terminal" + ("&pin=" + pin if pin else "")
    return web_e2e.run(tmp_path, name, q, steps, base, tmp_path / ("chrome_" + name),
                       boot_ms=30000)


def test_board_served_terminal_is_the_board_s_shell(tty_exe, tmp_path):
    import test_moy_tty
    host = test_moy_tty.Host(tty_exe, tmp_path)
    server, base = _twin(tmp_path, host.port)
    try:
        steps = ([{"note": "the Terminal connects"}, _until("ln => /\\$\\s*$/.test(ln)"),
                  _lines("OPEN")]
                 + _typed("echo web-$((6*7))") + [{"key": "Enter"}, _says("web-42"),
                                                   _lines("ECHO")]
                 + [{"shot": "term_board"}]
                 + _typed("exit 4") + [{"key": "Enter"}, _says("[the shell exited 4]"),
                                       _lines("EXIT")])
        out, js = _play(tmp_path, "term_board", base, steps)
        opened = _tagged(js, "OPEN")
        assert any(ln.rstrip().endswith("$") for ln in opened), opened
        echo = "\n".join(_tagged(js, "ECHO"))
        assert "web-42" in echo, echo + _sides(host, tmp_path, out)
        ended = "\n".join(_tagged(js, "EXIT"))
        assert "[the shell exited 4]" in ended, ended + _sides(host, tmp_path, out)
        assert host.stats()["sessions"] == "1", _sides(host, tmp_path, out)
    finally:
        server.terminate()
        host.end()


def test_a_board_served_terminal_says_why_it_has_no_shell(tty_exe, tmp_path):
    """A wrong pin, then the board gone: each said in the grid."""
    import test_moy_tty
    host = test_moy_tty.Host(tty_exe, tmp_path)
    server, base = _twin(tmp_path, host.port, pin="0000")
    try:
        steps = [{"note": "the twin takes 0000, the shell's webhost 4321"},
                 _says("refused the pin"), _lines("PIN")]
        _out, js = _play(tmp_path, "term_pin", base, steps, pin="0000")
        assert any("refused the pin" in ln for ln in _tagged(js, "PIN")), js
    finally:
        server.terminate()
        host.end()
    server, base = _twin(tmp_path / "gone", web_e2e.free_port())
    try:
        steps = [_says("does not answer"), _lines("GONE")]
        _out, js = _play(tmp_path, "term_gone", base, steps)
        assert any("does not answer" in ln for ln in _tagged(js, "GONE")), js
    finally:
        server.terminate()


def test_a_board_served_terminal_waiting_on_wrong_pins_says_how_long(tty_exe, tmp_path):
    import test_moy_tty
    host = test_moy_tty.Host(tty_exe, tmp_path)
    for _ in range(11):
        test_moy_tty.get(host, "/term?pin=0000")
    # The eleventh wrong pin's wait is a second; the clock stepped back a
    # minute holds it open past the page's boot, however long that takes.
    host.skew(-60000)
    server, base = _twin(tmp_path, host.port)
    try:
        steps = [_says("Too many wrong pins"), _lines("WAIT"), {"shot": "term_wait"}]
        _out, js = _play(tmp_path, "term_wait", base, steps)
        said = "\n".join(_tagged(js, "WAIT"))
        m = re.search(r"Too many wrong pins: wait (\d+) s, then press Enter\.", said)
        assert m and 1 <= int(m.group(1)) <= 61, said
    finally:
        server.terminate()
        host.end()


def test_the_site_s_terminal_runs_the_shell_on_the_browser_s_store(tmp_path):
    """The site's console: the session's root is the store's folder (/moy in
    the page), home its files; a run past its budget gives the tab back."""
    web_e2e.require("site_shell")
    port = web_e2e.free_port()
    p = subprocess.Popen([sys.executable, "serve.py", str(port), "dist_site"], cwd=RUNNER,
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/index.html", timeout=1).read(64)
                break
            except OSError:
                time.sleep(0.1)
        steps = ([{"note": "the site's shell", "wait": 1500}]
                 + _typed("pwd; ls /") + [{"key": "Enter"}, {"wait": 600}, _lines("LS")]
                 + _typed("sleep 30") + [{"key": "Enter"}, {"wait": 4500}, _lines("SLEPT")]
                 + _typed("echo back") + [{"key": "Enter"}, {"wait": 600}, _lines("BACK")])
        _out, js = _play(tmp_path, "term_site", base, steps, pin=None)
        ls = "\n".join(_tagged(js, "LS"))
        assert "/files" in ls and "carts/" in ls and "files/" in ls, ls
        slept = "\n".join(_tagged(js, "SLEPT"))
        assert "[stopped: a command in the browser runs for 3 s at most]" in slept, slept
        assert "back" in "\n".join(_tagged(js, "BACK"))
    finally:
        p.terminate()
