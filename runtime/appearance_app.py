"""Responsive visual wallpaper + theme picker system app.

A nod to the classic Display Properties dialog, simplified to the moybyte
vocabulary and arranged side-by-side (the owner's space verdict): the
selectable catalog is a LEFT column, and the RIGHT pane previews the choice --
the IMAGES/CARTS tabs draw a little MONITOR whose screen shows the wallpaper
IN FULL (the ONLY place it renders; the app sits on a flat panel, never the
live backdrop), and the THEMES tab draws how moybyte windows will LOOK (an
inactive strip behind an active window, real WM token roles). One layout,
every tier -- the monitor scales from the 320x240 shelf to the desktop.
"""

try:
    from chrome import THEMES, THEME_VARIANTS, theme_colors
except ImportError:  # pragma: no cover - direct host import
    from runtime.chrome import THEMES, THEME_VARIANTS, theme_colors

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui


class AppearanceLayout:
    # Min-size convention (ui.py): the WM clamps window resizes to these
    # (fs-scaled) -- the visual rails/catalog need the room at font scale 2.
    MIN_W = 310
    MIN_H = 230

    def __init__(self, w, h, fs=1, windowed=False):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(fs))
        fs = self.fs
        self.bar_h = 0 if windowed else 18 * fs
        self.tabs_h = 30 * fs
        # Side-by-side: the catalog COLUMN on the left, the preview pane on the
        # right. `wide` only picks the chunkier paddings/bezels of big surfaces.
        self.wide = self.w >= 420 * fs and self.h >= 240 * fs
        band_y = self.bar_h + self.tabs_h
        band_h = max(1, self.h - band_y)
        self.catalog_w = max(150 * fs, self.w * 2 // 5)
        self.catalog = (0, band_y, self.catalog_w, band_h)
        self.preview = (self.catalog_w, band_y,
                        max(1, self.w - self.catalog_w), band_h)
        labels = ("IMAGES", "CARTS", "THEMES")
        widths = (70, 62, 70)
        self.tabs = []
        x = 6 * fs
        for i in range(3):
            bw = widths[i] * fs
            self.tabs.append((x, self.bar_h + 3 * fs, bw, self.tabs_h - 6 * fs))
            x += bw + 4 * fs
        # Preview-pane geometry. `field` = the inset desk area the THEMES mock
        # windows draw over; `screen`/`bezel`/`stand_h` = the monitor. The
        # screen SNAPS to clean half-frame multiples (160x120 * k) when it can,
        # else to the exact 4:3 fit -- either way a cart frame fills it edge to
        # edge (the fit blit resamples below native size).
        cap = 18 * fs                       # the status caption strip at pane bottom
        px_, py_, pw_, ph_ = self.preview
        pad = 8 * fs if self.wide else 4 * fs
        self.field = (px_ + pad, py_ + pad // 2,
                      pw_ - pad * 2, max(1, ph_ - cap - pad))
        self.bezel = 6 * fs if self.wide else 4 * fs
        self.stand_h = 10 * fs if self.wide else 8 * fs
        avail_h = ph_ - cap - self.stand_h - 2 * pad - 2 * self.bezel
        avail_w = pw_ - 2 * pad - 2 * self.bezel
        if avail_h >= 24 * fs and avail_w >= 32 * fs:
            k = min(avail_w // 160, avail_h // 120)
            if k >= 1:
                sw, sh = 160 * k, 120 * k      # clean half-frame multiple
            else:
                sh = max(24, min(avail_h, avail_w * 3 // 4))
                sh -= sh % 3                   # exact 4:3 (the game canvas aspect)
                sw = sh * 4 // 3
            block_h = sh + 2 * self.bezel + self.stand_h
            sx = px_ + (pw_ - sw) // 2
            sy = py_ + max(0, (ph_ - cap - block_h) // 2) + self.bezel
            self.screen = (sx, sy, sw, sh)
        else:
            self.screen = None              # no room for a monitor: caption only

    # A card is a 4:3 preview (the game-canvas aspect every wallpaper draws at)
    # plus the 17px label strip _draw_wall_card paints along its bottom.
    LABEL_H = 17
    CARD_PAD = 3

    def card_ideal_h(self, card_w):
        """The height that shows a card's preview at its native 4:3 without
        letterboxing: the image box is (card_w - 2*pad) wide."""
        fs = self.fs
        img_w = max(1, card_w - 2 * self.CARD_PAD * fs)
        return img_w * 3 // 4 + (self.LABEL_H + self.CARD_PAD) * fs

    def cards(self, count):
        fs = self.fs
        x, y, w, h = self.catalog
        gap = 6 * fs
        min_w = 116 * fs
        cols = max(1, min(count if count else 1, (w - gap) // (min_w + gap)))
        card_w = (w - gap * (cols + 1)) // cols
        rows = max(1, (count + cols - 1) // cols)
        # Divide the available band exactly. On the 320x240 tier five themes use
        # three compact rows; a fixed minimum would push the last row off-screen.
        card_h = max(1, (h - gap * (rows + 1)) // rows)
        # ...but NEVER stretch a card past its 4:3 preview (owner screenshot
        # 2026-07-31: at 1024x600 / font 1 the band split into two rows of
        # 276px-tall cells holding 4:3 art -- tall, empty, and the previews
        # overflowed their boxes). Dividing the band exactly is only right when
        # the rows would otherwise OVERFLOW it; when there is spare room the
        # cards keep their natural proportion and the column simply ends early.
        # The cap only ever SHRINKS a card, so a band too small for `rows` still
        # divides exactly (the 320x240 tier's three compact rows are unchanged);
        # spare height just leaves the column ending early, which is what the
        # host tier already looked like.
        card_h = min(card_h, self.card_ideal_h(card_w))
        out = []
        for i in range(count):
            out.append((x + gap + (i % cols) * (card_w + gap),
                        y + gap + (i // cols) * (card_h + gap),
                        card_w, card_h))
        return out


class AppearanceAppLayer:
    """Display-Properties-style picker: monitor/window-mock preview on top,
    visual Images/Carts/Themes catalogs below."""

    id = "appearance"
    domain = "system"
    TITLE = "APPEARANCE"
    MODES = ("images", "carts", "themes")

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self.names = names
        self._in = in_rect
        self.layout = AppearanceLayout(ws.sys_canvas.w, ws.sys_canvas.h,
                                       ws._effective_font_scale(), ws.windowed_chrome)
        self.mode = "images"
        self.sel = 0
        self.status = "IMAGE WALLPAPERS"

    @staticmethod
    def is_app(cart):
        if (not cart or cart.get("title") != "Appearance"
                or "appearance" not in (cart.get("permissions") or ())):
            return False
        path = cart.get("path")
        if not path:
            return int(cart.get("version", 0)) >= 1
        # Two folder names claim the app: the host store copies the SOURCE
        # folder (theme_picker.moy), but the device's seed_builtins names the
        # seeded folder from the TITLE slug (appearance.moy) -- the mismatch
        # left every device's Appearance cart unclaimed, so Settings ->
        # APPEARANCE silently did nothing (on-glass P4, 2026-07-25). Pinned by
        # tests/test_device_seed_parity.py's app-identity parity test.
        base = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        return base in ("theme_picker.moy", "appearance.moy")

    def relayout(self, w, h, fs):
        self.layout = AppearanceLayout(w, h, fs, self.ws.windowed_chrome)

    def open(self):
        # Land on the current wallpaper's source category. Solid fills live on
        # the IMAGES tab beside My Art.
        wp = self.ws.wallpaper_id
        cart = self.ws._wp_cart_by_id(wp)
        my_art = cart is not None and cart.get("title") == "My Art"
        fill = isinstance(wp, str) and wp.startswith("fill:")
        self.mode = "images" if (my_art or fill) else "carts"
        self.sel = self._selected_index()
        self.status = self.mode.upper()
        self.ws._dirty = True

    def _image_items(self):
        # My Art (the paint document) + the built-in solid fills ("fill:<color>"
        # id strings) -- everything that isn't a live wallpaper CART.
        wall = next((c for c in self.ws.wallpaper_carts()
                     if c.get("title") == "My Art"), None)
        items = [wall] if wall is not None else []
        items.extend(self.ws._FILL_WALLPAPERS)
        return items

    def _cart_items(self):
        return [c for c in self.ws.wallpaper_carts() if c.get("title") != "My Art"]

    def _items(self):
        if self.mode == "images":
            return self._image_items()
        if self.mode == "carts":
            return self._cart_items()
        return list(THEMES)

    def _wall_id(self, item):
        """The selectable wallpaper id: a fill item IS its id string."""
        return item if isinstance(item, str) else self.ws._wp_id_for(item)

    def _selected_index(self):
        items = self._items()
        if self.mode == "themes":
            for i, item in enumerate(items):
                if item[0] == self.ws.theme_name:
                    return i
            return 0
        for i, item in enumerate(items):
            if self._wall_id(item) == self.ws.wallpaper_id:
                return i
        return 0

    def _set_mode(self, mode):
        self.mode = mode
        self.sel = self._selected_index()
        self.status = mode.upper()
        self.ws._dirty = True

    def _apply(self, index):
        items = self._items()
        if not items:
            return
        self.sel = max(0, min(int(index), len(items) - 1))
        item = items[self.sel]
        if self.mode == "themes":
            self.ws.set_theme(item[0], persist=True)
            self.status = (item[0] + " " + self.ws.theme_variant).upper()
        else:
            self.ws.select_wallpaper(self._wall_id(item), persist=True)
            self.status = self._wall_title(item).upper()
        self.ws._dirty = True

    @staticmethod
    def _wall_title(item):
        if isinstance(item, str):              # "fill:dark_blue" -> "Dark Blue"
            return " ".join(p[:1].upper() + p[1:]
                            for p in item[5:].split("_") if p)
        return item.get("title", "WALLPAPER")

    def handle_input(self, inp):
        # The catalog is a vertical column now: up/down walks (and applies) the
        # list, left/right steps the horizontal tab row.
        items = self._items()
        if inp.pressed("up") and items:
            self._apply((self.sel - 1) % len(items))
        elif inp.pressed("down") and items:
            self._apply((self.sel + 1) % len(items))
        elif inp.pressed("left"):
            self._set_mode(self.MODES[(self.MODES.index(self.mode) - 1) % 3])
        elif inp.pressed("right"):
            self._set_mode(self.MODES[(self.MODES.index(self.mode) + 1) % 3])
        return True

    def _variant_chip_rects(self):
        """The THEMES tab's DARK/LIGHT toggle chips, along the top of the
        preview field (same rects for draw and hit-test)."""
        fs = self.layout.fs
        x, y, w, _h = self.layout.field
        cw, ch = 42 * fs, 13 * fs
        out = []
        cx = x + w - (cw + 3 * fs) * len(THEME_VARIANTS)
        for v in THEME_VARIANTS:
            out.append((v, (cx, y + 2 * fs, cw, ch)))
            cx += cw + 3 * fs
        return out

    def _set_variant(self, variant):
        self.ws.set_theme_variant(variant, persist=True)
        self.status = (self.ws.theme_name + " " + variant).upper()
        self.ws._dirty = True

    def handle_pointer(self, px, py, click):
        lay = self.layout
        if not click:
            return True
        for i, r in enumerate(lay.tabs):
            if self._in(px, py, r):
                self._set_mode(self.MODES[i])
                return True
        if self.mode == "themes":
            for v, r in self._variant_chip_rects():
                if self._in(px, py, r):
                    self._set_variant(v)
                    return True
        for i, r in enumerate(lay.cards(len(self._items()))):
            if self._in(px, py, r):
                self._apply(i)
                return True
        return True

    def _button(self, cv, label, r, on=False):
        # One shared implementation now (ui.chip) -- pixel-identical delegate.
        _ui.chip(cv, self.ws.theme_colors, r, label, on=on, fs=self.layout.fs)

    # -- drawing --------------------------------------------------------------

    def draw(self, dt):
        cv = self.ws.sys_canvas
        lay = self.layout
        th = self.ws.theme_colors
        # A flat panel field -- the wallpaper renders ONLY inside the monitor's
        # screen (owner call: never doubled as a full-bleed backdrop here).
        cv.rect(0, lay.bar_h, lay.w, lay.h - lay.bar_h, th["panel"])
        cv.rect(0, lay.bar_h, lay.w, lay.tabs_h, 48)
        for i, r in enumerate(lay.tabs):
            self._button(cv, ("IMAGES", "CARTS", "THEMES")[i], r,
                         self.mode == self.MODES[i])

        if self.mode == "themes":
            fs = lay.fs
            fx, fy, fw, fh = lay.field
            band = 17 * fs
            for v, r in self._variant_chip_rects():
                self._button(cv, v.upper(), r, self.ws.theme_variant == v)
            self._draw_theme_preview(cv, (fx, fy + band, fw, max(1, fh - band)))
        elif lay.screen is not None:
            self._draw_monitor(cv, dt)

        # A quiet preview caption along the pane's bottom.
        px, py, pw, ph = lay.preview
        label = self.status[:max(1, pw // (8 * lay.fs) - 2)]
        cv.rect(px, py + ph - 18 * lay.fs, pw, 18 * lay.fs, self.names["black"])
        cv.print(label, px + 8 * lay.fs, py + ph - 13 * lay.fs,
                 self.names["white"], 1)

        cx, cy, cw, ch = lay.catalog
        cv.rect(cx, cy, cw, ch, th["panel"])
        cv.rect(cx + cw - 2 * lay.fs, cy, 2 * lay.fs, ch, th["edge"])
        items = self._items()
        cards = lay.cards(len(items))
        for i, r in enumerate(cards):
            if self.mode == "themes":
                self._draw_theme_card(cv, r, items[i], i == self.sel)
            else:
                self._draw_wall_card(cv, r, items[i], i == self.sel,
                                     self.mode == "images")

    def _draw_monitor(self, cv, dt):
        """The Background-tab nod: a little monitor whose 4:3 screen shows the
        FULL wallpaper -- a cart frame fills it edge to edge, My Art letterboxes
        to its own aspect, a fill floods it."""
        lay = self.layout
        n = self.names
        fs = lay.fs
        sx, sy, sw, sh = lay.screen
        bz = lay.bezel
        mx, my = sx - bz, sy - bz
        mw, mh = sw + 2 * bz, sh + 2 * bz
        cv.rect(mx, my, mw, mh, n["light_grey"])              # the case
        cv.rectb(mx, my, mw, mh, n["dark_grey"])
        cv.rectb(sx - 1, sy - 1, sw + 2, sh + 2, n["dark_grey"])  # screen inset
        self.ws.wallpaper.draw_preview(cv, lay.screen, dt)
        # power LED on the bezel's bottom-right
        cv.rect(mx + mw - 5 * fs, my + mh - bz + bz // 2 - fs, 2 * fs, 2 * fs,
                n["green"])
        # stand: neck + base
        neck_w = max(4 * fs, mw // 6)
        base_w = max(neck_w, mw // 2)
        neck_h = lay.stand_h // 2
        cv.rect(mx + (mw - neck_w) // 2, my + mh, neck_w, neck_h, n["light_grey"])
        cv.rect(mx + (mw - base_w) // 2, my + mh + neck_h, base_w,
                lay.stand_h - neck_h, n["light_grey"])
        cv.rect(mx + (mw - base_w) // 2, my + mh + lay.stand_h - fs, base_w, fs,
                n["dark_grey"])

    def _draw_theme_preview(self, cv, field):
        """The Appearance-tab nod: how moybyte windows will look in the chosen
        theme -- an inactive window behind, the active one in front, over the
        theme's desk field. Token roles mirror the windowed WM's strip drawing
        (focused = title/title_ink, unfocused = panel + light-grey ink)."""
        th = self.ws.theme_colors
        n = self.names
        fs = self.layout.fs
        x, y, w, h = field
        # The desk field behind the mock windows: the theme's desktop token, but
        # when that resolves to the window panel tone (most themes alias it),
        # drop to "dim" so the windows actually stand out against the desk.
        bg = th.get("desktop", th["panel"])
        if bg == th["panel"]:
            bg = th["dim"]
        cv.rect(x, y, w, h, bg)
        cv.rectb(x, y, w, h, th["edge"])
        strip = 10 * fs
        gap_x, gap_y = w // 12, h // 10
        ww = w - w // 3
        wh = h - h // 3
        # inactive window (back, up-left)
        ix, iy = x + gap_x, y + gap_y
        cv.rect(ix, iy, ww, wh, th["panel"])
        cv.rectb(ix, iy, ww, wh, th["dim"])
        cv.rect(ix + 1, iy + 1, ww - 2, strip, th["panel"])
        cv.rect(ix + 1, iy + strip, ww - 2, 1, th["dim"])
        if ww >= 70 * fs:
            cv.print("WINDOW", ix + 3 * fs, iy + 2 * fs, th["ink_dim"], 1)
        # active window (front, down-right)
        ax, ay = x + w - gap_x - ww, y + h - gap_y - wh
        cv.rect(ax, ay, ww, wh, th["panel"])
        cv.rectb(ax, ay, ww, wh, th["edge"])
        cv.rect(ax + 1, ay + 1, ww - 2, strip, th["title"])
        if ww >= 70 * fs:
            cv.print("MOYBYTE", ax + 3 * fs, ay + 2 * fs, th["title_ink"], 1)
        self.ws._glyph("close", (ax + ww - strip, ay + 1, strip - 1, strip - 1),
                       th["title_ink"], cv)
        # body: a text line, a selected row, an accent button
        body_y = ay + strip + 2 * fs
        body_h = wh - strip - 3 * fs
        if body_h >= 22 * fs:
            cv.print("AA", ax + 4 * fs, body_y + fs, th["ink"], 1)
            cv.rect(ax + 3 * fs, body_y + 10 * fs, ww - 6 * fs, 9 * fs,
                    th["hilite"])
            cv.print("PICK", ax + 5 * fs, body_y + 11 * fs, th["ink"], 1)
        bw_, bh_ = 26 * fs, 11 * fs
        if body_h >= 36 * fs:
            cv.rect(ax + ww - bw_ - 4 * fs, ay + wh - bh_ - 3 * fs, bw_, bh_,
                    th["accent"])
            cv.print("OK", ax + ww - bw_ + 1 * fs, ay + wh - bh_ - 1 * fs,
                     n["black"], 1)

    def _draw_wall_card(self, cv, r, cart, selected, image_kind):
        x, y, w, h = r
        th = self.ws.theme_colors
        pad = 3 * self.layout.fs
        ix, iy, iw, ih = x + pad, y + pad, w - pad * 2, max(12, h - 20 * self.layout.fs)
        title = self._wall_title(cart)
        if isinstance(cart, str):              # solid fill: the color itself
            cv.rect(ix, iy, iw, ih, self.names.get(cart[5:], 0))
        elif image_kind:
            preview = self.ws.artwork.thumbnail(iw, ih)
            if preview is not None:
                cv.spr(preview, ix, iy)
            else:
                cv.rect(ix, iy, iw, ih, self.names["light_grey"])
                cv.circ(ix + iw * 3 // 4, iy + ih // 3,
                        max(2, min(iw, ih) // 8), self.names["yellow"])
        else:
            self._cart_scene(cv, ix, iy, iw, ih, title)
        cv.rect(x, y + h - 17 * self.layout.fs, w, 17 * self.layout.fs,
                th["accent"] if selected else th["title"])
        cv.print(title[:max(1, w // (8 * self.layout.fs) - 2)], x + 5 * self.layout.fs,
                 y + h - 13 * self.layout.fs,
                 self.names["black"] if selected else th["title_ink"], 1)
        cv.rectb(x, y, w, h, th["accent"] if selected else th["dim"])

    def _cart_scene(self, cv, x, y, w, h, title):
        # Distinct miniature visual identities; the large area above is the real preview.
        if "Ocean" in title:
            cv.rect(x, y, w, h, self.names["blue"])
            for row in range(3):
                cv.line(x, y + h // 2 + row * 5 * self.layout.fs,
                        x + w - 1, y + h // 2 + row * 5 * self.layout.fs,
                        21 + row)
            cv.circ(x + w * 3 // 4, y + h // 3, max(2, h // 10), 7)
        elif "Sakura" in title:
            cv.rect(x, y, w, h, 22)
            cv.line(x + w // 3, y + h, x + w // 2, y + h // 4, 28)
            cv.circ(x + w // 2, y + h // 3, max(3, h // 4), self.names["pink"])
            cv.circ(x + w * 2 // 3, y + h // 3, max(2, h // 6), self.names["peach"])
        elif "Machine" in title:
            cv.rect(x, y, w, h, self.names["black"])
            step = max(4 * self.layout.fs, min(w, h) // 6)
            for gy in range(y + step // 2, y + h, step):
                for gx in range(x + step // 2, x + w, step):
                    cv.rect(gx, gy, self.layout.fs, self.layout.fs,
                            self.names["dark_blue"])
            cv.line(x + w * 2 // 3, y, x + w - 1, y + h // 3,
                    self.names["indigo"])
            cv.line(x + w - 1, y + h // 3, x + w * 2 // 3, y + h - 1,
                    self.names["indigo"])
            cv.line(x + 3 * self.layout.fs, y + 3 * self.layout.fs,
                    x + 9 * self.layout.fs, y + 3 * self.layout.fs,
                    self.names["yellow"])
            cv.line(x + 6 * self.layout.fs, y,
                    x + 6 * self.layout.fs, y + 6 * self.layout.fs,
                    self.names["yellow"])
        elif "Space" in title or "Night" in title:
            cv.rect(x, y, w, h, 60)
            for i in range(12):
                cv.rect(x + (i * 37) % max(1, w), y + (i * 19) % max(1, h),
                        self.layout.fs, self.layout.fs, 7 if i % 3 else 10)
            cv.circ(x + w * 3 // 4, y + h // 3, max(3, h // 6), 10)
        else:
            cv.rect(x, y, w, h, self.ws.theme_colors["hilite"])
            cv.rect(x, y + h * 2 // 3, w, h // 3, self.names["dark_green"])
            cv.circ(x + w * 3 // 4, y + h // 3, max(2, h // 8), self.names["yellow"])

    def _draw_theme_card(self, cv, r, item, selected):
        # Cards preview the theme in the ACTIVE variant, so flipping DARK/LIGHT
        # repaints the whole catalog in that presentation.
        name = item[0]
        tok = theme_colors(name, self.ws.theme_variant)
        x, y, w, h = r
        fs = self.layout.fs
        cv.rect(x, y, w, h, tok["panel"])
        compact = h < 46 * fs
        band_h = max(6 * fs, h - 18 * fs) if compact else 16 * fs
        cv.rect(x + 3 * fs, y + 3 * fs, w - 6 * fs, band_h, tok["title"])
        if compact:
            cv.rect(x + w - 17 * fs, y + 6 * fs, 10 * fs,
                    max(3 * fs, band_h - 6 * fs), tok["accent"])
        else:
            cv.rect(x + 8 * fs, y + 25 * fs, w * 3 // 5, 12 * fs, tok["hilite"])
            cv.rect(x + w - 25 * fs, y + 25 * fs, 17 * fs, 12 * fs, tok["accent"])
        cv.print(name.upper(), x + 6 * fs, y + h - 14 * fs, tok["ink"], 1)
        cv.rectb(x, y, w, h, tok["accent"] if selected else tok["dim"])
