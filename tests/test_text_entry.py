"""The console's ONE on-screen text-entry body, and the prompts that type
through it.

`editors_base.text_key` is the key ladder every typed field on the console
shares (Backspace/Delete trim, Enter commits, Esc cancels, printable ASCII
appends under a cap); `TextEntry` is a buffer over it that reads
`inp.last_key` through a `KeyEdge`, so one physical press types one
character. It used to be six hand-rolled copies with six edge trackers (the
block keypads, CART INFO, NEW SCRIPT, the wifi password, the scene TAG, the
Files rename), two of which did not trim on Delete (127) and none of which
agreed on where the edge was seeded.

The second thing pinned here is `widgets.arm_prompt` + the GUARDED edge: the
tap/key that OPENS a modal prompt (a held A / Enter byte, the tap on the
button) must never be read as its first keystroke, commit or cancel (#29). It
was one body in block_editor_ui and two inlined copies in cards_layer; the
tests below drive all three callers through the same latched-byte frame.
"""

from runtime import host_app, moy_carts
from runtime.editors_base import (KeyEdge, TextEntry, text_key, TE_EDIT,
                                  TE_COMMIT, TE_CANCEL)
from ws_helpers import shelf, select_on_home  # noqa: E402


class _Inp:
    def __init__(self, key=0):
        self.last_key = key


# -- the ladder ---------------------------------------------------------------

def test_text_key_is_the_one_ladder():
    assert text_key("ab", 8, 24) == ("a", TE_EDIT)
    assert text_key("ab", 127, 24) == ("a", TE_EDIT)        # Delete trims too
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


def test_text_entry_open_cuts_to_the_cap_and_types_one_char_per_press():
    e = TextEntry(4)
    e.open("abcdefgh")
    assert e.text == "abcd"                     # a seed longer than typing could make
    e.open("")
    held = _Inp(ord("z"))
    assert e.feed(held) == TE_EDIT
    assert e.feed(held) is None                  # the same byte held: no repeat
    assert e.feed(held) is None
    assert e.feed(_Inp(0)) is None               # the gap
    assert e.feed(held) == TE_EDIT               # ...re-arms the edge
    assert e.text == "zz"
    assert e.feed(_Inp(13)) == TE_COMMIT
    assert e.text == "zz"


def test_text_entry_seed_swallows_the_byte_held_at_open():
    """The wifi/TAG/rename discipline: a key still latched from the screen
    change is not a keystroke. Seeded, the held byte types nothing; a fresh
    press after a gap does."""
    e = TextEntry(8)
    e.open("", seed=13)
    assert e.feed(_Inp(13)) is None              # the Enter that opened it
    assert e.feed(_Inp(0)) is None
    assert e.feed(_Inp(13)) == TE_COMMIT         # a real second press


def test_guarded_open_arms_on_the_first_pass_only():
    """The modal discipline: the first input pass after a guarded open only
    re-seeds, whatever byte it carries -- even one that arrived AFTER the
    open and would otherwise read as fresh."""
    e = TextEntry(8)
    e.open("", seed=0, guard=True)
    inp = _Inp(ord("q"))                          # a byte the seed did not see
    assert e.edge.arming(inp.last_key) is True    # the caller's early-out
    assert e.edge.arming(inp.last_key) is False   # once
    assert e.feed(inp) is None                    # ...and it was re-seeded to 'q'
    assert e.feed(_Inp(0)) is None
    assert e.feed(_Inp(ord("q"))) == TE_EDIT
    assert e.text == "q"


def test_key_edge_reset_drops_a_pending_guard():
    k = KeyEdge()
    k.seed(5, guard=True)
    k.reset()
    assert k.arming(7) is False
    assert k.hit(7) is True


def test_a_shared_edge_does_not_refire_across_fields():
    """CART INFO's two fields share one KeyEdge: the Tab that moved focus is
    recorded once, so the byte held across the switch types nothing into the
    field it landed on."""
    edge = KeyEdge()
    a, b = TextEntry(8, edge=edge), TextEntry(8, edge=edge)
    a.open("", 0)
    b.open("", 0)
    assert a.feed(_Inp(ord("x"))) == TE_EDIT
    assert b.feed(_Inp(ord("x"))) is None        # still the same held byte
    assert b.text == ""


# -- arm_prompt: the three callers, one latched frame -------------------------

def _ws_with_lua_cart(tmp_path):
    root = str(tmp_path / "carts")
    moy_carts.ensure_dirs(root)
    moy_carts.create("Lua One", root, src="function _draw() cls(1) end\n",
                     runtime="lua", main="main.lua",
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
    ws.handle_input()
    ws.input.last_key = 0
    ws.input.set_held("a", False)
    ws.input.begin_frame()


def _typed_frame(ws, byte):
    """One typed byte, and the frames that draw it: a dialog's field takes
    its keys as it draws (ui.field)."""
    ws.input.begin_frame()
    ws.input.last_key = byte
    ws.handle_input()
    ws.frame(1 / 30)
    ws.input.last_key = 0
    ws.input.begin_frame()
    ws.handle_input()
    ws.frame(1 / 30)


def test_cart_info_survives_the_keypress_that_opened_it(tmp_path):
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    ws.input.set_held("a", True)
    ws.input.begin_frame()
    ws.input.last_key = 0x0D
    cl._open_meta()                               # opened while Enter/A are down
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
    ws = _ws_with_lua_cart(tmp_path)
    ws.set_menu_view("blocks")
    bu = ws.block_ui
    ws.input.begin_frame()
    ws.input.last_key = 0x0D
    bu._blk_open_kbd("text", "", block={"t": "x", "p": {}}, slot="s")
    assert bu.blk_kbd is not None and ws.input.text_mode
    _latched_frame(ws)
    ws.frame(1 / 30)
    assert bu.blk_kbd is not None, "the opening Enter must not commit the prompt"
    assert bu.blk_kbd["entry"].text == ""
    _typed_frame(ws, ord("h"))
    assert bu.blk_kbd["entry"].text == "h"
    bu._blk_kbd_cancel()
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


# -- the seeded fields: wifi and the scene TAG ---------------------------------

def test_wifi_password_prompt_does_not_connect_on_the_enter_that_opened_it(tmp_path):
    """The prompt is the engine's field (tests/test_settings_wifi_prompt.py):
    the Enter still held from picking the row types nothing into it."""
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    drv = host_app.ConsoleDriver(ws)
    ws.open_settings()
    sl = ws.settings_layer
    sl.open_wifi()
    sl.wifi_nets = [("Locked", 80, True)]
    sl.wifi_known = []
    sl.wifi_sel = 0
    drv.frame(1 / 30)
    ws.input.last_key = 0x0D                      # the Enter that picked the row
    sl._wifi_activate()
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
    assert sl.wifi_pw.text == "pw"
    key(127)                                      # Delete trims like Backspace now
    assert sl.wifi_pw.text == "p"
    key(27)
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
    cart = ws.project.cart
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
    assert moy_carts.cart_sources(ws.project.cart) == ["main.lua"]


def test_new_script_is_closed_by_a_cart_switch(tmp_path):
    ws = _ws_with_lua_cart(tmp_path)
    cl = ws.cards_layer
    cl._open_newf()
    ws.input.begin_frame()
    ws.frame(1 / 30)                               # draws the dialog
    cl.reset()
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
