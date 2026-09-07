"""SCRIPTS and the TEXT CONSOLE (step 6 of docs/text_editing_2026-09.md).

A script is a cart with NO FOLDER: a bare `.py`/`.lua` file in the vault that
RUN wraps in a manifest synthesized on the fly. Two halves, both driven through
the real console rather than through mocks -- the surface (`runtime/
text_console.py`: the scrollback ring, the prompt, the exit) and the runner
(`Workstation.run_script`: the manifest, the Player path, the #120 gate).

The keyboard half runs on the BOARDS' `InputState` (`device/moybyte/input.py`),
not the host's: the two are pinned separate on purpose (.claude/rules/shell.md)
and the prompt is fed by whichever one the tier has.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DT = 1 / 30


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


def _device_ws(tmp_path):
    """The console with the BOARDS' InputState under it; `(ws, keyboard)`."""
    import sys
    sys.path.insert(0, str(ROOT))
    from device.moybyte.input import InputState as DeviceInputState

    ws = _ws(tmp_path)
    inp = DeviceInputState()
    inp.cart_start_ms = 0
    ws.input = inp
    return ws, inp.source("kbd")


def _frame(ws, n=1):
    for _ in range(n):
        ws.input.begin_frame()
        ws.frame(DT)


def _type(ws, src, text):
    """Type `text` at the prompt, one key per frame, releasing between so the
    edge tracker fires -- which is what a real keyboard does."""
    for ch in text:
        src.last_key = ord(ch)
        _frame(ws)
        src.last_key = 0
        _frame(ws)


def _seed(ws, name, src):
    from runtime import moy_carts
    return moy_carts.save_file("docs", name, src, ws.carts_root)


def _run(ws, name, src):
    stored = _seed(ws, name, src)
    ok, why = ws.run_script("docs", stored)
    assert ok, "run_script refused %r: %s" % (stored, why)
    _frame(ws)
    return ws.script_console


# ---------------------------------------------------------------------------
# Part B -- the runner (Part A's surface + vault tests are
# tests/test_text_console.py)
# ---------------------------------------------------------------------------

def test_input_round_trips_through_the_console_on_the_device_input_state(tmp_path):
    """A whole `input()` cycle on the boards' InputState: the script asks, the
    prompt takes the line, and the answer comes back ONCE."""
    ws, kbd = _device_ws(tmp_path)
    con = _run(ws, "ask.py",
               "def _update(dt):\n"
               "    who = input('who? ')\n"
               "    if who is not None:\n"
               "        print('hi ' + who)\n"
               "        quit()\n")
    assert con.reading() and con._prompt == "who? "
    _type(ws, kbd, "ana")
    assert con.ed.text() == "ana"
    kbd.last_key = 0x0D                       # enter
    _frame(ws)
    kbd.last_key = 0
    _frame(ws, 2)
    text = con.text()
    assert "who? ana" in text                 # the prompt + the line are echoed
    assert "hi ana" in text
    assert text.count("hi ana") == 1          # the answer is handed over ONCE


def test_backspace_edits_the_prompt_line_and_never_exits(tmp_path):
    ws, kbd = _device_ws(tmp_path)
    con = _run(ws, "ask.py", "def _update(dt):\n    input('> ')\n")
    _type(ws, kbd, "abc")
    kbd.last_key = 0x08
    _frame(ws)
    kbd.last_key = 0
    _frame(ws)
    assert con.ed.text() == "ab"
    assert ws.wm.top_is_player(), "backspace is a text key here, not the exit"


def test_the_symbol_palette_types_the_keys_the_keyboard_lacks(tmp_path):
    """The T-Deck has no `=`/`[`/`{`/`<`/`%` keys, so the Code tab shows a
    tappable palette -- and the prompt shows the same one, because it is the
    same keyboard."""
    ws = _ws(tmp_path)
    con = _run(ws, "ask.py", "def _update(dt):\n    input('> ')\n")
    _frame(ws)
    assert con._sym_rects, "the palette is drawn while the console reads"
    r = con._sym_rects[0]
    con._pointer(r[0] + 1, r[1] + 1, True, True)
    assert con.ed.text() == con._symbols()[0]


def test_the_x_exits_and_flushes_nothing(tmp_path):
    """A script has no document, so leaving writes nothing: the exit is the
    OS bar's X (a script is tool-shaped), and the vault is untouched."""
    from runtime import moy_carts
    ws = _ws(tmp_path)
    src = "print('hello')\n"
    con = _run(ws, "note.py", src)
    assert ws._running_cart_shows_bar(), "a script runs WITH the exitable bar"
    before = sorted(moy_carts.list_files("docs", ws.carts_root))
    ws._exit_to_caller()
    _frame(ws)
    assert not ws.wm.top_is_player()
    assert sorted(moy_carts.list_files("docs", ws.carts_root)) == before
    assert moy_carts.load_file("docs", "note.py", ws.carts_root) == src
    assert con.text() == "hello"       # the scrollback is not a document either


def test_a_python_script_runs_through_the_real_player(tmp_path):
    ws = _ws(tmp_path)
    con = _run(ws, "hi.py", "print('one')\nprint('two')\n")
    assert con.text() == "one\ntwo"
    assert ws.cart["type"] == "script"
    assert ws.cart["runtime"] == "python"
    assert ws.cart["permissions"] == ["files", "prefs", "console"]
    assert ws.cart["title"] == "hi"
    assert ws.player._script and ws.player._is_tool
    assert ws.player.tick_ms == 0, "a script is unpaced: one tick per loop frame"


def test_a_lua_script_runs_the_same_way(tmp_path):
    import pytest
    ws = _ws(tmp_path)
    if ws.lua_runtime is None:
        pytest.skip("no host Lua binding in this build")
    con = _run(ws, "hi.lua", "print('one')\nprint('two', 3)\n")
    assert con.text() == "one\ntwo 3"
    assert ws.cart["runtime"] == "lua"


def test_running_a_script_creates_no_folder_and_writes_nothing(tmp_path):
    """The manifest is synthesized in RAM. Nothing about a run touches the
    store -- no `.moy` folder, no manifest file, not even a stat's worth of
    change to the vault."""
    import os
    ws = _ws(tmp_path)
    root = ws.carts_root
    _seed(ws, "hi.py", "print('hi')\n")
    before_carts = sorted(os.listdir(root))
    docs = os.path.join(os.path.dirname(root), "files", "docs")
    before_docs = sorted(os.listdir(docs))
    ok, _why = ws.run_script("docs", "hi.py")
    assert ok
    _frame(ws, 3)
    ws._exit_to_caller()
    _frame(ws)
    assert sorted(os.listdir(root)) == before_carts
    assert sorted(os.listdir(docs)) == before_docs


def test_the_capability_gate_refuses_carts_and_the_network(tmp_path):
    """#120, as the smallest correct thing: the synthesized manifest names
    three permissions, and the machinery that already refuses does the
    refusing -- an ungranted capability has no NAME at all. The console turns
    that into a sentence."""
    ws = _ws(tmp_path)
    for name, verb, said in (("net.py", "wifi.scan()", "wifi"),
                             ("mk.py", "carts.create('x')", "carts")):
        con = _run(ws, name, verb + "\n")
        text = con.text()
        assert "NameError" in text
        assert "a script can't use " + said in text.replace("\n", "")
        assert ws.player.cart_error is None, "the console IS the error surface"
    # ...and the three it DOES get are real
    con = _run(ws, "ok.py",
               "prefs.set('n', 7)\nprint(prefs.get('n'))\nprint(len(files.list()))\n")
    assert con.text().startswith("7\n")


def test_a_script_error_lands_in_the_console(tmp_path):
    """No crash-to-code: a script has no folder for the Editor to open, so the
    traceback is printed into its own scrollback and the surface stays live."""
    ws = _ws(tmp_path)
    con = _run(ws, "boom.py", "def _update(dt):\n    1 / 0\n")
    _frame(ws, 2)
    assert "ZeroDivisionError" in con.text()
    assert ws.player.cart_error is None
    assert ws.wm.top_is_player(), "the console stays up, and exitable"
    # and it stops running: one traceback, not one per frame
    once = con.text()
    _frame(ws, 3)
    assert con.text() == once


def test_a_script_body_that_does_not_parse_lands_there_too(tmp_path):
    ws = _ws(tmp_path)
    con = _run(ws, "bad.py", "def (\n")
    assert "SyntaxError" in con.text() or "invalid syntax" in con.text()
    assert ws.player.cart_error is None


def test_a_generator_update_is_driven_one_step_per_frame(tmp_path):
    """The linear way to write the same poll: `yield` waits a frame, and the
    run ends when the generator does."""
    ws, kbd = _device_ws(tmp_path)
    con = _run(ws, "gen.py",
               "def _update(dt):\n"
               "    name = None\n"
               "    while name is None:\n"
               "        name = input('name? ')\n"
               "        yield\n"
               "    print('hello ' + name)\n")
    assert con.reading()
    _type(ws, kbd, "sam")
    kbd.last_key = 0x0D
    _frame(ws)
    kbd.last_key = 0
    _frame(ws, 3)
    assert "hello sam" in con.text()
    assert not con.reading(), "the generator finished, so nothing is reading"


def test_run_script_refuses_what_is_not_a_script(tmp_path):
    ws = _ws(tmp_path)
    _seed(ws, "shopping", "milk\n")
    assert ws.run_script("docs", "shopping") == (False, "NOT A SCRIPT")
    assert ws.run_script("docs", "nothing.py")[0] is False


# ---------------------------------------------------------------------------
# Files: the RUN action, beside the router
# ---------------------------------------------------------------------------

def _files_app(ws):
    for i, cart in enumerate(ws.launcher.items):
        if cart.get("title") == "Files":
            ws.launcher.sel = i
            break
    ws.open()
    _frame(ws)
    return ws.files_app


def test_files_offers_RUN_on_a_script_and_not_on_a_note(tmp_path):
    ws = _ws(tmp_path)
    _seed(ws, "hi.py", "print('hi')\n")
    _seed(ws, "shopping", "milk\n")
    app = _files_app(ws)
    app._enter_kind("docs")
    app.grid.select("hi.py")
    assert app._action_labels()[0] == "RUN"
    app.grid.select("shopping")
    assert "RUN" not in app._action_labels()


def test_the_files_RUN_action_starts_the_script(tmp_path):
    ws = _ws(tmp_path)
    _seed(ws, "hi.py", "print('from files')\n")
    app = _files_app(ws)
    app._enter_kind("docs")
    app.grid.select("hi.py")
    app._act("RUN", "hi.py")
    _frame(ws)
    assert ws.wm.top_is_player()
    assert ws.script_console.text() == "from files"


def test_the_router_sends_a_script_to_the_text_page_not_the_editor(tmp_path):
    """The cart-main door and the `code` MODE are the same string; only a
    project file reaches the Editor, and OPEN on a script edits it."""
    from runtime import text_modes
    ws = _ws(tmp_path)
    _seed(ws, "hi.py", "print('hi')\n")
    app = _files_app(ws)
    app._enter_kind("docs")
    assert app.door("hi.py", "docs") == (text_modes.CODE, "hi.py")
    assert app.route("hi.py", kind="docs") == text_modes.CODE
    # OPEN edits the script: the Player is running Notes' editor skin over it
    # (a cart, since step 3), not the script itself.
    assert ws.wm.top_is_player()
    assert ws.cart.get("title") == "Notes", ws.cart.get("title")
    assert ws.script_console is None or ws.cart.get("type") != "script"
