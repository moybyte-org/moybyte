"""Storybook -- decks of pages that COMPILE to a story cartridge (#78).

The Desk Lab thesis (the HyperCard/Flash ladder): the document IS the game.
A deck is pages of art + words; saving it generates a short, readable
`main.py` (tap to turn the page), so every story is a real cart -- it shows on
the launcher, runs under the Player on every tier, and "open as code" is just
the Editor's Code tab on it. Page art comes from Paint: USE MY PAINTING copies
the shared drawing into the story cart as `images/pgN.moyimg`, drawn by the
generated code through the existing `image()` verb (the My Art mechanism).

Graduation (the MakeCode rule, v1 form): the deck remembers a signature of the
code it last generated (`deck.json` "gen"). If the cart's `main.py` no longer
matches -- the kid hand-edited their story past the deck's vocabulary --
Storybook opens it READ-ONLY with a "leveled up to code" notice instead of
ever clobbering hand-written code. Full `graduated`-manifest integration with
the blocks machinery is follow-up (#78).

Same app pattern as Paint/Appearance/Writer: a `.moy` cartridge identity
(`storybook.moy`) backed by this responsive system process."""

import json

try:
    from editors import CodeEditor
except ImportError:  # pragma: no cover - direct host import
    from runtime.editors import CodeEditor


MAX_PAGES = 16
MAX_TEXT_LINES = 4
MAX_LINE = 34
BGS = ("dark_blue", "indigo", "dark_purple", "black", "dark_green", "blue")
STORY_TYPE = "story"


def _sig(src):
    """A tiny stable signature of generated code -- a guard, not security."""
    total = 0
    for b in src.encode("utf-8"):
        total = (total + b) % 1000003
    return str(len(src)) + ":" + str(total)


def deck_to_code(deck, title="My Story"):
    """Compile a deck to a short, readable story cart. This output is
    curriculum: a kid opening the Code tab should be able to READ it."""
    pages = list(deck.get("pages") or [])
    if not pages:
        pages = [{"bg": BGS[0], "art": None, "text": ["My story starts here."]}]
    out = []
    a = out.append
    a("# " + str(title) + " -- made with Storybook!")
    a("# Every page is art + words. Tap (or press A) to turn the page.")
    a("# This is real cart code: open the Code tab in Make and change it.")
    a("")
    a("PAGES = [")
    for p in pages:
        a("    {\"bg\": %r, \"art\": %r," % (p.get("bg") or BGS[0], p.get("art")))
        a("     \"text\": %r}," % ([str(t) for t in (p.get("text") or [])],))
    a("]")
    a("")
    a("page = 0")
    a("")
    a("")
    a("def _update(dt):")
    a("    global page")
    a("    tp = touch()")
    a("    if (tp is not None and tp[2]) or btnp(\"a\") or btnp(\"right\"):")
    a("        page = (page + 1) % len(PAGES)   # the last page wraps to the start")
    a("    if btnp(\"left\"):")
    a("        page = (page - 1) % len(PAGES)")
    a("")
    a("")
    a("def _draw():")
    a("    p = PAGES[page]")
    a("    cls(col(p[\"bg\"]))")
    a("    art = image(p[\"art\"]) if p[\"art\"] else None")
    a("    if art is not None:")
    a("        spr(art, 0, 0)")
    a("    y = H - 16 * len(p[\"text\"]) - 18")
    a("    for ln in p[\"text\"]:")
    a("        rect(6, y - 3, W - 12, 14, col(\"black\"))")
    a("        print(ln, 12, y, col(\"white\"))")
    a("        y += 16")
    a("    print(str(page + 1) + \"/\" + str(len(PAGES)), W - 36, H - 12,")
    a("          col(\"light_grey\"))")
    a("")
    return "\n".join(out)


def _fit_art(w, h, idx, max_w=320, max_h=240):
    """Nearest-neighbor shrink so a big Paint drawing (512x300 desktop doc)
    fits the 320x240 story canvas. Small art passes through untouched."""
    factor = 1
    while (w + factor - 1) // factor > max_w or (h + factor - 1) // factor > max_h:
        factor += 1
    if factor == 1:
        return w, h, idx
    nw = (w + factor - 1) // factor
    nh = (h + factor - 1) // factor
    out = bytearray(nw * nh)
    for y in range(nh):
        row = (y * factor) * w
        di = y * nw
        for x in range(nw):
            out[di + x] = idx[row + x * factor]
    return nw, nh, bytes(out)


class StorybookLayout:
    def __init__(self, w, h, fs=1, windowed=False):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(fs))
        fs = self.fs
        self.bar_h = 0 if windowed else 18 * fs
        self.band_h = 24 * fs
        x = 6 * fs
        y = self.bar_h + 3 * fs
        bh = self.band_h - 6 * fs
        # Band buttons (which show depends on the view).
        self.btn1 = (x, y, 58 * fs, bh)                      # SHELF / PAGES
        self.btn2 = (x + 62 * fs, y, 52 * fs, bh)            # PLAY / BG
        self.btn3 = (x + 118 * fs, y, 72 * fs, bh)           # MY ART
        self.btn4 = (x + 194 * fs, y, 66 * fs, bh)           # NO ART / TEAR OUT
        self.status_x = x
        # Row lists (shelf + pages), Writer-style.
        self.row_h = 20 * fs
        self.list_y = self.bar_h + self.band_h
        self.list_rows = max(1, (self.h - self.list_y - 2 * fs) // self.row_h)
        # Page view: the text box (the kid types the page's words here) above a
        # live-ish preview strip.
        self.cell = 8 * fs
        self.lh = 10 * fs
        self.tx = 8 * fs
        self.ty = self.bar_h + self.band_h * 2 + 3 * fs
        self.cols = min(MAX_LINE, max(1, (self.w - 2 * self.tx) // self.cell))
        self.rows = MAX_TEXT_LINES
        self.text_area = (self.tx - 2 * fs, self.ty - 2 * fs,
                          self.cols * self.cell + 4 * fs,
                          self.rows * self.lh + 4 * fs)
        pv_y = self.ty + self.rows * self.lh + 6 * fs
        self.preview = (self.tx - 2 * fs, pv_y,
                        self.w - 2 * (self.tx - 2 * fs),
                        max(20 * fs, self.h - pv_y - 4 * fs))

    def row_rect(self, i):
        return (4 * self.fs, self.list_y + i * self.row_h,
                self.w - 8 * self.fs, self.row_h - 2 * self.fs)


class StorybookAppLayer:
    """Shelf of story carts -> a story's pages -> one page's words/art/bg."""

    id = "storybook"
    domain = "system"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self.names = names
        self._in = in_rect
        self.layout = StorybookLayout(ws.sys_canvas.w, ws.sys_canvas.h,
                                      ws._effective_font_scale(),
                                      ws.windowed_chrome)
        self.mode = "shelf"           # shelf | pages | page
        self.cart = None              # the open story cart dict
        self.deck = None              # its parsed deck.json
        self.page_i = -1              # index of the page open in the editor
        self.sel = 0
        self.top = 0
        self.editor = None            # CodeEditor over the page's text lines
        self.status = "MY STORIES"
        self.read_only = False        # the kid graduated this story to code
        self.del_armed = False
        self._ekey_prev = 0
        self._deck_dirty = False      # unsaved deck edits (commit no-ops when clean)

    @staticmethod
    def is_app(cart):
        """True only for the shipped Storybook identity, not a copy."""
        if (not cart or cart.get("title") != "Storybook"
                or "storybook" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:
            return int(cart.get("version", 0)) >= 1
        return str(path).replace("\\", "/").rsplit("/", 1)[-1] == "storybook.moy"

    # -- store -----------------------------------------------------------------

    def _ready(self):
        ws = self.ws
        return bool(ws.carts_store is not None and ws.carts_root is not None
                    and ws.can_manage)

    def _stories(self):
        return [c for c in self.ws._all_carts
                if c.get("type") == STORY_TYPE and c.get("path")]

    def _load_deck(self, cart):
        try:
            blob = self.ws._with_sd(
                lambda: self.ws.carts_store.load_deck(cart))
            data = json.loads(blob) if blob else None
        except Exception:  # noqa: BLE001 -- a bad deck opens as read-only code
            data = None
        return data if isinstance(data, dict) else None

    def _commit_deck(self):
        """Persist deck.json AND regenerate the story's main.py (the compile
        step). No-op for read-only (graduated) stories and for a clean deck, so
        the exit paths can call it unconditionally."""
        if self.cart is None or self.deck is None or self.read_only:
            return
        if not self._deck_dirty:
            return
        self._deck_dirty = False
        self._sync_editor()
        src = deck_to_code(self.deck, self.cart.get("title") or "My Story")
        self.deck["gen"] = _sig(src)
        if not self._ready():
            self.status = "CAN'T SAVE HERE"
            return
        ws = self.ws
        try:
            def _write():
                ws.carts_store.save_deck(self.cart, json.dumps(self.deck))
                return ws.carts_store.save_code(self.cart, src)
            ws._with_sd(_write)
        except Exception as exc:  # noqa: BLE001 -- surface, never crash the shell
            self.status = ("SAVE FAILED " + str(exc))[:28]

    def _sync_editor(self):
        ed = self.editor
        if ed is None or self.deck is None:
            return
        if 0 <= self.page_i < len(self.deck["pages"]):
            lines = [ln[:MAX_LINE] for ln in ed.text().split("\n")
                     if ln.strip()][:MAX_TEXT_LINES]
            self.deck["pages"][self.page_i]["text"] = lines

    # -- lifecycle ---------------------------------------------------------------

    def relayout(self, w, h, fs):
        self.layout = StorybookLayout(w, h, fs, self.ws.windowed_chrome)
        if self.editor is not None:
            self.editor.set_view_size(self.layout.cols, self.layout.rows)

    def open(self):
        self.mode = "shelf"
        self.cart = None
        self.deck = None
        self.editor = None
        self.page_i = -1
        self.sel = 0
        self.top = 0
        self.del_armed = False
        self.read_only = False
        self._ekey_prev = 0
        self._deck_dirty = False
        self.status = "MY STORIES"
        self.ws._dirty = True

    # -- story verbs ---------------------------------------------------------------

    def _new_story(self):
        if not self._ready():
            self.status = "CAN'T MAKE STORIES HERE"
            self.ws._dirty = True
            return
        ws = self.ws
        title = "Story " + str(len(self._stories()) + 1)
        deck = {"format": "moydeck-v1", "art_seq": 0,
                "pages": [{"bg": BGS[0], "art": None,
                           "text": ["Once upon a time..."]}]}
        src = deck_to_code(deck, title)
        deck["gen"] = _sig(src)
        try:
            def _make():
                cart = ws.carts_store.create(title, ws.carts_root, src=src,
                                             type=STORY_TYPE)
                ws.carts_store.save_deck(cart, json.dumps(deck))
                return cart, ws.carts_store.scan(ws.carts_root)
            cart, items = ws._with_sd(_make)
            ws._apply_items(items)
        except Exception as exc:  # noqa: BLE001
            self.status = ("NEW STORY FAILED " + str(exc))[:28]
            self.ws._dirty = True
            return
        # Open the freshly created story (find its scanned twin by path).
        for c in self._stories():
            if c.get("path") == cart.get("path"):
                self._open_story(c)
                return
        self._open_story(cart)

    def _open_story(self, cart):
        self.cart = cart
        self.deck = self._load_deck(cart)
        self.read_only = False
        if self.deck is None:
            # No deck (hand-made "story" cart) -- treat as graduated code.
            self.deck = {"format": "moydeck-v1", "pages": [], "gen": ""}
            self.read_only = True
        else:
            self.ws._rehydrate_cart(cart)
            src = cart.get("src") or ""
            if _sig(src) != self.deck.get("gen"):
                self.read_only = True     # hand-edited past the deck: never clobber
        self.mode = "pages"
        self.sel = 0
        self.top = 0
        self.del_armed = False
        self._deck_dirty = False
        self.status = ("LEVELED UP TO CODE - EDIT IN MAKE" if self.read_only
                       else (cart.get("title") or "STORY"))
        self.ws._dirty = True

    def _play_story(self):
        if self.cart is None:
            return
        self._commit_deck()
        ws = self.ws
        ws._open_workspace(self.cart)
        ws.run(ws.project, self)          # exit pops home (launcher root rule)

    def _back_to_shelf(self):
        self._commit_deck()
        self.open()

    # -- page verbs ------------------------------------------------------------------

    def _pages(self):
        return self.deck["pages"] if self.deck else []

    def _new_page(self):
        if self.read_only:
            return
        pages = self._pages()
        if len(pages) >= MAX_PAGES:
            self.status = "STORY FULL"
            self.ws._dirty = True
            return
        pages.append({"bg": BGS[0], "art": None, "text": []})
        self._deck_dirty = True
        self._open_page(len(pages) - 1)

    def _open_page(self, i):
        pages = self._pages()
        if not (0 <= i < len(pages)):
            return
        self._sync_editor()
        self.page_i = i
        lay = self.layout
        self.editor = CodeEditor("\n".join(pages[i].get("text") or []),
                                 lay.cols, lay.rows)
        # Typing continues the page: caret at the END of the words, not (0,0).
        self.editor.row = len(self.editor.lines) - 1
        self.editor.col = len(self.editor.lines[self.editor.row])
        self.editor._scroll()
        self.mode = "page"
        self.del_armed = False
        self._ekey_prev = 0
        self.status = "PAGE " + str(i + 1)
        self.ws._set_text_mode(True)       # the page's words are typed
        self.ws._dirty = True

    def _back_to_pages(self):
        self._sync_editor()
        self._commit_deck()
        self.editor = None
        self.sel = self.page_i + 1 if self.page_i >= 0 else 0
        self.page_i = -1
        self.mode = "pages"
        self.del_armed = False
        self.status = (self.cart.get("title") or "STORY") if self.cart else "STORY"
        self.ws._set_text_mode(False)
        self.ws._dirty = True

    def _cycle_bg(self):
        if self.read_only or not (0 <= self.page_i < len(self._pages())):
            return
        p = self._pages()[self.page_i]
        cur = p.get("bg") or BGS[0]
        i = BGS.index(cur) if cur in BGS else 0
        p["bg"] = BGS[(i + 1) % len(BGS)]
        self._deck_dirty = True
        self.ws._dirty = True

    def _attach_art(self):
        """USE MY PAINTING: copy the current shared Paint drawing onto this page
        (the Paint attach mechanism, one image per attach)."""
        if self.read_only or not (0 <= self.page_i < len(self._pages())):
            return
        art = getattr(self.ws, "artwork", None)
        loaded = art.load() if art is not None else None
        if loaded is None:
            self.status = "PAINT SOMETHING FIRST"
            self.ws._dirty = True
            return
        if not self._ready():
            self.status = "CAN'T SAVE HERE"
            self.ws._dirty = True
            return
        w, h, idx = loaded
        w, h, idx = _fit_art(w, h, idx)
        seq = int(self.deck.get("art_seq") or 0) + 1
        self.deck["art_seq"] = seq
        name = "pg" + str(seq)
        ws = self.ws
        try:
            blob = ws.carts_store.encode_moyimg(w, h, idx)
            ws._with_sd(lambda: ws.carts_store.save_image(self.cart, name, blob))
        except Exception as exc:  # noqa: BLE001
            self.status = ("ART FAILED " + str(exc))[:28]
            self.ws._dirty = True
            return
        self._pages()[self.page_i]["art"] = name
        self._deck_dirty = True
        self.status = "PAINTING ON PAGE " + str(self.page_i + 1)
        self.ws._dirty = True

    def _clear_art(self):
        if self.read_only or not (0 <= self.page_i < len(self._pages())):
            return
        self._pages()[self.page_i]["art"] = None
        self._deck_dirty = True
        self.ws._dirty = True

    def _delete_page(self):
        pages = self._pages()
        if self.read_only or not (0 <= self.page_i < len(pages)):
            return
        del pages[self.page_i]
        self.editor = None
        self.page_i = -1
        self.mode = "pages"
        self.sel = 0
        self.del_armed = False
        self.status = "PAGE TORN OUT"
        self._deck_dirty = True
        self._commit_deck()
        self.ws._set_text_mode(False)
        self.ws._dirty = True

    # -- input -------------------------------------------------------------------------

    def handle_input(self, inp):
        if self.mode == "page":
            self._typed_keys(inp)
            return True
        count = (len(self._stories()) if self.mode == "shelf"
                 else len(self._pages())) + 1
        if inp.pressed("up"):
            self.sel = (self.sel - 1) % count
            self._scroll_list()
        elif inp.pressed("down"):
            self.sel = (self.sel + 1) % count
            self._scroll_list()
        elif inp.pressed("a"):
            self._tap_row(self.sel)
        return True

    def _scroll_list(self):
        rows = self.layout.list_rows
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + rows:
            self.top = self.sel - rows + 1

    def _typed_keys(self, inp):
        ed = self.editor
        if ed is None or self.read_only:
            return
        k = inp.last_key
        if k and k != self._ekey_prev:
            block = False
            if k in (0x0D, 0x0A) and len(ed.lines) >= MAX_TEXT_LINES:
                block = True                       # a page holds 4 lines of words
            if 0x20 <= k <= 0x7E and len(ed.lines[ed.row]) >= MAX_LINE:
                block = True                       # keep lines on the page
            if block:
                self.status = "PAGE FULL"
            elif ed.key(k):
                self._deck_dirty = True
                self.status = "PAGE " + str(self.page_i + 1)
        self._ekey_prev = k

    def handle_pointer(self, px, py, click):
        ws = self.ws
        lay = self.layout
        if click and not ws.windowed_chrome and py < lay.bar_h:
            self._sync_editor()
            self._commit_deck()                   # the X must never lose a story
            return bool(ws.bar_layer.handle_bar_tap("tool", px, py))
        if not click:
            return True
        if self.mode == "shelf":
            for i in range(self.top, min(self.top + lay.list_rows,
                                         len(self._stories()) + 1)):
                if self._in(px, py, lay.row_rect(i - self.top)):
                    self.sel = i
                    self._tap_row(i)
                    return True
            return True
        if self.mode == "pages":
            if self._in(px, py, lay.btn1):
                self._back_to_shelf()
                return True
            if self._in(px, py, lay.btn2):
                self._play_story()
                return True
            for i in range(self.top, min(self.top + lay.list_rows,
                                         len(self._pages()) + 1)):
                if self._in(px, py, lay.row_rect(i - self.top)):
                    self.sel = i
                    self._tap_row(i)
                    return True
            return True
        # -- page view -------------------------------------------------------------
        if self._in(px, py, lay.btn1):
            self._back_to_pages()
            return True
        if self._in(px, py, lay.btn2):
            self._cycle_bg()
            return True
        if self._in(px, py, lay.btn3):
            self._attach_art()
            return True
        if self._in(px, py, lay.btn4):
            if self.del_armed:
                self._delete_page()
            else:
                self.del_armed = True
                self.status = "TAP AGAIN TO TEAR OUT"
                self.ws._dirty = True
            return True
        self.del_armed = False
        if (self.editor is not None and not self.read_only
                and self._in(px, py, lay.text_area)):
            self.editor.place((px - lay.tx) // lay.cell, (py - lay.ty) // lay.lh)
            self.ws._dirty = True
        return True

    def _tap_row(self, i):
        if self.mode == "shelf":
            if i == 0:
                self._new_story()
            else:
                stories = self._stories()
                if 0 <= i - 1 < len(stories):
                    self._open_story(stories[i - 1])
        else:
            if self.read_only:
                return
            if i == 0:
                self._new_page()
            else:
                self._open_page(i - 1)

    # -- draw -------------------------------------------------------------------------

    def _button(self, cv, label, r, hot=False):
        th = self.ws.theme_colors
        fs = self.layout.fs
        bg = self.names["red"] if hot else th["panel"]
        fg = self.names["white"] if hot else th["title_ink"]
        cv.rect(r[0], r[1], r[2], r[3], bg)
        cv.rectb(r[0], r[1], r[2], r[3], th["dim"])
        cv.print(label, r[0] + max(2, (r[2] - len(label) * 8 * fs) // 2),
                 r[1] + max(1, (r[3] - 8 * fs) // 2), fg, 1)

    def draw(self, dt):
        cv = self.ws.sys_canvas
        lay = self.layout
        th = self.ws.theme_colors
        cv.cls(th["panel"])
        cv.rect(0, lay.bar_h, lay.w, lay.band_h, th["title"])
        fs = lay.fs
        if self.mode == "shelf":
            cv.print(self.status[:max(1, lay.w // (8 * fs) - 2)],
                     lay.status_x, lay.bar_h + 8 * fs, th["title_ink"], 1)
            self._draw_rows(cv, self._stories(), "+ NEW STORY",
                            lambda c: (c.get("title") or "STORY").upper())
        elif self.mode == "pages":
            self._button(cv, "SHELF", lay.btn1)
            if not self.read_only:
                self._button(cv, "PLAY", lay.btn2)
            cv.print(self.status[:max(1, (lay.w - lay.btn3[0]) // (8 * fs))],
                     lay.btn3[0], lay.bar_h + 8 * fs, th["title_ink"], 1)
            self._draw_rows(cv, self._pages(),
                            None if self.read_only else "+ NEW PAGE",
                            self._page_label)
        else:
            self._button(cv, "PAGES", lay.btn1)
            self._button(cv, "BG", lay.btn2)
            self._button(cv, "MY ART", lay.btn3)
            self._button(cv, "TEAR OUT", lay.btn4, hot=self.del_armed)
            self._draw_page(cv)
        if not self.ws.windowed_chrome:
            self.ws.bar_layer._draw_status_strip("tool")

    def _page_label(self, p):
        text = (p.get("text") or [""])
        first = text[0] if text else ""
        tag = "PAGE + ART" if p.get("art") else "PAGE"
        return (tag + "  " + first)[:MAX_LINE + 8]

    def _draw_rows(self, cv, items, new_label, label_fn):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        rows = [new_label] + list(items) if new_label is not None else list(items)
        for i in range(self.top, min(self.top + lay.list_rows, len(rows))):
            x, y, w, h = lay.row_rect(i - self.top)
            selected = i == self.sel
            item = rows[i]
            if new_label is not None and i == 0:
                cv.rect(x, y, w, h, th["accent"] if selected else th["hilite"])
                cv.print(new_label, x + 6 * fs, y + (h - 8 * fs) // 2,
                         self.names["black"], 1)
            else:
                cv.rect(x, y, w, h, 7)
                cv.print(label_fn(item)[:max(1, w // (8 * fs) - 2)],
                         x + 6 * fs, y + (h - 8 * fs) // 2, 0, 1)
            cv.rectb(x, y, w, h, th["accent"] if selected else th["dim"])

    def _draw_page(self, cv):
        lay = self.layout
        th = self.ws.theme_colors
        fs = lay.fs
        # Words box (the typed text), then a page preview strip below.
        ax, ay, aw, ah = lay.text_area
        cv.rect(ax, ay, aw, ah, 7)
        cv.rectb(ax, ay, aw, ah, th["dim"])
        ed = self.editor
        if ed is not None:
            for r, line in enumerate(ed.visible_lines()):
                cv.print(line[ed.left:ed.left + lay.cols], lay.tx,
                         lay.ty + r * lay.lh, 0, 1)
            crow = ed.row - ed.top
            ccol = ed.col - ed.left
            if not self.read_only and 0 <= crow < lay.rows and 0 <= ccol <= lay.cols:
                cv.rect(lay.tx + ccol * lay.cell,
                        lay.ty + crow * lay.lh + 8 * fs,
                        lay.cell, max(1, 2 * fs), self.names["blue"])
        # Preview: the page's bg color + art tag + its words, story-style.
        px_, py_, pw, ph = lay.preview
        pages = self._pages()
        p = pages[self.page_i] if 0 <= self.page_i < len(pages) else None
        bg = self.names.get(p.get("bg") or BGS[0], 1) if p else 1
        cv.rect(px_, py_, pw, ph, bg)
        cv.rectb(px_, py_, pw, ph, th["dim"])
        if p is None:
            return
        if p.get("art"):
            cv.print("ART: " + p["art"], px_ + 4 * fs, py_ + 3 * fs,
                     self.names["light_grey"], 1)
        if ed is not None:
            y = py_ + ph - 12 * fs * max(1, len(ed.lines))
            for ln in ed.lines[:MAX_TEXT_LINES]:
                if ln.strip():
                    cv.rect(px_ + 2 * fs, y - 2 * fs, pw - 4 * fs, 11 * fs,
                            self.names["black"])
                    cv.print(ln[:max(1, pw // (8 * fs) - 2)], px_ + 6 * fs, y,
                             self.names["white"], 1)
                y += 12 * fs
