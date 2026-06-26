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

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import blocks  # noqa: E402
from runtime import kid_carts  # noqa: E402
from runtime.editors import BlockEditor  # noqa: E402

mk = blocks.make_block


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

class _FakeAPI(dict):
    """A cart namespace of recording stubs, so a compiled block program can exec +
    run _init/_update/_draw headlessly (mirrors tests/test_blocks.py)."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self["W"] = 320
        self["H"] = 240
        for name in ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
                     "spr", "print", "sfx", "beep", "music"):
            self[name] = self._rec(name)
        from runtime import palette
        self["col"] = palette.color
        self._btn = {}
        self._btnp = {}
        self._touch = None
        self["btn"] = lambda d=None: self._btn.get(d, False)
        self["btnp"] = lambda d=None: self._btnp.get(d, False)
        self["touch"] = lambda: self._touch
        self["rnd"] = lambda n=1.0: 0.0
        self["flr"] = lambda x: int(x // 1)

    def _rec(self, name):
        def fn(*a, **k):
            self.calls.append((name, a, k))
        return fn


def _run(src, frames=1, fake=None):
    code = compile(src, "<cart>", "exec")
    fake = fake or _FakeAPI()
    exec(code, fake)
    if fake.get("_init"):
        fake["_init"]()
    for _ in range(frames):
        if fake.get("_update"):
            fake["_update"](1 / 30)
        if fake.get("_draw"):
            fake["_draw"]()
    return fake


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
    cart = kid_carts.create(title, root, type="game")
    ws.launcher.items = kid_carts.scan(root)
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
    assert ws.menu_view == "blocks" and ws.blocks_ed is not None
    # renders without error
    for _ in range(2):
        ws.frame(1 / 30)
    assert ws.cart_error is None


def test_desktop_blocks_button_opens_editor(tmp_path):
    import runtime.console as C
    ws, _, _ = _ws_with_block_cart(tmp_path)
    drv = _driver(ws)
    drv.frame(1 / 30)                                  # on the desktop (cart running)
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
    be = ws.blocks_ed
    # park on the trailing insert under on_draw and press A. An idle frame between
    # presses mirrors a real release (the input edge needs the key to lift first).
    _go_to_insert(be, 1)
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)                                  # release
    assert ws.blk_menu is not None and ws.blk_menu["mode"] == "cat"
    # navigate to DRAW and select
    ws.blk_menu["sel"] = ws.blk_menu["items"].index(blocks.CAT_DRAW)
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.blk_menu["mode"] == "blk"
    ws.blk_menu["sel"] = ws.blk_menu["items"].index("cls")
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.blk_menu is None
    body = [c["t"] for c in be.program["scripts"][2].get("c", [])]
    assert "cls" in body
    # save + leave -> the compiled cart runs on the desktop
    ws.save_blocks()
    drv.escape()
    for _ in range(5):
        drv.frame(1 / 30)
    assert ws.cart_error is None


def test_save_persists_blocks_and_main_and_reloads(tmp_path):
    ws, cart, root = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.blocks_ed
    be.add_var("score")
    _go_to_insert(be, 1)                                # under on_start
    be.insert_block("set_var", {"var": "score", "value": 0})
    assert ws.save_blocks() is True and ws.blk_status == "SAVED"
    # both files landed; reload restores the program AND a runnable main.py
    reloaded = kid_carts.load(cart["path"])
    assert reloaded["blocks"] == be.program
    assert reloaded["src"].startswith("# Made with KidCode blocks")
    assert "score = 0" in reloaded["src"]
    # a fresh editor over the reloaded cart restores the same tree
    again = BlockEditor(blocks, kid_carts.load_blocks(cart["path"]))
    assert again.program == be.program


def test_dropdown_slot_picker_sets_the_value(tmp_path):
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "black"})
    assert _select_type(be, "cls")
    ws.blk_slot = 0
    ws._blk_a()                                     # A opens the color picker
    assert ws.blk_menu is not None and ws.blk_menu["mode"] == "dropdown"
    ws.blk_menu["sel"] = ws.blk_menu["items"].index("green")
    ws._blk_menu_select()
    assert ws.blk_menu is None
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
    be = ws.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("set_var")                      # no vars declared yet
    assert _select_type(be, "set_var")
    ws.blk_slot = 0                                 # the {var} slot
    ws._blk_a()                                     # opens the variable picker
    assert ws.blk_menu is not None and ws.blk_menu["mode"] == "variable"
    # the first item is the create-and-name entry
    assert ws.blk_menu["items"][0] == C._NEW_VAR_ITEM
    ws.blk_menu["sel"] = 0
    ws._blk_menu_select()                           # create + open the name prompt
    assert ws.blk_menu is None and ws.blk_kbd is not None
    ws._blk_kbd_commit()                            # accept the default name
    assert ws.blk_kbd is None
    # the var is declared, the slot points at it, and the program compiles
    assert be.variables()
    assert be.selected_block()["p"]["var"] in be.variables()
    blocks.compile_blocks(be.program)


def test_graduate_to_code_opens_code_editor_on_generated_source(tmp_path):
    ws, cart, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("cls", {"color": "indigo"})
    ws.graduate_to_code()
    assert ws.menu_view == "code" and ws.editor is not None
    assert 'cls(col("indigo"))' in ws.editor.text()
    # the code editor renders the graduated source without error
    ws.frame(1 / 30)
    assert ws.cart_error is None


def test_block_authored_cart_runs_normally(tmp_path):
    """A cart authored from blocks RUNs like any other cart (its compiled main.py)."""
    ws, cart, root = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.blocks_ed
    be.add_var("x")
    _go_to_insert(be, 1)                                # on_start: set x = 100
    be.insert_block("set_var", {"var": "x", "value": 100})
    ws.save_blocks()
    # reopen the cart fresh from disk and run it
    ws.launcher.items = kid_carts.scan(root)
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
    assert ws.blk_menu is not None
    # tap a menu row (DRAW) then dismiss back out
    # (drill: pick category by tapping its row)
    my = C._BLK_MENU[1] + 16
    draw_i = ws.blk_menu["items"].index(blocks.CAT_DRAW)
    drv.click(C._BLK_MENU[0] + 20, my + draw_i * C._BLK_MENU_ROW_H + 2)
    drv.frame(1 / 30)
    assert ws.blk_menu["mode"] == "blk"


# ----------------------------------------------------------------------------
# Kid-facing copy: forever-is-bounded + wait hints surface in the editor
# ----------------------------------------------------------------------------

def test_forever_and_wait_have_kidfacing_hints(tmp_path):
    ws, _, _ = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.blocks_ed
    _go_to_insert(be, 1)
    be.insert_block("forever")
    assert _select_type(be, "forever")
    assert "every frame" in ws._blk_hint()             # forever is bounded
    _go_to_insert(be, 1)
    be.insert_block("wait", {"secs": 1})
    assert _select_type(be, "wait")
    assert "pause" in ws._blk_hint()
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
    cart = kid_carts.create(title, root, src=src, type="game")
    ws.launcher.items = kid_carts.scan(root)
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
    assert ws.blk_protect is True
    # try to SAVE the empty block program -- it must refuse and keep the code
    assert ws.save_blocks() is False
    # in-RAM source is untouched...
    assert ws.cart["src"] == _HANDWRITTEN_SRC
    # ...and so is the on-disk main.py (nothing was written, no blocks.json appeared)
    reloaded = kid_carts.load(cart["path"])
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
    assert ws.blk_protect is True
    ws.graduate_to_code()
    assert ws.menu_view == "code" and ws.editor is not None
    assert ws.editor.text() == _HANDWRITTEN_SRC          # the kid's code, not blocks
    assert "Made with KidCode blocks" not in ws.editor.text()


def test_block_authored_cart_is_not_protected(tmp_path):
    """A genuinely block-authored cart (has blocks.json) stays fully editable --
    the guard only fires on hand-written code. Round-trip is unchanged."""
    ws, cart, root = _ws_with_block_cart(tmp_path)
    ws._open_blocks()
    be = ws.blocks_ed
    assert ws.blk_protect is False                       # new-template cart: editable
    be.add_var("score")
    _go_to_insert(be, 1)
    be.insert_block("set_var", {"var": "score", "value": 7})
    assert ws.save_blocks() is True
    # reopen fresh from disk: it loads its blocks.json, so it's NOT protected and
    # saving works again (round-trip unchanged).
    ws.launcher.items = kid_carts.scan(root)
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == cart["title"]:
            ws.launcher.sel = i
    ws.open()
    ws._open_blocks()
    assert ws.blk_protect is False
    assert ws.save_blocks() is True


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
    be = ws.blocks_ed
    # open the insert menu -> Variables category
    _go_to_insert(be, 1)
    ws._blk_open_categories()
    ws.blk_menu["sel"] = ws.blk_menu["items"].index(blocks.CAT_VARIABLES)
    ws._blk_menu_select()
    assert ws.blk_menu["mode"] == "blk"
    # the FIRST entry in Variables is "+ new variable"
    assert ws.blk_menu["items"][0] == C._NEW_VAR_ITEM
    assert ws._blk_menu_label(0) == C._NEW_VAR_LABEL
    ws.blk_menu["sel"] = 0
    ws._blk_menu_select()                                # create + open name prompt
    assert ws.blk_kbd is not None
    # type a name: "lives" (each char is a keystroke; backspace + a digit too)
    for ch in "lives":
        ws._blk_kbd_key(ord(ch))
    ws._blk_kbd_key(ord("2"))                            # "lives2"
    ws._blk_kbd_key(8)                                   # backspace -> "lives"
    ws._blk_kbd_commit()
    assert ws.blk_kbd is None
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
    be = ws.blocks_ed
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
    be = ws.blocks_ed
    drv = _driver(ws)
    # drive to the Variables block list and pick "+ new variable"
    ws._blk_open_categories()
    ws.blk_menu["sel"] = ws.blk_menu["items"].index(blocks.CAT_VARIABLES)
    ws._blk_menu_select()
    ws.blk_menu["sel"] = 0                               # "+ new variable"
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)                                    # release
    assert ws.blk_kbd is not None
    # type "gold" one char per frame (last_key edge), then confirm with A
    for ch in "gold":
        drv.type_char(ord(ch))
        drv.frame(1 / 30)
        drv.frame(1 / 30)                                # release so the next edge fires
    drv.press("a")
    drv.frame(1 / 30)
    drv.frame(1 / 30)
    assert ws.blk_kbd is None
    assert "gold" in be.variables()
