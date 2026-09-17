"""#89 -- the Code tab edits a cart's OTHER scripts, not only its main file.

The gap was the tracker's last unchecked box ("Multi-file editing (holds one
`cart["src"]` buffer only)"), and SPEC.md 4's `sources` is what made it bite: a
PICO-8 port arrives as `p8.lua`, `main.lua` and one file per PICO-8 tab, so two
thirds of the code a person opens the cart to read was reachable only through
the Config tab's ADVANCED row -- which opens a file in the shell's TEXT handle,
where there is no PLAY, no journal and no crash-to-code. A crash reported at
`p8.lua:412:` threw the kid at a tab that could not show line 412.

Three layers, because the seam runs through all three:
  * the STORE (`cart_sources` / `source_text` / `save_code(..., name)` /
    `add_source`) -- the load order and which file a write lands in;
  * the CONSOLE (`ws.code_file`, `open_code_file`, the crash router) -- which
    file the tab holds and what a switch costs;
  * the SURFACES (code_layer's chip + popup, cards_layer's NEW SCRIPT) driven
    through the same shared console the boards run.

A ONE-FILE cart is the control in nearly every test here: the chip does not
exist, the load order is `[main]`, and every path reads exactly as it did.
"""

import json

import pytest

from runtime import host_app, moy_carts

DT = 1 / 30


# ---------------------------------------------------------------------------
# fixtures: a cart of several scripts, and a cart of one
# ---------------------------------------------------------------------------

def _write_cart(root, folder, main_src, extra=(), runtime="lua",
                main_name="main.lua", title="Multi"):
    """A cart folder with `extra` = [(name, text, "before"|"after"), ...]."""
    d = root / folder
    d.mkdir(parents=True)
    before = [n for n, _t, side in extra if side == "before"]
    after = [n for n, _t, side in extra if side == "after"]
    man = {"format": "moy-1", "title": title, "main": main_name,
           "runtime": runtime, "type": "game"}
    if extra:
        man["sources"] = before + [main_name] + after
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (d / main_name).write_text(main_src, encoding="utf-8")
    for name, text, _side in extra:
        (d / name).write_text(text, encoding="utf-8")
    return d


def _ws_with(tmp_path, folder, **kw):
    """A workstation whose shelf holds the cart at `folder`, opened in the
    Editor on the Code tab."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    cart = moy_carts.load(str(folder))
    assert cart is not None
    ws.launcher.items.append(cart)
    ws.launcher.sel = len(ws.launcher.items) - 1
    ws.open_in_editor(cart)
    ws.set_menu_view("code")
    return ws


def _multi(tmp_path):
    return _write_cart(
        tmp_path / "carts", "multi.moy",
        "function _draw() cls(0) end\n",
        extra=[("lib.lua", "-- the generated half\nfunction helper() end\n",
                "before"),
               ("extra.lua", "-- a tab\n", "after")])


# ---------------------------------------------------------------------------
# the store: the load order, and which file a write lands in
# ---------------------------------------------------------------------------

def test_cart_sources_is_the_load_order_with_main_in_it(tmp_path):
    """ONE definition of "which files is this cart's code in". Three readers
    reassembling it from `src_before`/`main`/`src_after` is three places to get
    the order wrong, and the order is the whole of it."""
    cart = moy_carts.load(str(_multi(tmp_path)))
    assert moy_carts.cart_sources(cart) == ["lib.lua", "main.lua", "extra.lua"]
    assert moy_carts.source_text(cart, "extra.lua") == "-- a tab\n"
    assert moy_carts.source_text(cart, "main.lua") == cart["src"]
    assert moy_carts.source_text(cart, None) == cart["src"], \
        "None means main -- that is what the Code tab defaults to"
    assert moy_carts.source_text(cart, "nope.lua") is None


def test_a_one_file_cart_answers_with_its_main_and_nothing_else(tmp_path):
    """The control. A cart that declares no `sources` IS `[main]`."""
    d = _write_cart(tmp_path / "carts", "plain.moy", "function _draw() end\n")
    cart = moy_carts.load(str(d))
    assert moy_carts.cart_sources(cart) == ["main.lua"]


def test_save_code_writes_the_named_script_and_leaves_the_others(tmp_path):
    """A write lands in ONE file, on disk and in RAM. The in-RAM half matters
    as much: the loaded cart is what the next PLAY runs, so a store write that
    left `src_after` stale would run the pre-edit text."""
    d = _multi(tmp_path)
    cart = moy_carts.load(str(d))
    status, _msg = moy_carts.save_code(cart, "-- edited\n", name="extra.lua")
    assert status == moy_carts.SAVE_OK
    assert (d / "extra.lua").read_text(encoding="utf-8") == "-- edited\n"
    assert (d / "main.lua").read_text(encoding="utf-8") == \
        "function _draw() cls(0) end\n"
    assert moy_carts.source_text(cart, "extra.lua") == "-- edited\n"
    assert cart["src"] == "function _draw() cls(0) end\n"

    # ...and no name still means main, which is every cart with one file.
    moy_carts.save_code(cart, "-- new main\n")
    assert (d / "main.lua").read_text(encoding="utf-8") == "-- new main\n"
    assert cart["src"] == "-- new main\n"


# ---------------------------------------------------------------------------
# the console: which file the tab holds, and what a switch costs
# ---------------------------------------------------------------------------

def test_the_code_tab_opens_on_main_and_switches_to_another_script(tmp_path):
    ws = _ws_with(tmp_path, _multi(tmp_path))
    assert ws.code_file_name() == "main.lua"
    assert ws.code_sources() == ["lib.lua", "main.lua", "extra.lua"]
    assert ws.editor.text() == "function _draw() cls(0) end\n"

    assert ws.open_code_file("lib.lua")
    assert ws.code_file_name() == "lib.lua"
    assert "function helper()" in ws.editor.text()

    assert not ws.open_code_file("nope.lua"), \
        "a name the cart does not list is refused, never opened blank"
    assert ws.code_file_name() == "lib.lua"


def test_a_file_switch_hard_commits_the_file_being_left(tmp_path):
    """A switch is an exit path for the outgoing file, exactly as a TAB switch
    is for the outgoing tab (#111/#154) -- reusing that discipline rather than
    inventing a second one is why this is three lines. A kid who switches
    mid-line keeps the line."""
    d = _multi(tmp_path)
    ws = _ws_with(tmp_path, d)
    ws.editor.set_text("-- half typed")
    ws.editor.dirty = True
    ws.open_code_file("extra.lua")
    assert (d / "main.lua").read_text(encoding="utf-8") == "-- half typed"
    assert ws.editor.text() == "-- a tab\n", "the incoming buffer is the new file's"


def test_the_open_file_is_what_a_save_and_a_play_write(tmp_path):
    d = _multi(tmp_path)
    ws = _ws_with(tmp_path, d)
    ws.open_code_file("extra.lua")
    ws.editor.set_text("-- edited in the tab\n")
    assert ws.save_code(force=True)
    assert (d / "extra.lua").read_text(encoding="utf-8") == "-- edited in the tab\n"
    assert (d / "main.lua").read_text(encoding="utf-8") == \
        "function _draw() cls(0) end\n"
    assert moy_carts.source_text(ws.cart, "extra.lua") == "-- edited in the tab\n"


def test_undo_is_scoped_to_the_open_file(tmp_path):
    """The bar's journal walk is scoped to the active tab's own file(s) (#111)
    so one tab's undo never reverts another's newest commit. A Code tab holding
    a file has the same obligation one rung down: an undo on `lib.lua` must not
    revert the last commit to `main.lua`."""
    ws = _ws_with(tmp_path, _multi(tmp_path))
    assert ws.history.active_tab_files() == ("main.lua",)
    ws.open_code_file("lib.lua")
    assert ws.history.active_tab_files() == ("lib.lua",)


def test_a_new_cart_opens_on_its_own_main_file(tmp_path):
    """`code_file` is workspace state, not shell state: opening another project
    must not leave the tab pointed at the name the last one was showing."""
    multi = _multi(tmp_path)
    plain = _write_cart(tmp_path / "carts", "plain.moy",
                        "function _draw() end\n", title="Plain")
    ws = _ws_with(tmp_path, multi)
    ws.open_code_file("extra.lua")
    other = moy_carts.load(str(plain))
    ws.open_in_editor(other)
    ws.set_menu_view("code")
    assert ws.code_file_name() == "main.lua"
    assert ws.editor.text() == "function _draw() end\n"


# ---------------------------------------------------------------------------
# the crash router: the file that RAISED, not the file that was open
# ---------------------------------------------------------------------------

def test_the_crash_locator_reads_the_file_out_of_a_lua_error(tmp_path):
    """`lua_ext.cart_chunks` names main's chunk "@cart" and every other script
    after its own file, so a port's error reads `p8.lua:412:` where the cart's
    own reads `cart:12:`. Asking only about main found NOTHING for the two
    thirds of a ported cart that is not main.lua."""
    from runtime.player import _lua_cart_where

    cart = moy_carts.load(str(_multi(tmp_path)))
    assert _lua_cart_where("cart:12: boom", cart) == ("main.lua", 12)
    assert _lua_cart_where("lib.lua:412: boom", cart) == ("lib.lua", 412)
    assert _lua_cart_where("no position here", cart) == (None, None)
    # The FIRST position wins whichever file names it: that is the raise point,
    # and traceback frames come after it.
    assert _lua_cart_where(
        "lib.lua:9: boom\nstack traceback:\n\tcart:20: in f", cart) \
        == ("lib.lua", 9)


def test_a_crash_in_another_script_opens_that_script_on_that_line(tmp_path):
    """The whole point, end to end on the real Player: a cart whose generated
    half raises lands the kid IN the generated half, on the line, not on
    main.lua's line N -- which is somebody else's line N."""
    from runtime import lua_host
    if lua_host.moycore_supports("") is not True:
        pytest.skip("host lua binding not built (needs a C compiler)")

    d = _write_cart(
        tmp_path / "carts", "boom.moy",
        "function _draw() cls(0) end\nfunction _update() helper() end\n",
        extra=[("lib.lua",
                "-- line 1\nfunction helper()\n  local t = nil\n"
                "  return t.x\nend\n", "before")])
    ws = _ws_with(tmp_path, d)
    ws.run_code()
    for _ in range(4):
        ws.input.begin_frame()
        ws.frame(DT)
    assert ws.cart_error is not None, "the cart was supposed to raise"
    assert ws.player.crash_file == "lib.lua", ws.cart_error
    assert ws.player.crash_line == 4, ws.cart_error

    assert ws._crash_to_code()
    assert ws.menu_view == "code"
    assert ws.code_file_name() == "lib.lua", \
        "crash-to-code opened the wrong file"
    assert ws.editor.row == 3, "the caret is not on the line that raised"
    assert ws.code_err_row == 3


def test_a_crash_marker_never_lands_on_the_wrong_file(tmp_path):
    """Switching away from the crashed file must not carry its red line onto
    another file's line N."""
    from runtime import lua_host
    if lua_host.moycore_supports("") is not True:
        pytest.skip("host lua binding not built (needs a C compiler)")

    d = _write_cart(
        tmp_path / "carts", "boom2.moy",
        "function _draw() cls(0) end\nfunction _update() helper() end\n",
        extra=[("lib.lua",
                "function helper()\n  local t = nil\n  return t.x\nend\n",
                "before")])
    ws = _ws_with(tmp_path, d)
    ws.run_code()
    for _ in range(4):
        ws.input.begin_frame()
        ws.frame(DT)
    assert ws._crash_to_code()
    assert ws.code_file_name() == "lib.lua" and ws.code_err_row is not None
    ws.open_code_file("main.lua")
    assert ws.code_err_row is None, \
        "the crashed file's marker followed the switch onto main.lua"


# ---------------------------------------------------------------------------
# the Code tab's surface: the chip and its popup
# ---------------------------------------------------------------------------

def test_a_one_file_cart_draws_no_file_chip(tmp_path):
    """The control for the whole surface: at one source there is nothing to
    switch between, so the chrome does not exist -- no rect, no tap, no draw."""
    d = _write_cart(tmp_path / "carts", "plain.moy", "function _draw() end\n")
    ws = _ws_with(tmp_path, d)
    assert ws.code_layer._file_btn(ws.code_layout) is None
    assert not ws.code_layer._files_open


def test_tapping_the_chip_opens_the_list_and_picking_a_row_switches(tmp_path):
    ws = _ws_with(tmp_path, _multi(tmp_path))
    drv = host_app.ConsoleDriver(ws)
    cl = ws.code_layer
    lay = ws.code_layout

    b = cl._file_btn(lay)
    assert b is not None
    drv.touch(b[0] + b[2] // 2, b[1] + b[3] // 2)
    drv.frame(DT)
    assert cl._files_open
    assert cl._files_sel == ws.code_sources().index("main.lua"), \
        "the switcher opens on the file the tab is showing, not on row 0"

    _panel, rects = cl._files_geom(lay)
    i = ws.code_sources().index("extra.lua")
    r = rects[i]
    drv.touch(r[0] + r[2] // 2, r[1] + r[3] // 2)
    drv.frame(DT)
    assert not cl._files_open
    assert ws.code_file_name() == "extra.lua"
    assert ws.editor.text() == "-- a tab\n"


def test_the_chip_names_the_open_file(tmp_path):
    ws = _ws_with(tmp_path, _multi(tmp_path))
    assert ws.code_layer._file_label() == "MAIN"
    ws.open_code_file("extra.lua")
    assert ws.code_layer._file_label() == "EXTRA"


# ---------------------------------------------------------------------------
# NEW SCRIPT: the one door that adds a file (Config -> ADVANCED)
# ---------------------------------------------------------------------------

def test_add_source_creates_the_file_and_lists_it_after_the_rest(tmp_path):
    d = _multi(tmp_path)
    cart = moy_carts.load(str(d))
    assert moy_carts.add_source(cart, "new.lua", "-- new\n") == "new.lua"
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["sources"] == ["lib.lua", "main.lua", "extra.lua", "new.lua"]
    assert (d / "new.lua").read_text(encoding="utf-8") == "-- new\n"
    assert moy_carts.cart_sources(cart)[-1] == "new.lua", \
        "the in-RAM cart must follow, or the Editor opens a file it cannot read"


def test_add_source_writes_sources_in_full_on_a_one_file_cart(tmp_path):
    """An absent `sources` MEANS `[main]` (SPEC.md 4), so the cart getting its
    SECOND script is exactly the case with nothing to append to."""
    d = _write_cart(tmp_path / "carts", "plain.moy", "function _draw() end\n")
    cart = moy_carts.load(str(d))
    assert moy_carts.add_source(cart, "helpers.lua") == "helpers.lua"
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["sources"] == ["main.lua", "helpers.lua"]


def test_add_source_refuses_a_name_the_folder_already_holds(tmp_path):
    """Overwriting a file to "create" it is how a kid loses a cart -- and a
    cart's own assets are in that folder too."""
    d = _multi(tmp_path)
    cart = moy_carts.load(str(d))
    assert moy_carts.add_source(cart, "extra.lua") is None
    assert moy_carts.add_source(cart, "manifest.json") is None
    assert moy_carts.add_source(cart, "sub/dir.lua") is None
    assert (d / "extra.lua").read_text(encoding="utf-8") == "-- a tab\n"


def test_the_new_script_door_is_offered_only_where_it_would_run(tmp_path):
    """The console's PYTHON tier runs `main` and nothing else, so a second
    script listed on a python cart is a file that silently never runs -- worse
    than not offering to make one."""
    lua = _write_cart(tmp_path / "carts", "lua.moy", "function _draw() end\n")
    py = _write_cart(tmp_path / "carts", "py.moy", "def _draw():\n    pass\n",
                     runtime="python", main_name="main.py", title="Py")
    ws = _ws_with(tmp_path, lua)
    assert moy_carts.multi_script(moy_carts.load(str(lua)))
    assert not moy_carts.multi_script(moy_carts.load(str(py)))

    cl = ws.cards_layer
    cl._open_files()
    assert "+ NEW SCRIPT" in cl.files["rows"]
    ws.open_in_editor(moy_carts.load(str(py)))
    cl._open_files()
    assert "+ NEW SCRIPT" not in cl.files["rows"]


def test_new_script_names_the_file_and_opens_it_in_the_code_tab(tmp_path):
    ws = _ws_with(tmp_path, _multi(tmp_path))
    ws.set_menu_view("cards")
    cl = ws.cards_layer
    cl._open_files()
    cl._files_open("+ NEW SCRIPT")
    assert cl.prompt is not None and cl.prompt.kind == "newf"
    cl.prompt.fields[0].text = "Enemy Helpers!"
    cl._commit_prompt()

    assert cl.prompt is None and cl.files is None
    assert ws.menu_view == "code"
    assert ws.code_file_name() == "enemy_helpers.lua", \
        "a typed name is slugged under the cart's own runtime extension"
    assert ws.editor.text().startswith("-- enemy_helpers.lua")


def test_new_script_refuses_a_taken_name_and_an_unusable_one(tmp_path):
    """It stays OPEN and says why, rather than closing on a name it did not
    make -- the CART INFO modal's rule for a blank title."""
    ws = _ws_with(tmp_path, _multi(tmp_path))
    ws.set_menu_view("cards")
    cl = ws.cards_layer
    cl._open_files()
    cl._files_open("+ NEW SCRIPT")
    cl.prompt.fields[0].text = "extra"
    cl._commit_prompt()
    assert cl.prompt is not None and cl.prompt.msg == "THAT NAME IS TAKEN"
    cl.prompt.fields[0].text = "!!!"
    cl._commit_prompt()
    assert cl.prompt is not None and cl.prompt.msg == "NAME IT WITH LETTERS"


def test_a_projects_own_script_routes_to_the_code_tab_not_the_text_handle(
        tmp_path):
    """The ADVANCED row's router (docs/text_editing_2026-09.md step 5) sent any
    non-main file through the text handle. A cart's own SCRIPT belongs on the
    Code tab now -- that is where PLAY, the journal and crash-to-code are."""
    ws = _ws_with(tmp_path, _multi(tmp_path))
    ws.set_menu_view("cards")
    cl = ws.cards_layer
    cl._open_files()
    cl._files_open("lib.lua")
    assert ws.menu_view == "code"
    assert ws.code_file_name() == "lib.lua"


def test_a_script_added_here_actually_runs(tmp_path):
    """The claim the door rests on: the new file is a real chunk of the cart,
    so a global it defines is reachable from main. If `sources` had not been
    rewritten, this would fail as a nil call at the first tick."""
    from runtime import lua_host
    if lua_host.moycore_supports("") is not True:
        pytest.skip("host lua binding not built (needs a C compiler)")

    d = _write_cart(tmp_path / "carts", "grow.moy",
                    "seen = 0\nfunction _draw() cls(0) end\n")
    ws = _ws_with(tmp_path, d)
    ws.set_menu_view("cards")
    cl = ws.cards_layer
    cl._open_files()
    cl._files_open("+ NEW SCRIPT")
    cl.prompt.fields[0].text = "helpers"
    cl._commit_prompt()
    assert ws.code_file_name() == "helpers.lua"
    ws.editor.set_text("function bump() seen = seen + 1 end\n")
    assert ws.save_code(force=True)

    ws.open_code_file("main.lua")
    ws.editor.set_text("seen = 0\nfunction _update() bump() end\n"
                       "function _draw() cls(0) end\n")
    ws.run_code()
    for _ in range(5):
        ws.input.begin_frame()
        ws.frame(DT)
    assert ws.cart_error is None, ws.cart_error
    assert ws.player._lua.get_global("seen") == 5, \
        "the added script's global never reached the cart's own chunk"
