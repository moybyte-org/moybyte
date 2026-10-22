# Map (grep -n a name to jump there):
#   BlocksPage         the Blocks tab on the verbs, the handheld's and a desk pane's
#   BlocksPage.draw    the objects' tabs, the outline and the foot
#   BlocksPage._row    one outline row: a hat, a statement or an insert point
#   BlocksPage.act     a foot tool's verb
"""The Editor's Blocks tab on the public UI API, the handheld's and a desk
Editor's Blocks pane alike (docs/media/look_2026-10/EDITOR_LAYOUT.md's
`blocks` pictures): the objects as tabs (the Stage, then each sprite with
scripts or on the stage),
the outline of the picked object's scripts -- a hat in its category's
colour, a statement on a plate with its category's bar, indented by depth,
the cursor's row on the current plate and the cursor's insert point as
"add block" -- and the foot's tools (add, delete, up, down, more) and See
code, the door to the Code tab.

The outline is `BlockEditorUI`'s: its rows, its taps (a first tap picks a
row and its slot, a second acts), its drag scroll, its keys, and the
prompts it draws over the page. The frame begins with no stop holding
focus, and the verbs take the other taps. The insert menu, the block
actions and the pickers are a popover's menu over its items, whose keys are
the engine's while it is open."""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui

# The menu's caption by its mode.
MENU_TITLES = {"cat": "Pick a kind", "blk": "Pick a block", "dropdown": "Pick one",
               "variable": "Pick a variable", "list": "Pick a list",
               "expr": "Pick a value", "actions": "Block actions",
               "proc": "Edit this block"}
TOOLS = (("new", "Add", "add"), ("del", "Delete", "del"), ("arrow_up", "Up", "up"),
         ("arrow_down", "Down", "down"), ("more", "More", "more"))
TOOL = 26                        # a foot tool's pitch


def _width(text):
    ui.face("ui")
    w = ui.text_size(text)[0]
    ui.face()
    return w


class BlocksPage:
    """The Blocks tab over `bu`, the workstation's `BlockEditorUI`; `host`
    is the frame's `AppHost`."""

    def __init__(self, bu, host):
        self.bu = bu
        self.host = host

    # -- drawing ------------------------------------------------------------------

    def draw(self, cv, th, rect, desk):
        bu = self.bu
        host = self.host
        host.begin(cv, th, rect, desk, False, elsewhere=bu.blk_menu is None)
        ui.at(*rect)
        ui.page("")
        be = bu.blocks_ed
        x, y, w, h = rect
        if be is None:
            ui.at(*rect)
            ui.empty("blocks", "No blocks", ("This cart has no program to show",))
            ui.end()
            return
        self._head(be, x, y, w)
        lay = bu.block_layout
        rows = be.rows
        for vi in range(lay.rows):
            ridx = bu.blk_top + vi
            if ridx >= len(rows):
                break
            self._row(cv, th, lay, rows[ridx], vi, ridx == be.cur)
        self._foot(be, x, lay.bar_y, w)
        if bu.blk_menu is not None:
            self._menu(bu.blk_menu, w)
        ui.end()

    def _menu(self, m, w):
        """The insert menu, the block actions and the pickers: a popover
        centred on the page, its rows `BlockEditorUI`'s items."""
        bu = self.bu
        n = len(m["items"])
        words = [self.menu_word(k) for k in range(n)]
        if not ui.popover("Block menu", min(w - 40, 220)):
            bu._blk_menu_back()
            return
        got = ui.menu(MENU_TITLES.get(m["mode"], "Pick"), words)
        ui.popover()
        if got is not None and 0 <= got < n:
            m["sel"] = got
            bu._blk_menu_select()

    def menu_word(self, k):
        """The menu's row `k` as it reads (its id): the item's label, a
        category's in sentence case."""
        word = self.bu._blk_menu_label(k)
        if word.isupper():
            word = word[:1] + word[1:].lower()
        return word

    def _head(self, be, x, y, w):
        bu = self.bu
        if bu.blk_graduated:
            ui.at(x + 8, y + 4, w - 16, 20)
            ui.label("You levelled up to code!", {"tone": "value"})
            return
        status = bu.blk_status
        sw = 0
        if status:
            word = status[:1].upper() + status[1:].lower()
            sw = min(_width(word) + 4, w // 2)
            ui.at(x + w - 8 - sw, y + 6, sw, 18)
            ui.label(word, {"tone": "dim"})
        names = ["Stage"]
        cur = 0
        room = w - 16 - sw - 8 - (_width("Stage") + 20)
        for t in bu._roster_tags():
            tw = _width(t) + 20
            if tw > room:
                break
            room -= tw
            if t == be.target:
                cur = len(names)
            names.append(t)
        ui.at(x + 4, y + 3, w - 8 - sw - 8, 20)
        got = ui.tabs("Objects", names, cur)
        if got is not None and got != cur and 0 <= got < len(names):
            bu._select_sprite(None if got == 0 else names[got])
            bu.blk_top = 0
            bu.blk_slot = 0

    def _row(self, cv, th, lay, row, vi, cursor):
        bu = self.bu
        rh = lay.row_h
        y = lay.y0 + vi * rh
        x = lay.x0 + row.depth * lay.indent
        w = lay.outline_w - row.depth * lay.indent
        if row.kind == "insert":
            if cursor:
                aw = min(w, _width("add block") + 40)
                ui.at(x, y + 1, aw, rh - 2)
                ui.icon("new")
                ui.button("add block", "tonal", {"id": "Insert %d" % vi})
            else:
                cv.rect(x + 2, y + rh // 2 - 1, 10, 2, th["border"])
            return
        b = row.block
        label = bu._blk_row_text(b, row.is_else)
        fill = bu._cat_color("control" if row.is_else else bu._blk_block_cat(b))
        hat = not row.is_else and bu._is_hat_block(b)
        tw = min(w, ui.text_size(label)[0] + (20 if hat else 22))
        th_ = rh - 2
        if cursor:
            ui.at(x - 4, y - 1, tw + 8, rh)
            ui.tool("", "", {"id": "Row", "current": True})
        if hat:
            cv.rrect(x, y, tw, th_, 6, fill)
            ink = bu._blk_ink(fill)
            tx = x + 10
        else:
            cv.rrect(x, y, tw, th_, 5, th["surface_alt"])
            cv.rrect(x, y, 3, th_, 1, fill)
            ink = th["ink"]
            tx = x + 9
        lh = ui.text_size(label)[1]
        ty = y + (th_ - lh) // 2
        cv.print(label, tx, ty, ink, 1)
        if cursor and not row.is_else:
            self._caret(cv, th, b, label, tx, ty + lh)

    def _caret(self, cv, th, b, label, tx, y):
        """The selected slot's value underlined, what A and left and right
        change."""
        bu = self.bu
        slots = bu.blocks_ed.slots(b)
        if not slots:
            return
        si = bu.blk_slot % len(slots)
        col = bu._blk_slot_text_col(b, si)
        if col is None:
            return
        val = bu._blk_slot_display(b, slots[si])
        x0 = tx + ui.text_size(label[:col])[0]
        cv.rect(x0, y, max(4, ui.text_size(val)[0]), 2, th["value"])

    def _foot(self, be, x, y, w):
        bu = self.bu
        ui.at(x, y, w, 1)
        ui.rule()
        off = bu.blk_graduated
        for i, (icon, word, verb) in enumerate(TOOLS):
            ui.at(x + 4 + i * TOOL, y + 6, 24, 24)
            if ui.tool(icon, "", {"id": "Tool " + word, "disabled": off,
                                  "current": verb == "more" and be.moving()}):
                self.act(verb)
        word = "See code"
        bw = _width(word) + 40
        ui.at(x + w - 6 - bw, y + 7, bw, 22)
        ui.icon("code")
        if ui.button(word, "normal", {"id": "See code"}):
            self.act("code")

    # -- the verbs ------------------------------------------------------------------

    def act(self, verb):
        """Foot verb `verb`: the action bar's, as its buttons did."""
        bu = self.bu
        be = bu.blocks_ed
        if be is None:
            return
        if verb == "add":
            bu._blk_open_categories()
        elif verb == "del":
            be.delete()
            bu.blk_slot = 0
            bu._blk_reveal()
        elif verb in ("up", "down"):
            be.move_block(-1 if verb == "up" else 1)
            bu._blk_reveal()
        elif verb == "more":
            bu._blk_open_actions()
        elif verb == "code":
            bu.ws.defer(bu.graduate_to_code)

    # -- input ------------------------------------------------------------------------

    def keys(self, i):
        """A frame's keys while the menu is open: the engine's. Whether the
        page owes a frame."""
        return self.host.keys(i)

    def point(self, px, py, click, down):
        """A pointer sample for the verbs. Whether the page owes a frame."""
        return self.host.point(px, py, click, down)

    def owed(self):
        """Whether a tap or a rest frame is still to be drawn."""
        h = self.host
        return h.owed or h.tap != 0
