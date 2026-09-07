"""The EDITOR HANDLE from a Lua cart (#112, step 3 of
docs/text_editing_2026-09.md).

`open_editor` returns an OBJECT, and no binding here marshals one -- so without
the handle glue a Lua cart's `ed:draw(...)` would die on "index a nil value",
exactly as `make_layer` did before `runtime/lua_ext.py` existed and as the whole
placement family did before #214. It rides the same route: an int handle on the
Python side, a wrapper table in the prelude, and only numbers and strings
across the boundary.

What each check is watching for:

  * every documented verb reaches the SAME handle the Python side holds, with
    the value the Python form gives -- `docs/moy_cart_api.md` says the two
    languages agree verbatim, so anything less is a documented lie. The cart
    compares strings IN LUA and exports 0/1, because `get_global` speaks
    numbers; the Python assertions read the handle itself.
  * the two PAIR-valued verbs (`tap`, `save`) come back as two Lua values, and
    a tapped `[[link]]` names the note.
  * the GATE: a cart whose manifest did not earn `open_editor` has no such
    global at all, because a nil-returning stub is the one thing the permission
    model exists to prevent.

Skipped without a C compiler, like the other host-lua suites.
"""

import contextlib

import pytest

from runtime import lua_binding as lb
from runtime.lua_ext import PRELUDE_HANDLES, install_handles

pytestmark = pytest.mark.skipif(
    not lb.HostLuaRun.available(),
    reason="no C compiler for the host lua binding")


class _FakeCanvas:
    """What the handle draws through: the verbs it calls, recorded."""

    w = 96
    h = 64

    def __init__(self):
        self.calls = []

    def cls(self, c=0):
        self.calls.append(("cls", c))

    def rect(self, x, y, w, h, c):
        self.calls.append(("rect", x, y, w, h, c))

    def rectb(self, x, y, w, h, c):
        self.calls.append(("rectb", x, y, w, h, c))

    def clip(self, *a):
        self.calls.append(("clip", a))

    def print(self, s, x, y, c, scale=1):
        self.calls.append(("print", s, x, y, c, scale))

    def spr(self, img, x, y, scale=1, flip=0):
        self.calls.append(("spr", x, y))


class _FakeFiles:
    """The `(value, err)` shape of the `Files` role over a dict of documents."""

    def __init__(self, docs):
        self.docs = dict(docs)

    def load(self, kind, name):
        got = self.docs.get((kind, name))
        return (got, None) if got is not None else (None, "no file")

    def save(self, kind, name, blob):
        self.docs[(kind, name)] = blob
        return (name, None)

    def encode_text(self, body):
        return body

    def decode_text(self, blob):
        return str(blob).split("\n")

    def history_ops(self, kind, name):
        return ([], None)

    def history_commit(self, kind, name, ops, keyframe=None):
        return (True, None)


THEME = {"surface": 1, "ink": 7, "ink_dim": 6, "accent": 10, "focus": 10,
         "selection": 13, "danger": 8}

DOCS = {
    ("docs", "hub"): "# Hub\ngo [[other]] now\n- [ ] milk\n",
    ("docs", "other"): "the other one\n",
}


class Run:
    """One Lua cart over a real editor handle, wired as the Player wires it."""

    def __init__(self, granted=True):
        from runtime.editor_handle import EditorHandle

        self.canvas = _FakeCanvas()
        self.files = _FakeFiles(DOCS)
        self.made = []

        def open_editor(name=None, mode=None):
            if name is None:
                name = "hub"              # stands in for the router's request
            ed = EditorHandle(self.files, "docs", name, mode, self.canvas,
                              lambda: THEME)
            self.made.append(ed)
            return ed

        self.ns = {"open_editor": open_editor} if granted else {}
        self.buf = bytearray(96 * 64 * 2)
        self.run = lb.HostLuaRun(self.buf, 96, 64)
        install_handles(self.ns, self.run.register)
        assert self.run.exec(PRELUDE_HANDLES, "prelude") is None

    def get(self, name):
        return self.run.get_global(name)

    @property
    def ed(self):
        return self.made[0]


@contextlib.contextmanager
def run_cart(body, granted=True):
    """`load` runs the chunk AND `_init`, so the body below IS the cart."""
    r = Run(granted)
    try:
        err = r.run.load("function _init()\n%s\nend\n"
                         "function _update(dt) end\nfunction _draw() end\n"
                         % body, "@cart")
        assert err is None, err
        yield r
    finally:
        r.run.close()


# ---------------------------------------------------------------------------
# the handle crosses at all
# ---------------------------------------------------------------------------

def test_open_editor_hands_lua_a_table_it_can_call():
    with run_cart("""
      local ed = open_editor("other")
      TYPEOK = (type(ed) == "table") and 1 or 0
      NAMEOK = (ed:name() == "other") and 1 or 0
      MODEOK = (ed:mode() == "md") and 1 or 0
      TEXTOK = (ed:text() == "the other one\\n") and 1 or 0
      DIRTY = ed:dirty() and 1 or 0
    """) as r:
        assert r.get("TYPEOK") == 1
        assert r.get("NAMEOK") == 1, "name() did not cross"
        assert r.get("MODEOK") == 1, "mode() did not cross"
        assert r.get("TEXTOK") == 1, "text() did not cross"
        assert r.get("DIRTY") == 0


def test_the_parameterless_form_takes_the_consoles_own_request():
    with run_cart("""
      local ed = open_editor()
      OK = (ed ~= nil and ed:name() == "hub") and 1 or 0
    """) as r:
        assert r.get("OK") == 1


def test_a_cart_without_the_grant_has_no_such_global():
    """The permission model's whole claim, in Lua: not a stub that answers nil,
    not a friendlier error -- no name."""
    with run_cart("OK = (open_editor == nil) and 1 or 0", granted=False) as r:
        assert r.get("OK") == 1


def test_open_editor_is_never_registered_as_a_raw_trampoline():
    """A registered `open_editor` would shadow the prelude's wrapper with the
    Python closure, whose handle marshals to nil -- the exact failure the deny
    list exists for."""
    from runtime.lua_ext import NOT_REGISTRABLE

    assert "open_editor" in NOT_REGISTRABLE


# ---------------------------------------------------------------------------
# every verb, against the Python side of the same handle
# ---------------------------------------------------------------------------

def test_typing_undo_redo_and_the_caret_agree_with_the_python_form():
    with run_cart("""
      local ed = open_editor("other")
      ed:set_text("")
      for i = 1, 3 do ed:key(97 + i) end
      TYPEDOK = (ed:text() == "bcd") and 1 or 0
      CANUNDO = ed:can_undo() and 1 or 0
      ed:undo()
      AFTEROK = (ed:text() == "") and 1 or 0
      CANREDO = ed:can_redo() and 1 or 0
      ed:redo()
      BACKOK = (ed:text() == "bcd") and 1 or 0
      ROW, COL = ed:caret()
    """) as r:
        assert r.get("TYPEDOK") == 1 and r.get("CANUNDO") == 1
        assert r.get("AFTEROK") == 1 and r.get("CANREDO") == 1
        assert r.get("BACKOK") == 1
        assert (r.get("ROW"), r.get("COL")) == (0, 3)
        assert r.ed.text() == "bcd", "the SAME handle, not a copy"
        assert r.ed.dirty() is True


def test_the_clipboard_verbs_round_trip():
    with run_cart("""
      local ed = open_editor("other")
      ed:select_all()
      COPIED = ed:copy() and 1 or 0
      ed:set_text("")
      PASTED = ed:paste() and 1 or 0
    """) as r:
        assert r.get("COPIED") == 1 and r.get("PASTED") == 1
        assert r.ed.text() == "the other one\n"


def test_save_answers_two_values_and_writes_the_document():
    with run_cart("""
      local ed = open_editor("other")
      ed:set_text("rewritten")
      local ok, badge = ed:save()
      OK = ok and 1 or 0
      CLEAN = (badge == "") and 1 or 0
    """) as r:
        assert r.get("OK") == 1 and r.get("CLEAN") == 1
        assert r.files.docs[("docs", "other")] == "rewritten"


def test_a_soft_save_refuses_and_badges_in_lua_too():
    with run_cart("""
      local ed = open_editor("cfg", "json")
      ed:set_text("{")
      local ok, badge = ed:save(true)
      OK = ok and 1 or 0
      BADGED = (badge:sub(1, 7) == "INVALID") and 1 or 0
    """) as r:
        assert r.get("OK") == 0
        assert r.get("BADGED") == 1, "the mode gate's badge did not cross"
        assert ("docs", "cfg") not in r.files.docs, "a refused save wrote"


DRAWN = 'ED = open_editor("hub")\nED:draw(0, 0, 96, 60)\n'


def _tap_at(r, buffer_row, col, tail):
    """Tap the drawn cell of one BUFFER row through Lua. The y comes from the
    layout the draw actually produced -- a wrapped line is several rows, so a
    hardcoded row number would be a guess about the wrap, not about the tap."""
    x0, y0, cell, lh, _scale = r.ed._geom
    y = next(y for brow, _si, y in r.ed._vis if brow == buffer_row)
    return r.run.exec(
        "do local verb, arg = ED:tap(%d, %d)\n%s\nend"
        % (x0 + col * cell + 2, y + 2, tail), "tap")


def test_draw_and_tap_reach_the_canvas_and_name_the_tapped_link():
    with run_cart(DRAWN + 'NIL = (ED:tap(0, 500) == nil) and 1 or 0') as r:
        assert any(c[0] == "print" for c in r.canvas.calls), "nothing drew"
        assert r.get("NIL") == 1, "a tap outside the editor must answer nil"
        assert _tap_at(r, 1, 4,
                       'LINK = (verb == "link" and arg == "other") and 1 or 0'
                       ) is None
        assert r.get("LINK") == 1


def test_a_checkbox_tap_answers_its_row_as_a_number():
    with run_cart(DRAWN) as r:
        assert _tap_at(r, 2, 3,
                       'VERBOK = (verb == "check") and 1 or 0\nROW = arg'
                       ) is None
        assert r.get("VERBOK") == 1
        assert r.get("ROW") == 2, "the row must arrive as a NUMBER"
        assert "- [x] milk" in r.ed.text()


def test_focus_scroll_and_close_are_callable_without_a_shell():
    """The handle degrades where there is no console to flip a keyboard: focus
    is still tracked, and nothing raises."""
    with run_cart("""
      local ed = open_editor("other")
      TOOK = ed:focus() and 1 or 0
      ON = ed:focused() and 1 or 0
      ed:scroll(1, 0)
      ed:focus(false)
      OFF = ed:focused() and 1 or 0
      ed:close()
    """) as r:
        assert (r.get("TOOK"), r.get("ON"), r.get("OFF")) == (1, 1, 0)
