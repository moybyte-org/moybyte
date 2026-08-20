"""Tests for the structured-outline block editor UI (issue #29, Part 2).

Two layers:
  * BlockEditor core (runtime/editors.py) -- pure tree edits + cursor, tested
    directly against the Part-1 blocks model/compiler.
  * The console UI -- driven through the real shared host Workstation (the same
    code the device runs) via ConsoleDriver (mouse == touch, arrows == trackball),
    so the insert/edit/save/graduate flows go through the actual input + frame
    paths, and the editor renders without error.

The decided interaction is a structured outline: a vertical script of nested
colored blocks, cursor nav, press A to insert from a category menu, no dragging.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import blocks  # noqa: E402
from runtime import moy_carts  # noqa: E402
from runtime.editors import BlockEditor  # noqa: E402

mk = blocks.make_block


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

from blocks_helpers import run_cart as _run  # noqa: E402


def _be():
    return BlockEditor(blocks)


def _go_to_insert(be, depth, which=-1):
    """Park the cursor on an insert row at `depth` (the last one by default)."""
    found = [i for i, r in enumerate(be.rows) if r.kind == "insert" and r.depth == depth]
    assert found, "no insert row at depth %d" % depth
    be.cur = found[which]


def _select_type(be, tid):
    for i, r in enumerate(be.rows):
        if (r.block or {}).get("t") == tid:
            be.cur = i
            return True
    return False


# ----------------------------------------------------------------------------
# BlockEditor core: flatten + cursor
# ----------------------------------------------------------------------------

def test_empty_program_flattens_to_three_hats_with_inserts():
    be = _be()
    kinds = [(r.kind, (r.block or {}).get("t")) for r in be.rows]
    # the three lifecycle hats, each with a trailing insert point under it
    assert ("block", "on_start") in kinds
    assert ("block", "on_update") in kinds
    assert ("block", "on_draw") in kinds
    assert any(r.kind == "insert" and r.depth == 1 for r in be.rows)


def test_cursor_moves_and_clamps():
    be = _be()
    be.move(-5)
    assert be.cur == 0
    be.move(100)
    assert be.cur == len(be.rows) - 1


# ----------------------------------------------------------------------------
# BlockEditor core: insert / delete / move mutate the tree, and it compiles
# ----------------------------------------------------------------------------

def test_insert_blocks_then_compile_and_run():
    be = _be()
    _go_to_insert(be, 1)                    # the on_start trailing insert
    be.insert_block("set_var", {"var": "x", "value": 0})
    be.add_var("x")
    # insert a draw cls + spr under on_draw
    _go_to_insert(be, 1)                    # now the last depth-1 insert (under on_draw)
    be.insert_block("cls", {"color": "black"})
    _go_to_insert(be, 1)
    be.insert_block("spr", {"id": 1, "x": mk("var", {"var": "x"}), "y": 100})
    src = blocks.compile_blocks(be.program)
    assert "cls(col(" in src and "spr(1," in src
    _run(src, frames=2)                     # runs clean as a cart


def test_nested_insert_inside_a_cblock():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("if", {"cond": mk("btn", {"dir": "left"})})
    # the new if is selected; its body opened a depth-2 insert point
    _go_to_insert(be, 2)
    be.insert_block("pix", {"x": 1, "y": 1, "color": "white"})
    src = blocks.compile_blocks(be.program)
    assert 'if btn("left"):' in src
    assert "    pix(1, 1, col(" in src or "        pix(1, 1, col(" in src
    _run(src)


def test_delete_removes_subtree_but_refuses_hats():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "red"})
    assert _select_type(be, "cls")
    assert be.delete() is True
    assert not _select_type(be, "cls")          # gone from the tree
    # a hat can never be deleted (a script must keep its lifecycle)
    assert _select_type(be, "on_start")
    assert be.delete() is False


def test_move_block_reorders_siblings():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    _go_to_insert(be, 1)
    be.insert_block("circ", {"x": 1, "y": 2, "r": 3, "color": "red"})
    # both live in the same body; move the circ up above the cls
    assert _select_type(be, "circ")
    assert be.move_block(-1) is True
    body = [c["t"] for c in be.program["scripts"][2]["c"]]    # on_draw body
    assert body[:2] == ["circ", "cls"]
    # moving past the start fails
    assert be.move_block(-1) is False


def test_if_else_divider_and_branches():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("if_else", {"cond": mk("btnp", {"dir": "a"})})
    assert be.insert_else() is True
    assert be.insert_else() is False             # only one else
    # else divider shows as a non-deletable label row
    else_rows = [r for r in be.rows if r.is_else]
    assert len(else_rows) == 1
    be.cur = be.rows.index(else_rows[0])
    assert be.delete() is False                  # can't delete the divider
    # put a block in each branch
    inserts2 = [i for i, r in enumerate(be.rows) if r.kind == "insert" and r.depth == 2]
    be.cur = inserts2[0]                          # if-body
    be.insert_block("cls", {"color": "green"})
    inserts2 = [i for i, r in enumerate(be.rows) if r.kind == "insert" and r.depth == 2]
    be.cur = inserts2[-1]                          # else-body (after the divider)
    be.insert_block("cls", {"color": "red"})
    src = blocks.compile_blocks(be.program)
    assert "    else:" in src
    _run(src)


# ----------------------------------------------------------------------------
# BlockEditor core: slot edits change the generated code
# ----------------------------------------------------------------------------

def test_editing_a_slot_changes_generated_code():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    assert _select_type(be, "cls")
    before = blocks.compile_blocks(be.program)
    assert 'cls(col("black"))' in before
    # cycle the dropdown to the next color
    newval = be.cycle_dropdown("color", 1)
    after = blocks.compile_blocks(be.program)
    assert ('cls(col("' + newval + '"))') in after
    assert before != after


def test_set_expr_slot_nests_an_expression():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("spr", {"id": 0, "x": 0, "y": 0})
    assert _select_type(be, "spr")
    be.set_slot("x", mk("op_add", {"a": 5, "b": 3}))
    src = blocks.compile_blocks(be.program)
    assert "spr(0, (5 + 3), 0)" in src


# ----------------------------------------------------------------------------
# The console UI -- driven through the real host Workstation
# ----------------------------------------------------------------------------

def _ws_with_block_cart(tmp_path, title="UI Block Cart"):
    from runtime import host_app
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    cart = moy_carts.create(title, root, type="game")
    ws.launcher.items = moy_carts.scan(root)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()
    return ws, cart, root


def _driver(ws):
    from runtime import host_app
    return host_app.ConsoleDriver(ws)


def test_blocks_view_opens_and_renders(tmp_path):
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    assert ws.menu_view == "blocks" and ws.block_ui.blocks_ed is not None
    # renders without error
    for _ in range(2):
        ws.frame(1 / 30)
    assert ws.cart_error is None


def test_desktop_blocks_button_opens_editor(tmp_path):
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    drv = _driver(ws)
    drv.frame(1 / 30)                                  # on the desktop (cart running)
    ws.cart_error = "boom"   # Stage 5: the in-cart bar is CRASH chrome (pause retired)
    ws._dirty = True
    bx, by = C._BLOCKS_BTN[0] + 2, C._BLOCKS_BTN[1] + 2
    drv.click(bx, by)
    drv.frame(1 / 30)
    assert ws.menu_view == "blocks"


def test_insert_flow_through_the_menu_then_runs(tmp_path):
    """Press A on an insert point -> category menu -> block list -> a block is
    inserted, the program compiles, and the cart runs through the Workstation."""
    ws, cart, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    drv = _driver(ws)
    be = ws.block_ui.blocks_ed
    # park on the trailing insert under on_draw and press A. An idle frame between
    # presses mirrors a real release (the input edge needs the key to lift first).
    _go_to_insert(be, 1)
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)                                  # release
    assert ws.block_ui.blk_menu is not None and ws.block_ui.blk_menu["mode"] == "cat"
    # navigate to DRAW and select
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index(blocks.CAT_DRAW)
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_menu["mode"] == "blk"
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index("cls")
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_menu is None
    body = [c["t"] for c in be.program["scripts"][2].get("c", [])]
    assert "cls" in body
    # save + leave -> the compiled cart runs on the desktop
    ws.block_ui.save_blocks()
    drv.escape()
    for _ in range(5):
        drv.frame(1 / 30)
    assert ws.cart_error is None


def test_save_persists_blocks_and_main_and_reloads(tmp_path):
    ws, cart, root = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    be.add_var("score")
    _go_to_insert(be, 1)                                # under on_start
    be.insert_block("set_var", {"var": "score", "value": 0})
    assert ws.block_ui.save_blocks() is True and ws.block_ui.blk_status is None
    # both files landed; reload restores the program AND a runnable main.py
    reloaded = moy_carts.load(cart["path"])
    assert reloaded["blocks"] == be.program
    assert reloaded["src"].startswith("# Made with Moybyte blocks")
    assert "score = 0" in reloaded["src"]
    # a fresh editor over the reloaded cart restores the same tree
    again = BlockEditor(blocks, moy_carts.load_blocks(cart["path"]))
    assert again.program == be.program


def test_dropdown_slot_picker_sets_the_value(tmp_path):
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    assert _select_type(be, "cls")
    ws.block_ui.blk_slot = 0
    ws.block_ui._blk_a()                                     # A opens the color picker
    assert ws.block_ui.blk_menu is not None and ws.block_ui.blk_menu["mode"] == "dropdown"
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index("green")
    ws.block_ui._blk_menu_select()
    assert ws.block_ui.blk_menu is None
    assert be.selected_block()["p"]["color"] == "green"
    assert 'cls(col("green"))' in blocks.compile_blocks(be.program)


def test_variable_slot_picker_leads_with_new_variable(tmp_path):
    """Opening a {var} slot with no variables yet shows a picker that LEADS with
    '+ new variable' (Bug 2): choosing it creates one, opens the name prompt, and
    the slot ends up filled with the (default-named) variable -- so the program
    compiles. The kid never hits an empty/uncreatable slot."""
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("set_var")                      # no vars declared yet
    assert _select_type(be, "set_var")
    ws.block_ui.blk_slot = 0                                 # the {var} slot
    ws.block_ui._blk_a()                                     # opens the variable picker
    assert ws.block_ui.blk_menu is not None and ws.block_ui.blk_menu["mode"] == "variable"
    # the first item is the create-and-name entry
    assert ws.block_ui.blk_menu["items"][0] == C._NEW_VAR_ITEM
    ws.block_ui.blk_menu["sel"] = 0
    ws.block_ui._blk_menu_select()                           # create + open the name prompt
    assert ws.block_ui.blk_menu is None and ws.block_ui.blk_kbd is not None
    ws.block_ui._blk_kbd_commit()                            # accept the default name
    assert ws.block_ui.blk_kbd is None
    # the var is declared, the slot points at it, and the program compiles
    assert be.variables()
    assert be.selected_block()["p"]["var"] in be.variables()
    blocks.compile_blocks(be.program)


def test_graduate_to_code_opens_code_editor_on_generated_source(tmp_path):
    ws, cart, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "indigo"})
    ws.block_ui.graduate_to_code()
    assert ws.menu_view == "code" and ws.editor is not None
    assert 'cls(col("indigo"))' in ws.editor.text()
    # the code editor renders the graduated source without error
    ws.frame(1 / 30)
    assert ws.cart_error is None


def test_block_authored_cart_runs_normally(tmp_path):
    """A cart authored from blocks RUNs like any other cart (its compiled main.py)."""
    ws, cart, root = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    be.add_var("x")
    _go_to_insert(be, 1)                                # on_start: set x = 100
    be.insert_block("set_var", {"var": "x", "value": 100})
    ws.block_ui.save_blocks()
    # reopen the cart fresh from disk and run it
    ws.launcher.items = moy_carts.scan(root)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == cart["title"]:
            ws.launcher.sel = i
    ws.open()
    assert ws.cart_error is None and ws.ns is not None
    for _ in range(5):
        ws.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.ns["x"] == 100


def test_pointer_taps_insert_and_action_bar(tmp_path):
    """The outline is fully pointer/touch-drivable (no dragging): a tap selects a
    row, a second tap on an insert opens the menu, and the action-bar buttons fire."""
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    drv = _driver(ws)
    drv.frame(1 / 30)
    # tap ADD -> the category menu opens
    drv.click(C._BLK_ADD[0] + 2, C._BLK_ADD[1] + 2)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_menu is not None
    # tap a menu row (DRAW) then dismiss back out
    # (drill: pick category by tapping its row)
    my = C._BLK_MENU[1] + 16
    draw_i = ws.block_ui.blk_menu["items"].index(blocks.CAT_DRAW)
    drv.click(C._BLK_MENU[0] + 20, my + draw_i * C._BLK_MENU_ROW_H + 2)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_menu["mode"] == "blk"


# ----------------------------------------------------------------------------
# Kid-facing copy: forever-is-bounded + wait hints surface in the editor
# ----------------------------------------------------------------------------

def test_forever_and_wait_have_kidfacing_hints(tmp_path):
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("forever")
    assert _select_type(be, "forever")
    assert "every frame" in ws.block_ui._blk_hint()             # forever is bounded
    _go_to_insert(be, 1)
    be.insert_block("wait", {"secs": 1})
    assert _select_type(be, "wait")
    assert "pause" in ws.block_ui._blk_hint()
    # and the compiled forever is the bounded loop (Part-1 contract)
    src = blocks.compile_blocks(be.program)
    assert "while True" not in src and "range(100000)" in src


# ----------------------------------------------------------------------------
# Bug 1 (data loss): opening BLOCKS on a hand-written-code cart must NEVER
# clobber that cart's main.py.
# ----------------------------------------------------------------------------

_HANDWRITTEN_SRC = (
    "# my own game, do not lose this!\n"
    "x = 5\n"
    "\n"
    "def _draw():\n"
    "    cls(col('black'))\n"
    "    circ(x, 50, 8, col('red'))\n"
)


def _ws_with_code_cart(tmp_path, src, title="Code Cart"):
    """A workstation opened on a cart whose main.py is HAND-WRITTEN code (no
    blocks.json) -- the case Bug 1 must protect."""
    from runtime import host_app
    root = str(tmp_path / "carts")
    ws = host_app.build_workstation(root)
    cart = moy_carts.create(title, root, src=src, type="game")
    ws.launcher.items = moy_carts.scan(root)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()
    return ws, cart, root


def test_blocks_on_handwritten_cart_does_not_lose_code(tmp_path):
    """THE core regression: a plain-Python cart's main.py is NOT overwritten when
    the kid opens BLOCKS and saves an (empty) block program."""
    ws, cart, root = _ws_with_code_cart(tmp_path, _HANDWRITTEN_SRC)
    # the editor opens in PROTECTED mode (hand-written main.py, no blocks.json)
    ws._open_blocks()
    assert ws.menu_view == "blocks"
    assert ws.block_ui.blk_protect is True
    # try to SAVE the empty block program -- it must refuse and keep the code
    assert ws.block_ui.save_blocks() is False
    # in-RAM source is untouched...
    assert ws.cart["src"] == _HANDWRITTEN_SRC
    # ...and so is the on-disk main.py (nothing was written, no blocks.json appeared)
    reloaded = moy_carts.load(cart["path"])
    assert reloaded["src"] == _HANDWRITTEN_SRC
    assert reloaded["blocks"] is None
    # leaving the editor re-runs the kid's real code (not an empty blocks cart)
    ws._leave_menu()
    for _ in range(3):
        ws.frame(1 / 30)
    assert ws.cart_error is None
    assert ws.ns is not None and ws.ns.get("x") == 5     # the real code ran


def test_blocks_protect_blocks_graduate_overwrite(tmp_path):
    """In protected mode, 'graduate to code' opens the EXISTING code (it must not
    compile the empty blocks over it)."""
    ws, cart, _ = _ws_with_code_cart(tmp_path, _HANDWRITTEN_SRC)
    ws._open_blocks()
    assert ws.block_ui.blk_protect is True
    ws.block_ui.graduate_to_code()
    assert ws.menu_view == "code" and ws.editor is not None
    assert ws.editor.text() == _HANDWRITTEN_SRC          # the kid's code, not blocks
    assert "Made with Moybyte blocks" not in ws.editor.text()


def test_block_authored_cart_is_not_protected(tmp_path):
    """A genuinely block-authored cart (has blocks.json) stays fully editable --
    the guard only fires on hand-written code. Round-trip is unchanged."""
    ws, cart, root = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    assert ws.block_ui.blk_protect is False                       # new-template cart: editable
    be.add_var("score")
    _go_to_insert(be, 1)
    be.insert_block("set_var", {"var": "score", "value": 7})
    assert ws.block_ui.save_blocks() is True
    # reopen fresh from disk: it loads its blocks.json, so it's NOT protected and
    # saving works again (round-trip unchanged).
    ws.launcher.items = moy_carts.scan(root)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == cart["title"]:
            ws.launcher.sel = i
    ws.open()
    ws._open_blocks()
    assert ws.block_ui.blk_protect is False
    assert ws.block_ui.save_blocks() is True


# ----------------------------------------------------------------------------
# Bug 2 (UX): create + name a new variable, then use it.
# ----------------------------------------------------------------------------

def test_new_variable_entry_creates_names_and_is_usable(tmp_path):
    """The Variables category leads with '+ new variable'. Choosing it creates a
    variable, opens the on-screen-keyboard name prompt, the kid types a name, and
    the named variable is then usable in a set block -- and compiles into the
    generated Python under that exact name."""
    import runtime.console as C
    ws, cart, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    # open the insert menu -> Variables category
    _go_to_insert(be, 1)
    ws.block_ui._blk_open_categories()
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index(blocks.CAT_VARIABLES)
    ws.block_ui._blk_menu_select()
    assert ws.block_ui.blk_menu["mode"] == "blk"
    # the FIRST entry in Variables is "+ new variable"
    assert ws.block_ui.blk_menu["items"][0] == C._NEW_VAR_ITEM
    assert ws.block_ui._blk_menu_label(0) == C._NEW_VAR_LABEL
    ws.block_ui.blk_menu["sel"] = 0
    ws.block_ui._blk_menu_select()                                # create + open name prompt
    assert ws.block_ui.blk_kbd is not None
    # type a name: "lives" (each char is a keystroke; backspace + a digit too)
    for ch in "lives":
        ws.block_ui._blk_kbd_key(ord(ch))
    ws.block_ui._blk_kbd_key(ord("2"))                            # "lives2"
    ws.block_ui._blk_kbd_key(8)                                   # backspace -> "lives"
    ws.block_ui._blk_kbd_commit()
    assert ws.block_ui.blk_kbd is None
    assert "lives" in be.variables()
    # now USE it: insert a set_var and point its {var} slot at "lives"
    _go_to_insert(be, 1)
    be.insert_block("set_var", {"var": "lives", "value": 3})
    src = blocks.compile_blocks(be.program)
    assert "lives = 0" in src                            # declared at module level
    assert "lives = 3" in src                            # assigned in the body
    _run(src)                                            # runs clean as a cart


def test_new_variable_name_is_sanitized_and_renames_references(tmp_path):
    """Free-typed names are coerced to safe identifiers, and renaming a variable
    rewrites every slot that referenced it (so set/change/expr keep working)."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    name = be.new_var("var")                             # default-named variable
    _go_to_insert(be, 1)
    be.insert_block("set_var", {"var": name, "value": 1})
    # rename it via free text with spaces/punctuation -> a safe identifier
    applied = be.rename_var(name, "my score!!")
    assert applied == "my_score"
    assert "my_score" in be.variables() and name not in be.variables()
    # the set_var slot followed the rename (find it wherever it landed)
    assert _select_type(be, "set_var")
    assert be.selected_block()["p"]["var"] == "my_score"
    src = blocks.compile_blocks(be.program)
    assert "my_score = 0" in src and "my_score = 1" in src


def test_name_prompt_keyboard_flow_through_the_driver(tmp_path):
    """The name prompt is reachable + typeable through the real input model
    (type_char == on-screen keyboard, press('a') == confirm), end to end."""
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    drv = _driver(ws)
    # drive to the Variables block list and pick "+ new variable"
    ws.block_ui._blk_open_categories()
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index(blocks.CAT_VARIABLES)
    ws.block_ui._blk_menu_select()
    ws.block_ui.blk_menu["sel"] = 0                               # "+ new variable"
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)                                    # release
    assert ws.block_ui.blk_kbd is not None
    # type "gold" one char per frame (last_key edge), then confirm with A
    for ch in "gold":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
        drv.frame(1 / 30)                                # release so the next edge fires
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_kbd is None
    assert "gold" in be.variables()


def test_new_variable_prompt_survives_the_opening_keypress(tmp_path):
    """Regression (#29): the A/Enter press that SELECTS '+ new variable' must NOT
    carry into the freshly opened name prompt and instantly commit it (the device
    bug: the prompt flashed for a frame and the variable kept its default name).
    Here A is *held* across the open frame (mimicking the device keyboard latch and
    the held Enter byte) -- the prompt must stay open, no commit -- and only a name
    the kid then types becomes the variable's name (compiled under that name)."""
    ws, cart, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    drv = _driver(ws)
    # drill to Variables -> "+ new variable" highlighted
    ws.block_ui._blk_open_categories()
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index(blocks.CAT_VARIABLES)
    ws.block_ui._blk_menu_select()
    ws.block_ui.blk_menu["sel"] = 0
    # SELECT it with A *while ALSO* holding the Enter byte (0x0D) -- on the device A
    # is the Enter/z alias, so last_key carries that byte; with no idle frame between
    # the select and the next pass the trigger could leak into the prompt's commit.
    drv.hold("a", True)
    drv.type_char(0x0D)
    drv.frame(1 / 30)                                    # this frame opens the prompt
    assert ws.block_ui.blk_kbd is not None, "prompt should open"
    # A + the Enter byte STILL held the very next frame: the prompt must NOT
    # commit. The held byte is the DEVICE keyboard's latch (the same byte
    # repeating across adjacent frames), injected directly -- the driver's
    # typed queue is device-faithful now and ships a 0 gap between two queued
    # discrete repeats, so it can no longer express a latched hold.
    ws.input.begin_frame()
    ws.input.last_key = 0x0D
    ws.handle_input()
    ws.frame(1 / 30)
    ws.input.last_key = 0
    assert ws.block_ui.blk_kbd is not None, "opening keypress must not auto-commit the prompt"
    assert not be.variables() or be.variables() == [ws.block_ui.blk_kbd["var"]]
    drv.hold("a", False)
    drv.type_char(0)
    drv.frame(1 / 30)                                    # release the trigger
    # now the kid actually types a name and confirms -> the TYPED name sticks
    for ch in "lives":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
        drv.frame(1 / 30)                               # release so each edge fires
    drv.press("a")                                      # confirm
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_kbd is None
    assert "lives" in be.variables()
    assert "var" not in be.variables()                 # NOT the default name
    # and it compiles into the generated Python under the typed name
    _go_to_insert(be, 1)
    be.insert_block("set_var", {"var": "lives", "value": 3})
    src = blocks.compile_blocks(be.program)
    assert "lives = 0" in src and "lives = 3" in src
    _run(src)


def test_new_variable_prompt_survives_the_opening_tap(tmp_path):
    """Regression (#29), touch path: the TAP that selects '+ new variable' must not
    carry into the new prompt and hit OK. Open the prompt by tapping its menu row,
    then assert it stays open across the next frame (the opening click is cleared)."""
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    drv = _driver(ws)
    # drill to Variables block list
    ws.block_ui._blk_open_categories()
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index(blocks.CAT_VARIABLES)
    ws.block_ui._blk_menu_select()
    assert ws.block_ui.blk_menu["mode"] == "blk"
    # tap the "+ new variable" row (index 0) to select it -> opens the name prompt
    mx, my = C._BLK_MENU[0], C._BLK_MENU[1]
    row0_y = (my + 16) + 0 * C._BLK_MENU_ROW_H + 2
    drv.click(mx + 20, row0_y)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_kbd is not None, "tapping '+ new variable' should open the prompt"
    # an immediate next frame (the tap is gone) -- the prompt must still be open,
    # not closed by the opening tap leaking onto OK/X.
    drv.frame(1 / 30)
    assert ws.block_ui.blk_kbd is not None, "opening tap must not auto-commit the prompt"


# ----------------------------------------------------------------------------
# Typed literals in slots (#29 blocking gap): an expr slot is a Scratch editable
# oval -- type a number OR drop a reporter block; number slots are typeable too.
# ----------------------------------------------------------------------------

def test_expr_slot_defaults_to_a_typeable_literal(tmp_path):
    """Editing an expr slot opens the number pad by default (not the block menu), so
    a kid can TYPE a literal -- the previously-impossible `set score to 0`."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    # a fresh set_var: its {value} expr slot defaults to the literal 0
    be.new_var("score")
    _go_to_insert(be, 1)
    blk = be.insert_block("set_var", {"var": "score", "value": 0})
    # edit the value (expr) slot -> the NUMBER PAD opens (a literal), not the block menu
    slot = [s for s in be.slots(blk) if s["name"] == "value"][0]
    ws.block_ui._blk_edit_slot(blk, slot)
    assert ws.block_ui.blk_kbd is not None and ws.block_ui.blk_kbd["kind"] == "num"
    assert ws.block_ui.blk_kbd.get("allow_block"), "an expr slot must offer the BLOCK escape"
    assert ws.block_ui.blk_menu is None


def test_type_a_literal_into_an_expr_slot_compiles_to_bare_value(tmp_path):
    """The headline fix: typing 5 into a set_var value (an expr slot) compiles to the
    BARE literal `score = 5` -- no quotes, no code injection -- and runs."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    be.new_var("score")
    _go_to_insert(be, 1)
    blk = be.insert_block("set_var", {"var": "score", "value": 0})
    slot = [s for s in be.slots(blk) if s["name"] == "value"][0]
    ws.block_ui._blk_edit_slot(blk, slot)
    # type "5" then OK
    ws.block_ui._blk_kbd_key(ord("5"))
    ws.block_ui._blk_kbd_commit()
    assert ws.block_ui.blk_kbd is None
    assert blk["p"]["value"] == 5 and isinstance(blk["p"]["value"], int)
    src = blocks.compile_blocks(be.program)
    assert "score = 5" in src                    # bare literal, not "5"
    _run(src)


def test_set_var_to_zero_and_three_roundtrip_and_execute(tmp_path):
    """`set score to 0` and `set lives to 3` -- the exact cases that were impossible --
    round-trip through the model and EXECUTE with the right values."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    be.new_var("score")
    be.new_var("lives")
    _go_to_insert(be, 1)
    b0 = be.insert_block("set_var", {"var": "score", "value": 0})
    _go_to_insert(be, 1)
    b3 = be.insert_block("set_var", {"var": "lives", "value": 0})
    # type 0 into score's value, 3 into lives' value, both via the number pad
    s0 = [s for s in be.slots(b0) if s["name"] == "value"][0]
    ws.block_ui._blk_edit_slot(b0, s0)
    ws.block_ui._blk_kbd_key(ord("0"))
    ws.block_ui._blk_kbd_commit()
    s3 = [s for s in be.slots(b3) if s["name"] == "value"][0]
    ws.block_ui._blk_edit_slot(b3, s3)
    ws.block_ui._blk_kbd_key(ord("3"))
    ws.block_ui._blk_kbd_commit()
    src = blocks.compile_blocks(be.program)
    assert "score = 0" in src and "lives = 3" in src
    # these run in _init; check the values land
    fake = _run(src)
    assert fake["score"] == 0 and fake["lives"] == 3


def test_negative_and_decimal_literals_type_correctly(tmp_path):
    """A leading '-' and a single '.' are accepted; the slot stores a real int/float."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    be.new_var("v")
    _go_to_insert(be, 1)
    blk = be.insert_block("change_var", {"var": "v", "value": 0})
    slot = [s for s in be.slots(blk) if s["name"] == "value"][0]
    ws.block_ui._blk_edit_slot(blk, slot)
    for ch in "-2":                              # type "-2"
        ws.block_ui._blk_kbd_key(ord(ch))
    # a stray second '-' and letters are filtered out of the buffer
    ws.block_ui._blk_kbd_key(ord("-"))
    ws.block_ui._blk_kbd_key(ord("x"))
    ws.block_ui._blk_kbd_commit()
    assert blk["p"]["value"] == -2
    src = blocks.compile_blocks(be.program)
    assert "v = v + (-2)" in src


def test_number_slot_is_typeable(tmp_path):
    """A plain number slot (repeat times) also opens the number pad -- no more dozens
    of +1 taps to reach a value like 440."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    blk = be.insert_block("beep", {"freq": 440})
    slot = [s for s in be.slots(blk) if s["name"] == "freq"][0]
    ws.block_ui._blk_edit_slot(blk, slot)
    assert ws.block_ui.blk_kbd is not None and ws.block_ui.blk_kbd["kind"] == "num"
    assert not ws.block_ui.blk_kbd.get("allow_block")     # a number slot can't hold a block
    for ch in "880":
        ws.block_ui._blk_kbd_key(ord(ch))
    ws.block_ui._blk_kbd_commit()
    assert blk["p"]["freq"] == 880


def test_expr_menu_leads_with_type_a_number_and_keeps_reporters(tmp_path):
    """The expr chooser still exists (for dropping a reporter), now headed by
    'type a number' so a literal is the first, obvious choice -- and the operator /
    input / variable reporters are all still there."""
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    blk = be.insert_block("spr", {"id": 0, "x": 0, "y": 0})
    ws.block_ui._blk_open_expr_menu(blk, "x")
    items = ws.block_ui.blk_menu["items"]
    assert items[0] == C._NUM_LITERAL_ITEM
    assert ws.block_ui._blk_menu_label(0) == C._NUM_LITERAL_LABEL
    # the reporters survived
    for rid in ("op_add", "op_gt", "var", "btn", "touched"):
        assert rid in items
    # picking "type a number" opens the number pad on that slot
    ws.block_ui.blk_menu["sel"] = 0
    ws.block_ui._blk_menu_select()
    assert ws.block_ui.blk_menu is None
    assert ws.block_ui.blk_kbd is not None and ws.block_ui.blk_kbd["kind"] == "num"


def test_expr_slot_can_still_hold_a_reporter_block(tmp_path):
    """Dropping a reporter into an expr slot still works (the white oval accepts a
    block too): a var reporter in spr's x compiles to the bare variable name."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    be.new_var("px")
    _go_to_insert(be, 1)
    blk = be.insert_block("spr", {"id": 0, "x": 0, "y": 0})
    ws.block_ui._blk_open_expr_menu(blk, "x")
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index("var")
    ws.block_ui._blk_menu_select()                         # drop a `var` reporter into x
    # the reporter's own {var} slot then points at px
    be.set_slot("var", "px", blk["p"]["x"])
    src = blocks.compile_blocks(be.program)
    assert "spr(0, px, 0)" in src
    # editing that slot again RE-OPENS the block chooser (it holds a block, not a literal)
    slot = [s for s in be.slots(blk) if s["name"] == "x"][0]
    ws.block_ui._blk_edit_slot(blk, slot)
    assert ws.block_ui.blk_menu is not None and ws.block_ui.blk_menu["mode"] == "expr"
    assert ws.block_ui.blk_kbd is None


def test_number_prompt_typing_through_the_driver(tmp_path):
    """End-to-end through the real input model: open a number slot, type via last_key,
    confirm with A -- the slot holds the typed value and compiles bare."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    drv = _driver(ws)
    _go_to_insert(be, 1)
    blk = be.insert_block("beep", {"freq": 0})
    slot = [s for s in be.slots(blk) if s["name"] == "freq"][0]
    ws.block_ui._blk_edit_slot(blk, slot)                  # opens the number pad (armed next frame)
    assert ws.block_ui.blk_kbd is not None
    drv.frame(1 / 30)                             # one frame to ARM the prompt
    for ch in "440":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
        drv.frame(1 / 30)                         # release so each edge fires
    drv.press("a")                               # confirm
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_kbd is None
    assert blk["p"]["freq"] == 440


def test_number_prompt_survives_the_opening_keypress(tmp_path):
    """Regression parity with the var prompt (#29): the A/Enter that OPENS the number
    pad must not carry in and instantly commit it on the first frame."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    drv = _driver(ws)
    _go_to_insert(be, 1)
    blk = be.insert_block("beep", {"freq": 7})
    # select the freq slot and press A to open the pad, A still held the next frame
    ws.block_ui.blk_slot = 0
    drv.hold("a", True)
    drv.type_char(0x0D)
    drv.frame(1 / 30)                            # A opens the slot editor (number pad)
    assert ws.block_ui.blk_kbd is not None
    drv.type_char(0x0D)
    drv.frame(1 / 30)
    assert ws.block_ui.blk_kbd is not None, "opening keypress must not auto-commit the number pad"
    drv.hold("a", False)
    drv.type_char(0)
    drv.frame(1 / 30)


def test_left_nudges_an_expr_literal_slot(tmp_path):
    """The quick -1 nudge (left) works on an expr slot holding a numeric literal,
    not just plain number slots -- but leaves a slot holding a block alone."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    be.new_var("x")
    _go_to_insert(be, 1)
    blk = be.insert_block("set_var", {"var": "x", "value": 5})  # value is an expr-literal
    # cursor on the block, slot index pointing at "value"
    assert _select_type(be, "set_var")
    ws.block_ui.blk_slot = [s["name"] for s in be.slots(blk)].index("value")
    ws.block_ui._blk_left()
    assert blk["p"]["value"] == 4
    # now put a block in the slot -> left must NOT touch it
    be.set_slot("value", mk("op_add", {"a": 1, "b": 2}), blk)
    ws.block_ui._blk_left()
    assert isinstance(blk["p"]["value"], dict)


# ----------------------------------------------------------------------------
# #48: Lists -- the list-slot picker + "+ new list" mirror the variable flow.
# ----------------------------------------------------------------------------

def test_lists_category_block_list_leads_with_new_list(tmp_path):
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    ws.block_ui._blk_open_categories()
    ws.block_ui.blk_menu["sel"] = ws.block_ui.blk_menu["items"].index(blocks.CAT_LISTS)
    ws.block_ui._blk_menu_select()
    assert ws.block_ui.blk_menu["mode"] == "blk"
    assert ws.block_ui.blk_menu["items"][0] == C._NEW_LIST_ITEM
    assert ws.block_ui._blk_menu_label(0) == C._NEW_LIST_LABEL


def test_list_slot_picker_leads_with_new_list_and_fills_the_slot(tmp_path):
    """Opening a {list} slot with no lists yet shows a picker that LEADS with
    '+ new list': choosing it creates + names a list and fills the slot, so the
    program compiles (the kid never hits an empty/uncreatable list slot)."""
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    blk = be.insert_block("list_add")               # no lists declared yet
    assert _select_type(be, "list_add")
    # step the slot highlight to the {list} slot, then A opens the list picker
    names = [s["name"] for s in be.slots(blk)]
    ws.block_ui.blk_slot = names.index("list")
    ws.block_ui._blk_a()
    assert ws.block_ui.blk_menu is not None and ws.block_ui.blk_menu["mode"] == "list"
    assert ws.block_ui.blk_menu["items"][0] == C._NEW_LIST_ITEM
    ws.block_ui.blk_menu["sel"] = 0
    ws.block_ui._blk_menu_select()                           # create + open the name prompt
    assert ws.block_ui.blk_menu is None and ws.block_ui.blk_kbd is not None
    assert ws.block_ui.blk_kbd["kind"] == "list"
    ws.block_ui._blk_kbd_commit()                            # accept the default name
    assert ws.block_ui.blk_kbd is None
    assert be.lists()                               # a list now exists
    assert be.selected_block()["p"]["list"] in be.lists()
    blocks.compile_blocks(be.program)               # and it compiles


def test_for_each_block_inserts_and_saves(tmp_path):
    """A for-each over a list builds, saves, and the compiled cart runs."""
    ws, cart, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    be.add_var("it")
    be.new_list("nums")
    _go_to_insert(be, 1)                            # on_start
    be.insert_block("list_add", {"item": 1, "list": "nums"})
    _go_to_insert(be, 1)
    be.insert_block("for_each", {"var": "it", "list": "nums"})
    _go_to_insert(be, 2)
    be.insert_block("spr", {"id": 0, "x": mk("var", {"var": "it"}), "y": 0})
    assert ws.block_ui.save_blocks() is True
    reloaded = moy_carts.load(cart["path"])
    assert "for it in nums:" in reloaded["src"]
    assert reloaded["blocks"]["lists"] == ["nums"]
    _run(reloaded["src"], frames=2)


# ----------------------------------------------------------------------------
# #93: copy / paste / duplicate of blocks + subtrees (BlockEditor core)
# ----------------------------------------------------------------------------

def _snap(be):
    """A structural deep copy of the program for equality compares (json round-trip
    -- the schema is json-serializable by contract)."""
    return blocks.loads(blocks.dumps(be.program))


def _inserts_at(be, depth):
    return [i for i, r in enumerate(be.rows) if r.kind == "insert" and r.depth == depth]


def test_copy_paste_subtree_keeps_structure_and_deep_copies():
    """Copying a c-block copies its WHOLE subtree (if/else arms + loop bodies), and
    a paste is an independent deep copy -- mutating one pasted block never touches
    the clipboard or another paste."""
    be = _be()
    _go_to_insert(be, 1)                                  # under on_draw
    be.insert_block("if_else", {"cond": mk("btnp", {"dir": "a"})})
    assert be.insert_else() is True
    ins2 = _inserts_at(be, 2)
    be.cur = ins2[0]                                      # if-body
    be.insert_block("cls", {"color": "green"})
    ins2 = _inserts_at(be, 2)
    be.cur = ins2[-1]                                     # else-body (after divider)
    be.insert_block("cls", {"color": "red"})
    # copy the whole if_else (subtree: both arms + the else divider)
    assert _select_type(be, "if_else")
    assert be.copy_block() is True
    subtree = be.clipboard
    # the clipboard carries both arms
    kinds = [c.get("t") for c in subtree.get("c", [])]
    assert "cls" in kinds and blocks.ELSE_MARKER in kinds
    # paste it under on_draw (a second, independent copy)
    be.cur = _inserts_at(be, 1)[-1]
    pasted = be.paste()
    assert pasted is not None
    # deep-copy: distinct objects, distinct nested param/children containers
    assert pasted is not subtree
    assert pasted["c"] is not subtree["c"]
    assert pasted["c"][0] is not subtree["c"][0]
    # mutating the paste leaves the clipboard untouched
    pasted["p"]["cond"] = mk("btnp", {"dir": "b"})
    assert subtree["p"]["cond"]["p"]["dir"] == "a"
    # and the whole program still compiles + runs
    _run(blocks.compile_blocks(be.program))


def test_copy_and_duplicate_refuse_hats_and_else_divider():
    be = _be()
    # a hat can't be copied (it can't live inside a body)
    assert _select_type(be, "on_start")
    assert be.copy_block() is False
    assert be.duplicate() is None
    assert be.clipboard is None
    # the else divider is protected too
    _go_to_insert(be, 1)
    be.insert_block("if_else", {"cond": mk("btnp", {"dir": "a"})})
    be.insert_else()
    else_row = [i for i, r in enumerate(be.rows) if r.is_else][0]
    be.cur = else_row
    assert be.copy_block() is False
    assert be.duplicate() is None


def test_duplicate_in_place_inserts_a_sibling_copy():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("spr", {"id": 3, "x": 1, "y": 2})
    assert _select_type(be, "spr")
    dup = be.duplicate()
    assert dup is not None
    body = be.program["scripts"][2]["c"]                 # on_draw
    assert [c["t"] for c in body] == ["spr", "spr"]
    # a real deep copy: same values, no shared param dict
    assert body[0]["p"] == body[1]["p"]
    assert body[0]["p"] is not body[1]["p"]
    body[1]["p"]["id"] = 9
    assert body[0]["p"]["id"] == 3                        # original untouched
    _run(blocks.compile_blocks(be.program))


def test_paste_only_at_insert_point_and_needs_clipboard():
    be = _be()
    # nothing copied yet -> paste is a no-op even at an insert point
    _go_to_insert(be, 1)
    assert be.paste() is None
    # copy a block, then try to paste while the cursor is ON A BLOCK (not an insert)
    be.insert_block("cls", {"color": "black"})
    assert _select_type(be, "cls")
    assert be.copy_block() is True
    assert be.paste() is None                             # cursor is on a block
    # at an insert it works
    be.cur = _inserts_at(be, 1)[-1]
    assert be.paste() is not None


def test_compile_after_paste_is_correct():
    be = _be()
    be.add_var("x")
    _go_to_insert(be, 1)                                  # under on_draw
    be.insert_block("if", {"cond": mk("op_gt", {"a": mk("var", {"var": "x"}), "b": 5})})
    be.cur = _inserts_at(be, 2)[0]
    be.insert_block("spr", {"id": 0, "x": 1, "y": 2})
    assert _select_type(be, "if")
    assert be.copy_block() is True
    be.cur = _inserts_at(be, 1)[-1]
    be.paste()
    src = blocks.compile_blocks(be.program)
    assert src.count("if (x > 5):") == 2                 # the whole guarded block, twice
    assert src.count("spr(0, 1, 2)") == 2
    _run(src)


# ----------------------------------------------------------------------------
# #93: move a block across the if/else divider or to a different parent
# ----------------------------------------------------------------------------

def test_move_across_the_if_else_divider():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("if_else", {"cond": mk("btnp", {"dir": "a"})})
    be.insert_else()
    be.cur = _inserts_at(be, 2)[0]                        # if-body
    be.insert_block("cls", {"color": "green"})
    assert _select_type(be, "cls")
    assert be.start_move() is True and be.moving() is True
    be.cur = _inserts_at(be, 2)[-1]                       # else-body destination
    assert be.complete_move() is True
    assert be.moving() is False
    src = blocks.compile_blocks(be.program)
    # cls now lives in the ELSE branch (after `else:`); the if-body is empty (`pass`)
    assert src.index("cls(col") > src.index("else:")
    _run(src)


def test_move_to_a_different_parent():
    be = _be()
    _go_to_insert(be, 1)                                  # under on_draw (last depth-1)
    be.insert_block("cls", {"color": "black"})
    assert _select_type(be, "cls")
    assert be.start_move() is True
    be.cur = _inserts_at(be, 1)[0]                        # under on_start
    assert be.complete_move() is True
    assert be.program["scripts"][0]["c"][0]["t"] == "cls"   # moved into _init
    assert be.program["scripts"][2].get("c", []) == []      # gone from _draw
    _run(blocks.compile_blocks(be.program))


def test_move_rejects_dropping_a_block_inside_itself():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("if", {"cond": mk("btn", {"dir": "left"})})
    assert _select_type(be, "if")
    assert be.start_move() is True
    be.cur = _inserts_at(be, 2)[0]                        # the if's OWN body
    assert be.complete_move() is False                   # can't orphan itself
    assert be.moving() is True                            # still armed
    be.cancel_move()
    assert be.moving() is False


# ----------------------------------------------------------------------------
# #93: in-session undo / redo over each mutation type
# ----------------------------------------------------------------------------

def test_undo_redo_over_insert():
    be = _be()
    s0 = _snap(be)
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "red"})
    assert be.program != s0
    assert be.undo() is True and be.program == s0
    assert be.redo() is True and be.program != s0


def test_undo_redo_over_delete():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "red"})
    s1 = _snap(be)
    assert _select_type(be, "cls")
    assert be.delete() is True and be.program != s1
    assert be.undo() is True and be.program == s1        # the block is back
    assert _select_type(be, "cls")


def test_undo_redo_over_reorder():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    _go_to_insert(be, 1)
    be.insert_block("circ", {"x": 1, "y": 2, "r": 3, "color": "red"})
    s = _snap(be)
    assert _select_type(be, "circ")
    assert be.move_block(-1) is True and be.program != s
    assert be.undo() is True and be.program == s


def test_undo_redo_over_slot_edit():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    assert _select_type(be, "cls")
    s = _snap(be)
    be.cycle_dropdown("color", 1)                        # a slot edit
    assert be.program != s
    assert be.undo() is True and be.program == s
    # a set_slot is undoable too
    be.set_slot("color", "green")
    assert be.program != s
    assert be.undo() is True and be.program == s


def test_undo_redo_over_paste():
    be = _be()
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    assert _select_type(be, "cls")
    be.copy_block()
    s = _snap(be)
    be.cur = _inserts_at(be, 1)[-1]
    be.paste()
    assert be.program != s
    assert be.undo() is True and be.program == s
    assert be.redo() is True and be.program != s


def test_undo_stack_is_bounded():
    from runtime.editors import _BLK_UNDO_MAX
    be = _be()
    be.add_var("x")
    for _ in range(_BLK_UNDO_MAX + 20):
        _go_to_insert(be, 1)
        be.insert_block("change_var", {"var": "x", "value": 1})
    assert len(be._undo) <= _BLK_UNDO_MAX


# ----------------------------------------------------------------------------
# #93: the UI wiring -- actions menu, move flow, undo/redo buttons + shortcut
# ----------------------------------------------------------------------------

def test_actions_menu_copy_then_paste_ui(tmp_path):
    from runtime import block_editor_ui as B
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    bu = ws.block_ui
    be = bu.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "green"})
    assert _select_type(be, "cls")
    bu._blk_open_actions()
    assert bu.blk_menu is not None and bu.blk_menu["mode"] == "actions"
    bu.blk_menu["sel"] = bu.blk_menu["items"].index(B._ACT_COPY)
    bu._blk_menu_select()
    assert bu.blk_menu is None and be.has_clipboard()
    # move to an insert point and paste through the menu
    be.cur = _inserts_at(be, 1)[-1]
    bu._blk_open_actions()
    assert B._ACT_PASTE in bu.blk_menu["items"]
    bu.blk_menu["sel"] = bu.blk_menu["items"].index(B._ACT_PASTE)
    bu._blk_menu_select()
    body = [c["t"] for c in be.program["scripts"][2].get("c", [])]
    assert body.count("cls") == 2
    ws.frame(1 / 30)                                      # renders without error
    assert ws.cart_error is None


def test_move_flow_via_ui_taps_a_destination(tmp_path):
    from runtime import block_editor_ui as B
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    bu = ws.block_ui
    be = bu.blocks_ed
    _go_to_insert(be, 1)                                  # under on_draw
    be.insert_block("cls", {"color": "black"})
    assert _select_type(be, "cls")
    bu._blk_open_actions()
    bu.blk_menu["sel"] = bu.blk_menu["items"].index(B._ACT_MOVE)
    bu._blk_menu_select()
    assert be.moving() is True
    # in move mode, the A action on an insert point completes the move
    be.cur = _inserts_at(be, 1)[0]                        # under on_start
    bu._blk_a()
    assert be.moving() is False
    assert be.program["scripts"][0]["c"][0]["t"] == "cls"
    assert bu.blk_status == "MOVED"


def test_undo_redo_buttons_ui(tmp_path):
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    bu = ws.block_ui
    be = bu.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "red"})
    assert _select_type(be, "cls")
    bu._blk_undo()
    assert not _select_type(be, "cls")                   # the insert was reverted
    bu._blk_redo()
    assert _select_type(be, "cls")                       # and re-applied
    ws.frame(1 / 30)
    assert ws.cart_error is None


def test_ctrl_z_keyboard_shortcut_undoes(tmp_path):
    """Host convenience: Ctrl+Z (0x1A via last_key) undoes in the outline, like the
    code editor. Driven through the real Workstation input path."""
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    drv = _driver(ws)
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "red"})
    assert _select_type(be, "cls")
    drv.type_char(0x1A)                                   # Ctrl+Z
    drv.frame(1 / 30)
    assert not _select_type(be, "cls")                   # undone
    assert ws.menu_view == "blocks"                      # still in the editor


def _snap_of(prog):
    """A structural deep copy via the schema's own json round-trip contract."""
    return blocks.loads(blocks.dumps(prog))


def _body_types(prog):
    """Every statement type id across all scripts' bodies (top level only)."""
    return [c.get("t") for s in prog.get("scripts", []) for c in s.get("c", [])]


def test_undo_after_save_does_not_leak_into_cart_blocks(tmp_path):
    """#93 regression: save_blocks must SNAPSHOT (deep-copy) the program into
    cart["blocks"], never alias the editor's live tree -- otherwise a post-save
    in-place edit mutates the cart's copy, and an undo (which rebinds the editor's
    program to a restored snapshot) leaves cart["blocks"] holding the un-undone
    state (matching neither disk nor the editor; the graduation compare reads it)."""
    ws, cart, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    assert ws.block_ui.save_blocks() is True
    saved = _snap_of(ws.project.cart["blocks"])
    # a post-save in-place edit must NOT reach through into the cart's snapshot...
    _go_to_insert(be, 1)
    be.insert_block("spr", {"id": 0, "x": 1, "y": 2})
    assert ws.project.cart["blocks"] == saved
    assert "spr" not in _body_types(ws.project.cart["blocks"])
    # ...and after an undo, cart["blocks"] still can't hold the undone block
    assert be.undo() is True
    assert "spr" not in _body_types(ws.project.cart["blocks"])
    assert ws.project.cart["blocks"] == saved


def test_in_ram_save_and_build_fallback_do_not_alias_cart_blocks(tmp_path):
    """The other two #93 alias windows: the in-RAM save (no path -> apply in RAM)
    snapshots too, and build()'s fallback (prog from cart["blocks"]) clones the
    cart's tree before handing it to the editor."""
    ws, cart, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.block_ui.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    ws.project.cart["path"] = None                       # force the in-RAM save path
    assert ws.block_ui.save_blocks() is True
    saved = _snap_of(ws.project.cart["blocks"])
    _go_to_insert(be, 1)
    be.insert_block("spr", {"id": 1, "x": 3, "y": 4})
    assert ws.project.cart["blocks"] == saved            # no reach-through
    assert be.undo() is True
    assert ws.project.cart["blocks"] == saved
    # a FRESH editor built from cart["blocks"] (the no-store fallback) gets a clone:
    # its edits never touch the cart's tree until the next save
    ws.block_ui.reset()
    ws.block_ui.build()
    be2 = ws.block_ui.blocks_ed
    assert be2 is not None and be2.program == saved
    _go_to_insert(be2, 1)
    be2.insert_block("beep", {"freq": 220})
    assert ws.project.cart["blocks"] == saved
    assert "beep" not in _body_types(ws.project.cart["blocks"])
