# Map (grep -n a name to jump there):
#   SettingsPage          the Editor's Settings tab on the verbs
#   SettingsPage.draw     the cards as one group, then This project
#   SettingsPage.keys     a frame's keys: the engine's, then a picker's left and right
#   SettingsPage.point    a pointer sample, and a tap on a picker's tile
"""The Editor's Settings tab (E-06; docs/media/look_2026-10/EDITOR_LAYOUT.md's
`config` pictures): the cart's `edit` cards drawn on the public UI API, the
handheld's tab and the desk Editor's Settings pane alike.

The cards are one group: an `int` card a slider whose value stands at its
right, a two-way `choice` a toggle, a `sprite-tiles` choice a row of the
sprites to pick from (a part: left and right step it, a tap takes a tile),
any other choice a cycle. Then This project: Cart info and Files, the doors
the tab already had (`CardsLayer`'s dialog and its file list). A card's
label is its `card` text up to its value, as a kid reads it ("Run at").
A change is the config's and the undo history's, as a stepper's was
(`Project.record_config`)."""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui


def card_label(f):
    """A card's label: its `card` text before `{value}`, else its `label`,
    in sentence case and without a closing "is" ("RUN AT {value}" -> "Run
    at", "HERO IS {value}" -> "Hero")."""
    text = f.get("card") or f.get("label") or f.get("key") or ""
    cut = text.find("{")
    if cut >= 0:
        text = text[:cut]
    text = text.strip().rstrip(":=").strip()
    if text.lower().endswith(" is"):
        text = text[:-3]
    if not text:
        text = str(f.get("label") or f.get("key") or "")
    return text[:1].upper() + text[1:].lower()


class SettingsPage:
    """One Settings page over a workstation's open cart. `host` is the
    frame's `AppHost`; `doors` is (open the cart's info, open its files)."""

    def __init__(self, ws, host, doors):
        self.ws = ws
        self.host = host
        self._doors = doors
        self._tiles = {}                # a picker's key -> [(rect, choice index)]
        self._focus = None
        self._count = (None, 0)         # (the cart's path, its files)
        # the page's override table: its gen moves with what the page shows
        # (docs/scroll_and_text_2026-10.md §3.3), so a frame that only moves
        # the focus or the scroll draws the rows it changed
        self._o = {"gen": 0}
        self._shows = None

    # -- the data ---------------------------------------------------------------

    def _fields(self):
        ws = self.ws
        valid = ws.cards_layer._validate_field
        return [f for f in (ws.cart.get("edit") or []) if not valid(f)]

    def _get(self, f):
        return self.ws.project.config.get(f["key"], f.get("default"))

    def _set(self, f, v):
        ws = self.ws
        old = self._get(f)
        if v == old:
            return
        ws.project.config[f["key"]] = v
        ws.project.record_config(f["key"], old, v)
        ws._dirty = True

    # -- drawing -----------------------------------------------------------------

    def draw(self, cv, th, rect, desk):
        """The page in `rect` of `cv`, under theme `th`."""
        host = self.host
        host.begin(cv, th, rect, desk, False)
        fields = self._fields()
        cart = self.ws.cart
        shows = (id(cart), str(cart.get("title") or ""), self._file_count(),
                 tuple(self._get(f) for f in fields), len(fields))
        if shows != self._shows:
            self._shows = shows
            self._o["gen"] += 1
        ui.at(*rect)
        ui.page("", self._o)
        ui.group("", {"card": True})
        tap = None
        for f in fields:
            self._card(cv, th, f, desk, tap)
        ui.group()
        ui.group("This project")
        if ui.link("Cart info", str(cart.get("title") or "")):
            self._doors[0]()
        if ui.link("Files", str(self._file_count())):
            self._doors[1]()
        ui.group()
        if desk:
            ui.hints(("change", "Change", "F6", "Next pane"))
        else:
            ui.hints(("change", "Change"))
        ui.end()
        self._focus = ui.focus()

    def _file_count(self):
        """The project's files, counted once a cart (the list reads the
        store) and again after the list closes (`recount`)."""
        ws = self.ws
        path = ws.cart.get("path")
        if self._count[0] != path:
            n = 0
            try:
                kind = ws.carts_store.project_kind(path)
                n = len(ws._with_sd(lambda: ws.carts_store.list_files(kind, ws.carts_root)))
            except Exception:  # noqa: BLE001 -- an unreadable folder counts none
                pass
            self._count = (path, n)
        return self._count[1]

    def recount(self):
        self._count = (None, 0)

    def _card(self, cv, th, f, desk, tap):
        label = card_label(f)
        key = f["key"]
        cur = self._get(f)
        if f["type"] == "int":
            lo, hi = f.get("min", 0), f.get("max", 100)
            try:
                v = int(cur)
            except (TypeError, ValueError):
                v = lo
            got = ui.slider(label, v, lo, hi, {"id": key})
            step = max(1, int(f.get("step", 1)))
            if got != v:
                d = got - v
                if abs(d) < step:               # a key's or a nudge's step: the card's
                    got = v + (step if d > 0 else -step)
                else:
                    got = lo + ((got - lo + step // 2) // step) * step
                self._set(f, max(lo, min(hi, got)))
            return
        ch = f["choices"]
        idx = ch.index(cur) if cur in ch else 0
        if f.get("display") in ("sprite-tiles", "choice-icons", "bg-thumbs"):
            self._tiles_row(cv, th, f, label, idx, desk, tap)
            return
        if len(ch) == 2 and not f.get("names"):
            on = ui.toggle(label, idx == 1, {"id": key})
            if on != (idx == 1):
                self._set(f, ch[1 if on else 0])
            return
        names = [str(f.get("names", ch)[k]) if k < len(f.get("names", ch)) else str(c)
                 for k, c in enumerate(ch)]
        got = ui.cycle(label, names, idx, {"id": key})
        if got != idx:
            self._set(f, ch[got])

    def _tiles_row(self, cv, th, f, label, idx, desk, tap):
        """A picker of pictures (a sprite, a glyph, a background): the label,
        then each choice in a tile at the row's right, the current one on the
        current plate."""
        s = 2 if desk else 1
        side = 32 * s
        x, y, w, h, _st = ui.part(f["key"], 0, side + 12 * s)
        ui.at(x + 10 * s, y, w // 2, h)
        ui.label(label)
        n = len(f["choices"])
        gap = 8 * s
        tx = x + w - 10 * s - n * side - (n - 1) * gap
        rects = []
        for k in range(n):
            r = (tx + k * (side + gap), y + (h - side) // 2, side, side)
            rects.append((r, k))
            cv.rrect(r[0], r[1], side, side, 4 * s,
                     th["selection"] if k == idx else th["surface_alt"])
            self.ws.cards_layer.draw_choice(f, k, r)
        self._tiles[f["key"]] = rects
        if tap is not None:
            for r, k in rects:
                if r[0] <= tap[0] < r[0] + r[2] and r[1] <= tap[1] < r[1] + r[3]:
                    self._set(f, f["choices"][k])

    # -- input ---------------------------------------------------------------------

    def keys(self, i):
        """A frame's keys: the engine moves focus and steps the slider,
        toggle and cycle; left and right step a focused sprite picker.
        Whether the page owes a frame."""
        owed = self.host.keys(i)
        foc = self._focus
        if foc in self._tiles:
            d = i.steps("right") - i.steps("left")
            if d:
                for f in self._fields():
                    if f["key"] == foc:
                        ch = f["choices"]
                        cur = self._get(f)
                        k = ch.index(cur) if cur in ch else 0
                        self._set(f, ch[(k + d) % len(ch)])
                owed = True
        return owed

    def on_door(self):
        """Whether the focus is on Cart info or Files, which A opens."""
        return self._focus in ("Cart info", "Files")

    def open_door(self, i):
        """A on Cart info or Files with no move among this frame's keys:
        open it, as its link does, the page left as it was drawn. Whether
        it did."""
        if not (i.pressed("a") and self.on_door()):
            return False
        for n in ("up", "down", "left", "right", "b", "run"):
            if i.pressed(n):
                return False
        self._doors[0 if self._focus == "Cart info" else 1]()
        return True

    def point(self, px, py, click, down):
        """A pointer sample in the page's surface; a click on a picker's tile
        takes it."""
        if click:
            for key, rects in self._tiles.items():
                for r, k in rects:
                    if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                        for f in self._fields():
                            if f["key"] == key:
                                self._set(f, f["choices"][k])
        return self.host.point(px, py, click, down)
