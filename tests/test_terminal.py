"""The Terminal (runtime/apps/terminal_app.py's TerminalAppLayer): the C
shell's screen face in the console, over the host binding of the session
(runtime/host/moy_sh.py) and the console's own ROLE door. What a typed line
does, Tab, the history, Ctrl-C at the prompt and inside a run, and that an
idle frame makes nothing."""

import time

import pytest

from runtime import native_build

if native_build.cc() is None:
    pytest.skip("no C compiler", allow_module_level=True)


@pytest.fixture
def term(tmp_path):
    from runtime.host_app import build_workstation
    ws = build_workstation(str(tmp_path / "carts"))
    app = ws.terminal_app
    app.open()
    yield ws, app
    app.close_session()


def run(app, text):
    app.ed.set_text(text)
    app.ed.move(0, len(text))
    app.submit()


def plain(app):
    import re
    app._take()
    return [re.sub("\x1b\\[[0-9;]*m", "", ln) for ln in app.lines]


def test_the_terminal_is_a_system_app_on_its_identity_cart(term):
    ws, app = term
    cart = [c for c in ws.carts.all if c.get("title") == "Terminal"]
    assert cart and app.is_app(cart[0])
    assert ws._apps_by_id["terminal"] is app


def test_a_line_is_echoed_and_run_in_the_session(term):
    _ws, app = term
    run(app, "echo hello; mkdir /made; cd /made; pwd")
    assert plain(app) == ["$ echo hello; mkdir /made; cd /made; pwd", "hello", "/made"]
    assert app.lines[0] == "\x1b[2m$ \x1b[36mecho hello; mkdir /made; cd /made; pwd\x1b[0m"


def test_a_command_that_goes_on_takes_the_continuation_prompt(term):
    _ws, app = term
    run(app, "for i in 1 2")
    assert app.more and app.prompt() == "> "
    run(app, "do echo $i; done")
    assert not app.more and plain(app)[-2:] == ["1", "2"]
    run(app, "if true")
    app.key(3)                          # Ctrl-C drops what `more` held
    assert not app.more and plain(app)[-1] == "> ^C"
    run(app, "echo after")
    assert plain(app)[-1] == "after"


def test_tab_completes_a_command_and_a_path(term, tmp_path):
    ws, app = term
    app.ed.set_text("ech")
    app.ed.move(0, 3)
    app.key(9)
    assert app.ed.text() == "echo "
    app.ed.set_text("e")
    app.ed.move(0, 1)
    app.key(9)
    assert app.ed.text() == "e" and [c[0] for c in app.cands] == [
        "echo", "eval", "exec", "exit", "export"]
    assert plain(app)[-1] == "echo  eval  exec  exit  export"
    app.pick(3)
    assert app.ed.text() == "exit " and app.cands == []
    run(app, "mkdir /tree; mkdir /tree/alpha; touch /tree/beta")
    app.ed.set_text("ls /tree/a")
    app.ed.move(0, 10)
    app.key(9)
    assert app.ed.text() == "ls /tree/alpha/"


def test_up_and_down_walk_the_lines_run_before(term):
    _ws, app = term
    run(app, "echo one")
    run(app, "echo two")
    app.recall(-1)
    assert app.ed.text() == "echo two"
    app.recall(-1)
    app.recall(-1)
    assert app.ed.text() == "echo one"
    app.recall(1)
    app.recall(1)
    assert app.ed.text() == ""


def test_exit_starts_a_new_session(term):
    _ws, app = term
    run(app, "x=5; exit 3")
    assert plain(app)[-1] == "[the shell exited 3]"
    run(app, "echo ${x:-gone}")
    assert plain(app)[-1] == "gone"


def test_ctrl_c_stops_a_running_built_in(term):
    """The screen face's poll answers its Ctrl-C inside the run, and the
    rest of the line goes; the latched key is not a second Ctrl-C after."""
    _ws, app = term
    app.scr.interrupt()
    t0 = time.monotonic()
    run(app, "sleep 5; echo not-after")
    assert time.monotonic() - t0 < 2
    assert "not-after" not in plain(app) and app.scr.status() == 130
    app.key(3)
    assert plain(app)[-1] != "$ ^C"


def test_ctrl_c_ends_a_role_command_waiting_on_its_device(term, monkeypatch):
    """`ble scan` waits in Python across the door; the screen face's
    Ctrl-C reaches it through moy_sh.interrupted and it gives the radio
    back (runtime/shell/sh_roles.py)."""
    from runtime import devices, sh_roles
    held = []

    class Ble:
        def scan(self, ms):
            held.append(1)

        def close(self):
            held.pop()

        def devices(self):
            return []
    monkeypatch.setattr(devices, "api", lambda name: Ble() if name == "ble" else None)
    _ws, app = term
    polls = []
    real = sh_roles.stopped

    def stopped(d):
        polls.append(1)
        if len(polls) == 3:
            app.scr.interrupt()
        return real(d)
    monkeypatch.setattr(sh_roles, "stopped", stopped)
    t0 = time.monotonic()
    run(app, "ble scan 5000; echo not-after")
    assert time.monotonic() - t0 < 2
    assert held == [] and app.scr.status() == 130
    assert "not-after" not in plain(app)


def test_an_idle_frame_makes_no_line(term):
    """The grid's lines are taken when its generation moves, the prompt
    row when what it shows does: an idle frame rebuilds neither."""
    ws, app = term
    run(app, "echo a")
    app.draw(0)
    app.draw(0)
    lines, row = list(app.lines), app._prompt_row[0]
    assert not app._take()
    app.draw(0)
    assert all(a is b for a, b in zip(lines, app.lines)) and app._prompt_row[0] is row


def test_the_grid_follows_the_view(term):
    """The session's grid takes the log view's columns and rows."""
    _ws, app = term
    app.draw(0)
    app.draw(0)
    run(app, "printf '%0100d\\n' 0")
    assert max(len(x) for x in plain(app)) == app.cols


# -- the key row ------------------------------------------------------------------

SHELL_SYMBOLS = "|><&;$~/-*\"'\\{}[]()=!#?:"


def _tdeck_types():
    """The bytes the T-Deck's keyboard types, plain and with Sym (its matrix
    as native/moy_input/moy_kbd.c's RAW_KEYS decodes it)."""
    import os
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "native", "moy_input",
                            "moy_kbd.c")).read()
    table = src[src.index("RAW_KEYS[][4] = {"):]
    table = table[:table.index("};")]
    out = set()
    for m in re.finditer(r"\{\d+, 0x[0-9a-fA-F]+, ('(?:\\.|[^'])'|0x[0-9A-Fa-f]+|\d+), "
                         r"('(?:\\.|[^'])'|0x[0-9A-Fa-f]+|\d+)\}", table):
        for v in m.groups():
            if v.startswith("'"):
                out.add(v[1:-1].replace("\\'", "'").replace("\\\\", "\\"))
    return out


def test_the_tdeck_row_holds_the_shell_symbols_its_keyboard_lacks():
    from runtime.apps.terminal_app import TDECK_SYMBOLS, _TDECK_ROW
    typed = _tdeck_types()
    assert "$" in typed and "/" in typed and ";" in typed       # the parse reads the table
    assert sorted(TDECK_SYMBOLS) == sorted(c for c in SHELL_SYMBOLS if c not in typed)
    assert _TDECK_ROW == ("Tab",) + tuple(TDECK_SYMBOLS)


def test_a_desk_draws_no_key_row(tmp_path):
    from runtime import ui_draw
    from test_shell_goldens import CONFIGS, _build
    ws = _build(CONFIGS["p4_1024x600_cs2_fs1_windowed"], tmp_path)
    app = ws.terminal_app
    kind = ui_draw.input_kind()
    try:
        for k in ("keys", "touch", "pad", "tdeck"):
            ui_draw.input_kind(k)
            assert app.row_kind() is None, k
    finally:
        ui_draw.input_kind(kind)
        app.close_session()


def test_the_trackball_walks_the_tdeck_row_and_back(tmp_path):
    """On the T-Deck the trackball moves the caret and walks the history; down
    past the newest line enters the key row, along it a roll moves, Enter
    presses the key, and up goes back to the prompt."""
    from runtime import ui_draw
    from runtime.host_app import build_workstation, ConsoleDriver
    ws = build_workstation(str(tmp_path / "carts"))
    drv = ConsoleDriver(ws)
    ws.open_app(ws._apps_by_id["terminal"])
    drv.frame(1 / 30)
    app = ws.terminal_app
    kinds = ui_draw.input_kind(), ui_draw.hint_kind()
    ui_draw.input_kind("tdeck")
    ui_draw.hint_kind("tdeck")

    def roll(dx, dy):
        time.sleep(0.16)                # a hand's pause: the roll's axis is forgotten
        drv.pan(dx, dy)
        drv.frame(1 / 30)
        drv.frame(1 / 30)
        drv.pan(0, 0)
        drv.frame(1 / 30)

    def enter():
        drv.ball_click()
        drv.frame(1 / 30)
        drv.frame(1 / 30)

    try:
        assert app.row_kind() == "tdeck"
        run(app, "echo one")
        for ch in "ls":
            drv.type_char(ord(ch))
            drv.frame(1 / 30)
        roll(-1, 0)
        assert app.ed.col == 1                    # the caret, left
        roll(0, -1)
        assert app.ed.text() == "echo one"        # the history, up
        roll(0, 1)
        assert app.ed.text() == "" and not app.in_row
        roll(0, 1)
        assert app.in_row                         # down past the newest line
        roll(1, 0)
        roll(1, 0)
        enter()                                   # the trackball's click
        assert app.ed.text() == ">" and app.in_row
        roll(1, 0)
        drv.type_char(13)                         # the keyboard's Enter
        drv.frame(1 / 30)
        drv.frame(1 / 30)
        assert app.ed.text() == "><" and app.in_row
        roll(0, -1)
        assert not app.in_row
        drv.type_char(ord("x"))
        drv.frame(1 / 30)
        assert app.ed.text() == "><x"
        roll(0, 1)
        assert app.in_row
        drv.type_char(ord("y"))                   # a typed key goes back to the prompt
        drv.frame(1 / 30)
        assert app.ed.text() == "><xy" and not app.in_row
    finally:
        ui_draw.input_kind(kinds[0])
        ui_draw.hint_kind(kinds[1])
        app.close_session()
