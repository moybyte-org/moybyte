"""The Settings app (#28/#39/#53), extracted from Workstation (runtime/console.py) as
its own Layer -- docs/history/shell_layers_refactor_v1.md Phase 2.

Settings is the console's AGGREGATOR: a scrolling list of rows (APPEARANCE / font size /
volume / brightness / name / EDIT ICONS / PERF DIAG, plus the injected OTA + web-view
rows) drawn over the live wallpaper backdrop. SettingsLayer owns the SCREEN: the row
list + drawing + scroll window (set_msel/set_top) + the row hit-testing, and the
settings-only constants (_SET_*).

Boundary (the anti-spaghetti line, per the doc): SettingsLayer owns NO config. Every
value it steps or shows is CART/SYSTEM state on Workstation -- ws.system (the
system.json dict), ws.font_scale, ws.diag_live, the
updater queries -- and every mutation goes through the ws setters (
cycle_font_scale / set_diag_live / _cycle_channel / _persist_system).
Wallpaper + panel-theme picking is NOT here: the Appearance app is the ONE
appearance surface, and Settings just deep-links to it (the APPEARANCE action row).
The actions Settings hosts delegate OUT to other layers (ws.open_theme / ws.update_ui.
open_update / ws.show_achievements). ws.open_settings / _exit_settings (the lifecycle,
tested) stay on ws. `NAMES` / `_in` / `_clamp_scroll` are injected to keep the
surface independent of console.py. Shared draw toolkit (ws._glyph/_mini_btn) +
the bar (ws.bar_layer) stay put.
"""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui



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
# picked in the Appearance app; the "night" default is the moybyte site colorway.)


class SettingsLayer:
    """The Settings content Layer (system domain): the row list over the live
    wallpaper backdrop. Owns the scroll window (set_msel/set_top) + row geometry +
    drawing; reads ws config/system state and dispatches every mutation to ws setters.

    Stage 4 (#46 zoned bar, docs/history/shell_ux_technical_plan_v1.md): Settings lends the
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
        # APPEARANCE: wallpaper + panel theme moved into the visual Appearance
        # app (the ONE appearance surface); this action row deep-links to it.
        ("appearance", "APPEARANCE", "action"),
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
        # OFF -- the on-glass feel pass kept it opt-in (2026-07-10, both boards).
        # Reads ws.frameskip (the "diag" kind's generic getattr ON/OFF rendering).
        ("frameskip", "FRAMESKIP", "diag"),
        # SHOW FPS: the in-game FPS chip (default ON). It rides the GAME
        # canvas, so on a small-canvas cart (celeste) it scales up with the
        # composite -- 2x size -- which is what prompted the off switch.
        ("show_fps", "SHOW FPS", "diag"),
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
        self._taps = None             # lazy ui.DragTap over it (tap-vs-drag)
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
        # P4-only Bluetooth keyboard panel.  It is capability-gated through
        # keyboard.settings_capable, so the host and local T-Deck keyboard keep
        # the exact Settings rows/pixels they have today.
        self.bt_view = False
        self.bt_devices = []          # service rows; opaque address stays opaque
        self.bt_sel = 0
        self.bt_msg = ""
        self._bt_state = None
        self._bt_hits = _ui.Hits()    # visual-identity v1: draw pass == tap map
        # Memo for _settings_rows (see there): the built tuple plus the four
        # capability flags it was built from, held as separate attributes so the
        # comparison allocates NOTHING (a signature tuple would itself cost ~40B
        # per call, and this is called ~15x per frame).
        self._rows_cache = None
        self._rows_bt = False
        self._rows_upd = False
        self._rows_onl = False
        self._rows_web = False

    def reset(self):
        """Reset the selection + scroll window (called by ws.open_settings each visit)."""
        self.set_msel = 0
        self.set_top = 0
        if self.wifi_view:
            self.close_wifi()
        if self.bt_view:
            self.close_bluetooth()

    # -- BLUETOOTH KEYBOARD panel (P4 capability; visual identity v1) ---------

    def _bt_service(self):
        keyboard = getattr(self.ws, "keyboard", None)
        return keyboard if getattr(keyboard, "settings_capable", False) else None

    def open_bluetooth(self):
        """Open the Bluetooth keyboard picker without disrupting a live link."""
        self.bt_view = True
        self.bt_msg = ""
        self._bt_state = None
        self._bt_refresh()
        self.ws._dirty = True

    def close_bluetooth(self):
        self.bt_view = False
        self.ws._dirty = True

    def _bt_refresh(self):
        svc = self._bt_service()
        if svc is None:
            self.bt_devices = []
            self.bt_msg = "NO BLUETOOTH KEYBOARD SERVICE"
            return (False, "off", None, None, self.bt_msg)
        try:
            status = svc.settings_status()
            self.bt_devices = list(svc.settings_devices())
        except Exception as exc:  # noqa: BLE001 -- Settings must stay usable
            self.bt_devices = []
            self.bt_msg = ("KEYBOARD ERROR: " + str(exc))[:40]
            return (False, "error", None, None, self.bt_msg)
        if self.bt_sel >= len(self.bt_devices):
            self.bt_sel = max(0, len(self.bt_devices) - 1)
        state = status[1]
        if state != self._bt_state:
            if not status[0]:
                self.bt_msg = "KEYBOARD INPUT IS OFF"
            elif status[4]:
                self.bt_msg = ("ERROR: " + str(status[4]))[:40]
            elif state == "ready":
                self.bt_msg = "CONNECTED + SAVED"
            elif state == "scanning":
                self.bt_msg = "LOOKING FOR KEYBOARDS..."
            elif state in ("connecting", "pairing", "discovering",
                           "subscribe-retry", "found"):
                self.bt_msg = "CONNECTING..."
            elif state == "choose":
                self.bt_msg = "PICK A KEYBOARD"
            else:
                self.bt_msg = "READY TO SCAN"
            self._bt_state = state
        return status

    def _bt_action(self, action, address=None):
        svc = self._bt_service()
        if action == "back":
            self.close_bluetooth()
            return
        if svc is None:
            self.bt_msg = "NO BLUETOOTH KEYBOARD SERVICE"
            return
        try:
            if action == "toggle":
                on = not bool(svc.settings_status()[0])
                svc.set_enabled(on)
                self.bt_msg = "KEYBOARD INPUT ON" if on else "KEYBOARD INPUT OFF"
            elif action == "scan":
                svc.discover_devices()
                self.bt_msg = "LOOKING FOR KEYBOARDS..."
            elif action == "forget":
                svc.forget()
                self.bt_devices = []
                self.bt_sel = 0
                self.bt_msg = "FORGOT SAVED KEYBOARD"
            elif action == "connect":
                if address is None and self.bt_devices:
                    address = self.bt_devices[self.bt_sel][0]
                if address is None:
                    self.bt_msg = "SCAN, THEN PICK A KEYBOARD"
                elif svc.connect_device(address):
                    self.bt_msg = "CONNECTING + SAVING..."
                else:
                    self.bt_msg = "COULD NOT PICK KEYBOARD"
        except Exception as exc:  # noqa: BLE001
            self.bt_msg = ("KEYBOARD ERROR: " + str(exc))[:40]
        self._bt_state = None
        self.ws._dirty = True

    def _bt_input(self, i):
        if i.pressed("up") and self.bt_devices:
            self.bt_sel = (self.bt_sel - 1) % len(self.bt_devices)
            self.ws._dirty = True
        if i.pressed("down") and self.bt_devices:
            self.bt_sel = (self.bt_sel + 1) % len(self.bt_devices)
            self.ws._dirty = True
        if i.pressed("a"):
            self._bt_action("connect")
        if i.pressed("run"):
            self._bt_action("scan")
        if i.pressed("left"):
            svc = self._bt_service()
            if svc is not None and svc.settings_status()[0]:
                self._bt_action("toggle")
        if i.pressed("right"):
            svc = self._bt_service()
            if svc is not None and not svc.settings_status()[0]:
                self._bt_action("toggle")
        if i.pressed("b") or i.pressed("stop"):
            self.close_bluetooth()
        return True

    def bluetooth_animating(self):
        """Keep a quiet Settings window repainting while async BLE state moves."""
        if not self.bt_view:
            return False
        svc = self._bt_service()
        if svc is None:
            return False
        try:
            state = svc.settings_status()[1]
        except Exception:  # noqa: BLE001
            return False
        return state in ("scanning", "found", "connecting", "pairing",
                         "discovering", "subscribe-retry")

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
            # A saved/open network reconnects with "" -- the SERVICE resolves
            # the stored password (FakeWifi/DeviceWifi.connect). If a LOCKED
            # known net still fails (stale/wrong saved password), reopen the
            # password prompt so the kid can just retype it -- without this a
            # bad saved password stranded the network forever (on-glass P4,
            # 2026-07-25).
            ok = self._wifi_connect(ssid, "")
            if not ok and locked:
                self.wifi_pick = ssid
                self.wifi_pw = ""
                self._wifi_kprev = 0
                self.wifi_msg = "TYPE THE PASSWORD"
                ws._set_text_mode(True)
        ws._dirty = True

    def _wifi_connect(self, ssid, pw):
        ws = self.ws
        ok = False
        try:
            ok = bool(ws.wifi.connect(ssid, pw)) if ws.wifi is not None else False
        except Exception as exc:  # noqa: BLE001
            self.wifi_msg = ("CAN'T CONNECT: " + str(exc))[:34]
        else:
            # Voice (docs/os_voice_v1.md): say the fix, not the fault -- a typed
            # password gets the password message; an open/saved net stays generic.
            self.wifi_msg = ("CONNECTED TO " + str(ssid))[:34] if ok \
                else ("That password didn't work." if pw
                      else "Couldn't connect.")
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
        th = ws.theme_colors
        ws._icon("wifi" if connected else "wifi_off", x, y, cv)
        if connected:
            cv.print(("ON  " + str(ssid))[:22], x + 20 * fs, y + 5, th["play"], 1)
            if ip:
                cv.print(str(ip)[:15], x + w - 15 * fw, y + 5, NAMES["blue"], 1)
        else:
            cv.print("NOT CONNECTED", x + 20 * fs, y + 5, th["ink_dim"], 1)
        if self.wifi_pick is not None:
            # Password prompt: the picked ssid + the typed password + a caret.
            x, y, w, h = self._wifi_row_rect(0)
            cv.print(("PASSWORD FOR " + str(self.wifi_pick))[:30], x + 4, y + 5,
                     th["ink"], 1)
            bx, by, bw2, bh2 = self._wifi_row_rect(1)
            cv.rect(bx, by, bw2, bh2 - 2, NAMES["black"])
            cv.rectb(bx, by, bw2, bh2 - 2, ws.theme_colors["edge"])
            shown = self.wifi_pw[-max(4, bw2 // fw - 3):]
            cv.print(shown, bx + 4, by + 5, NAMES["yellow"], 1)
            cv.rect(bx + 4 + len(shown) * fw, by + 3, fs, bh2 - 8, NAMES["yellow"])
            x, y, w, h = self._wifi_row_rect(2)
            cv.print("ENTER = CONNECT   ESC = BACK", x + 4, y + 5,
                     th["ink_dim"], 1)
        else:
            # The network list.
            for k in range(len(self.wifi_nets)):
                ssid_k, sig, locked = self.wifi_nets[k]
                x, y, w, h = self._wifi_row_rect(k)
                if y + h > py + ph - 30 * fs:
                    break                          # keep clear of the button row
                sel = (k == self.wifi_sel)
                if sel:
                    cv.rect(x, y, w, h, th["hilite"])
                fg = th["selection_ink"] if sel else th["ink_dim"]
                cv.print(str(ssid_k)[:16], x + 4, y + 5, fg, 1)
                bars = max(0, min(4, int(sig) // 25 + 1))
                for s in range(4):
                    c = th["play"] if s < bars else th["ink_dim"]
                    cv.rect(x + w - 46 * fs + s * 8 * fs, y + h - 6 * fs - 2 * fs * s,
                            5 * fs, (2 + 2 * s) * fs, c)
                if locked:
                    ws._glyph("lock", (x + w - 62 * fs, y + 2, 12 * fs, 12 * fs),
                              NAMES["orange"], cv)
                if str(ssid_k) in self.wifi_known:
                    cv.print("SAVED", x + w - 110 * fs, y + 5, NAMES["blue"], 1)
        if self.wifi_msg:
            mx, my = px + 10 * fs, py + ph - 30 * fs - 10 * fs
            cv.print(self.wifi_msg[:36], mx, my, th["accent"], 1)
        for name, rect in self._wifi_btns():
            label = name.upper()
            color = {"connect": th["play"], "forget": th["danger"],
                     "rescan": NAMES["blue"], "back": NAMES["dark_grey"]}[name]
            ws._btn(label, rect, color, cv)

    def _draw_bluetooth(self):
        """Responsive Bluetooth keyboard picker in the visual-identity v1
        vocabulary: semantic panel/status/buttons, focus ring, and Hits where
        the draw pass itself registers every pointer target."""
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        lay = ws.layout
        fs = lay.fs
        fw = lay.font_w
        px, py, pw, ph = lay.settings_panel
        status = self._bt_refresh()

        # Occupy the Settings body below its own title strip. ui.panel returns
        # the exact content rect; every remaining band is cut responsively.
        body = (lay.set_x, lay.set_row_y0, lay.set_w,
                max(1, py + ph - lay.set_row_y0 - 4 * fs))
        content = _ui.panel(cv, th, body, title="BLUETOOTH KEYBOARD", fs=fs)
        status_r, content = _ui.cut_top(content, 18 * fs)
        msg_r, content = _ui.cut_top(content, 14 * fs)
        actions_r, list_r = _ui.cut_bottom(content, 24 * fs)
        list_r = _ui.inset(list_r, 4 * fs, 3 * fs)

        enabled, state, name, _preferred, _error = status
        _ui.status_row(cv, th, status_r,
                       (("ON" if enabled else "OFF"), state.upper(), name or "NO DEVICE"))
        cv.print(self.bt_msg[:max(1, msg_r[2] // fw - 1)],
                 msg_r[0] + 3 * fs, msg_r[1] + (msg_r[3] - 8 * fs) // 2,
                 th["ink_dim"], 1)

        self._bt_hits.clear()
        row_h = 24 * fs
        visible = max(1, list_r[3] // row_h)
        top = 0
        if self.bt_sel >= visible:
            top = self.bt_sel - visible + 1
        end = min(len(self.bt_devices), top + visible)
        if not self.bt_devices:
            label = "SCANNING..." if state == "scanning" else "NO KEYBOARDS FOUND"
            cv.print(label, list_r[0] + 3 * fs, list_r[1] + 6 * fs,
                     th["ink_dim"], 1)
        for i in range(top, end):
            address, dev_name, rssi, preferred, connected = self.bt_devices[i]
            rect = (list_r[0], list_r[1] + (i - top) * row_h,
                    list_r[2], row_h - 3 * fs)
            prefix = "ONLINE " if connected else ("SAVED " if preferred else "")
            tail = "  %d" % int(rssi) if int(rssi) > -127 else ""
            _ui.button(cv, th, rect, prefix + str(dev_name) + tail,
                       kind="play" if connected else "normal", on=preferred)
            self._bt_hits.add(rect, "device", address)
            if i == self.bt_sel:
                _ui.focus_ring(cv, th, rect, fs)

        action_defs = (("toggle", "OFF" if enabled else "ON", "normal"),
                       ("connect", "USE", "play"),
                       ("scan", "SCAN", "author"),
                       ("forget", "FORGET", "danger"),
                       ("back", "BACK", "normal"))
        rects = _ui.hsplit(_ui.inset(actions_r, 3 * fs), len(action_defs), 3 * fs)
        for j in range(len(action_defs)):
            action, label, kind = action_defs[j]
            _ui.button(cv, th, rects[j], label, kind=kind,
                       on=(action == "toggle" and enabled))
            self._bt_hits.add(rects[j], action)

    def _bt_pointer(self, px, py, click):
        if not click:
            return True
        hit = self._bt_hits.at(px, py)
        if hit is None:
            return True
        action, arg = hit
        if action == "device":
            for i in range(len(self.bt_devices)):
                if self.bt_devices[i][0] == arg:
                    if i == self.bt_sel:
                        self._bt_action("connect", arg)  # second tap = USE
                    else:
                        self.bt_sel = i
                        self.ws._dirty = True
                    break
        else:
            self._bt_action(action)
        return True

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
        the updater without re-statting per draw.

        MEMOIZED on the four capability flags, because "on demand" turned out to mean
        ~15 times per FRAME -- the draw loop asks once per ROW, and the pointer/scroll
        paths ask again -- and each call rebuilt the tuple through up to four
        concatenations. On P4 glass that was ~2.3KB of the ~4.5KB a Settings frame
        allocated, and since ~70KB of churn buys one 55ms mark-sweep there, it was the
        largest single contributor to the scroll hitches (measured with
        tools/p4_alloc.py, 2026-07-26). Each flag is a cached bool / getattr, so
        re-checking them per call stays free; only the rebuild is skipped. The flags
        are compared as separate attributes rather than a signature tuple, because
        building a tuple to test the cache would reintroduce ~40B of the churn the
        cache exists to remove."""
        ws = self.ws
        bt = self._bt_service() is not None
        upd = ws._update_available()
        onl = ws._online_update_available()
        web = getattr(ws, "webhost", None) is not None
        if (self._rows_cache is not None and bt == self._rows_bt
                and upd == self._rows_upd and onl == self._rows_onl
                and web == self._rows_web):
            return self._rows_cache
        rows = self._SETTINGS_ROWS
        if bt:
            # Keep network/input together. Dynamic capability gating preserves
            # the non-P4 Settings row indices and frozen 320x240 pixels.
            rows = rows[:1] + (("bluetooth", "BLUETOOTH KEYBOARD", "bluetooth"),) \
                + rows[1:]
        if web:
            # WEB CONSOLE (moycore plan 3.4): serve the wasm console from this
            # board, so a browser on the same network opens YOUR carts. Its own
            # kind rather than a "diag" ON/OFF because when it is on the useful
            # thing to show is the ADDRESS -- an "ON" with no url is just an
            # instruction to go find the IP somewhere else.
            rows = rows + (("webhost", "WEB CONSOLE", "webhost"),)
        if upd:
            rows = rows + (("update", "UPDATE FW", "action"),)
        if onl:
            rows = rows + (("ota_channel", "CHANNEL", "channel"),)
            rows = rows + (("update_online", "UPDATE ONLINE", "action"),)
        self._rows_bt = bt
        self._rows_upd = upd
        self._rows_onl = onl
        self._rows_web = web
        self._rows_cache = rows
        return rows

    def _activate_settings_action(self, key):
        """Fire an "action" Settings row by key: APPEARANCE opens the Appearance app
        (wallpaper + theme), EDIT ICONS opens the theme editor, UPDATE FW installs a
        local SD image, UPDATE ONLINE checks WiFi for one (#53)."""
        ws = self.ws
        if key == "appearance":
            ws.open_app(ws.appearance_app)   # the one appearance surface
        elif key == "update":
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
        elif key == "show_fps":
            ws.set_show_fps(not ws.show_fps)
        else:
            ws.set_diag_live(not ws.diag_live)

    def settings_adjust(self, d):
        """Step the selected Settings row by d. Font applies + persists; the
        mock rows just move a cosmetic value held in ws.system (not acted on); an
        "action" row (APPEARANCE / EDIT ICONS) fires its action regardless of
        direction."""
        ws = self.ws
        key, _label, kind = self._settings_rows()[self.set_msel]
        if kind == "wifi-net":                  # WIFI: any step/tap opens the panel (#38)
            self.open_wifi()
            return
        if kind == "bluetooth":
            self.open_bluetooth()
            return
        if kind == "action":                    # EDIT ICONS / UPDATE FW: open the tool
            self._activate_settings_action(key)
            return
        if kind == "diag":                      # the ON/OFF gates (#68 diag, #77 frameskip)
            self._toggle_diag_row(key)
            return
        if kind == "webhost":                   # WEB CONSOLE: serve / stop serving
            ws.toggle_webhost()
            return
        if key == "ota_channel":                # OTA update channel STABLE <-> BETA
            ws._cycle_channel(d)
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

    # -- scroll window -------------------------------------------------------

    def _scroll_region(self):
        """The rows' ui.ScrollRegion. set_top stays the row-slot source of truth;
        the region is the touch INTERACTION model (drag) + the shelf-tier
        scrollbar, synced from set_top when a drag is not active."""
        if self.scroll is None:
            self.scroll = _ui.ScrollRegion()
            self._taps = _ui.DragTap(self.scroll)
        ws = self.ws
        lay = ws.layout
        rows = self._settings_rows()
        area = (lay.set_x, lay.set_row_y0, lay.set_w,
                self._settings_visible() * lay.set_row_h)
        self.scroll.set(area, len(rows) * lay.set_row_h)
        # Keep the sub-row remainder while a drag is active.  Re-snapping from
        # set_top on every pointer sample discards normal 3-5px finger movement,
        # so a gradual drag can never accumulate enough travel to cross a row.
        if not self.scroll.drag_active:
            self.scroll.offset = self.set_top * lay.set_row_h
        return self.scroll

    def _rows_pointer(self, px, py, click):
        """The row list's pointer machine -- the SAME shared ui.DragTap the
        Library shelf rides: a held drag scrolls the rows (snapped to whole
        rows; set_top stays the state of record), and a row activates only on
        a clean tap RELEASE -- so letting go of a scroll can never 'click' the
        row under the finger. Returns True when it consumed a tap."""
        ws = self.ws
        sr = self._scroll_region()
        press = self._taps.frame(px, py, click, ws.pointer.down,
                                 slop=4 * ws.layout.fs + 2)
        if self._taps.dragging:
            rows = len(self._settings_rows())
            vis = self._settings_visible()
            top = sr.offset // ws.layout.set_row_h
            self.set_top = max(0, min(max(0, rows - vis), top))
            # Drag the SELECTION along with the view: the highlighted row must
            # stay on screen, or the next d-pad press (which nudges the view
            # back to the selection) would yank the list to wherever the
            # selection was left behind.
            if self.set_msel < self.set_top:
                self.set_msel = self.set_top
            elif self.set_msel >= self.set_top + vis:
                self.set_msel = self.set_top + vis - 1
        if press is not None:
            return self._row_tap(press[0], press[1])
        return False

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
        if self.bt_view:
            return self._bt_input(i)
        rows = self._settings_rows()
        # The keep-selection-visible clamp (#53) fires ONLY when the keyboard
        # moves the selection -- NOT every frame. The per-frame form fought the
        # touch drag: every drag frame's set_top got yanked back to keep the
        # (never-moved) selected row in view, so the rows never visibly
        # scrolled and the release re-snap threw the list back to the top
        # (the on-glass P4 report, 2026-07-25).
        if i.pressed("up"):
            self.set_msel = (self.set_msel - 1) % len(rows)
            self._settings_scroll()
        if i.pressed("down"):
            self.set_msel = (self.set_msel + 1) % len(rows)
            self._settings_scroll()
        # Range-only safety clamp (what the per-frame _settings_scroll used to
        # provide): a shrinking row set / a font-scale change must not strand
        # set_top past the end. No selection nudge -- that is the drag's fight.
        self.set_top = max(0, min(self.set_top,
                                  max(0, len(rows) - self._settings_visible())))
        if i.pressed("left"):
            self.settings_adjust(-1)
        if i.pressed("right"):
            self.settings_adjust(1)
        if i.pressed("a") or i.pressed("run"):  # activate an action row (EDIT ICONS / UPDATE FW)
            row = rows[self.set_msel % len(rows)]
            if row[2] == "wifi-net":            # WIFI: open the panel (#38)
                self.open_wifi()
            elif row[2] == "bluetooth":
                self.open_bluetooth()
            elif row[2] == "action":
                self._activate_settings_action(row[0])
            elif row[2] == "diag":              # ... and the ON/OFF gates (#68/#77)
                self._toggle_diag_row(row[0])
        if i.pressed("b"):
            ws._exit_settings()          # back -> resume the cart if opened from one
        elif i.pressed("home") or i.pressed("stop"):
            ws.go_home()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if not self.wifi_view and not self.bt_view and not ws.show_achievements:
            # The rows' shared press/drag/release machine: scrolls on drag,
            # activates a row only on a clean tap release.
            if self._rows_pointer(px, py, click):
                return True
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
        if self.bt_view:
            return self._bt_pointer(px, py, click)
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
        return True

    def _row_tap(self, px, py):
        """Activate the settings row under a clean tap (dispatched from
        _rows_pointer on the RELEASE, never the press edge)."""
        ws = self.ws
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
                if rows[i][2] == "bluetooth":
                    self.open_bluetooth()
                    return True
                if rows[i][2] == "action":
                    self._activate_settings_action(rows[i][0])  # EDIT ICONS / UPDATE FW
                    return True
                if rows[i][2] == "webhost":        # WEB CONSOLE: any tap toggles
                    self.ws.toggle_webhost()
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
        return False

    # -- draw ----------------------------------------------------------------

    def draw(self, dt):
        """The Settings app (#28): wallpaper picker + font-size picker (both
        FUNCTIONAL, persist) plus the mocked rows, over the live wallpaper so the
        backdrop preview is honest. On the SYSTEM canvas; panel + title-row controls
        reflow with the layout/font scale (#39)."""
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        fs = lay.fs
        px, py, pw, ph = lay.settings_panel
        th = ws.theme_colors
        # Backdrop. FULLSCREEN tiers keep the live wallpaper behind the panel (the
        # honest preview this app is partly about). Inside a WM WINDOW that is pure
        # waste: the window already sits ON the desktop wallpaper, and rendering the
        # whole cover-crop composite AGAIN into the window buffer measured **70ms of
        # a 98ms** Settings frame on the P4 -- the single worst UI cost found in the
        # 2026-07-26 panel sweep (settings scroll drag p90 174ms vs the fullscreen
        # Library's 40ms). The panel does not cover the window (646x422 inside
        # 678x510), so the margin still needs filling -- one flat fill does it.
        _windowed = getattr(ws, "windowed_chrome", False)
        if _windowed:
            cv.cls(th["panel"])
        else:
            ws.wallpaper.draw(dt)
            # (windowed: cls above already laid this exact colour over the whole
            # buffer -- re-filling the panel rect would paint 272k of the same
            # pixels a second time, ~9ms of pure duplicate fill.)
            cv.rect(px, py, pw, ph, th["panel"])
        cv.rectb(px, py, pw, ph, th["edge"])
        # Inside a WM window (#73) the title strip already says SETTINGS and carries
        # the closing X, so the panel's own header + X are suppressed (no doubled
        # chrome); the trophy (the achievements door, #21) stays either way.
        p_ink = th["ink"] if th.get("bar_light", False) else th["chrome_ink"]
        if not getattr(ws, "windowed_chrome", False):
            ws._glyph("gear", (px + 6, py + 2, 14 * fs, 14 * fs), th["accent"], cv)
            cv.print("SETTINGS", px + 24, py + 4, p_ink, 2)
            ws._mini_btn("X", lay.set_back, th["danger"], cv)
        if self.wifi_view:
            # The WIFI panel (#38) replaces the row list (its BACK returns here).
            self._draw_wifi()
            ws.bar_layer._draw_status_strip("settings")
            ws.bar_layer._draw_dock("settings")
            return
        if self.bt_view:
            self._draw_bluetooth()
            ws.bar_layer._draw_status_strip("settings")
            ws.bar_layer._draw_dock("settings")
            return
        # Achievements view button (#21): a trophy badge with the unlocked count.
        sa = lay.set_ach
        cv.rect(sa[0], sa[1], sa[2], sa[3], th["hilite"])
        ws._glyph("trophy", (sa[0] - 2, sa[1], 14 * fs, 14 * fs), th["accent"], cv)
        cv.print(str(ws.ach.count()), sa[0] + 13 * fs, sa[1] + 4,
                 th["selection_ink"], 1)
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
        th_ = ws.theme_colors
        _ui.scroll_cues(
            cv, (xr, lay.set_row_y0), (xr, py + ph - 9 * lay.fs),
            self.set_top > 0, self.set_top + vis < len(rows),
            th_["ink"] if th_.get("bar_light", False) else th_["chrome_ink"])
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
        th = ws.theme_colors
        key, label, kind = self._settings_rows()[i]
        x, y, w, h = self._settings_row_rect(i)
        sel = (i == self.set_msel)
        if sel:
            cv.rect(x, y, w, h, th["hilite"])
        fg = th["selection_ink"] if sel else th["chrome_ink_dim"]
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
        if kind == "bluetooth":
            # Capability-gated P4 row: exact selected name when available,
            # otherwise the radio/input gate. No legacy +/- stepper.
            try:
                enabled, state, name, _preferred, _error = \
                    self._bt_service().settings_status()
            except Exception:  # noqa: BLE001 -- a radio hiccup reads OFF
                enabled, state, name = False, "error", None
            value = (str(name)[:12] if name else state.upper()) if enabled else "OFF"
            cv.print(value, x + w - 106 * lay.fs, y + 5,
                     NAMES["green"] if state == "ready" else NAMES["dark_grey"], 1)
            cv.print("OPEN", x + w - 38 * lay.fs, y + 5, NAMES["blue"], 1)
            return
        if kind == "action":
            # An action row (APPEARANCE / EDIT ICONS / UPDATE FW / UPDATE ONLINE): no
            # value/stepper -- just an OPEN affordance at the right so a tap (or A) is
            # the obvious activate. The glyph cues what it does (star = wallpaper+theme;
            # paint = repaint chrome; run = install; wifi = online update).
            if key == "appearance":
                g, c = "star", NAMES["pink"]
            elif key == "update":
                g, c = "run", NAMES["yellow"]
            elif key == "update_online":
                g, c = "wifi", NAMES["yellow"]
            else:
                g, c = "paint", NAMES["green"]
            ws._glyph(g, (x + w - 18 * lay.fs, y + 2, 14 * lay.fs, 14 * lay.fs), c, cv)
            return
        # < value > stepper at the right (the chevrons print at double size = 2*fw).
        cv.print("<", x + w - 11 * fw - 2, y + 5, th["author"], 2)
        cv.print(">", x + w - 2 * fw + 2, y + 5, th["author"], 2)
        vx = x + w - 78 * lay.fs           # value column (baseline x+w-78)
        if kind == "font":                 # system-UI font size (#39): 1x / 2x / 3x
            cv.print("%dx" % ws.font_scale, vx, y + 5, th["play"], 1)
        elif kind == "mock-gauge":
            lvl = int(ws.system.get(key, 3))
            for s in range(5):
                c = th["play"] if s < lvl else NAMES["dark_grey"]
                cv.rect(vx + s * 8 * lay.fs, y + 6, 6 * lay.fs, 8 * lay.fs, c)
        elif kind == "mock-name":
            cv.print(str(ws.system.get("name", self._MOCK_NAMES[0]))[:8], vx, y + 5,
                     NAMES["peach"], 1)
        elif kind == "channel":            # OTA update channel: STABLE / BETA (#53)
            beta = ws._ota_channel() == "unstable"
            cv.print("BETA" if beta else "STABLE", vx, y + 5,
                     NAMES["orange"] if beta else NAMES["green"], 1)
        elif kind == "diag":               # #68 diag gates: ON/OFF (key-driven)
            on = bool(getattr(ws, key, False))
            cv.print("ON" if on else "OFF", vx, y + 5,
                     NAMES["orange"] if on else NAMES["dark_grey"], 1)
        elif kind == "webhost":            # WEB CONSOLE: the ADDRESS, not "ON"
            # RIGHT-ALIGNED, not printed at the value column, because the value
            # column is 78px = 9 characters and the thing this row exists to
            # show is 18 ("192.168.1.155:8080"). It rendered as "192.168.1" on
            # glass -- an address that is not merely ugly but WRONG, since a kid
            # would type it into a browser and get nothing. Right-aligning lets
            # it use the empty gap between the label and the edge, which is
            # where the room already was; it stops at the label rather than
            # overprinting it, and only then falls back to dropping :8080 (the
            # default port a browser assumes for nothing, so it is the last
            # resort and not the first).
            lbl = ws.webhost_label()
            col = (NAMES["orange"] if ws.webhost_serving()
                   else NAMES["dark_grey"])
            room = w - 84 * lay.fs         # gap after "WEB CONSOLE" at this scale
            if len(lbl) * fw > room and lbl.endswith(":8080"):
                lbl = lbl[:-5]
            tx = x + w - len(lbl) * fw - 2
            if tx < x + 84 * lay.fs:       # still too long: keep the tail, which
                tx = x + 84 * lay.fs       # is the part that varies (the host)
            cv.print(lbl, tx, y + 5, col, 1)
        # Mark not-yet-functional rows clearly (wifi + font + channel +
        # diag + actions work).
        if kind not in ("wifi-net", "bluetooth", "font", "action", "channel",
                        "diag", "webhost"):
            cv.print("soon", x + 4, y + 6 + fw, NAMES["dark_grey"], 1)
