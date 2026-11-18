"""The console's ONE on-screen text-entry body, and the prompts that type
through it.

`editors_base.text_key` is the key ladder every typed field on the console
shares (Backspace trims, Delete finds nothing after the caret at the end,
Enter commits, Esc cancels, printable ASCII appends under a cap);
`TextEntry` is a buffer over it; a typed byte is the input table's press
(`typed`), so one physical press types one character.

The second thing pinned here is `widgets.arm_prompt`: the tap/key that OPENS
a modal prompt (a held A / Enter byte, the tap on the button) must never be
read as its first keystroke, commit or cancel (#29); its `clear_edges` spends
the press. The tests below drive all three callers through the same
latched-byte frame.
"""

from runtime import host_app, moy_carts
from runtime.editors_base import TextEntry, text_key, TE_EDIT, TE_COMMIT, TE_CANCEL
from ws_helpers import shelf, select_on_home  # noqa: E402


# -- the ladder ---------------------------------------------------------------

def test_text_key_is_the_one_ladder():
    assert text_key("ab", 8, 24) == ("a", TE_EDIT)
    assert text_key("ab", 127, 24) == ("ab", None)          # Delete: nothing after the end
    assert text_key("", 8, 24) == ("", TE_EDIT)             # a trim on empty is quiet
    assert text_key("ab", 13, 24) == ("ab", TE_COMMIT)
    assert text_key("ab", 10, 24) == ("ab", TE_COMMIT)
    assert text_key("ab", 27, 24) == ("ab", TE_CANCEL)
    assert text_key("ab", ord("c"), 24) == ("abc", TE_EDIT)
    assert text_key("ab", 9, 24) == ("ab", None)            # Tab is the caller's
    assert text_key("ab", 200, 24) == ("ab", None)          # not printable ASCII
    assert text_key("ab", 32, 24) == ("ab ", TE_EDIT)
    assert text_key("ab", 126, 24) == ("ab~", TE_EDIT)


def test_text_key_caps_and_filters():
    assert text_key("abc", ord("d"), 3) == ("abc", None)    # full: refused
    assert text_key("abc", 8, 3) == ("ab", TE_EDIT)         # ...but still trims
    digits = lambda c, text: c.isdigit()                    # noqa: E731
    assert text_key("1", ord("x"), 8, digits) == ("1", None)
    assert text_key("1", ord("2"), 8, digits) == ("12", TE_EDIT)


def test_text_entry_open_cuts_to_the_cap_and_keys_through_the_ladder():
    e = TextEntry(4)
    e.open("abcdefgh")
    assert e.text == "abcd"                     # a seed longer than typing could make
    e.open("")
    assert e.key(ord("z")) == TE_EDIT
    assert e.key(ord("z")) == TE_EDIT
    assert e.key(13) == TE_COMMIT
    assert e.text == "zz"


# -- arm_prompt: the three callers, one latched frame -------------------------

def _ws_with_lua_cart(tmp_path, python=False):
    """A console with one cart open in the Editor: a Lua one, or where
    `python` a Python one (the only kind with a Blocks tab)."""
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    src, rt, main = (("def _draw():\n    cls(1)\n", "python", "main.py") if python
                     else ("function _draw() cls(1) end\n", "lua", "main.lua"))
    moy_carts.create("Lua One", root, src=src, runtime=rt, main=main,
                     edit=[{"key": "n", "type": "int", "min": 0, "max": 9,
                            "card": "N"}])
    ws = host_app.build_workstation(root)
    ws.launcher.set_items(shelf(root))
    select_on_home(ws, "Lua One")
    ws.open_in_editor()
    assert ws.menu_view == "cards"
    ws.input.begin_frame()
    ws.frame(1 / 30)
    return ws


def _latched_frame(ws, byte=0x0D):
    """One input frame with the A button and the Enter byte both still held,
    the way the device keyboard reports them the frame after a press."""
    ws.input.set_held("a", True)
    ws.input.begin_frame()
    ws.input.last_key = byte
    ws.router.handle_input()
    ws.input.last_key = 0
    ws.input.set_held("a", False)
    ws.input.begin_frame()


def _typed_frame(ws, byte):
    """One typed byte, and the frames that draw it: a dialog's field takes
    its keys as it draws (ui.field)."""
    ws.input.begin_frame()
    ws.input.last_key = byte
    ws.router.handle_input()
    ws.frame(1 / 30)
    ws.input.last_key = 0
    ws.input.begin_frame()
    ws.router.handle_input()
    ws.frame(1 / 30)


def test_cart_info_survives_the_keypress_that_opened_it(tmp_path):
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    ws.input.set_held("a", True)
    ws.input.begin_frame()
    ws.input.last_key = 0x0D
    cl._open_meta()                               # opened while Enter/A are down
    ws.input_policy.sync()                        # the frame's look at the mode
    assert cl.prompt is not None and ws.input.text_mode
    assert not ws.input.held("a")                 # arm_prompt: everybody let go
    _latched_frame(ws)
    ws.frame(1 / 30)
    assert cl.prompt is not None, "the opening Enter must not commit the dialog"
    assert cl.prompt.texts[0] == "Lua One"
    _typed_frame(ws, ord("!"))
    assert cl.prompt.texts[0] == "Lua One!"


def test_new_script_survives_the_keypress_that_opened_it(tmp_path):
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    ws.input.begin_frame()
    ws.input.last_key = 0x0D
    cl._open_newf()
    assert cl.prompt is not None and cl.prompt.kind == "newf"
    _latched_frame(ws)
    ws.frame(1 / 30)
    assert cl.prompt is not None, "the opening Enter must not commit the dialog"
    assert cl.prompt.texts[0] == ""
    _typed_frame(ws, ord("h"))
    assert cl.prompt.texts[0] == "h"


def test_block_prompt_survives_the_keypress_that_opened_it(tmp_path):
    ws = _ws_with_lua_cart(tmp_path, python=True)
    ws.text.set_menu_view("blocks")
    bu = ws.block_ui
    ws.input.begin_frame()
    ws.input.last_key = 0x0D
    bu._blk_open_kbd("text", "", block={"t": "x", "p": {}}, slot="s")
    ws.input_policy.sync()
    assert bu.blk_kbd is not None and ws.input.text_mode
    _latched_frame(ws)
    ws.frame(1 / 30)
    assert bu.blk_kbd is not None, "the opening Enter must not commit the prompt"
    assert bu.blk_kbd["entry"].text == ""
    _typed_frame(ws, ord("h"))
    assert bu.blk_kbd["entry"].text == "h"
    bu._blk_kbd_cancel()
    ws.input_policy.sync()
    assert bu.blk_kbd is None and not ws.input.text_mode


def test_block_prompt_kinds_type_what_they_allow():
    ws = host_app.build_workstation(None)
    bu = ws.block_ui
    bu._blk_open_kbd("num", "", cur=None, block={}, slot="s", allow_block=False)
    for ch in "-1.5.x2":
        bu._blk_kbd_key(ord(ch))
    assert bu.blk_kbd["entry"].text == "-1.52"
    bu._blk_kbd_cancel()
    bu._blk_open_kbd("var", "", var="v", slot_target=None)
    for ch in "my var-1!":
        bu._blk_kbd_key(ord(ch))
    assert bu.blk_kbd["entry"].text == "my var-1"
    bu._blk_kbd_cancel()
    bu._blk_open_kbd("text", "", block={}, slot="s")
    for ch in "hi! 12345678901234567":
        bu._blk_kbd_key(ord(ch))
    assert bu.blk_kbd["entry"].text == "hi! 123456789012"   # the 16-char row cap
    bu._blk_kbd_cancel()


# -- the fields a held byte opens: wifi ----------------------------------------

def test_wifi_password_prompt_does_not_connect_on_the_enter_that_opened_it(tmp_path):
    """The prompt is the engine's field (tests/test_settings_wifi_prompt.py):
    the Enter still held from picking the row types nothing into it."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_page("wifi")
    sl.wifi_nets = [("Locked", 80, True)]
    sl.wifi_known = []
    sl.wifi_sel = 0
    drv.frame(1 / 30)
    ws.input.last_key = 0x0D                      # the Enter that picked the row
    sl._wifi_activate()
    ws.input_policy.sync()
    assert sl.wifi_pick == "Locked" and ws.input.text_mode
    sl.wifi_input(ws.input, sl._sub)                      # the byte is still held
    sl._draw_page()
    assert sl.wifi_pick == "Locked", "a latched Enter must not connect with ''"
    ws.input.last_key = 0

    def key(code):
        ws.input.last_key = code
        sl.wifi_input(ws.input, sl._sub)
        sl._draw_page()
        ws.input.last_key = 0
        sl.wifi_input(ws.input, sl._sub)
        sl._draw_page()
    for ch in "pw":
        key(ord(ch))
    assert sl.wifi_pw == "pw"
    key(127)                                      # Delete: the caret is the end
    assert sl.wifi_pw == "pw"
    key(8)
    assert sl.wifi_pw == "p"
    key(27)
    ws.input_policy.sync()
    assert sl.wifi_pick is None and not ws.input.text_mode


# -- the NEW SCRIPT dialog (#89) ----------------------------------------------

def test_new_script_slugs_the_typed_name_under_the_carts_extension(tmp_path):
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    assert cl._newf_filename("Helpers") == "helpers.lua"
    assert cl._newf_filename("  My Helpers.LUA ") == "my_helpers.lua"
    assert cl._newf_filename("a--b__c") == "a_b__c.lua"   # runs of punctuation fold, typed underscores stay
    assert cl._newf_filename("1abc") is None      # must start with a letter
    assert cl._newf_filename("!!!") is None


def test_new_script_commit_creates_the_file_and_opens_it_in_code(tmp_path):
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    cl._open_newf()
    for ch in "helpers":
        cl._prompt_key(ord(ch))
    cl._prompt_key(13)
    assert cl.prompt is None
    cart = ws.run.project.cart
    assert "helpers.lua" in moy_carts.cart_sources(cart)
    assert ws.menu_view == "code"
    assert ws.code_file == "helpers.lua"


def test_new_script_refuses_a_bad_or_taken_name_and_stays_open(tmp_path):
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    cl._open_newf()
    cl._prompt_key(ord("1"))
    cl._prompt_key(13)
    assert cl.prompt is not None and cl.prompt.msg == "Start its name with a letter."
    cl._prompt_key(8)                              # an edit clears the message
    assert cl.prompt.msg is None
    for ch in "main":
        cl._prompt_key(ord(ch))
    cl._prompt_key(13)
    assert cl.prompt is not None and cl.prompt.msg == "There's a file with that name."
    cl._prompt_key(27)
    assert cl.prompt is None and not ws.input.text_mode
    assert moy_carts.cart_sources(ws.run.project.cart) == ["main.lua"]


def test_new_script_is_closed_by_a_cart_switch(tmp_path):
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    cl._open_newf()
    ws.input.begin_frame()
    ws.frame(1 / 30)                               # draws the dialog
    cl.reset()
    ws.input_policy.sync()
    assert cl.prompt is None and not ws.input.text_mode


def test_the_dialogs_are_captioned_fields_and_the_tdeck_has_no_button_row(tmp_path):
    """Cart info and New script are `ui.dialog`s of captioned `ui.field`s
    (GAPS 52): each field under its caption inside the card, the first
    focused; on the T-Deck no Cancel or OK (its keys are the hint strip's),
    where a keyboard or a touch glass adds them."""
    from runtime import ui_draw
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    for kind in ("tdeck", "keys"):
        ui_draw.input_kind(kind)
        try:
            for opener, labels in ((cl._open_meta, ("Title", "Made by")),
                                   (cl._open_newf, ("Name",))):
                opener()
                ws.frame(1 / 30)
                ws.frame(1 / 30)
                host = cl.prompt.host
                boxes = [host.box(lb) for lb in labels]
                assert all(b is not None for b in boxes)
                for a, b in zip(boxes, boxes[1:]):
                    assert a[1] + a[3] <= b[1]
                buttons = host.box("Cancel")
                assert (buttons is None) == (kind == "tdeck")
                cl._close_prompt()
        finally:
            ui_draw.input_kind("keys")


def test_a_cards_dialogs_field_takes_nothing_on_delete():
    """A dialog's field (Cart info, New script) types at its end, so Delete
    has nothing after the caret; Backspace trims."""
    from runtime.cards_layer import _Prompt
    p = _Prompt("newf", "New script", (("Name", "name", 16),), None, "Create")
    for ch in "ab":
        p.key(ord(ch))
    assert not p.types(127)
    p.key(127)
    assert p.texts == ["ab"]
    assert p.types(8)
    p.key(8)
    assert p.texts == ["a"]
