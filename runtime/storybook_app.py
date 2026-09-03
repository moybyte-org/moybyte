"""Storybook -- decks of pages that COMPILE to a story cartridge (#78).

The Desk Lab thesis (the HyperCard/Flash ladder): the document IS the game.
A deck is pages of art + words; saving it generates a short, readable
`main.py` (tap to turn the page), so every story is a real cart -- it shows on
the launcher, runs under the Player on every tier, and "open as code" is just
the Editor's Code tab on it. Page art comes from Paint: USE MY PAINTING copies
the shared drawing into the story cart as `images/pgN.moyimg`, drawn by the
generated code through the existing `image()` verb (the My Art mechanism).

Graduation (the MakeCode rule, full #78 form): a story is a deck-authored
"origin" exactly like a block program is -- when the kid hand-edits `main.py`
past the deck's page/art/bg vocabulary (in the Editor's Code tab), the SAME
manifest `graduated` flag + undo-journal `grad` rider the block editor uses
(runtime/project.py's `_journal_code`/`_journal_code_toward`) flips true, and
undoing past that commit un-graduates it, exactly like a block cart. Storybook
itself just READS that persisted flag (`cart["graduated"]`) to decide
read-only, and -- as a bootstrap/fallback for a divergence that reached disk
some other way (a direct write, or a cart authored before this integration
existed) -- opportunistically detects + GRADUATES it into the real mechanism
right here on open (`_graduate_hand_edit`), so "leveled up to code" is never a
transient, un-persisted, un-undoable guess again.

Same app pattern as Paint/Appearance/Writer: a `.moy` cartridge identity
(`storybook.moy`) backed by this responsive system process."""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui


import json

try:
    from editors import CodeEditor
except ImportError:  # pragma: no cover - direct host import
    from runtime.editors import CodeEditor

try:
    from app_shell import ListShellLayout, ListShellApp
except ImportError:  # pragma: no cover - direct host import
    from runtime.app_shell import ListShellLayout, ListShellApp


MAX_PAGES = 16
MAX_TEXT_LINES = 4
MAX_LINE = 34
BGS = ("dark_blue", "indigo", "dark_purple", "black", "dark_green", "blue")
STORY_TYPE = "story"


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


class StorybookLayout(ListShellLayout):
    def __init__(self, w, h, fs=1, windowed=False):
        self._init_frame(w, h, fs, windowed)
        fs = self.fs
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
        # Row lists (shelf + pages), Writer-style (geometry: ListShellLayout).
        self._init_list(self.bar_h + self.band_h)
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

class StorybookAppLayer(ListShellApp):
    """Shelf of story carts -> a story's pages -> one page's words/art/bg."""

    id = "storybook"
    domain = "system"
    TITLE = "STORYBOOK"
    # The shipped identity (ListShellApp.is_app gates on these).
    APP_TITLE = "Storybook"
    APP_PERM = "storybook"
    APP_FOLDER = "storybook.moy"
    # The shell roles this app uses (runtime/app_context.py). Storybook is the
    # ONE shipped app that authors CARTS -- which is exactly why ctx.carts is a
    # role of its own and not folded into ctx.files (a story is executable
    # content; a drawing is not).
    NEEDS = ("surface", "theme", "damage", "carts", "nav", "artwork",
             "clipboard")

    def __init__(self, ctx, names, in_rect):
        self.ctx = ctx
        # Roles bound ONCE (the hoist mandate, ui_refactor_2026-08 Section 2.4).
        self._surf = ctx.surface
        self._theme = ctx.theme
        self._damage = ctx.damage
        self._store = ctx.carts    # ListShellApp's storage role: CARTS here
        self._nav = ctx.nav
        self._art = ctx.artwork
        self._clip = ctx.clipboard
        self.names = names
        self._in = in_rect
        cv = ctx.surface.canvas()
        self.layout = StorybookLayout(cv.w, cv.h, self._surf.font_scale(),
                                      self._surf.windowed())
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

    # -- store -----------------------------------------------------------------
    # (is_app / _store_ready / _load_json: ListShellApp)

    def _stories(self):
        return [c for c in self._store.all()
                if c.get("type") == STORY_TYPE and c.get("path")]

    def _load_deck(self, cart):
        # a bad deck opens as read-only code (guarded read: ListShellApp)
        data = self._load_json(self._store.load_deck(cart))
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
        if not self._store_ready():
            self.status = "CAN'T SAVE HERE"
            return
        cart = self.cart
        deck_blob = json.dumps(self.deck)

        def _write(c):
            c.save_deck(cart, deck_blob)
            return c.save_code(cart, src)

        _v, err = self._store.batch(_write)
        if err is not None:      # surface, never crash the shell
            self.status = ("CAN'T SAVE " + str(err))[:28]

    def commit(self):
        """The app-API hard-commit hook (docs/app_api_v1.md): the host calls it
        before routing a tap into this app's bar band -- the context-X there is
        an exit path and must never lose a story."""
        self._sync_editor()
        self._commit_deck()

    def close(self):
        """The app-API LEAVING hook (docs/app_api_v1.md): the host calls it when
        this app comes off the screen by ANY route. `_commit_deck` syncs the page
        editor itself and no-ops on a clean deck."""
        self._commit_deck()

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
        self.layout = StorybookLayout(w, h, fs, self._surf.windowed())
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
        self._damage.all()

    # -- story verbs ---------------------------------------------------------------

    def _new_story(self):
        if not self._store_ready():
            self.status = "CAN'T MAKE STORIES HERE"
            self._damage.all()
            return
        title = "Story " + str(len(self._stories()) + 1)
        deck = {"format": "moydeck-v1", "art_seq": 0,
                "pages": [{"bg": BGS[0], "art": None,
                           "text": ["Once upon a time..."]}]}
        src = deck_to_code(deck, title)
        deck_blob = json.dumps(deck)

        def _make(c):
            cart = c.create(title, src=src, type=STORY_TYPE)
            c.save_deck(cart, deck_blob)
            return cart, c.scan()

        got, err = self._store.batch(_make)
        if err is not None:
            self.status = ("NEW STORY FAILED " + str(err))[:28]
            self._damage.all()
            return
        cart, items = got
        self._store.apply(items)
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
            self.deck = {"format": "moydeck-v1", "pages": []}
            self.read_only = True
        else:
            self._store.hydrate(cart)
            if bool(cart.get("graduated")):
                self.read_only = True     # persisted (#78): already graduated
            else:
                src = cart.get("src") or ""
                title = cart.get("title") or "My Story"
                expected = deck_to_code(self.deck, title)
                if src != expected:
                    # Hand-edited past the deck's vocabulary. The Editor's Code
                    # tab is the normal trigger (runtime/project.py's
                    # _journal_code graduates it there, durably); this is the
                    # bootstrap path for a divergence that reached disk some
                    # other way -- fold it into the real mechanism NOW rather
                    # than just refusing locally.
                    self._graduate_hand_edit(cart, expected, src)
                self.read_only = bool(cart.get("graduated"))
        self.mode = "pages"
        self.sel = 0
        self.top = 0
        self.del_armed = False
        self._deck_dirty = False
        self.status = ("LEVELED UP TO CODE - EDIT IN MAKE" if self.read_only
                       else (cart.get("title") or "STORY"))
        self._damage.all()

    def _graduate_hand_edit(self, cart, baseline_src, diverged_src):
        """GRADUATE a story cart whose main.py diverged from what its deck would
        generate (#78 full graduated-manifest integration): journal the deck's
        own regenerated source as a grad=0 BASELINE (an undo restore point) then
        the diverged source as grad=1 -- the exact blocks-machinery shape
        (runtime/project.py's _journal_code_toward), so the SAME manifest flag +
        journal rider + undo/redo un-graduate mechanism covers a deck-authored
        story too (journal_append never touches the live main.py -- it only
        records history + flips the manifest, so this never clobbers the
        diverged code already on disk). Best-effort: a store/journal hiccup
        still marks the RAM copy graduated (never re-offers a clobbering SAVE
        this session), it just won't persist until the next successful write."""
        carts = self._store
        path = cart.get("path")
        if path and carts.ready() and carts.can_journal():
            mainf = cart.get("main", "main.py")

            def _write(c):
                c.journal_append(path, mainf, baseline_src, grad=0)
                c.journal_append(path, mainf, diverged_src, grad=1)

            _v, err = carts.batch(_write)
            if err is not None:  # never crash the shell over this
                print("Moybyte story graduation failed:", err)
        cart["graduated"] = True

    def _play_story(self):
        if self.cart is None:
            return
        self._commit_deck()
        # exit pops home (launcher root rule)
        self._nav.play(self.cart, self)

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
            self._damage.all()
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
                                 lay.cols, lay.rows,
                                 clip=self._clip)
        # Typing continues the page: caret at the END of the words, not (0,0).
        self.editor.row = len(self.editor.lines) - 1
        self.editor.col = len(self.editor.lines[self.editor.row])
        self.editor._scroll()
        self.mode = "page"
        self.del_armed = False
        self._ekey_prev = 0
        self.status = "PAGE " + str(i + 1)
        self._nav.text_mode(True)       # the page's words are typed
        self._damage.all()

    def _back_to_pages(self):
        self._sync_editor()
        self._commit_deck()
        self.editor = None
        self.sel = self.page_i + 1 if self.page_i >= 0 else 0
        self.page_i = -1
        self.mode = "pages"
        self.del_armed = False
        self.status = (self.cart.get("title") or "STORY") if self.cart else "STORY"
        self._nav.text_mode(False)
        self._damage.all()

    def _cycle_bg(self):
        if self.read_only or not (0 <= self.page_i < len(self._pages())):
            return
        p = self._pages()[self.page_i]
        cur = p.get("bg") or BGS[0]
        i = BGS.index(cur) if cur in BGS else 0
        p["bg"] = BGS[(i + 1) % len(BGS)]
        self._deck_dirty = True
        self._damage.all()

    def _attach_art(self):
        """USE MY PAINTING: copy the current shared Paint drawing onto this page
        (the Paint attach mechanism, one image per attach)."""
        if self.read_only or not (0 <= self.page_i < len(self._pages())):
            return
        art = self._art
        loaded = art.load() if art is not None else None
        if loaded is None:
            self.status = "PAINT SOMETHING FIRST"
            self._damage.all()
            return
        if not self._store_ready():
            self.status = "CAN'T SAVE HERE"
            self._damage.all()
            return
        w, h, idx = loaded
        w, h, idx = _fit_art(w, h, idx)
        seq = int(self.deck.get("art_seq") or 0) + 1
        self.deck["art_seq"] = seq
        name = "pg" + str(seq)
        blob = self._store.encode_image(w, h, idx)
        _v, err = self._store.save_image(self.cart, name, blob)
        if err is not None:
            self.status = ("ART FAILED " + str(err))[:28]
            self._damage.all()
            return
        self._pages()[self.page_i]["art"] = name
        self._deck_dirty = True
        self.status = "PAINTING ON PAGE " + str(self.page_i + 1)
        self._damage.all()

    def _clear_art(self):
        if self.read_only or not (0 <= self.page_i < len(self._pages())):
            return
        self._pages()[self.page_i]["art"] = None
        self._deck_dirty = True
        self._damage.all()

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
        self._nav.text_mode(False)
        self._damage.all()

    # -- input -------------------------------------------------------------------------

    def handle_input(self, inp):
        if self.mode == "page":
            self._typed_keys(inp)
            return True
        # shelf rows / page rows + the NEW row (nav + scroll window: ListShellApp)
        return self._list_nav(inp, (len(self._stories()) if self.mode == "shelf"
                                    else len(self._pages())) + 1)

    def _typed_keys(self, inp):
        ed = self.editor
        if ed is None or self.read_only:
            return
        k = self._edge_key(inp)
        if k:
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

    def handle_pointer(self, px, py, click):
        lay = self.layout
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
                self._damage.all()
            return True
        self.del_armed = False
        if (self.editor is not None and not self.read_only
                and self._in(px, py, lay.text_area)):
            self.editor.place((px - lay.tx) // lay.cell, (py - lay.ty) // lay.lh)
            self._damage.all()
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

    def draw(self, dt):
        cv = self._surf.canvas()
        lay = self.layout
        th = self._theme.colors()
        cv.cls(th["panel"])
        _ui.toolbar(cv, th, (0, lay.bar_h, lay.w, lay.band_h))
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

    def _page_label(self, p):
        text = (p.get("text") or [""])
        first = text[0] if text else ""
        tag = "PAGE + ART" if p.get("art") else "PAGE"
        return (tag + "  " + first)[:MAX_LINE + 8]

    def _draw_rows(self, cv, items, new_label, label_fn):
        lay = self.layout
        th = self._theme.colors()
        fs = lay.fs
        rows = [new_label] + list(items) if new_label is not None else list(items)
        for i in range(self.top, min(self.top + lay.list_rows, len(rows))):
            r = lay.row_rect(i - self.top)
            selected = i == self.sel
            edge = th["accent"] if selected else th["dim"]
            if new_label is not None and i == 0:
                # The + NEW row: a themed call-to-action field, black ink -- the
                # skin's "row_cta" kind since #207, so it restyles with the rest.
                label, kind, colors = new_label, "row_cta", None
            else:
                # A deck row: cream paper with black ink -- frozen OFF-token
                # pixels, which is exactly what ui.row's `colors` escape hatch
                # is for. The label is truncated HERE because this site's frozen
                # cap (`w // 8fs - 2`) is one column tighter than the toolkit's
                # pad-derived one at some widths.
                label = label_fn(rows[i])[:max(1, r[2] // (8 * fs) - 2)]
                kind, colors = "row", (7, 0, edge)
            _ui.row(cv, th, r, label, kind=kind, on=selected, colors=colors,
                    pad=6 * fs, fs=fs)

    def _draw_page(self, cv):
        lay = self.layout
        th = self._theme.colors()
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
