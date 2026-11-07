"""The Terminal and the sign-in in a real browser, in both web modes.

BOARD-SERVED: the page a board serves pairs with it and opens its Terminal
straight into `remote`, a face of the board's shell
(native/moy_sh/moy_sh_link.h), over the worker's WebSocket to GET /term, every
frame sealed. The board is real C: the web terminal's host harness (the
kernel's webhost with its sign-in, the bridge, the face and the board's shell,
tests/test_moy_tty.py), and serve.py --board serves the bundle in front of it.
A page paired by the QR's link reloads signed in with its device key; a page
pairs by the console's password in its prompt; a wrong one, a wait, a board
that no longer takes the browser and one that does not answer each say so.

SITE: the site's console (dist_site/, `build.sh --site`) runs the shell
itself in the wasm on the browser's store; a command past its run's budget
gives the tab back.

    MOYBYTE_WEB_E2E=1 .venv/bin/python -m pytest tests/test_web_term_e2e.py
"""

import base64
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import pytest

import web_e2e
from web_e2e import RUNNER, ROOT

sys.path.insert(0, os.path.dirname(__file__))

pytestmark = pytest.mark.skipif(
    not os.environ.get("MOYBYTE_WEB_E2E"),
    reason="MOYBYTE_WEB_E2E not set (spawns headless Chrome)")

PASSWORD = "correct horse"


@pytest.fixture(scope="module")
def tty_exe(tmp_path_factory):
    web_e2e.require("term", "signin")
    import test_moy_tty
    return test_moy_tty._build(tmp_path_factory.mktemp("tty"))


@pytest.fixture
def short():
    """The harness's folder: short enough for the webhost's paths, whose
    system folder past MOY_WEB_PATH_MAX (96 bytes) is refused, however deep
    pytest's tmp_path runs (xdist's popen-gwN, a long test name)."""
    d = tempfile.mkdtemp(prefix="moyw")
    yield pathlib.Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _board(tty_exe, root, password=None):
    """The harness under `root` with the seed carts in its store (and the
    console's password, as `passwd` writes it, when given)."""
    import test_moy_tty
    from runtime.system import ssh_service
    host = test_moy_tty.Host(tty_exe, root)
    store = host.tree / "sd" / "moybyte" / "carts"
    for cart in sorted((ROOT / "system_carts").glob("*.moy")):
        shutil.copytree(cart, store / cart.name)
    if password:
        (host.tree / "sd" / "moybyte" / "system" / "password").write_text(
            ssh_service.hashed(password, "e2e0salt"))
    return host


def _serve(tmp_path, board_port, dist="dist"):
    web_e2e.warm_host_library()
    port = web_e2e.free_port()
    log = tmp_path / "serve.log"
    args = [sys.executable, "serve.py", str(port), dist, "--board", "127.0.0.1:%d" % board_port]
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


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _qr_fragment(host, board=None):
    """What the console's QR carries after its address: the token, and the
    first 8 bytes of the board's id (moy_webconsole.c's paired URL)."""
    import seal_client
    if board is None:
        board = seal_client.Browser(host.send).hello()["id"]
    return "#t=%s&b=%s" % (_b64url(bytes.fromhex(host.token())),
                           _b64url(bytes.fromhex(board)[:8]))


def _lines(tag):
    return {"js": "window.__moyState().then(s => '%s ' + JSON.stringify(s.term))" % tag}


def _until(test, timeout=30000):
    """A step that holds until a grid line passes `test` (a JS predicate over
    one line), so each check waits on what it checks, never on a guess."""
    return {"waitFor": "window.__moyState().then(s => s.term.some(%s))" % test,
            "timeout": timeout}


def _says(text):
    return _until("ln => ln.includes(%s)" % json.dumps(text))


_SHELL = "ln => /\\$\\s*$/.test(ln)"

# The sign-in's prompt as the page shows it, and the keys it holds.
_PROMPT = ("({shown: getComputedStyle(document.getElementById('pin')).display, "
           "said: document.getElementById('pinm').textContent, url: location.href, "
           "keys: Object.keys(JSON.parse(localStorage.getItem('moybyte.keys') || '{}'))"
           ".length})")


def _prompt(tag):
    return {"js": "'%s ' + JSON.stringify(%s)" % (tag, _PROMPT)}


def _asks(test, timeout=30000):
    """A step that holds until the prompt's state passes `test` (a JS
    predicate over _PROMPT's object)."""
    return {"waitFor": "(%s)(%s)" % (test, _PROMPT), "timeout": timeout}


def _gone(timeout=30000):
    """A step that holds until the board behind serve.py stops answering."""
    return {"waitFor": "fetch('/no-such-route').then(r => r.status === 502, () => true)",
            "timeout": timeout}


def _password(pw):
    return {"js": "(document.getElementById('pinf').value = %s, "
                  "document.getElementById('pinb').click(), 'typed')" % json.dumps(pw)}


def _tagged(js, tag):
    for j in js:
        if isinstance(j, str) and j.startswith(tag + " "):
            return json.loads(j[len(tag) + 1:])
    return None


def _typed(text):
    return [{"key": c} for c in text]


def _sides(host, tmp_path, out):
    """Each side's own account, for a failure to say which one stuck: the
    shell's host (its STATS: sessions, live, bytes each way), serve.py's log
    and the page's run."""
    return "\n".join(["", "-- the shell's host:", host.log()[-1500:],
                      "-- serve.py:", (tmp_path / "serve.log").read_text()[-1500:],
                      "-- the page:", out[-2500:]])


def _play(tmp_path, name, base, steps, after="", profile="chrome"):
    q = "?dev=1&desktop=1&cart=Terminal" + after
    return web_e2e.run(tmp_path, name, q, steps, base, tmp_path / profile, boot_ms=30000)


def test_a_page_paired_by_the_qr_reloads_signed_in_and_its_terminal_is_sealed(
        tty_exe, tmp_path, short):
    host = _board(tty_exe, short)
    server, base = _serve(tmp_path, host.port)
    try:
        steps = ([{"note": "paired by the token, the Terminal connects"}, _until(_SHELL),
                  _prompt("PAIRED"), _lines("OPEN")]
                 + _typed("echo web-$((6*7))") + [{"key": "Enter"}, _says("web-42"),
                                                   _lines("ECHO")]
                 + [{"shot": "term_board"}])
        out, js = _play(tmp_path, "term_board", base, steps, after=_qr_fragment(host))
        paired = _tagged(js, "PAIRED")
        assert paired and paired["keys"] == 1 and "#" not in paired["url"], (
            str(paired) + _sides(host, tmp_path, out))
        assert any(ln.rstrip().endswith("$") for ln in _tagged(js, "OPEN")), js
        assert "web-42" in "\n".join(_tagged(js, "ECHO")), (
            str(js) + _sides(host, tmp_path, out))
        assert int(host.stats()["in"]) > 0, "no key crossed the sealed socket"
        # the same profile, no token: the stored key signs in, nothing typed;
        # then the board goes away, and the next connect says so
        gone = threading.Thread(target=_end_when, args=(host, "2"), daemon=True)
        gone.start()
        steps = ([_until(_SHELL), _prompt("AGAIN"), _lines("OPEN")]
                 + _typed("exit 4") + [{"key": "Enter"}, _says("[the shell exited 4]"),
                                       _lines("EXIT"), _gone(), {"key": "Enter"},
                                       _says("does not answer"), _lines("GONE")])
        out, js = _play(tmp_path, "term_again", base, steps)
        assert _tagged(js, "AGAIN")["shown"] == "none", js
        assert any(ln.rstrip().endswith("$") for ln in _tagged(js, "OPEN")), js
        assert "[the shell exited 4]" in "\n".join(_tagged(js, "EXIT")), (
            str(js) + _sides(host, tmp_path, out))
        assert any("does not answer" in ln for ln in _tagged(js, "GONE")), js
        gone.join(5)
    finally:
        server.terminate()
        host.end()


def _end_when(host, sessions):
    """The board gone once `sessions` sockets were served and none is live."""
    end = time.monotonic() + 120
    while time.monotonic() < end:
        try:
            s = host.stats()
        except (IndexError, ValueError, OSError):
            s = {}
        if s.get("sessions") == sessions and s.get("live") == "0":
            host.proc.kill()
            return
        time.sleep(0.1)


def test_a_dropped_browser_is_asked_to_pair_again(tty_exe, tmp_path, short):
    host = _board(tty_exe, short)
    server, base = _serve(tmp_path, host.port)
    try:
        steps = [_asks("p => p.keys === 1 && p.shown === 'none'"), _prompt("PAIRED")]
        _out, js = _play(tmp_path, "paired", base, steps, after=_qr_fragment(host))
        assert _tagged(js, "PAIRED")["keys"] == 1, js
        host.say("drop")
        host.wait_line("DROPPED 1")
        steps = [_asks("p => p.shown === 'flex' && p.keys === 0"), _prompt("ASKED")]
        _out, js = _play(tmp_path, "dropped", base, steps)
        asked = _tagged(js, "ASKED")
        assert asked["shown"] == "flex" and asked["keys"] == 0, asked
    finally:
        server.terminate()
        host.end()


def test_a_page_pairs_by_the_console_s_password(tty_exe, tmp_path, short):
    host = _board(tty_exe, short, password=PASSWORD)
    server, base = _serve(tmp_path, host.port)
    try:
        wrong = "that password did not work -- try again"
        steps = ([_asks("p => p.shown === 'flex'"), _prompt("ASK"), _password("wrong horse"),
                  _asks("p => p.said === %s" % json.dumps(wrong)), _prompt("WRONG"),
                  _password(PASSWORD), _until(_SHELL), _prompt("PAIRED"), _lines("OPEN")]
                 + _typed("echo pw-ok") + [{"key": "Enter"}, _says("pw-ok"),
                                           _lines("ECHO")])
        out, js = _play(tmp_path, "term_password", base, steps)
        assert _tagged(js, "ASK")["shown"] == "flex", js
        assert _tagged(js, "WRONG")["said"] == wrong, js
        paired = _tagged(js, "PAIRED")
        assert paired["shown"] == "none" and paired["keys"] == 1, (
            str(paired) + _sides(host, tmp_path, out))
        assert "pw-ok" in "\n".join(_tagged(js, "ECHO")), js
    finally:
        server.terminate()
        host.end()


def test_a_page_says_how_long_a_board_holding_wrong_tries_off_waits(tty_exe, tmp_path, short):
    """Wrong tokens from this source past its free tries open a window; the
    page's hello meets the wait, and its prompt says the backoff's one
    sentence."""
    import seal_client
    host = _board(tty_exe, short)
    page = seal_client.Browser(host.send)
    board = page.hello()["id"]
    for _ in range(11):
        with pytest.raises(seal_client.Refused) as e:
            page.pair_token("00" * 16)
        assert e.value.status == 403, e.value.doc
    # The eleventh wrong try's window is a second; the clock stepped back a
    # minute holds it open past the page's boot, however long that takes.
    host.skew(-60000)
    qr = _qr_fragment(host, board)
    server, base = _serve(tmp_path, host.port)
    try:
        steps = [_asks("p => p.said.startsWith('Too many wrong tries')"), _prompt("ASK"),
                 {"shot": "signin_wait"}]
        _out, js = _play(tmp_path, "signin_wait", base, steps, after=qr)
        ask = _tagged(js, "ASK")
        assert ask["shown"] == "flex", ask
        m = re.match(r"^Too many wrong tries -- try again in (\d+) s$", ask["said"])
        assert m and 1 <= int(m.group(1)) <= 61, ask
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
        _out, js = _play(tmp_path, "term_site", base, steps)
        ls = "\n".join(_tagged(js, "LS"))
        assert "/files" in ls and "carts/" in ls and "files/" in ls, ls
        slept = "\n".join(_tagged(js, "SLEPT"))
        assert "[stopped: a command in the browser runs for 3 s at most]" in slept, slept
        assert "back" in "\n".join(_tagged(js, "BACK"))
    finally:
        p.terminate()
