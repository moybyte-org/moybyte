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

from array import array

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


_COLS = ("",)
_O_ROW = {"id": "Row", "current": True}
_O_ROW_ADD = {"id": "Row"}
_O_CODE = {"id": "See code"}
_O_DIM = {"tone": "dim"}
_O_LEVEL = {"tone": "value"}
_ADD_W = [0]                     # "add block"'s button width


class BlocksPage:
    """The Blocks tab over `bu`, the workstation's `BlockEditorUI`; `host`
    is the frame's `AppHost`."""

    def __init__(self, bu, host):
        self.bu = bu
        self.host = host
        # The outline is a table whose rows are parts (`ui.table_part`), each
        # row's cell its key: a frame draws the rows whose block or marks
        # changed, the rest carried as drawn. The cursor's row wears the
        # plate's own mark, so the table draws none, and its slot is the
        # table's gen, which keys the current row.
        self._o = {"id": "Outline", "part": (0, 0), "current": False, "gen": 0}
        self._rows = []                 # [key] a row
        self._made = [None] * 8         # what the keys were made from
        # each row's look, made with its key: [label, plate width, fill,
        # ink, hat, label height] (None for an insert point)
        self._looks = []
        self._caret_at = [None] * 4     # (row, slot, made) -> its x off and width
        self._hk = [None] * 7           # what the tabs were made from
        self._names = ["Stage"]
        self._tab = [0, 0, None]        # the current tab, the status's width, its word
        self._tools = [{"id": "Tool " + t[1], "disabled": False, "current": False}
                       for t in TOOLS]
        self._code_w = [0]
        self._r = array("i", [0] * 4)
        # what the page shows beside the outline (its head and foot), as the
        # page's gen keys it: [gen, then what it was made from]
        self._pg = [0, None, None, None, None, None, None, None, None, None, None]
        self._po = {"gen": 0}
        self._over = False              # a prompt drew over the page (`forget`)

    # -- drawing ------------------------------------------------------------------

    def forget(self):
        """The page as the last frame drew it is not what the glass holds
        (a prompt draws over it): each frame draws it whole until one has
        drawn it with no prompt over it."""
        self._over = True

    def _gen(self, be, rect, desk):
        """The page's options, its gen moved where what it shows beside the
        outline changed: the objects' tabs and the status over it, the
        foot's tools under it (the outline's rows are the table's)."""
        bu = self.bu
        se = bu.ws.scene_ui.sceneedit
        g = self._pg
        sg = se._hist.gen if se is not None else -1
        mv = be.moving() if be is not None else False
        tg = be.target if be is not None else None
        if (g[10] != tg or g[1] is not be or g[3] is not se or g[4] != sg
                or g[5] is not bu.blk_status or g[6] != bu.blk_graduated
                or g[7] != mv or g[8] != rect or g[9] != desk):
            g[0] += 1
            g[1] = be
            g[3] = se
            g[4] = sg
            g[5] = bu.blk_status
            g[6] = bu.blk_graduated
            g[7] = mv
            g[8] = rect
            g[9] = desk
            g[10] = tg
            self._po["gen"] = g[0]
        return self._po

    def _keys(self, be, lay):
        """The table's rows: each outline row's key -- its kind, depth and
        label -- made again where the program changed."""
        bu = self.bu
        rows = self._rows
        m = self._made
        mv = be.moving()
        lg = bu.ws.look.look_gen
        if (m[0] is not be or m[1] != be._hist.gen or m[2] != be.target
                or m[3] != len(be.rows) or m[4] != mv or m[5] != bu.blk_graduated
                or m[6] is not lay or m[7] != lg):
            m[0] = be
            m[1] = be._hist.gen
            m[2] = be.target
            m[3] = len(be.rows)
            m[4] = mv
            m[5] = bu.blk_graduated
            m[6] = lay
            m[7] = lg
            self._caret_at[0] = None
            looks = self._looks
            del rows[:]
            del looks[:]
            for row in be.rows:
                if row.kind == "insert":
                    rows.append(["+%d" % row.depth])
                    looks.append(None)
                    continue
                b = row.block
                label = bu._blk_row_text(b, row.is_else)
                rows.append(["%d %d %s" % (row.depth, row.is_else, label)])
                fill = bu._cat_color("control" if row.is_else else bu._blk_block_cat(b))
                hat = not row.is_else and bu._is_hat_block(b)
                tw, lh = ui.text_size(label)
                looks.append([label, tw + (20 if hat else 22), fill,
                              bu._blk_ink(fill) if hat else None, hat, lh])
        return rows

    def draw(self, cv, th, rect, desk):
        bu = self.bu
        host = self.host
        be = bu.blocks_ed
        if self._over:
            host.whole()
            self._over = bu.blk_kbd is not None
        host.begin(cv, th, rect, desk, False, elsewhere=bu.blk_menu is None)
        ui.at(rect[0], rect[1], rect[2], rect[3])
        ui.page("", self._gen(be, rect, desk))
        x, y, w, h = rect
        if be is None:
            ui.at(*rect)
            ui.empty("blocks", "No blocks", ("This cart has no program to show",))
            ui.end()
            return
        self._head(be, x, y, w)
        self._outline(cv, th, be, x, w)
        self._foot(be, x, bu.block_layout.bar_y, w)
        if bu.blk_menu is not None:
            self._menu(bu.blk_menu, w)
        ui.end()

    def _outline(self, cv, th, be, x, w):
        """The outline's rows as the table's, each drawn in its part; a tap
        on a row is the outline's (a first picks it and its slot, a second
        acts)."""
        bu = self.bu
        lay = bu.block_layout
        rh = lay.row_h
        o = self._o
        if o["part"][1] != rh:
            o["part"] = (0, rh)
        o["gen"] = bu.blk_slot
        ui.at(x, lay.y0 - 1, w, lay.rows * rh)
        got, opened = ui.table("", _COLS, self._keys(be, lay), be.cur, o)
        if got >= 0 and (got != be.cur or opened) and got < len(be.rows):
            bu._blk_tap_row(got, self.host.px, opened)
        rows = be.rows
        looks = self._looks
        r = self._r
        i = ui.table_part(r)
        while i >= 0:
            if i < len(rows):
                self._row(cv, th, lay, rows[i], looks[i], r[1] + 1, i == be.cur)
            i = ui.table_part(r)

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
            ui.label("You levelled up to code!", _O_LEVEL)
            return
        status = bu.blk_status
        se = bu.ws.scene_ui.sceneedit
        k = self._hk
        tab = self._tab
        names = self._names
        if (k[0] is not se or k[1] != (se._hist.gen if se is not None else -1)
                or k[2] is not be or k[3] != be._hist.gen or k[4] != be.target
                or k[5] is not status or k[6] != w):
            k[0] = se
            k[1] = se._hist.gen if se is not None else -1
            k[2] = be
            k[3] = be._hist.gen
            k[4] = be.target
            k[5] = status
            k[6] = w
            sw = 0
            tab[2] = None
            if status:
                tab[2] = status[:1].upper() + status[1:].lower()
                sw = min(_width(tab[2]) + 4, w // 2)
            tab[1] = sw
            del names[1:]
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
            tab[0] = cur
        sw = tab[1]
        if tab[2] is not None:
            ui.at(x + w - 8 - sw, y + 6, sw, 18)
            ui.label(tab[2], _O_DIM)
        cur = tab[0]
        ui.at(x + 4, y + 3, w - 8 - sw - 8, 20)
        got = ui.tabs("Objects", names, cur)
        if got is not None and got != cur and 0 <= got < len(names):
            bu._select_sprite(None if got == 0 else names[got])
            bu.blk_slot = 0

    def _row(self, cv, th, lay, row, look, y, cursor):
        """Outline row `row`, its top at y, `look` as `_keys` made it: a
        hat, a statement on its plate (the cursor's under the plate's
        current mark, its slot underlined), or an insert point (the
        cursor's as "add block")."""
        rh = lay.row_h
        x = lay.x0 + row.depth * lay.indent
        w = lay.outline_w - row.depth * lay.indent
        if look is None:
            if cursor:
                aw = _ADD_W[0]
                if not aw:
                    aw = _ADD_W[0] = _width("add block") + 40
                ui.at(x, y + 1, min(w, aw), rh - 2)
                ui.icon("new")
                ui.button("add block", "tonal", _O_ROW_ADD)
            else:
                cv.rect(x + 2, y + rh // 2 - 1, 10, 2, th["border"])
            return
        label = look[0]
        fill = look[2]
        tw = min(w, look[1])
        th_ = rh - 2
        if cursor:
            ui.at(x - 4, y - 1, tw + 8, rh)
            ui.tool("", "", _O_ROW)
        if look[4]:
            cv.rrect(x, y, tw, th_, 6, fill)
            ink = look[3]
            tx = x + 10
        else:
            cv.rrect(x, y, tw, th_, 5, th["surface_alt"])
            cv.rrect(x, y, 3, th_, 1, fill)
            ink = th["ink"]
            tx = x + 9
        lh = look[5]
        ty = y + (th_ - lh) // 2
        cv.print(label, tx, ty, ink, 1)
        if cursor and not row.is_else:
            self._caret(cv, th, row.block, label, tx, ty + lh)

    def _caret(self, cv, th, b, label, tx, y):
        """The selected slot's value underlined, what A and left and right
        change."""
        bu = self.bu
        be = bu.blocks_ed
        c = self._caret_at
        if c[0] is not b or c[1] != bu.blk_slot:
            c[0] = b
            c[1] = bu.blk_slot
            c[2] = None
            slots = be.slots(b)
            if slots:
                si = bu.blk_slot % len(slots)
                col = bu._blk_slot_text_col(b, si)
                if col is not None:
                    val = bu._blk_slot_display(b, slots[si])
                    c[2] = ui.text_size(label[:col])[0]
                    c[3] = max(4, ui.text_size(val)[0])
        if c[2] is not None:
            cv.rect(tx + c[2], y, c[3], 2, th["value"])

    def _foot(self, be, x, y, w):
        bu = self.bu
        ui.at(x, y, w, 1)
        ui.rule()
        off = bu.blk_graduated
        mv = be.moving()
        tools = self._tools
        for i in range(len(TOOLS)):
            t = TOOLS[i]
            o = tools[i]
            o["disabled"] = off
            o["current"] = mv and t[2] == "more"
            ui.at(x + 4 + i * TOOL, y + 6, 24, 24)
            if ui.tool(t[0], "", o):
                self.act(t[2])
        bw = self._code_w[0]
        if not bw:
            bw = self._code_w[0] = _width("See code") + 40
        ui.at(x + w - 6 - bw, y + 7, bw, 22)
        ui.icon("code")
        if ui.button("See code", "normal", _O_CODE):
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
        elif verb in ("up", "down"):
            be.move_block(-1 if verb == "up" else 1)
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
