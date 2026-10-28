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


class BlocksPage:
    """The Blocks tab over `bu`, the workstation's `BlockEditorUI`; `host`
    is the frame's `AppHost`."""

    def __init__(self, bu, host):
        self.bu = bu
        self.host = host
        # The outline's rows are the app's own pixels: a frame draws the
        # rows a key step changed alone (`rows`), never a plan the engine
        # makes without them. The page's gen says what else it shows.
        host.own_rows = True
        self._o = {"gen": 0}
        self._shows = None
        self._drawn = None              # (gen, cursor, top, slot) of the last frame drawn
        self._bands = array("i", [0] * 16)  # this frame's row bands (first, last) when its rows alone
        self._nb = 0
        self._to_foot = False
        self._rects = array("i", [0] * 24)
        self._floor = array("i", [0] * 64)
        self._mark = bytearray(64)

    # -- drawing ------------------------------------------------------------------

    def _gen(self, be, rect, desk):
        """The page's gen: it moves with everything the page shows but the
        outline's cursor, its scroll and its slot."""
        bu = self.bu
        lay = bu.block_layout
        shows = (id(be), be._hist.gen if be is not None else 0,
                 be.target if be is not None else None, len(be.rows) if be is not None else 0,
                 be.moving() if be is not None else False, bu.blk_graduated, bu.blk_status,
                 bu.blk_menu is not None, tuple(bu._roster_tags()), rect, desk, id(lay))
        if shows != self._shows:
            self._shows = shows
            self._o["gen"] += 1
        return self._o["gen"]

    def _plan(self, be, gen):
        """Ask the host for this frame's rows where the last frame drawn
        differs from it only by the cursor, the scroll or the slot (or not
        at all): the bands of rows that changed (`_bands`), the outline
        moved by the rows it scrolled."""
        self._nb = 0
        d = self._drawn
        bu = self.bu
        if d is None or d[0] != gen or be is None:
            return
        cur, top, slot = be.cur, bu.blk_top, bu.blk_slot
        lay = bu.block_layout
        n = lay.rows
        dt = top - d[2]
        if dt >= n or -dt >= n:
            return
        mark = self._mark
        if n > len(mark):
            return
        for vi in range(n):
            mark[vi] = 0
        self._to_foot = d[1] != cur or dt != 0
        if self._to_foot:
            # the cursor's stop moved: the engine's floor takes the page
            # from its top down (every stop after a moved one draws whole)
            a = min(cur - top, d[1] - top)
            for vi in range(a if a > 0 else 0, n):
                mark[vi] = 1
        elif d[3] != slot:
            if 0 <= cur - top < n:
                mark[cur - top] = 1
        if dt > 0:
            for vi in range(n - dt, n):
                mark[vi] = 1
        elif dt < 0:
            for vi in range(-dt):
                mark[vi] = 1
        nb = self._band(n)
        if nb < 0:
            return
        x, _y, w, _h = self._rect
        self._nb = nb if nb else -1
        self.host.rows(self._rects, nb, (x, lay.y0 - 1, w, n * lay.row_h, -dt * lay.row_h)
                       if dt else None)

    def _band(self, n):
        """The runs of marked rows (`_mark`) as rects across the page
        (`_rects`, `_bands`): their count, -1 past what a frame names."""
        mark = self._mark
        b = self._bands
        nb = 0
        vi = 0
        while vi < n:
            if mark[vi]:
                a = vi
                while vi + 1 < n and mark[vi + 1]:
                    vi += 1
                if nb == 6:
                    return -1
                b[2 * nb] = a
                b[2 * nb + 1] = vi
                nb += 1
            vi += 1
        lay = self.bu.block_layout
        x, _y, w, _h = self._rect
        rh = lay.row_h
        r = self._rects
        for i in range(nb):
            r[4 * i] = x
            r[4 * i + 1] = lay.y0 + b[2 * i] * rh - 1
            r[4 * i + 2] = w
            r[4 * i + 3] = (b[2 * i + 1] - b[2 * i] + 1) * rh
        if nb and self._to_foot and b[2 * nb - 1] == n - 1:
            # ...and from there the page's foot
            j = 4 * (nb - 1)
            r[j + 3] = self._rect[1] + self._rect[3] - r[j + 1]
        return nb

    def _floor_rows(self, cv, n):
        """Every row the frame's floor reaches joins the marked rows, and
        each run of them joins the floor over the page's ground: their
        count, -1 where the frame cannot draw them so."""
        host = self.host
        f = self._floor
        k = host.floor_rects(f)
        lay = self.bu.block_layout
        rh = lay.row_h
        y0 = lay.y0 - 1
        mark = self._mark
        for i in range(0, 4 * k, 4):
            a = (f[i + 1] - y0) // rh
            e = (f[i + 1] + f[i + 3] - y0 + rh - 1) // rh
            if a < 0:
                a = 0
            if e > n:
                e = n
            for vi in range(a, e):
                mark[vi] = 1
        nb = self._band(n)
        if nb < 0:
            return -1
        r = self._rects
        for i in range(nb):
            j = 4 * i
            if not host.floor_take(cv, r[j], r[j + 1], r[j + 2], r[j + 3]):
                return -1
        return nb

    def draw(self, cv, th, rect, desk):
        bu = self.bu
        host = self.host
        be = bu.blocks_ed
        self._rect = rect
        gen = self._gen(be, rect, desk)
        self._plan(be, gen)
        host.begin(cv, th, rect, desk, False, elsewhere=bu.blk_menu is None)
        ui.at(*rect)
        ui.page("", self._o)
        x, y, w, h = rect
        if be is None:
            ui.at(*rect)
            ui.empty("blocks", "No blocks", ("This cart has no program to show",))
            ui.end()
            self._drawn = None
            return
        self._head(be, x, y, w)
        lay = bu.block_layout
        rows = be.rows
        part = self._nb != 0 and host.shifting
        nb = self._floor_rows(cv, lay.rows) if part else 0
        broke = nb < 0
        if broke:
            host.break_rows()
        if part and not broke:
            r = self._rects
            b = self._bands
            for i in range(nb):
                cv.clip(r[4 * i], r[4 * i + 1], r[4 * i + 2], r[4 * i + 3])
                for vi in range(b[2 * i], b[2 * i + 1] + 1):
                    ridx = bu.blk_top + vi
                    if ridx < len(rows):
                        self._row(cv, th, lay, rows[ridx], vi, ridx == be.cur)
                cv.clip()
        else:
            for vi in range(lay.rows):
                ridx = bu.blk_top + vi
                if ridx >= len(rows):
                    break
                self._row(cv, th, lay, rows[ridx], vi, ridx == be.cur)
        self._foot(be, x, lay.bar_y, w)
        if bu.blk_menu is not None:
            self._menu(bu.blk_menu, w)
        ui.end()
        self._drawn = None if broke else (gen, be.cur, bu.blk_top, bu.blk_slot)

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
