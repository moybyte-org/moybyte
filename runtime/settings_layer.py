"""The Settings app (#28/#39/#53), extracted from Workstation (runtime/console.py) as
its own Layer -- docs/shell_layers_refactor_v1.md Phase 2.

Settings is the console's AGGREGATOR: a scrolling list of rows (wallpaper / font size /
volume / brightness / name / EDIT ICONS / PERF DIAG, plus the injected OTA + web-view
rows) drawn over the live wallpaper backdrop. SettingsLayer owns the SCREEN: the row
list + drawing + scroll window (set_msel/set_top) + the row hit-testing, and the
settings-only constants (_SET_*).

Boundary (the anti-spaghetti line, per the doc): SettingsLayer owns NO config. Every
value it steps or shows is CART/SYSTEM state on Workstation -- ws.system (the
system.json dict), ws.wallpaper_id, ws.font_scale, ws.diag_live, ws.web_hook, the
updater queries -- and every mutation goes through the ws setters (cycle_wallpaper /
cycle_font_scale / set_diag_live / _cycle_channel / _toggle_web_view / _persist_system).
The WALLPAPER cluster is SHARED (the launcher draws the same backdrop), so it stays
single-sourced on ws -- SettingsLayer just calls ws.wallpaper.draw + the picker verbs.
The actions Settings hosts delegate OUT to other layers (ws.open_theme / ws.update_ui.
open_update / ws.show_achievements). ws.open_settings / _exit_settings (the lifecycle,
tested) stay on ws. `NAMES` / `_in` / `_clamp_scroll` are injected (the circular-import
dodge). Shared draw toolkit (ws._glyph/_mini_btn) + the bar (ws.bar_layer) stay put.
"""


# -- settings-screen geometry (single source; console.py imports these back) --
# These are ALSO used by console's Layout (the responsive lay.set_* fields), so they're
# re-exported by console.py rather than duplicated -- the block_editor_ui.py pattern.
_SET_X = 18
_SET_W = 284
_SET_ROW_Y0 = 40
_SET_ROW_H = 26
_SET_BACK = (288, 18, 18, 14)       # close Settings (X), in the panel title row
_SET_ACH = (262, 18, 22, 14)        # open the achievements view (trophy), title row (#21)
_SET_TITLE_HIT = (30, 18, 130, 16)  # the "SETTINGS" panel title (secret door, #21)


class SettingsLayer:
    """The Settings content Layer (system domain): the row list over the live
    wallpaper backdrop. Owns the scroll window (set_msel/set_top) + row geometry +
    drawing; reads ws config/system state and dispatches every mutation to ws setters."""

    id = "settings"
    domain = "system"

    _SETTINGS_ROWS = (
        ("wallpaper", "WALLPAPER", "wallpaper"),
        ("font_scale", "FONT SIZE", "font"),
        ("volume", "VOLUME", "mock-gauge"),
        ("brightness", "BRIGHTNESS", "mock-gauge"),
        ("name", "NAME", "mock-name"),
        # EDIT ICONS (Stage 2): the one FUNCTIONAL "theme" control -- an action row
        # that opens the PAINT editor on the system icon sheet so a kid can repaint
        # the top-bar chrome. The dropdown menu that would otherwise host it is
        # deferred to #52, so it lives in Settings for now. "action" rows aren't
        # +/- steppers: any tap / left / right activates them (open_theme).
        ("icons", "EDIT ICONS", "action"),
        # PERF DIAG (#68 "kid mode" gate): OFF (default) skips the diag costs a
        # player can FEEL on device -- the 30s forced GC sample and the periodic
        # diag->SD write -- and hushes the live serial echo. Crash/cart-exit
        # flushes keep working, so OFF still yields a diag.log. Owners flip it ON
        # for a measurement session.
        ("diag_live", "PERF DIAG", "diag"),
        # TAP OPENS (spec Section 4): the launcher's tap default -- MAKER (a maker's own
        # device: tap -> the Editor, on Config) or PLAYER (a kid's player: tap -> plays).
        # Persisted in system.json; both Play and Edit stay reachable regardless (#55).
        # Appended LAST so it never shifts the existing rows' indices.
        ("tap_mode", "TAP OPENS", "tapmode"),
    )
    _MOCK_NAMES = ("ALEX", "SAM", "KIT", "RAE")

    def __init__(self, ws, names, in_rect, clamp_scroll):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._clamp_scroll = clamp_scroll
        self.set_msel = 0             # selected row in the Settings screen
        self.set_top = 0              # first visible Settings row (scroll offset, #53)

    def reset(self):
        """Reset the selection + scroll window (called by ws.open_settings each visit)."""
        self.set_msel = 0
        self.set_top = 0

    # -- rows / actions ------------------------------------------------------

    def _settings_rows(self):
        """The Settings rows for this session: the static set, plus "UPDATE FW" (install
        from SD) and "UPDATE ONLINE" (WiFi download, #53 Phase 3) action rows when the
        injected updater supports them. Built on demand so the rows appear/disappear with
        the updater without re-statting per draw."""
        ws = self.ws
        rows = self._SETTINGS_ROWS
        if ws.web_hook is not None:           # device web view (#41): a WiFi browser feed
            rows = rows + (("web", "WEB VIEW", "web"),)
        if ws._update_available():
            rows = rows + (("update", "UPDATE FW", "action"),)
        if ws._online_update_available():
            rows = rows + (("ota_channel", "CHANNEL", "channel"),)
            rows = rows + (("update_online", "UPDATE ONLINE", "action"),)
        return rows

    def _activate_settings_action(self, key):
        """Fire an "action" Settings row by key: EDIT ICONS opens the theme editor,
        UPDATE FW installs a local SD image, UPDATE ONLINE checks WiFi for one (#53)."""
        ws = self.ws
        if key == "update":
            ws.update_ui.open_update()
        elif key == "update_online":
            ws.update_ui.open_update_online()
        else:
            ws.open_theme()       # EDIT ICONS (#52)

    def settings_adjust(self, d):
        """Step the selected Settings row by d. Wallpaper/font apply + persist; the
        mock rows just move a cosmetic value held in ws.system (not acted on); an
        "action" row (EDIT ICONS) fires its action regardless of direction."""
        ws = self.ws
        key, _label, kind = self._settings_rows()[self.set_msel]
        if kind == "action":                    # EDIT ICONS / UPDATE FW: open the tool
            self._activate_settings_action(key)
            return
        if key == "web":                        # device web view ON <-> OFF (#41)
            ws._toggle_web_view()
            return
        if key == "diag_live":                  # perf diagnostics ON <-> OFF (#68)
            ws.set_diag_live(not ws.diag_live)
            return
        if key == "tap_mode":                   # launcher tap default MAKER <-> PLAYER
            ws.cycle_tap_mode(d)
            return
        if key == "ota_channel":                # OTA update channel STABLE <-> BETA
            ws._cycle_channel(d)
            return
        if key == "wallpaper":
            ws.cycle_wallpaper(d)
            return
        if key == "font_scale":                 # system-UI font size (#39): live + persisted
            ws.cycle_font_scale(d)
            return
        if key == "name":
            cur = ws.system.get("name", self._MOCK_NAMES[0])
            i = self._MOCK_NAMES.index(cur) if cur in self._MOCK_NAMES else 0
            ws.system["name"] = self._MOCK_NAMES[(i + d) % len(self._MOCK_NAMES)]
        else:  # mock-gauge (volume / brightness): a 0..5 placeholder
            v = int(ws.system.get(key, 3)) + d
            ws.system[key] = max(0, min(5, v))

    def _settings_wallpaper_label(self):
        """A friendly label for the current wallpaper: the cart's TITLE for a
        wallpaper cart, or the color name for a built-in solid fill."""
        ws = self.ws
        wp = ws.wallpaper_id or ""
        if isinstance(wp, str) and wp.startswith("fill:"):
            return wp[5:].replace("_", " ").upper()
        cart = ws._wp_cart_by_id(wp)
        if cart is not None:
            return cart["title"].upper()
        return str(wp).replace("_", " ").upper()

    # -- scroll window -------------------------------------------------------

    def _settings_visible(self):
        """How many Settings rows fit in the panel at the current font scale (#39)."""
        lay = self.ws.layout
        _px, py, _pw, ph = lay.settings_panel
        n = (py + ph - lay.set_row_y0) // lay.set_row_h
        return max(1, int(n))

    def _settings_scroll(self):
        """Keep the selected row (set_msel) inside the visible window by moving the
        scroll offset set_top. The list scrolls once it has more rows than fit -- the
        #53 OTA rows (UPDATE FW / CHANNEL / UPDATE ONLINE) push it past one screen."""
        rows = len(self._settings_rows())
        vis = self._settings_visible()
        self.set_top = self._clamp_scroll(self.set_top, self.set_msel, vis, rows)

    def _settings_row_visible(self, i):
        return self.set_top <= i < self.set_top + self._settings_visible()

    def _settings_row_rect(self, i):
        # Scrolled position: row i sits in on-screen slot (i - set_top). Rows outside
        # the visible window get an off-panel rect that the draw + pointer loops skip.
        return self.ws.layout.settings_row_rect(i - self.set_top)

    # -- Layer facets: input + pointer ---------------------------------------

    def handle_input(self, i):
        ws = self.ws
        rows = self._settings_rows()
        if i.pressed("up"):
            self.set_msel = (self.set_msel - 1) % len(rows)
        if i.pressed("down"):
            self.set_msel = (self.set_msel + 1) % len(rows)
        self._settings_scroll()        # keep the selection in view (#53)
        if i.pressed("left"):
            self.settings_adjust(-1)
        if i.pressed("right"):
            self.settings_adjust(1)
        if i.pressed("a") or i.pressed("run"):  # activate an action row (EDIT ICONS / UPDATE FW)
            row = rows[self.set_msel % len(rows)]
            if row[2] == "action":
                self._activate_settings_action(row[0])
            elif row[2] == "web":               # A/run also toggles the web view (#41)
                ws._toggle_web_view()
            elif row[2] == "diag":              # ... and the PERF DIAG gate (#68)
                ws.set_diag_live(not ws.diag_live)
        if i.pressed("b"):
            ws._exit_settings()          # back -> resume the cart if opened from one
        elif i.pressed("home") or i.pressed("stop"):
            ws.go_home()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if not click:
            return True
        # The achievements view is a modal overlay: while it's up, any tap closes it
        # (it has no controls of its own besides "tap to dismiss").
        if ws.show_achievements:
            ws.show_achievements = False
            return True
        lay = ws.layout
        if self._in(px, py, lay.set_ach):      # trophy: open the achievements view (#21)
            ws.show_achievements = True
            ws.ach_ui._secret_taps = 0
            return True
        if self._in(px, py, lay.set_back):
            ws._exit_settings()
            return True
        # Secret-door Easter egg (#21): tapping the SETTINGS title (not a button)
        # _SECRET_TAP_GOAL times knocks the hidden door open. Reset on any other tap.
        if self._in(px, py, lay.set_title_hit):
            ws.ach_ui._tap_secret_door()
            return True
        ws.ach_ui._secret_taps = 0
        slot = ws.bar_layer._dock_slot_at(px, py)
        if slot is not None:
            ws.bar_layer._activate_dock(slot)
            return True
        edge = 5 * ws.layout.font_w           # the "<"/">" hit zone (40px at fs=1)
        rows = self._settings_rows()
        for i in range(len(rows)):
            if not self._settings_row_visible(i):
                continue                       # off-screen (scrolled) rows aren't tappable
            x, y, w, h = self._settings_row_rect(i)
            if self._in(px, py, (x, y, w, h)):
                self.set_msel = i
                if rows[i][2] == "action":
                    self._activate_settings_action(rows[i][0])  # EDIT ICONS / UPDATE FW
                    return True
                if rows[i][2] == "web":            # web view: any tap flips ON/OFF (#41)
                    ws._toggle_web_view()
                    return True
                if rows[i][2] == "diag":           # PERF DIAG: any tap flips it (#68)
                    ws.set_diag_live(not ws.diag_live)
                    return True
                # left third = "<" (decrement), right third = ">" (increment).
                if px >= x + w - edge:
                    self.settings_adjust(1)
                elif px <= x + edge:
                    self.settings_adjust(-1)
                return True
        return True

    # -- draw ----------------------------------------------------------------

    def draw(self, dt):
        """The Settings app (#28): wallpaper picker + font-size picker (both
        FUNCTIONAL, persist) plus the mocked rows, over the live wallpaper so the
        backdrop preview is honest. On the SYSTEM canvas; panel + title-row controls
        reflow with the layout/font scale (#39)."""
        NAMES = self._NAMES
        ws = self.ws
        ws.wallpaper.draw(dt)
        cv = ws.sys_canvas
        lay = ws.layout
        fs = lay.fs
        px, py, pw, ph = lay.settings_panel
        cv.rect(px, py, pw, ph, NAMES["dark_purple"])
        cv.rectb(px, py, pw, ph, NAMES["pink"])
        ws._glyph("gear", (px + 6, py + 2, 14 * fs, 14 * fs), NAMES["yellow"], cv)
        cv.print("SETTINGS", px + 24, py + 4, NAMES["white"], 2)
        # Achievements view button (#21): a trophy badge with the unlocked count.
        sa = lay.set_ach
        cv.rect(sa[0], sa[1], sa[2], sa[3], NAMES["indigo"])
        ws._glyph("trophy", (sa[0] - 2, sa[1], 14 * fs, 14 * fs), NAMES["yellow"], cv)
        cv.print(str(ws.ach.count()), sa[0] + 13 * fs, sa[1] + 4, NAMES["white"], 1)
        ws._mini_btn("X", lay.set_back, NAMES["red"], cv)
        rows = self._settings_rows()
        for i in range(len(rows)):
            if self._settings_row_visible(i):
                self._draw_settings_row(i)
        self._draw_settings_more(rows)
        ws.bar_layer._draw_status_strip("settings")
        ws.bar_layer._draw_dock("settings")

    def _draw_settings_more(self, rows):
        """Up/down chevrons at the panel's right edge when the Settings list scrolls
        past the visible window (the #53 OTA rows can push it over one screen)."""
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        px, py, pw, ph = lay.settings_panel
        xr = px + pw - 9 * lay.fs
        if self.set_top > 0:
            cv.print("^", xr, lay.set_row_y0, NAMES["white"], 1)
        if self.set_top + self._settings_visible() < len(rows):
            cv.print("v", xr, py + ph - 9 * lay.fs, NAMES["white"], 1)

    def _draw_settings_row(self, i):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        fw = lay.font_w
        key, label, kind = self._settings_rows()[i]
        x, y, w, h = self._settings_row_rect(i)
        sel = (i == self.set_msel)
        if sel:
            cv.rect(x, y, w, h, NAMES["indigo"])
        fg = NAMES["white"] if sel else NAMES["light_grey"]
        cv.print(label, x + 4, y + 5, fg, 1)
        if kind == "action":
            # An action row (EDIT ICONS / UPDATE FW / UPDATE ONLINE): no value/stepper --
            # just an OPEN affordance at the right so a tap (or A) is the obvious activate.
            # The glyph cues what it does (paint = repaint chrome; run = install; wifi =
            # online update).
            if key == "update":
                g, c = "run", NAMES["yellow"]
            elif key == "update_online":
                g, c = "wifi", NAMES["yellow"]
            else:
                g, c = "paint", NAMES["green"]
            ws._glyph(g, (x + w - 18 * lay.fs, y + 2, 14 * lay.fs, 14 * lay.fs), c, cv)
            return
        # < value > stepper at the right (the chevrons print at double size = 2*fw).
        cv.print("<", x + w - 11 * fw - 2, y + 5, NAMES["yellow"], 2)
        cv.print(">", x + w - 2 * fw + 2, y + 5, NAMES["yellow"], 2)
        vx = x + w - 78 * lay.fs           # value column (baseline x+w-78)
        if kind == "wallpaper":
            cv.print(self._settings_wallpaper_label()[:9], vx, y + 5, NAMES["green"], 1)
        elif kind == "font":               # system-UI font size (#39): 1x / 2x / 3x
            cv.print("%dx" % ws.font_scale, vx, y + 5, NAMES["green"], 1)
        elif kind == "mock-gauge":
            lvl = int(ws.system.get(key, 3))
            for s in range(5):
                c = NAMES["green"] if s < lvl else NAMES["dark_grey"]
                cv.rect(vx + s * 8 * lay.fs, y + 6, 6 * lay.fs, 8 * lay.fs, c)
        elif kind == "mock-name":
            cv.print(str(ws.system.get("name", self._MOCK_NAMES[0]))[:8], vx, y + 5,
                     NAMES["peach"], 1)
        elif kind == "channel":            # OTA update channel: STABLE / BETA (#53)
            beta = ws._ota_channel() == "unstable"
            cv.print("BETA" if beta else "STABLE", vx, y + 5,
                     NAMES["orange"] if beta else NAMES["green"], 1)
        elif kind == "web":                # device web view (#41): ON/OFF + the URL
            on = False
            url = ""
            try:
                on = bool(ws.web_hook.enabled)
                url = str(ws.web_hook.url() or "")
            except Exception:  # noqa: BLE001 -- a backend hiccup just reads OFF
                pass
            cv.print("ON" if on else "OFF", vx, y + 5,
                     NAMES["green"] if on else NAMES["dark_grey"], 1)
            if on and url:
                # The URL to open in a phone/desktop browser, under the row label.
                cv.print(url[:34], x + 4, y + 6 + fw, NAMES["blue"], 1)
        elif kind == "diag":               # #68 perf-diagnostics gate: ON/OFF
            on = bool(ws.diag_live)
            cv.print("ON" if on else "OFF", vx, y + 5,
                     NAMES["orange"] if on else NAMES["dark_grey"], 1)
        elif kind == "tapmode":            # launcher tap default: MAKER / PLAYER (Section 4)
            maker = ws.tap_mode() != "player"
            cv.print("MAKER" if maker else "PLAYER", vx, y + 5,
                     NAMES["green"] if maker else NAMES["orange"], 1)
        # Mark not-yet-functional rows clearly (wallpaper + font + channel + web +
        # diag + tapmode + actions work).
        if kind not in ("wallpaper", "font", "action", "channel", "web", "diag", "tapmode"):
            cv.print("soon", x + 4, y + 6 + fw, NAMES["dark_grey"], 1)
