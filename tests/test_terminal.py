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
    app.face.set_line(text)
    app.face.submit()
    settle(app)


def settle(app, limit=10.0):
    """The frames' tails until the line's run ends: a run that waits parks
    (native/moy_sh/moy_sh_board.h) and the loop's job resumes it."""
    from runtime import moy_sh
    t0 = time.monotonic()
    while app.face.busy() and time.monotonic() - t0 < limit:
        moy_sh.jobs()
        time.sleep(0.002)


def plain(app):
    import re
    app.face.take()
    return [re.sub("\x1b\\[[0-9;]*m", "", ln) for ln in app.face.lines()]


def test_the_terminal_is_a_system_app_on_its_identity_cart(term):
    ws, app = term
    cart = [c for c in ws.carts.all if c.get("title") == "Terminal"]
    assert cart and app.is_app(cart[0])
    assert ws._apps_by_id["terminal"] is app


def test_a_line_is_echoed_and_run_in_the_session(term):
    _ws, app = term
    run(app, "echo hello; mkdir /made; cd /made; pwd")
    assert plain(app) == ["$ echo hello; mkdir /made; cd /made; pwd", "hello", "/made"]
    assert app.face.lines()[0] == "\x1b[2m$ \x1b[36mecho hello; mkdir /made; cd /made; pwd\x1b[0m"


def test_a_command_that_goes_on_takes_the_continuation_prompt(term):
    _ws, app = term
    run(app, "for i in 1 2")
    assert app.face.more() and app.face.prompt() == "> "
    run(app, "do echo $i; done")
    assert not app.face.more() and plain(app)[-2:] == ["1", "2"]
    run(app, "if true")
    app.face.key(3)                     # Ctrl-C drops what `more` held
    assert not app.face.more() and plain(app)[-1] == "> ^C"
    run(app, "echo after")
    assert plain(app)[-1] == "after"


def test_tab_completes_a_command_and_a_path(term, tmp_path):
    ws, app = term
    f = app.face
    f.set_line("ech")
    f.key(9)
    assert f.line() == "echo "
    f.set_line("e")
    f.key(9)
    assert f.line() == "e" and f.cands() == ["echo", "edit", "eval", "exec", "exit", "export"]
    assert plain(app)[-1] == "echo  edit  eval  exec  exit  export"
    f.pick(4)
    assert f.line() == "exit " and f.cands() == []
    run(app, "mkdir /tree; mkdir /tree/alpha; mkdir /tree/apex; touch /tree/beta")
    f.set_line("ls /tree/al")
    f.key(9)
    assert f.line() == "ls /tree/alpha/"
    f.set_line("ls /tree/a")
    f.key(9)
    assert f.line() == "ls /tree/a" and f.cands() == ["alpha/", "apex/"]


def test_tab_completes_an_argument_with_no_folder_from_the_working_one(term):
    """An argument with no `/` names the working folder: `cat <Tab>` lists it
    and `cat asd<Tab>` completes from it (a word with no slash once read the
    folder's end from strrchr's NULL and took the console down)."""
    _ws, app = term
    run(app, "mkdir /w; cd /w; touch asdf.txt; mkdir apex; touch beta")
    for n in range(40):
        run(app, f"touch long_name_of_a_file_that_runs_past_the_row_{n:02}")
    f = app.face
    f.set_line("cat asd")
    f.key(9)
    assert f.line() == "cat asdf.txt "
    f.set_line("cat a")
    f.key(9)
    assert f.line() == "cat a" and f.cands() == ["apex/", "asdf.txt"]
    f.set_line("cat ")
    f.key(9)
    assert f.line() == "cat " and len(f.cands()) == 24
    assert "apex/  asdf.txt  beta  long_name" in "".join(plain(app))
    f.set_line("cat zz")
    f.key(9)
    assert f.line() == "cat zz"
    run(app, "cd /")
    f.set_line("cat ")
    f.key(9)
    assert f.line() == "cat " and "w/" in f.cands()


def test_up_and_down_walk_the_lines_run_before(term):
    _ws, app = term
    run(app, "echo one")
    run(app, "echo two")
    f = app.face
    f.recall(-1)
    assert f.line() == "echo two"
    f.recall(-1)
    f.recall(-1)
    assert f.line() == "echo one"
    f.recall(1)
    f.recall(1)
    assert f.line() == "" and f.history() == ["echo one", "echo two"]


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
    app.face.interrupt()
    t0 = time.monotonic()
    run(app, "sleep 5; echo not-after")
    assert time.monotonic() - t0 < 2
    assert "not-after" not in plain(app) and app.face.status() == 130
    app.face.key(3)
    assert plain(app)[-1] != "$ ^C"


def test_ctrl_c_ends_a_role_command_waiting_on_its_device(term, monkeypatch):
    """`http get` waits in Python across the door; the screen face's
    Ctrl-C reaches it through moy_sh.interrupted and it gives the request
    back (runtime/shell/sh_roles.py)."""
    from runtime import devices, sh_roles
    held = []

    class Http:
        CAP_MAX = 65536

        def get(self, url, cap):
            held.append(1)

            class R:
                def state(self):
                    return None

                def close(self):
                    held.pop()
            return R()
    monkeypatch.setattr(devices, "api", lambda name: Http() if name == "http" else None)
    _ws, app = term
    polls = []
    real = sh_roles.stopped

    def stopped(d):
        polls.append(1)
        if len(polls) == 3:
            app.face.interrupt()
        return real(d)
    monkeypatch.setattr(sh_roles, "stopped", stopped)
    t0 = time.monotonic()
    run(app, "http get http://x/; echo not-after")
    assert time.monotonic() - t0 < 2
    assert held == [] and app.face.status() == 130
    assert "not-after" not in plain(app)


def _slow_role(monkeypatch, steps=3, ms=30):
    """`lease` as a role command that waits: a line, then `ms`, `steps`
    times (runtime/shell/sh_roles.py's generators); `ended` once its
    `finally` ran."""
    from runtime import sh_roles
    ended = []

    def slow(d, a):
        try:
            for i in range(steps):
                d.out("step %d\n" % i)
                if (yield from sh_roles._wait(d, ms)):
                    return sh_roles.INTERRUPTED
            return 0
        finally:
            ended.append(1)
    monkeypatch.setattr(sh_roles, "r_lease", slow)
    return ended


def test_a_waiting_role_command_parks_and_its_output_streams(term, monkeypatch):
    """A role command that waits is stepped across the door a frame at a
    time: Enter returns while it waits (the run parks on the session's
    runner, native/moy_sh/moy_sh_board.h), its lines reach the grid as they
    come, the prompt stays down until it ends, and the line goes on after."""
    from runtime import moy_sh
    ended = _slow_role(monkeypatch)
    _ws, app = term
    app.face.set_line("lease; echo after")
    t0 = time.monotonic()
    app.face.submit()
    assert time.monotonic() - t0 < 0.025
    assert app.face.busy() and app.face.prompt() == ""
    assert plain(app)[-1] == "step 0"
    app.face.set_line("echo typed ahead")
    app.face.submit()                   # Enter while it runs does nothing
    t0 = time.monotonic()
    while "step 1" not in plain(app) and time.monotonic() - t0 < 2:
        moy_sh.jobs()
        time.sleep(0.005)
    assert "step 1" in plain(app) and app.face.busy() and "after" not in plain(app)
    settle(app)
    assert not app.face.busy() and ended == [1] and app.face.status() == 0
    assert plain(app)[-4:] == ["step 0", "step 1", "step 2", "after"]
    assert app.face.prompt() == "$ " and app.face.line() == "echo typed ahead"


def test_ctrl_c_stops_a_parked_role_command(term, monkeypatch):
    """Ctrl-C while the run is parked is the run's: the role command is
    stopped at its next turn (its `finally` gives back what it took), the
    rest of the line goes, and the prompt is back."""
    ended = _slow_role(monkeypatch, steps=50)
    _ws, app = term
    app.face.set_line("lease; echo not-after")
    app.face.submit()
    assert app.face.busy()
    app.face.key(3)
    settle(app, limit=2)
    assert not app.face.busy() and ended == [1] and app.face.status() == 130
    assert "not-after" not in plain(app) and "step 49" not in plain(app)
    run(app, "echo next")
    assert plain(app)[-1] == "next"


def test_b_while_a_run_is_parked_stops_the_run_and_keeps_the_terminal(term, monkeypatch):
    """The gamepad's B is the console's back: while a run is parked it is the
    run's cancel, as Ctrl-C is, and the Terminal stays open; at the prompt it
    leaves the Terminal as before."""
    ended = _slow_role(monkeypatch, steps=50)
    ws, app = term
    drv = _frame(ws)
    app.face.set_line("lease; echo not-after")
    app.face.submit()
    assert app.face.busy()
    drv.press("b")
    for _ in range(3):
        drv.frame(1 / 30)
    settle(app, limit=2)
    assert ws.wm.top_kind() == "terminal"
    assert not app.face.busy() and ended == [1] and app.face.status() == 130
    assert "not-after" not in plain(app)
    run(app, "echo next")
    assert plain(app)[-1] == "next", "the B stopped that run and no other"
    drv.press("b")
    for _ in range(3):
        drv.frame(1 / 30)
    assert ws.wm.top_kind() != "terminal"


def test_the_console_s_frames_go_on_while_a_command_waits(term, monkeypatch):
    """The kernel's frame (moy_loop_step, the host's driver tier) runs the
    parked run's turn at its tail: frames keep their pace while `sleep` and
    a waiting role command go on, and the run ends on the frames alone."""
    from runtime import moy_loop as _loop
    _slow_role(monkeypatch, steps=2, ms=40)
    _ws, app = term
    app.face.set_line("sleep 0.1; lease; echo done")
    app.face.submit()
    assert app.face.busy()
    worst, t_end = 0.0, time.monotonic() + 3
    while app.face.busy() and time.monotonic() < t_end:
        t0 = time.monotonic()
        _loop.drive(lambda: 0, lambda: 0, lambda dt: 1, 1 / 30)
        worst = max(worst, time.monotonic() - t0)
        time.sleep(1 / 60)
    assert not app.face.busy() and plain(app)[-1] == "done"
    assert worst < 0.025, worst


def test_an_idle_frame_makes_no_line(term):
    """The grid's lines are taken when its generation moves: an idle frame
    takes none."""
    ws, app = term
    run(app, "echo a")
    app.draw(0)
    app.draw(0)
    assert not app.face.take()
    app.draw(0)
    assert not app.face.take()


def test_the_grid_follows_the_view(term):
    """The session's grid takes the log view's columns and rows."""
    _ws, app = term
    app.draw(0)
    app.draw(0)
    run(app, "printf '%0100d\\n' 0")
    assert max(len(x) for x in plain(app)) == app.face.size()[0]


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
    from runtime import moy_sh
    typed = _tdeck_types()
    assert "$" in typed and "/" in typed and ";" in typed       # the parse reads the table
    assert moy_sh.tdeck_row() == "".join(c for c in SHELL_SYMBOLS if c not in typed)
    assert moy_sh.tdeck_row() == "|><&~\\{}[]="


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
        assert app.face.col() == 1                    # the caret, left
        roll(0, -1)
        assert app.face.line() == "echo one"        # the history, up
        roll(0, 1)
        assert app.face.line() == "" and not app.face.in_row()
        roll(0, 1)
        assert app.face.in_row()                         # down past the newest line
        roll(1, 0)
        roll(1, 0)
        enter()                                   # the trackball's click
        assert app.face.line() == ">" and app.face.in_row()
        roll(1, 0)
        drv.type_char(13)                         # the keyboard's Enter
        drv.frame(1 / 30)
        drv.frame(1 / 30)
        assert app.face.line() == "><" and app.face.in_row()
        roll(0, -1)
        assert not app.face.in_row()
        drv.type_char(ord("x"))
        drv.frame(1 / 30)
        assert app.face.line() == "><x"
        roll(0, 1)
        assert app.face.in_row()
        drv.type_char(ord("y"))                   # a typed key goes back to the prompt
        drv.frame(1 / 30)
        assert app.face.line() == "><xy" and not app.face.in_row()
    finally:
        ui_draw.input_kind(kinds[0])
        ui_draw.hint_kind(kinds[1])
        app.close_session()


# -- the text apps (native/moy_sh/moy_sh_app.h) ----------------------------------------


def _frame(ws):
    from runtime.host_app import ConsoleDriver
    drv = ConsoleDriver(ws)
    if ws.wm.top_kind() != "terminal":
        ws.open_app(ws.terminal_app)
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    return drv


def _keys(app, data):
    for b in data:
        app.face.key(b)


def _arrow(app, d, n=1):
    steps = {"left": (n, 0, 0, 0), "right": (0, n, 0, 0), "up": (0, 0, n, 0), "down": (0, 0, 0, n)}
    app.face.input(app.layout.desk, 0, 0, 0, *steps[d])


def test_the_app_screen_is_sized_to_the_page(term):
    """A full-screen app's screen is the page's rows and columns, the prompt
    row gone: a resize is the app's own."""
    ws, app = term
    cols0, rows0 = app.face.size()
    run(app, "top")
    _frame(ws)
    assert len(plain(app)) > rows0
    _keys(app, b"q")
    assert app.face.app() is None and app.face.size() == (cols0, rows0)


def test_top_shows_the_process_table_and_quits(term):
    """`top` reads what `ps` reads, the task manager's rows across the door,
    a row picked with the arrows; q gives the shell back."""
    ws, app = term
    run(app, "top")
    assert app.face.app() == "top"
    _frame(ws)
    shown = plain(app)
    assert shown[0].startswith("top ") and any(ln.startswith(">") for ln in shown)
    assert any(ln.rstrip().endswith("shell") for ln in shown)
    _arrow(app, "down")
    assert plain(app)[3].startswith(" ") and plain(app)[4].startswith(">")
    _keys(app, b"q")
    assert app.face.app() is None and plain(app)[-1] == "$ top"


def test_edit_opens_full_screen_saves_and_gives_the_grid_back(term, tmp_path):
    """`edit FILE` takes the face's whole page with a screen of its own; the
    keys edit, ^S saves through the store's crash-safe write, ^X with a
    change asks first, and the shell's grid is back as it was."""
    ws, app = term
    run(app, "mkdir -p /sd/moybyte/system")
    run(app, "printf 'theme = \"dusk\"\\n' > /sd/moybyte/system/settings")
    before = plain(app)
    run(app, "edit /sd/moybyte/system/settings")
    assert app.face.app() == "edit" and app.face.full()
    _frame(ws)
    shown = plain(app)
    assert shown[0] == "edit settings" and shown[1] == 'theme = "dusk"'
    _arrow(app, "down")
    _keys(app, b"volume = 3")
    _keys(app, b"\x13")                                 # ^S
    path = tmp_path / "sd" / "moybyte" / "system" / "settings"
    assert path.read_text() == 'theme = "dusk"\nvolume = 3'
    _keys(app, b"!\x18")                                # a change, then ^X
    assert app.face.app() == "edit" and "Save the changes?" in plain(app)[-1]
    _keys(app, b"n")
    assert app.face.app() is None
    assert path.read_text() == 'theme = "dusk"\nvolume = 3'
    assert plain(app)[:len(before)] == before
    assert plain(app)[-1] == "$ edit /sd/moybyte/system/settings"


def test_python_is_a_line_app_over_the_prompt(term):
    """`python` with the terminal for its stdin keeps the prompt line, its
    prompt the REPL's: a line at a time into the console's VM, a block held
    until it ends, Ctrl-D on an empty line back to the shell."""
    _ws, app = term
    run(app, "python")
    assert app.face.app() == "python" and not app.face.full()
    assert app.face.prompt() == ">>> "
    run(app, "6 * 7")
    assert plain(app)[-2:] == [">>> 6 * 7", "42"]
    run(app, "def f(x):")
    assert app.face.prompt() == "... "
    app.face.set_line("")
    app.face.key(9)                                     # Tab: four spaces
    app.face.set_line(app.face.line() + "return x + 1")
    app.face.submit()
    run(app, "")
    run(app, "print(f(1))")
    assert plain(app)[-1] == "2" and app.face.prompt() == ">>> "
    app.face.key(4)
    assert app.face.app() is None and app.face.prompt() == "$ "
    run(app, "echo $?")
    assert plain(app)[-1] == "0"


def test_mic_enters_the_app_key_row_on_the_tdeck(term):
    """On the T-Deck the board's cancel (Mic) goes to a full-screen app's key
    row, whose Quit ends it; with no app in front Mic stays the console's."""
    from runtime import ui_draw
    ws, app = term
    kinds = ui_draw.input_kind(), ui_draw.hint_kind()
    ui_draw.input_kind("tdeck")
    ui_draw.hint_kind("tdeck")
    try:
        assert not app.cancel_field()
        run(app, "top")
        _frame(ws)
        assert app.cancel_field() and app.face.in_row()
        assert app.cancel_field() and not app.face.in_row()
        app.face.press("Quit")
        assert app.face.app() is None
    finally:
        ui_draw.input_kind(kinds[0])
        ui_draw.hint_kind(kinds[1])


def test_a_session_starts_in_the_user_files_folder_beside_the_carts(term, tmp_path):
    """The host's files tree is the carts folder's parent, so the user's
    files folder is its `/files`; a session starts there and `cd` returns
    there, and `/` still lists the tree's root."""
    _ws, app = term
    (tmp_path / "files").mkdir(exist_ok=True)
    run(app, "exit")
    run(app, "pwd; echo $HOME; cd /; cd; pwd")
    assert plain(app)[-3:] == ["/files", "/files", "/files"]
    run(app, "ls -1 /")
    listed = plain(app)
    assert {"bin", "carts", "files"} <= set(listed[listed.index("files$ ls -1 /") + 1:])


def test_neofetch_reads_the_console_s_own_rows_from_its_proc_file(term):
    """/proc/console is the door's: the rows only the console knows, which
    neofetch (C) shows beside its own; on the screen face in colour, the
    title kept on a screen too short for every row."""
    ws, app = term
    run(app, "cat /proc/console")
    rows = dict(ln.split(": ", 1) for ln in plain(app)[1:] if ": " in ln)
    assert rows["name"].startswith("moybyte-")
    assert rows["display"] == "%dx%d" % (ws.glass_canvas.w, ws.glass_canvas.h)
    run(app, "ls /proc")
    assert "console" in plain(app)[-1].split()
    run(app, "neofetch")
    shown = plain(app)
    assert any(rows["name"] in ln for ln in shown)
    assert any(ln.strip().startswith("Shell: moy_sh") for ln in shown)
    # the T-Deck's screen: the title first, the rows that fit, no palette
    assert shown[shown.index("$ neofetch") + 1] == rows["name"] + "@moybyte"
    lines = app.face.lines()
    assert any("\x1b[38;2;255;119;168mDisplay\x1b[0m" in ln for ln in lines)
    assert not any("\x1b[48;2;" in ln for ln in lines)
