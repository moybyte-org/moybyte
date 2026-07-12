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

# ui (the shared widget toolkit) is bound LAZILY: ui imports chrome, and chrome
# imports this module's constants at load (the established cycle dodge).
_ui_mod = None


def _toolkit():
    global _ui_mod
    if _ui_mod is None:
        try:
            import ui as mod
        except ImportError:  # pragma: no cover - host fallback
            from runtime import ui as mod
        _ui_mod = mod
    return _ui_mod



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

# (The panel colors moved to the selectable THEME tokens -- chrome.THEMES,
# Settings -> THEME; the "night" default is the moybyte site colorway.)


class SettingsLayer:
    """The Settings content Layer (system domain): the row list over the live
    wallpaper backdrop. Owns the scroll window (set_msel/set_top) + row geometry +
    drawing; reads ws config/system state and dispatches every mutation to ws setters.

    Stage 4 (#46 zoned bar, docs/shell_ux_technical_plan_v1.md): Settings lends the
    top bar's left zone too, via draw_zone/zone_tap -- but it has nothing to put
    there today (its own panel already shows a title + row list below the bar), so
    both are no-ops and `zone_gen` is a constant. Wired now so a future "which
    section" indicator has somewhere to go without BarLayer needing to change."""

    id = "settings"
    domain = "system"
    zone_gen = 0

    _SETTINGS_ROWS = (
        # WIFI (#38, spec shell_ux_v1.md §10: wifi setup lives in Settings): a
        # status row that opens the wifi PANEL below -- scan/pick/password/connect
        # over the injected ws.wifi service. Settings is a system APP (not a
        # cart), so this works while a game keeps running in the one cart slot.
        ("wifi", "WIFI", "wifi-net"),
        ("wallpaper", "WALLPAPER", "wallpaper"),
        # Panel THEME (owner ask 2026-07-08): cycles chrome.THEMES -- the token
        # set the panels / window chrome / selection accents read. Persisted.
        ("theme", "THEME", "theme"),
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
        # FRAMESKIP (#77): while a GAME plays, tick its logic + input at the full
        # loop rate but render every SECOND frame -- halves the whole render-side
        # cost (per-draw-call dispatch, the measured tax) for 30Hz motion. Default
        # OFF pending the on-glass feel verdict. Reads ws.frameskip (the "diag"
        # kind's generic getattr ON/OFF rendering).
        ("frameskip", "FRAMESKIP", "diag"),
        # PERF DIAG (#68 "kid mode" gate): OFF (default) skips the diag costs a
        # player can FEEL on device -- the 30s forced GC sample and the periodic
        # diag->SD write -- and hushes the live serial echo. Crash/cart-exit
        # flushes keep working, so OFF still yields a diag.log. Owners flip it ON
        # for a measurement session.
        ("diag_live", "PERF DIAG", "diag"),
        # The periodic diag->SD write is its own gate (owner call 2026-07-08):
        # PERF DIAG ON + this OFF = serial-only measurement, no 20s sdflush
        # stutter. ON restores the offline play-then-read-diag.log workflow.
        ("diag_sd", "DIAG SD LOG", "diag"),
    )
    _MOCK_NAMES = ("ALEX", "SAM", "KIT", "RAE")

    def __init__(self, ws, names, in_rect, clamp_scroll):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._clamp_scroll = clamp_scroll
        self.set_msel = 0             # selected row in the Settings screen
        self.set_top = 0              # first visible Settings row (scroll offset, #53)
        self.scroll = None            # lazy ui.ScrollRegion (drag + scrollbar)
        # The WIFI panel (#38, spec §10): a Settings SUB-VIEW over the injected
        # ws.wifi service -- scan list -> pick -> (password) -> connect/forget.
        self.wifi_view = False        # the wifi panel replaces the row list
        self.wifi_nets = []           # [(ssid, signal, locked), ...] last scan
        self.wifi_sel = 0
        self.wifi_pick = None         # ssid being typed for (password mode)
        self.wifi_pw = ""
        self.wifi_msg = ""
        self.wifi_known = []
        self._wifi_kprev = 0          # keyboard edge detect (password typing)

    def reset(self):
        """Reset the selection + scroll window (called by ws.open_settings each visit)."""
        self.set_msel = 0
        self.set_top = 0
        if self.wifi_view:
            self.close_wifi()

    # -- the WIFI panel (#38; spec shell_ux_v1.md §10) -------------------------

    def open_wifi(self):
        """Open the wifi panel (the WIFI row / the bar wifi icon's windowed
        deep-link). Rescans on entry so the list is fresh."""
        ws = self.ws
        self.wifi_view = True
        self.wifi_pick = None
        self.wifi_pw = ""
        self._wifi_kprev = 0
        self._wifi_rescan()
        ws._dirty = True

    def close_wifi(self):
        """Back from the wifi panel to the Settings rows (NOT out of Settings)."""
        ws = self.ws
        self.wifi_view = False
        self.wifi_pick = None
        ws._set_text_mode(False)
        ws._dirty = True

    def _wifi_rescan(self):
        ws = self.ws
        if ws.wifi is None:
            self.wifi_nets = []
            self.wifi_known = []
            self.wifi_msg = "NO WIFI SERVICE"
            return
        try:
            self.wifi_nets = list(ws.wifi.scan())
            self.wifi_known = list(ws.wifi.known())
            self.wifi_msg = "TAP A NETWORK"
        except Exception as exc:  # noqa: BLE001 -- a radio hiccup must not crash Settings
            self.wifi_nets = []
            self.wifi_msg = ("SCAN FAILED: " + str(exc))[:34]
        if self.wifi_sel >= len(self.wifi_nets):
            self.wifi_sel = max(0, len(self.wifi_nets) - 1)

    def _wifi_activate(self):
        """CONNECT the selected network: open networks (or already-saved ones)
        connect straight away; a locked, unknown one opens the password prompt."""
        ws = self.ws
        if not self.wifi_nets:
            return
        ssid, _sig, locked = self.wifi_nets[self.wifi_sel % len(self.wifi_nets)]
        if locked and ssid not in self.wifi_known:
            self.wifi_pick = ssid
            self.wifi_pw = ""
            self._wifi_kprev = 0
            self.wifi_msg = "TYPE THE PASSWORD"
            ws._set_text_mode(True)      # clean ASCII typing (device keyboard)
        else:
            self._wifi_connect(ssid, "")
        ws._dirty = True

    def _wifi_connect(self, ssid, pw):
        ws = self.ws
        ok = False
        try:
            ok = bool(ws.wifi.connect(ssid, pw)) if ws.wifi is not None else False
        except Exception as exc:  # noqa: BLE001
            self.wifi_msg = ("CONNECT FAILED: " + str(exc))[:34]
        else:
            self.wifi_msg = ("CONNECTED TO " + str(ssid))[:34] if ok \
                else "COULD NOT CONNECT"
        self.wifi_pick = None
        ws._set_text_mode(False)
        if ws.wifi is not None:
            try:
                self.wifi_known = list(ws.wifi.known())
            except Exception:  # noqa: BLE001
                pass
        ws._dirty = True
        return ok

    def _wifi_forget(self):
        ws = self.ws
        if not self.wifi_nets or ws.wifi is None:
            return
        ssid = self.wifi_nets[self.wifi_sel % len(self.wifi_nets)][0]
        try:
            ws.wifi.forget(ssid)
            self.wifi_known = list(ws.wifi.known())
            self.wifi_msg = ("FORGOT " + str(ssid))[:34]
        except Exception as exc:  # noqa: BLE001
            self.wifi_msg = ("FORGET FAILED: " + str(exc))[:34]
        ws._dirty = True

    def _wifi_input(self, i):
        """Keyboard for the wifi panel. In PASSWORD mode typed bytes edit the
        password (ENTER connects, BACKSPACE deletes, ESC cancels -- Settings is a
        taskbar app, so BACKSPACE is an ordinary key, spec §9); in LIST mode the
        d-pad moves, A connects, B backs out to the Settings rows."""
        ws = self.ws
        if self.wifi_pick is not None:
            k = ws.input.last_key
            if k and k != self._wifi_kprev:
                if k in (10, 13):                       # ENTER -> connect
                    self._wifi_connect(self.wifi_pick, self.wifi_pw)
                elif k == 8:                            # BACKSPACE -> delete
                    self.wifi_pw = self.wifi_pw[:-1]
                elif k == 27:                           # ESC -> cancel the prompt
                    self.wifi_pick = None
                    ws._set_text_mode(False)
                elif 32 <= k <= 126 and len(self.wifi_pw) < 32:
                    self.wifi_pw += chr(k)
                ws._dirty = True
            self._wifi_kprev = k
            return True
        if i.pressed("up") and self.wifi_nets:
            self.wifi_sel = (self.wifi_sel - 1) % len(self.wifi_nets)
            ws._dirty = True
        if i.pressed("down") and self.wifi_nets:
            self.wifi_sel = (self.wifi_sel + 1) % len(self.wifi_nets)
            ws._dirty = True
        if i.pressed("a") or i.pressed("run"):
            self._wifi_activate()
        if i.pressed("b") or i.pressed("stop"):
            self.close_wifi()
        return True

    # wifi panel geometry (panel-derived; per-call, no stored rects)
    def _wifi_btns(self):
        """The bottom action row: CONNECT / FORGET / RESCAN / BACK rects."""
        lay = self.ws.layout
        fs = lay.fs
        px, py, pw, ph = lay.settings_panel
        bw = (pw - 10 * fs * 5) // 4
        bh = 20 * fs
        y = py + ph - bh - 6 * fs
        out = []
        x = px + 10 * fs
        for name in ("connect", "forget", "rescan", "back"):
            out.append((name, (x, y, bw, bh)))
            x += bw + 10 * fs
        return out

    def _wifi_row_rect(self, slot):
        return self.ws.layout.settings_row_rect(slot + 1)   # slot 0 = the status line

    def _wifi_pointer(self, px, py, click):
        ws = self.ws
        if not click:
            return True
        for name, rect in self._wifi_btns():
            if self._in(px, py, rect):
                if name == "connect":
                    if self.wifi_pick is not None:
                        self._wifi_connect(self.wifi_pick, self.wifi_pw)
                    else:
                        self._wifi_activate()
                elif name == "forget":
                    self._wifi_forget()
                elif name == "rescan":
                    self._wifi_rescan()
                    ws._dirty = True
                else:
                    if self.wifi_pick is not None:  # BACK inside the prompt -> list
                        self.wifi_pick = None
                        ws._set_text_mode(False)
                        ws._dirty = True
                    else:
                        self.close_wifi()
                return True
        if self.wifi_pick is None:
            for k in range(len(self.wifi_nets)):
                if self._in(px, py, self._wifi_row_rect(k)):
                    if self.wifi_sel == k:
                        self._wifi_activate()       # second tap = connect
                    else:
                        self.wifi_sel = k
                        ws._dirty = True
                    return True
        return True

    def _draw_wifi(self):
        """The wifi panel body (drawn instead of the Settings rows): a status
        line, the scanned network list (signal bars + lock + SAVED markers), the
        password prompt when one is being typed, and the bottom action row."""
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        fs = lay.fs
        fw = lay.font_w
        px, py, pw, ph = lay.settings_panel
        # Status line (slot 0).
        x, y, w, h = lay.settings_row_rect(0)
        connected, ssid, ip = (False, None, None)
        if ws.wifi is not None:
            try:
                connected, ssid, ip = ws.wifi.status()
            except Exception:  # noqa: BLE001
                pass
        ws._icon("wifi" if connected else "wifi_off", x, y, cv)
        if connected:
            cv.print(("ON  " + str(ssid))[:22], x + 20 * fs, y + 5, NAMES["green"], 1)
            if ip:
                cv.print(str(ip)[:15], x + w - 15 * fw, y + 5, NAMES["blue"], 1)
        else:
            cv.print("NOT CONNECTED", x + 20 * fs, y + 5, NAMES["light_grey"], 1)
        if self.wifi_pick is not None:
            # Password prompt: the picked ssid + the typed password + a caret.
            x, y, w, h = self._wifi_row_rect(0)
            cv.print(("PASSWORD FOR " + str(self.wifi_pick))[:30], x + 4, y + 5,
                     NAMES["white"], 1)
            bx, by, bw2, bh2 = self._wifi_row_rect(1)
            cv.rect(bx, by, bw2, bh2 - 2, NAMES["black"])
            cv.rectb(bx, by, bw2, bh2 - 2, ws.theme_colors["edge"])
            shown = self.wifi_pw[-max(4, bw2 // fw - 3):]
            cv.print(shown, bx + 4, by + 5, NAMES["yellow"], 1)
            cv.rect(bx + 4 + len(shown) * fw, by + 3, fs, bh2 - 8, NAMES["yellow"])
            x, y, w, h = self._wifi_row_rect(2)
            cv.print("ENTER = CONNECT   ESC = BACK", x + 4, y + 5,
                     NAMES["dark_grey"], 1)
        else:
            # The network list.
            for k in range(len(self.wifi_nets)):
                ssid_k, sig, locked = self.wifi_nets[k]
                x, y, w, h = self._wifi_row_rect(k)
                if y + h > py + ph - 30 * fs:
                    break                          # keep clear of the button row
                sel = (k == self.wifi_sel)
                if sel:
                    cv.rect(x, y, w, h, ws.theme_colors["hilite"])
                fg = NAMES["white"] if sel else NAMES["light_grey"]
                cv.print(str(ssid_k)[:16], x + 4, y + 5, fg, 1)
                bars = max(0, min(4, int(sig) // 25 + 1))
                for s in range(4):
                    c = NAMES["green"] if s < bars else NAMES["dark_grey"]
                    cv.rect(x + w - 46 * fs + s * 8 * fs, y + h - 6 * fs - 2 * fs * s,
                            5 * fs, (2 + 2 * s) * fs, c)
                if locked:
                    ws._glyph("lock", (x + w - 62 * fs, y + 2, 12 * fs, 12 * fs),
                              NAMES["orange"], cv)
                if str(ssid_k) in self.wifi_known:
                    cv.print("SAVED", x + w - 110 * fs, y + 5, NAMES["blue"], 1)
        if self.wifi_msg:
            mx, my = px + 10 * fs, py + ph - 30 * fs - 10 * fs
            cv.print(self.wifi_msg[:36], mx, my, NAMES["yellow"], 1)
        for name, rect in self._wifi_btns():
            label = name.upper()
            color = {"connect": NAMES["green"], "forget": NAMES["red"],
                     "rescan": NAMES["blue"], "back": NAMES["dark_grey"]}[name]
            ws._btn(label, rect, color, cv)

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------

    def draw_zone(self, cv, rect):
        """Settings' lent left zone: currently empty -- its own panel already
        shows a title + the row list below the bar, so there's nothing to add
        here yet. Structurally wired for #46 (see the class docstring)."""

    def zone_tap(self, px, py, rect=None):
        return False

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

    def _toggle_diag_row(self, key):
        """Flip one of the "diag"-kind ON/OFF gate rows (any tap/step/A toggles):
        PERF DIAG + DIAG SD LOG (#68) and FRAMESKIP (#77). One dispatch shared by
        the step/key/tap paths so a new gate row is wired in exactly one place."""
        ws = self.ws
        if key == "diag_sd":
            ws.set_diag_sd(not ws.diag_sd)
        elif key == "frameskip":
            ws.set_frameskip(not ws.frameskip)
        else:
            ws.set_diag_live(not ws.diag_live)

    def settings_adjust(self, d):
        """Step the selected Settings row by d. Wallpaper/font apply + persist; the
        mock rows just move a cosmetic value held in ws.system (not acted on); an
        "action" row (EDIT ICONS) fires its action regardless of direction."""
        ws = self.ws
        key, _label, kind = self._settings_rows()[self.set_msel]
        if kind == "wifi-net":                  # WIFI: any step/tap opens the panel (#38)
            self.open_wifi()
            return
        if kind == "action":                    # EDIT ICONS / UPDATE FW: open the tool
            self._activate_settings_action(key)
            return
        if key == "web":                        # device web view ON <-> OFF (#41)
            ws._toggle_web_view()
            return
        if kind == "diag":                      # the ON/OFF gates (#68 diag, #77 frameskip)
            self._toggle_diag_row(key)
            return
        if key == "ota_channel":                # OTA update channel STABLE <-> BETA
            ws._cycle_channel(d)
            return
        if key == "wallpaper":
            ws.cycle_wallpaper(d)
            return
        if key == "theme":                      # panel THEME: cycle chrome.THEMES
            ws.cycle_theme(d)
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

    def _toolkit(self):
        return _toolkit()

    def _scroll_region(self):
        """The rows' ui.ScrollRegion (lazy: ui imports chrome, and chrome imports
        THIS module's constants, so a module-level import would re-enter chrome
        half-initialized). set_top stays the row-slot source of truth; the region
        is the touch INTERACTION model (drag) + the shelf-tier scrollbar, synced
        from set_top each use."""
        if self.scroll is None:
            self.scroll = self._toolkit().ScrollRegion()
        ws = self.ws
        lay = ws.layout
        rows = self._settings_rows()
        area = (lay.set_x, lay.set_row_y0, lay.set_w,
                self._settings_visible() * lay.set_row_h)
        self.scroll.set(area, len(rows) * lay.set_row_h)
        # Keep the sub-row remainder while a drag is active.  Re-snapping from
        # set_top on every pointer sample discards normal 3-5px finger movement,
        # so a gradual drag can never accumulate enough travel to cross a row.
        if self.scroll._drag_y is None:
            self.scroll.offset = self.set_top * lay.set_row_h
        return self.scroll

    def _rows_drag(self, px, py):
        """Touch drag on the row list scrolls it (rows follow the finger), snapped
        to whole rows -- set_top stays the state of record. Pure behavior: no
        pixels change on a tier that doesn't draw the scrollbar."""
        ws = self.ws
        sr = self._scroll_region()
        if not ws.pointer.down:
            sr.drag_end()
            return
        if sr._drag_y is None:
            if not self._in(px, py, sr.view):
                return                     # a drag must START on the rows...
            sr.drag_start(py)
            return
        sr.drag_move(py)                   # ...but may continue past the edge
        rows = len(self._settings_rows())
        vis = self._settings_visible()
        top = sr.offset // ws.layout.set_row_h
        self.set_top = max(0, min(max(0, rows - vis), top))

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
        if self.wifi_view:
            return self._wifi_input(i)
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
            if row[2] == "wifi-net":            # WIFI: open the panel (#38)
                self.open_wifi()
            elif row[2] == "action":
                self._activate_settings_action(row[0])
            elif row[2] == "web":               # A/run also toggles the web view (#41)
                ws._toggle_web_view()
            elif row[2] == "diag":              # ... and the ON/OFF gates (#68/#77)
                self._toggle_diag_row(row[0])
        if i.pressed("b"):
            ws._exit_settings()          # back -> resume the cart if opened from one
        elif i.pressed("home") or i.pressed("stop"):
            ws.go_home()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if not self.wifi_view and not ws.show_achievements:
            self._rows_drag(px, py)       # touch drag scrolls the row list
        if not click:
            return True
        # The achievements view is a modal overlay: while it's up, any tap closes it
        # (it has no controls of its own besides "tap to dismiss").
        if ws.show_achievements:
            ws.show_achievements = False
            return True
        # The top bar's tap slice (clock egg / ≡) is bar/right-zone-owned (Stage 4,
        # #46) -- Settings' own lent left zone is empty (draw_zone above), so this
        # only ever fires the clock egg or ≡, same as home/menu.
        if ws.bar_layer.handle_bar_tap("settings", px, py):
            return True
        if self.wifi_view:                     # the wifi panel owns the body (#38)
            return self._wifi_pointer(px, py, click)
        lay = ws.layout
        if self._in(px, py, lay.set_ach):      # trophy: open the achievements view (#21)
            ws.show_achievements = True
            ws.ach_ui._secret_taps = 0
            return True
        # The panel's own X + title only exist outside a WM window (the strip owns
        # both there -- see _draw_settings), so their taps are gated the same way.
        if not getattr(ws, "windowed_chrome", False):
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
                if rows[i][2] == "wifi-net":       # WIFI: any tap opens the panel (#38)
                    self.open_wifi()
                    return True
                if rows[i][2] == "action":
                    self._activate_settings_action(rows[i][0])  # EDIT ICONS / UPDATE FW
                    return True
                if rows[i][2] == "web":            # web view: any tap flips ON/OFF (#41)
                    ws._toggle_web_view()
                    return True
                if rows[i][2] == "diag":           # ON/OFF gates: any tap flips (#68/#77)
                    self._toggle_diag_row(rows[i][0])
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
        th = ws.theme_colors
        cv.rect(px, py, pw, ph, th["panel"])
        cv.rectb(px, py, pw, ph, th["edge"])
        # Inside a WM window (#73) the title strip already says SETTINGS and carries
        # the closing X, so the panel's own header + X are suppressed (no doubled
        # chrome); the trophy (the achievements door, #21) stays either way.
        if not getattr(ws, "windowed_chrome", False):
            ws._glyph("gear", (px + 6, py + 2, 14 * fs, 14 * fs), NAMES["yellow"], cv)
            cv.print("SETTINGS", px + 24, py + 4, NAMES["white"], 2)
            ws._mini_btn("X", lay.set_back, NAMES["red"], cv)
        if self.wifi_view:
            # The WIFI panel (#38) replaces the row list (its BACK returns here).
            self._draw_wifi()
            ws.bar_layer._draw_status_strip("settings")
            ws.bar_layer._draw_dock("settings")
            return
        # Achievements view button (#21): a trophy badge with the unlocked count.
        sa = lay.set_ach
        cv.rect(sa[0], sa[1], sa[2], sa[3], ws.theme_colors["hilite"])
        ws._glyph("trophy", (sa[0] - 2, sa[1], 14 * fs, 14 * fs), NAMES["yellow"], cv)
        cv.print(str(ws.ach.count()), sa[0] + 13 * fs, sa[1] + 4, NAMES["white"], 1)
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
        vis = self._settings_visible()
        self._toolkit().scroll_cues(
            cv, (xr, lay.set_row_y0), (xr, py + ph - 9 * lay.fs),
            self.set_top > 0, self.set_top + vis < len(rows), NAMES["white"])
        if not lay._base:
            # Shelf tiers: the toolkit scrollbar alongside (base keeps the frozen
            # chevron-only pixels).
            self._scroll_region().draw_bar(cv, ws.theme_colors)

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
            cv.rect(x, y, w, h, ws.theme_colors["hilite"])
        fg = NAMES["white"] if sel else NAMES["light_grey"]
        cv.print(label, x + 4, y + 5, fg, 1)
        if kind == "wifi-net":
            # WIFI (#38): the connected SSID (or OFF) + the status icon as the OPEN
            # affordance -- a tap / A opens the wifi panel, no stepper.
            connected, ssid = False, None
            if ws.wifi is not None:
                try:
                    connected, ssid, _ip = ws.wifi.status()
                except Exception:  # noqa: BLE001
                    pass
            cv.print((str(ssid)[:12] if connected else "OFF"),
                     x + w - 78 * lay.fs, y + 5,
                     NAMES["green"] if connected else NAMES["dark_grey"], 1)
            ws._icon("wifi" if connected else "wifi_off",
                     x + w - 18 * lay.fs, y + 1, cv)
            return
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
        if kind == "theme":                # panel THEME: the current name, tinted
            cv.print(str(ws.theme_name)[:9].upper(), vx, y + 5,
                     ws.theme_colors["edge"], 1)
        elif kind == "wallpaper":
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
        elif kind == "diag":               # #68 diag gates: ON/OFF (key-driven)
            on = bool(getattr(ws, key, False))
            cv.print("ON" if on else "OFF", vx, y + 5,
                     NAMES["orange"] if on else NAMES["dark_grey"], 1)
        # Mark not-yet-functional rows clearly (wifi + wallpaper + font + channel +
        # web + diag + actions work).
        if kind not in ("wifi-net", "wallpaper", "font", "action", "channel",
                        "web", "diag", "theme"):
            cv.print("soon", x + 4, y + 6 + fw, NAMES["dark_grey"], 1)
