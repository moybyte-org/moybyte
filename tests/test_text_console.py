"""The TEXT CONSOLE and the vault that holds scripts (docs/text_editing_2026-09.md
step 6, Part A).

A script is a cart with NO FOLDER -- a bare `.py`/`.lua` file beside the notes --
and this is the half that is true before anything runs it: the vault lists it
under its whole name, and `runtime/text_console.py` is the surface it will get
(a bounded scrollback ring, wrapped as it is written, and a prompt line over the
shared `CodeEditor`). The RUNNER and its `input()` round trip are
`tests/test_scripts.py`.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


# ---------------------------------------------------------------------------
# the store: a script is a vault item under its WHOLE name
# ---------------------------------------------------------------------------

def test_the_vault_lists_scripts_beside_the_notes(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    moy_carts.save_file("docs", "shopping", "milk\n", ws.carts_root)
    moy_carts.save_file("docs", "hello.py", "print(1)\n", ws.carts_root)
    moy_carts.save_file("docs", "hello.lua", "print(1)\n", ws.carts_root)
    names = moy_carts.list_files("docs", ws.carts_root)
    # the note keeps its bare stem; a script carries the extension that says
    # which runtime runs it, so the two `hello`s do not collide
    assert set(names) == {"shopping", "hello.py", "hello.lua"}
    assert moy_carts.count_files("docs", ws.carts_root) == 3
    assert moy_carts.load_file("docs", "hello.py", ws.carts_root) == "print(1)\n"


def test_a_script_name_slugs_its_stem_and_keeps_its_extension(tmp_path):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    stored = moy_carts.save_file("docs", "My Script.py", "x = 1\n", ws.carts_root)
    assert stored == "my_script.py"
    assert moy_carts.load_file("docs", stored, ws.carts_root) == "x = 1\n"
    new = moy_carts.rename_file("docs", stored, "Hello There.py", ws.carts_root)
    assert new == "hello_there.py"


# ---------------------------------------------------------------------------
# Part A -- the console surface
# ---------------------------------------------------------------------------

def test_the_scrollback_is_a_bounded_ring(tmp_path):
    from runtime.text_console import TextConsole
    con = TextConsole(_ws(tmp_path), {})
    for i in range(con.LINES + 50):
        con.write("line %d\n" % i)
    assert con.count() == con.LINES              # bounded: nothing grows
    assert len(con._buf) == con.LINES            # and the ring is preallocated
    assert con.row(0) == "line 50"               # the oldest 50 rolled off
    assert con.row(con.LINES - 1) == "line %d" % (con.LINES + 49)


def test_print_wraps_at_the_view_width_and_end_keeps_the_row_open(tmp_path):
    from runtime.text_console import TextConsole
    con = TextConsole(_ws(tmp_path), {})
    con.cols = 8
    con.write("abcdefghij\n")
    assert [con.row(0), con.row(1)] == ["abcdefgh", "ij"]
    con.clear()
    con.write("ab")            # print(..., end="") leaves the row open
    con.write("cd\n")
    assert con.count() == 1 and con.row(0) == "abcd"
    con.clear()
    con.write("\n\n")          # a bare newline is a blank row, not nothing
    assert con.count() == 2 and con.row(0) == ""


def test_scrolling_back_holds_a_window_of_the_log(tmp_path):
    from runtime.text_console import TextConsole
    con = TextConsole(_ws(tmp_path), {})
    con.rows = 4
    for i in range(20):
        con.write("row %d\n" % i)
    assert con.back == 0                     # a write follows the tail
    con.scroll(3)
    assert con.back == 3
    con.scroll(100)
    assert con.back == 20 - con.rows         # clamped at the oldest row
    con.scroll(-100)
    assert con.back == 0
