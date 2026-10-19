# Map (grep -n a name to jump there):
#   -- settings-screen geometry      the Settings constants (console.py imports them back)
#   -- the settings-toggle registry  the rows a toggle derives
#   SettingsLayer                    the Settings app: its pages, rows and panels
#   SettingsLayer.settings_adjust    step a row
#   SettingsLayer.open_bluetooth     the Bluetooth keyboard picker
#   SettingsLayer.draw               the Settings app
"""The Settings app (#28/#39/#53): the console's AGGREGATOR, a page of rows
in captioned groups (docs/look_2026-10.md step 19) drawn with the public UI
API's verbs (`runtime/ui.py`, docs/ui_api_2026-10.md) and nothing else: the
engine owns focus, navigation, activation and the page's scroll; this layer
owns what each row shows and does.

The groups: General (Wi-Fi, the Bluetooth keyboard where a board has one,
Appearance, Text size, Layout, Volume, Brightness, Name, and Edit icons
where the theme draws the IconSheet), Play and Developer (the SETTINGS_TOGGLES registry's ON/OFF
rows), Power (the idle ladder's rungs), Updates (the web console, the OTA
channel and the update flows) and About (firmware, board, storage, battery
and the achievements). Brightness and Name store a value nothing applies
yet. The Wi-Fi panel, the Bluetooth picker, a toggle's warning and the last
crash record are pages of their own in place of the rows; the Wi-Fi panel is
also what the bar's wifi popover holds (`wifi_page`, runtime/popover.py).

Boundary: SettingsLayer owns NO config. Every value it steps or shows is
CART/SYSTEM state on Workstation -- ws.system (the system.json rows),
ws.look.font_scale, ws.diag_live, the updater queries -- and every mutation
goes through the ws setters (ws.look.cycle_font_scale / _cycle_channel /
ws.system.set / the SETTINGS_TOGGLES verbs). The module-level
SETTINGS_TOGGLES registry DECLARES the ON/OFF settings -- name, label,
default, verb, capability gate, serial word -- and console.py and
dev_channel.py import it back rather than each keeping a hand copy (#209
section 7). Wallpaper + panel-theme picking is the Appearance app's; Settings
deep-links to it. The actions Settings hosts delegate OUT to other layers
(ws.open_app / ws.update_ui.open_update / ws.show_achievements);
ws.open_settings / _exit_settings (the lifecycle) stay on ws. The frame the
verbs draw in is begun by the host side the console hands it
(the AppHost a shipped app is handed as `ctx.ui`), fed the keys and the
pointer this layer is routed.
"""

try:
    import ui as _ui
    import layout_base as _lb
    from crash_guard import crash_available, crash_lines, last_crash
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui
    from runtime import layout_base as _lb
    from runtime.crash_guard import crash_available, crash_lines, last_crash

try:
    from editors_base import TextEntry
    from audio_session import console_volume
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors_base import TextEntry
    from runtime.audio_session import console_volume

# -- settings-screen geometry: the row height console's Layout scales ---------
_SET_ROW_H = 26

# (The panel colors are the selectable THEME tokens -- chrome.theme_colors,
# picked in the Appearance app; the default is Moy's Day.)


# -- the settings-toggle registry (#209, console_architecture_2026-08.md 7) ---
#
# ONE declaration per persisted ON/OFF setting. Adding a toggle used to walk
# FIVE hand-kept sites -- the `ws.set_*` verb, the boot apply in load_system,
# the row list below plus its capability splice, the tap dispatch, and the dev
# channel's serial word -- and crisp_pixels walked every one of them in
# 2026-08. Four of the five are this table now, read by console.py (the flat
# defaults, the boot apply and the persistence tail) and dev_channel.py (the
# serial words). What is deliberately NOT here is what each toggle uniquely
# DOES: the phase reset, the canvas hook, the keyboard hand-over stay written
# out in the `set_*` method the entry NAMES. The registry declares; it does not
# absorb.
#
# Per entry:
#   key      the ONE name -- the system.json key, the flat `ws.<key>` mirror,
#            and the Settings row's dispatch key. All six were already the same
#            string in three places; the registry is what pins that.
#   label    the row text. THIS ORDER IS THE ON-SCREEN ORDER (the block sits
#            below EDIT ICONS, above the injected web/OTA rows).
#   default  the value before system.json is read, and the fallback when the
#            store says nothing about this key.
#   setter   the `ws` verb every path goes through. Never a raw attribute
#            write -- a toggle that only set its mirror would skip its apply.
#   gate     None, or a predicate answering "can this BOARD serve it". The
#            gates are per-board / per-service and are EXPRESSED, not
#            flattened: a falsey gate hides the row (which is what keeps the
#            other tiers' frozen Settings pixels) and declines the serial word.
#            It never quietly turns the toggle into a no-op that still reports
#            ON -- `set_two_player` is the honest end of the same rule, and
#            reports OFF whatever it is told when no second keyboard exists.
#   dev      the dev-channel word, or None. `diag_live` has none ON PURPOSE:
#            `diag` is not a plain toggle over there (it drives perf_capture
#            and the FPS chip along with the gate), so it stays written out.
#
# Every entry renders as the "diag" row kind: a generic ON/OFF that reads the
# flat mirror by name. Which is the other half of the contract -- the mirrors
# stay FLAT ATTRIBUTES and this table never becomes a read path. Both WMs read
# `show_fps` on every painted game frame on all three boards, and a dict lookup
# there would buy a boot-time convenience at a per-frame price.


def _gate_second_keyboard(ws):
    """LOCAL 2P needs a SECOND keyboard -- see Workstation.second_keyboard: on
    the touch-only boards the paired Bluetooth keyboard IS ws.keyboard, so
    handing it to player two leaves player one with nothing to press."""
    return ws.second_keyboard() is not None


def _gate_crisp_scale(ws):
    """CRISP PIXELS exists only where the hardware scaler is fixed bilinear
    (the P4's PPA, the one such tier); every other composite is nearest
    already, and the canvas hook is how a board says so."""
    return getattr(ws.sys_canvas, "set_crisp_scale", None) is not None


SETTINGS_TOGGLES = (
    # 2 PLAYERS (#65 Phase 1): hand a PAIRED BLUETOOTH KEYBOARD to player two,
    # so two kids play one console on two real keyboards with no radio between
    # consoles. Gated to a board that has a SECOND keyboard (the T-Deck, whose
    # BLE one sits alongside its physical C3). Default OFF: somebody playing
    # alone with a Bluetooth keyboard wants to be player one, not player two.
    ("two_player", "2 PLAYERS", False, "set_two_player",
     _gate_second_keyboard, None),
    # STEADY (#217): the tick model's one knob. A GAME's logic always runs at
    # its declared rate; its draw runs on an integer divisor the Player picks
    # from what draw frames cost. ON, the divisor is re-decided once every
    # couple of seconds with hysteresis, so a cadence holds through a hitch
    # and a heavy menu does not condemn the game; OFF (FREE) it follows load
    # on every draw frame and judders at transitions. Default ON.
    ("steady", "STEADY", True, "set_steady", None, "steady"),
    # CRISP PIXELS (#204): nearest-neighbour composite of the palette-based
    # game -- every cart drawn through the canvas, and a compiled cart's
    # palette frames -- instead of the PPA's fixed-bilinear scaler; a compiled
    # cart's direct-colour (blit565) frame keeps the bilinear scale either
    # way. Sits by STEADY -- both are play-time quality/perf trades. Default
    # OFF: smooth is the shipped behaviour, and the trade is sharp pixel art
    # against a real per-frame CPU cost the async PPA path does not pay.
    ("crisp_pixels", "CRISP PIXELS", False, "set_crisp_pixels",
     _gate_crisp_scale, "crisp"),
    # SHOW FPS: the in-game FPS chip (default ON). It rides the GAME canvas and
    # its composite scale, so on a small-canvas cart (celeste) it draws 2x big
    # -- which is what prompted the off switch. Purely cosmetic: the perf
    # fields keep updating and PERF DIAG is untouched.
    ("show_fps", "SHOW FPS", True, "set_show_fps", None, None),
    # PERF DIAG (#68 "kid mode" gate): OFF (the kid default) writes nothing
    # periodic on any board -- no PERF line, no T-Deck diag tick, no audio
    # rate lines, no deep capture meters, no periodic diag->SD write -- because
    # a player FEELS each of them on device, the lines as garbage the collector
    # stops the frame for (owner call 2026-09-30). The RAM ring still collects
    # EVENTS (boot, errors, hitches) and flushes on crash / cart exit, so "play
    # -> crash -> read diag.log" works either way. The board loop reads
    # ws.diag_live each cycle, so a flip lands within a frame; a tool that
    # reads PERF turns it on and puts it back. Host: nothing to gate.
    ("diag_live", "PERF DIAG", False, "set_diag_live", None, None),
    # DIAG SD LOG (#68 follow-up, owner call 2026-07-08): the periodic
    # diag->SD write is its OWN gate -- PERF DIAG ON + this OFF = serial-only
    # measurement with no 20s sdflush stutter. ON restores the offline
    # play-then-read-diag.log workflow. Crash/cart-exit flushes stay
    # unconditional either way (the safety net).
    ("diag_sd", "DIAG SD LOG", False, "set_diag_sd", None, None),
    # UNKNOWN SOURCES (owner, 2026-09-29, docs/wasm_tier_plan_2026-09.md): a
    # compiled cart whose module carries no signature may load -- a cart
    # somebody rebuilt from its source, on their own console. The module's
    # provenance key is still checked, and one whose signature is present but
    # does not verify is refused either way (native/moy_wasm/README.md).
    # Default OFF; ON goes through the warning in TOGGLE_CONFIRMS, OFF is
    # immediate. Every tier carries the row, though only a board checks
    # signatures.
    ("unknown_sources", "UNKNOWN SOURCES", False, "set_unknown_sources", None,
     "unknown_sources"),
)

# The toggles whose ON goes through a warning first: key -> (the warning's
# title, what turning it on means in one or two Spoken sentences, the button
# that turns it on). A tap, a step or A on the row opens the warning instead of
# flipping the toggle, and the warning's keyboard focus starts on KEEP OFF, so
# turning it on is a deliberate move and a press; turning it OFF is immediate.
# The boot apply and the dev channel's word call the setter directly: neither
# is somebody deciding at the screen.
TOGGLE_CONFIRMS = {
    "unknown_sources": (
        "UNKNOWN SOURCES",
        "Unsigned carts can do anything on this console. "
        "Only run ones you trust.",
        "TURN ON"),
}
KEEP_OFF = "KEEP OFF"


# What a row's label says on the page (sentence case; the registry and the
# row tuples keep their keys and words).
_SAY = {"WIFI": "Wi-Fi", "FONT SIZE": "Text size", "UPDATE FW": "Update from SD card",
        "DIAG SD LOG": "Diag SD log", "UPGRADE C6 RADIO": "Upgrade C6 radio",
        "SHOW FPS": "Show FPS"}


_SAID = {}                    # label -> its words, made once


def say(label):
    """Row label `label` as the page shows it."""
    s = _SAID.get(label)
    if s is None:
        s = _SAY.get(label)
        if s is None:
            s = label[:1] + label[1:].lower()
        _SAID[label] = s
    return s


# The text sizes the Text size row names, by font scale (appearance FONT_SCALES).
_SIZES = {1: "Medium", 2: "Large", 3: "Largest"}
_STABLE_BETA = ("Stable", "Beta")

# A board's name in About, by its OTA id (each board.toml's [board] ota).
_BOARDS = {"tdeck": "T-Deck Plus", "p4": "ESP32-P4 7B", "guition_s3": "JC3248W535",
           "guition_p4": "JC8012P4A1C", "xiao_zero": "XIAO ESP32-S3"}

# The groups, in page order: (caption, row keys). A row of kind `idle` is
# Power's.
_GROUPS = (
    ("General", ("wifi", "bluetooth", "appearance", "font_scale", "layout_theme",
                 "volume", "brightness", "name", "icons")),
    ("Play", ("two_player", "steady", "crisp_pixels", "show_fps")),
    ("Power", ()),
    ("Updates", ("webhost", "ota_channel", "update_online", "update", "update_c6")),
    ("Developer", ("diag_live", "diag_sd", "unknown_sources", "crash")),
)

# The keycap hints for the focused row's kind.
_HINT_OPEN = ("confirm", "Open", "back", "Back")
_HINT_CHANGE = ("change", "Change", "back", "Back")
_HINT_TYPE = ("confirm", "Done", "cancel", "Cancel")
_HINTS = {"cycle": _HINT_CHANGE, "seg": _HINT_CHANGE, "slider": _HINT_CHANGE,
          "toggle_on": ("confirm", "Turn off", "back", "Back"),
          "toggle_off": ("confirm", "Turn on", "back", "Back"), "field": _HINT_TYPE}

# The level rows, by system.json key: (lowest, highest, the value with none
# stored). Volume is the console's level (`audio_session.console_volume`,
# the one the boot applies and the dev channel's `vol` sets); Brightness is
# stored and applied by nothing yet.
LEVELS = {"volume": (0, 7, 7), "brightness": (0, 7, 4)}
_STEPPED = {"steps": True}    # a level row's slider: a segment a level, no number
NAME_MAX = 16                 # the Name row's bytes
_NAME_HINT = {"hint": "Your name"}   # what the empty Name field shows
WIFI_PW_MAX = 64              # a Wi-Fi password's bytes (WPA2 takes 63)

_NETS = 24                    # the networks the Wi-Fi page lists


def _capital(s):
    """`s` with each word's first letter upper case (MicroPython's str has
    no `title`)."""
    out = ""
    up = True
    for ch in s:
        out += ch.upper() if up else ch
        up = ch == " "
    return out


def _wrap(text, n):
    """`text` in lines of at most `n` characters, broken at spaces."""
    out, line = [], ""
    for w in text.split():
        if line and len(line) + 1 + len(w) > n:
            out.append(line)
            line = w
        else:
            line = w if not line else line + " " + w
    if line:
        out.append(line)
    return out


class SettingsLayer:
    """The Settings content Layer (system domain): the rows page, or the
    page of a panel it opened, drawn with the public UI API's verbs. Reads
    ws config/system state and dispatches every mutation to ws setters.

    The bar's lent zone is the back chevron and the title where the bar
    places no X (runtime/bar_layer.py draws it from `back_title`): a tap on
    the chevron is `back`, as Esc and B are, one on the title knocks on the
    secret door (#21)."""

    id = "settings"
    domain = "system"
    zone_gen = 0
    back_title = "Settings"

    _SETTINGS_ROWS = (
        # WIFI (#38, spec shell_ux_v1.md §10: wifi setup lives in Settings): a
        # row that opens the wifi PANEL -- scan/pick/password/connect over the
        # injected ws.wifi service. Settings is a system APP (not a cart), so
        # this works while a game keeps running in the one cart slot.
        ("wifi", "WIFI", "wifi-net"),
        # APPEARANCE: wallpaper + panel theme are the Appearance app's (the
        # ONE appearance surface); this row deep-links to it.
        ("appearance", "APPEARANCE", "action"),
        ("font_scale", "FONT SIZE", "font"),
        # VOLUME applies the console's level; BRIGHTNESS and NAME are
        # stored in system.json and applied by nothing yet.
        ("volume", "VOLUME", "level"),
        ("brightness", "BRIGHTNESS", "level"),
        ("name", "NAME", "name"),
        # EDIT ICONS (Stage 2): opens the PAINT editor on the system icon
        # sheet so a kid can repaint the top-bar chrome.
        ("icons", "EDIT ICONS", "action"),
        # The ON/OFF gate rows are NOT here: they are declared once in
        # SETTINGS_TOGGLES above and spliced in by _toggle_rows below, in
        # registry order, directly after this row.
    )

    def __init__(self, ws, host):
        self.ws = ws
        self._host = host()           # the rows page's UI context (an AppHost)
        self._sub = host()            # a panel's, fresh each time one opens
        self._view = None             # the page drawn last
        self._want = None             # the label focus moves to next frame
        self._focused = None          # the focused id after the last frame
        self._sub_focused = None      # ... and on the open panel's page
        self._kinds = {}              # a row's label -> its verb, for the hints
        self._words = {}              # a cycle's values -> the words it shows
        self._about = ()              # About's facts, read at each visit
        # The WIFI panel (#38, spec §10): scan list -> pick -> (password) ->
        # connect/forget over the injected ws.wifi service.
        self.wifi_view = False
        self.wifi_nets = []           # [(ssid, signal, locked), ...] last scan
        self.wifi_sel = 0
        self.wifi_pick = None         # ssid being typed for (password mode)
        self._wifi_seed = None        # the byte held as the prompt opened
        self.wifi_pw = TextEntry(WIFI_PW_MAX)  # the password being typed (its .text)
        self.wifi_msg = ""
        self.wifi_known = []
        # Bluetooth keyboard panel, capability-gated through _bt_service():
        # a board with no BLE service never shows the row.
        self.bt_view = False
        self.bt_devices = []          # service rows; opaque address stays opaque
        self.bt_sel = 0
        self.bt_msg = ""
        self._bt_state = None
        # Memo for _settings_rows (see there): the built tuple plus the
        # capability flags it was built from, held as separate attributes so
        # the comparison allocates NOTHING.
        self._rows_cache = None
        self._rows_bt = False
        self._rows_upd = False
        self._rows_onl = False
        self._rows_web = False
        self._rows_c6 = False
        self._rows_idle = None
        self._rows_lgen = -1
        self._rows_tog = None
        self._groups_of = None        # the rows the groups were cut from
        self._groups = ()
        # _toggle_rows' own memo: the registry block, plus the gate answers
        # it was built from in a PREALLOCATED list compared element-wise.
        self._toggle_cache = None
        self._toggle_gates = [False] * len(SETTINGS_TOGGLES)
        # The warning a TOGGLE_CONFIRMS toggle shows before it turns ON: the
        # toggle's key while it is up (its page replaces the rows).
        self.confirm_key = None
        # The kernel's last crash record (Settings -> LAST CRASH): a read-only
        # page in place of the rows, on a board whose kernel keeps one.
        self.crash_view = False
        self._typing = False          # the Name field holds focus: text mode
        self._name = None             # the Name being typed, until it is stored

    def reset(self):
        """A fresh visit (ws.open_settings): focus on the first row, the page
        at its top, every panel closed, About read again."""
        self.commit_name()
        self._host.reset()
        self._sub.reset()
        self._view = None
        self._want = None
        self._focused = None
        if self.wifi_view:
            self.close_wifi()
        if self.bt_view:
            self.close_bluetooth()
        self.confirm_key = None
        self.crash_view = False
        self._typing = False
        self._about = self._read_about()

    # -- the cursor, as a row index ---------------------------------------------

    @property
    def set_msel(self):
        """The focused row's index in `_settings_rows()` (0 with none)."""
        rows = self._settings_rows()
        f = self._want if self._want is not None else self._focused
        for i in range(len(rows)):
            if say(rows[i][1]) == f:
                return i
        return 0

    @set_msel.setter
    def set_msel(self, i):
        rows = self._settings_rows()
        if rows:
            self._want = say(rows[i % len(rows)][1])
            self.ws._dirty = True

    # -- BLUETOOTH KEYBOARD panel (capability-gated) ------------------------------

    def _bt_service(self):
        """The BLE keyboard service, or None on a board without one.

        TWO SHAPES, because the boards genuinely differ. On the touch-only
        tiers (P4, Guition) a paired BLE keyboard IS the only keyboard, so it
        is `ws.keyboard` and the capability rides the keyboard itself. The
        T-Deck has a physical C3 keyboard over I2C AND can pair a BLE one, so
        `ws.keyboard` stays the local keyboard and the BLE service hangs off
        `ws.ble_keyboard`; both drivers write into the SAME InputState.
        `settings_capable` is what gates."""
        for svc in (getattr(self.ws, "ble_keyboard", None),
                    getattr(self.ws, "keyboard", None)):
            if getattr(svc, "settings_capable", False):
                return svc
        return None

    def open_bluetooth(self):
        """Open the Bluetooth keyboard picker, STARTING the service if it is idle.

        The touch-only boards start their service at boot (a paired keyboard
        is their only way out of a cart); a board that already has a keyboard
        (the T-Deck) starts nothing until a kid opens this picker. `start()`
        early-returns on an available service, so a live link is not
        disturbed (found on glass 2026-08-21)."""
        svc = self._bt_service()
        if svc is not None:
            try:
                svc.start()
            except Exception:  # noqa: BLE001 -- _bt_refresh reports the state
                pass
        self.bt_view = True
        self.bt_msg = ""
        self._bt_state = None
        self._bt_refresh()
        self.ws._dirty = True

    def close_bluetooth(self):
        self.bt_view = False
        self._want = say("BLUETOOTH KEYBOARD")
        self.ws._dirty = True

    def _bt_refresh(self):
        svc = self._bt_service()
        if svc is None:
            self.bt_devices = []
            self.bt_msg = "No Bluetooth keyboard service."
            return (False, "off", None, None, self.bt_msg)
        try:
            status = svc.settings_status()
            self.bt_devices = list(svc.settings_devices())
        except Exception as exc:  # noqa: BLE001 -- Settings must stay usable
            self.bt_devices = []
            self.bt_msg = ("Keyboard error: " + str(exc))[:40]
            return (False, "error", None, None, self.bt_msg)
        if self.bt_sel >= len(self.bt_devices):
            self.bt_sel = max(0, len(self.bt_devices) - 1)
        state = status[1]
        if state != self._bt_state:
            if not status[0]:
                self.bt_msg = "Keyboard input is off."
            elif status[4]:
                self.bt_msg = ("Error: " + str(status[4]))[:40]
            elif state == "ready":
                self.bt_msg = "Connected and saved."
            elif state == "scanning":
                self.bt_msg = "Looking for keyboards..."
            elif state in ("connecting", "pairing", "discovering",
                           "subscribe-retry", "found"):
                self.bt_msg = "Connecting..."
            elif state == "choose":
                self.bt_msg = "Pick a keyboard."
            else:
                self.bt_msg = "Ready to scan."
            self._bt_state = state
        return status

    def _bt_action(self, action, address=None):
        svc = self._bt_service()
        if action == "back":
            self.close_bluetooth()
            return
        if svc is None:
            self.bt_msg = "No Bluetooth keyboard service."
            return
        try:
            if action == "toggle":
                on = not bool(svc.settings_status()[0])
                svc.set_enabled(on)
                self.bt_msg = "Keyboard input on." if on else "Keyboard input off."
            elif action == "scan":
                svc.discover_devices()
                self.bt_msg = "Looking for keyboards..."
            elif action == "forget":
                svc.forget()
                self.bt_devices = []
                self.bt_sel = 0
                self.bt_msg = "Forgot the saved keyboard."
            elif action == "connect":
                if address is None and self.bt_devices:
                    address = self.bt_devices[self.bt_sel][0]
                if address is None:
                    self.bt_msg = "Scan, then pick a keyboard."
                elif svc.connect_device(address):
                    self.bt_msg = "Connecting and saving..."
                else:
                    self.bt_msg = "Couldn't pick that keyboard."
        except Exception as exc:  # noqa: BLE001
            self.bt_msg = ("Keyboard error: " + str(exc))[:40]
        self._bt_state = None
        self.ws._dirty = True

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

    def _bt_page(self):
        """The picker: the keyboard input switch and its state, the keyboards
        found (one taps or Enter to use), Scan and Forget."""
        status = self._bt_refresh()
        enabled, state, name, _preferred, _error = status
        _ui.page("")
        _ui.hints(("confirm", "Use", "back", "Back"))
        _ui.group("Bluetooth keyboard")
        if _ui.toggle("Keyboard input", bool(enabled)) != bool(enabled):
            self._bt_action("toggle")
        _ui.info(name or "No keyboard", state[:1].upper() + state[1:])
        if self.bt_msg:
            _ui.label(self.bt_msg)
        _ui.group("Keyboards")
        if not self.bt_devices:
            _ui.label("Scanning..." if state == "scanning" else "No keyboards found.")
        for k in range(min(len(self.bt_devices), _NETS)):
            address, dev_name, rssi, preferred, connected = self.bt_devices[k]
            _ui.icon("key")
            value = "Online" if connected else ("Saved" if preferred else
                                                ("%d dBm" % int(rssi) if int(rssi) > -127
                                                 else ""))
            if _ui.link(str(dev_name), value, {"id": "bt%d" % k}):
                self.bt_sel = k
                self._bt_action("connect", address)
        _ui.group("Options")
        if _ui.link("Scan"):
            self._bt_action("scan")
        if _ui.link("Forget the saved keyboard"):
            self._bt_action("forget")
        _ui.group()
        f = _ui.focus()
        if f and f.startswith("bt") and f[2:].isdigit():
            self.bt_sel = int(f[2:])

    # -- the WIFI panel (#38; spec shell_ux_v1.md §10) ----------------------------

    def open_wifi(self):
        """Open the wifi panel in place of the rows (the WIFI row, and the
        bar's wifi widget in console mode). Rescans on entry so the list is
        fresh."""
        self.wifi_view = True
        self.wifi_enter()
        self.ws._dirty = True

    def close_wifi(self):
        """Back from the wifi panel to the Settings rows (NOT out of Settings)."""
        self.wifi_view = False
        self.wifi_leave()
        self._want = say("WIFI")
        self.ws._dirty = True

    def wifi_enter(self):
        """The panel's entry, wherever it is held (these rows, or the wifi
        popover): a fresh password field, the radio lease, a rescan."""
        self.wifi_pick = None
        self.wifi_pw.open("")
        self.ws.wifi_hold("settings")  # the radio lease: wifi_leave lets go
        self._wifi_rescan()

    def wifi_leave(self):
        """The panel's exit: the typing ends and the lease goes, unless the
        other container still shows the panel."""
        ws = self.ws
        self.wifi_pick = None
        ws._set_text_mode(False)
        pop = ws.sysmenu
        if not self.wifi_view and not (pop.open and pop.widget == "wifi"):
            ws.wifi_release("settings")

    def _wifi_back(self, popover=False):
        """BACK out of the panel: to the Settings rows, or out of the popover
        that holds it."""
        if popover:
            self.ws.sysmenu.close()
        else:
            self.close_wifi()

    def _wifi_rescan(self):
        ws = self.ws
        if ws.wifi is None:
            self.wifi_nets = []
            self.wifi_known = []
            self.wifi_msg = "No Wi-Fi service."
            return
        try:
            self.wifi_nets = list(ws.wifi.scan())
            self.wifi_known = list(ws.wifi.known())
            self.wifi_msg = ""
        except Exception as exc:  # noqa: BLE001 -- a radio hiccup must not crash Settings
            self.wifi_nets = []
            self.wifi_msg = ("Scan failed: " + str(exc))[:34]
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
            self._wifi_ask(ssid)
        else:
            # A saved/open network reconnects with "" -- the SERVICE resolves
            # the stored password. A LOCKED known net that still fails (a stale
            # saved password) reopens the prompt, so the kid can retype it
            # (on-glass P4, 2026-07-25).
            ok = self._wifi_connect(ssid, "")
            if not ok and locked:
                self._wifi_ask(ssid)
        ws._dirty = True

    def _wifi_ask(self, ssid):
        ws = self.ws
        self.wifi_pick = ssid
        self.wifi_pw.open("")
        # the byte that opened the prompt is held: its field never types it
        self._wifi_seed = getattr(ws.input, "last_key", 0) or 0
        self.wifi_msg = ""
        ws._set_text_mode(True)      # clean ASCII typing (device keyboard)
        ws._dirty = True

    def _wifi_connect(self, ssid, pw):
        ws = self.ws
        ok = False
        try:
            ok = bool(ws.wifi.connect(ssid, pw)) if ws.wifi is not None else False
        except Exception as exc:  # noqa: BLE001
            self.wifi_msg = ("Can't connect: " + str(exc))[:34]
        else:
            # Voice (docs/os_voice_v1.md): say the fix, not the fault -- a typed
            # password gets the password message; an open/saved net stays generic.
            self.wifi_msg = ("Connected to " + str(ssid))[:34] if ok \
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
            self.wifi_msg = ("Forgot " + str(ssid))[:34]
        except Exception as exc:  # noqa: BLE001
            self.wifi_msg = ("Forget failed: " + str(exc))[:34]
        ws._dirty = True

    def _wifi_cancel(self):
        self.wifi_pick = None
        self.ws._set_text_mode(False)
        self.ws._dirty = True

    def wifi_input(self, i, host, popover=False):
        """Keys for the wifi panel held by `host`. In PASSWORD mode the
        prompt's field takes the typed bytes (ENTER connects, BACKSPACE
        deletes -- Settings is a taskbar app, so BACKSPACE is an ordinary
        key, spec §9) and ESC, or B, cancels; in LIST mode the page takes
        the keys and B backs out. True when the panel took them."""
        if self.wifi_pick is not None:
            if self._wifi_seed is not None:
                host.key = self._wifi_seed      # the held byte types nothing
                self._wifi_seed = None
            host.keys(i)
            if host.inp[5] == 27 or i.pressed("b") or i.pressed("stop"):
                host.inp[5] = 0
                self._wifi_cancel()
            return True
        if i.pressed("b") or i.pressed("stop"):
            self._wifi_back(popover)
            return True
        host.keys(i)
        return True

    def wifi_page(self, popover=False):
        """The wifi panel as a page of the frame begun (the rows' place, or
        the wifi popover's rect): the connection, the networks the last scan
        found (a lock where one needs a password), Scan again and Forget; or,
        while a password is typed, its field and Connect."""
        ws = self.ws
        connected, ssid, ip = (False, None, None)
        if ws.wifi is not None:
            try:
                connected, ssid, ip = ws.wifi.status()
            except Exception:  # noqa: BLE001
                pass
        _ui.page("")
        if self.wifi_pick is not None:
            _ui.hints(("confirm", "Connect", "back", "Cancel"))
        else:
            _ui.hints(("confirm", "Connect", "back", "Back"))
        _ui.group("Wi-Fi")
        _ui.icon("wifi" if connected else "wifi_off")
        _ui.info("Connected" if connected else "Not connected",
                 str(ssid) if connected and ssid else "")
        if connected and ip:
            _ui.info("Address", str(ip))
        if self.wifi_msg:
            _ui.label(self.wifi_msg)
        _ui.group("Networks")
        if not self.wifi_nets:
            _ui.label("No networks found.")
        for k in range(min(len(self.wifi_nets), _NETS)):
            ssid_k, _sig, locked = self.wifi_nets[k]
            _ui.icon("lock" if locked else "wifi")
            if _ui.link(str(ssid_k)[:24], "Saved" if str(ssid_k) in self.wifi_known else "",
                        {"id": "net%d" % k}):
                self.wifi_sel = k
                self._wifi_activate()
        _ui.group("Options")
        if _ui.link("Scan again"):
            self._wifi_rescan()
            ws._dirty = True
        sel = self.wifi_nets[self.wifi_sel % len(self.wifi_nets)][0] if self.wifi_nets else None
        if sel is not None and str(sel) in self.wifi_known:
            if _ui.link("Forget", str(sel)[:16]):
                self._wifi_forget()
        _ui.group()
        if self.wifi_pick is not None:
            self._wifi_prompt()
            return
        f = _ui.focus()
        if f and f.startswith("net") and f[3:].isdigit():
            self.wifi_sel = int(f[3:])

    def _wifi_prompt(self):
        """The password for the network picked, over the list: a dialog of
        the engine's field (focused as it opens: typing goes in) and Cancel
        and Connect side by side at its foot. Enter in the field connects."""
        title = ("Password for " + str(self.wifi_pick))[:30]
        if not self.ws.can_type():
            # nothing in use can type: no field and no Connect a key can't
            # fill (#239 is the on-screen keyboard)
            if _ui.dialog(title, ("Cancel",)) == 0:
                self._wifi_cancel()
            else:
                _ui.label(self.ws.no_keyboard_text)
            _ui.dialog()
            return
        pick = _ui.dialog(title, ("Cancel", "Connect"))
        text, done = _ui.field("Password", self.wifi_pw.text, WIFI_PW_MAX, {"id": "password"})
        _ui.dialog()
        self.wifi_pw.text = text
        if pick == 1 or done:
            self._wifi_connect(self.wifi_pick, text)
        elif pick == 0:
            self._wifi_cancel()

    # -- the warning before a TOGGLE_CONFIRMS toggle turns ON -------------------

    def open_confirm(self, key):
        """Show `key`'s warning in place of the rows, KEEP OFF focused."""
        self.confirm_key = key
        self.ws._dirty = True

    @property
    def confirm_sel(self):
        """Which of the warning's buttons holds focus: 0 KEEP OFF, 1 the one
        that turns the toggle on."""
        key = self.confirm_key
        if key is None:
            return 0
        return 1 if self._sub_focused == say(TOGGLE_CONFIRMS[key][2]) else 0

    def close_confirm(self):
        """Back to the rows with the toggle as it was."""
        key = self.confirm_key
        self.confirm_key = None
        for t in SETTINGS_TOGGLES:
            if t[0] == key:
                self._want = say(t[1])
        self.ws._dirty = True

    def _confirm_accept(self):
        """The confirming button: turn the toggle ON through its verb (which
        persists it) and go back to the rows."""
        key = self.confirm_key
        self.close_confirm()
        for t in SETTINGS_TOGGLES:
            if t[0] == key:
                getattr(self.ws, t[3])(True)
                return

    def _confirm_page(self):
        """The warning: the toggle's name over what turning it on means, then
        KEEP OFF (focused when it opens) and the danger button that turns it
        on."""
        title, text, yes = TOGGLE_CONFIRMS[self.confirm_key]
        _x, _y, w, _h = _ui.page("")
        _ui.hints(("back", "Back"))
        _ui.group(say(title))
        for line in _wrap(text, max(8, (w - 32) // max(1, self.ws.layout.font_w))):
            _ui.label(line)
        if _ui.button(say(KEEP_OFF)):
            self.close_confirm()
        elif _ui.button(say(yes), "danger"):
            self._confirm_accept()
        _ui.group()

    # -- LAST CRASH page (the kernel's record, read-only) -----------------------

    def open_crash(self):
        self.crash_view = True
        self.ws._dirty = True

    def close_crash(self):
        self.crash_view = False
        self._want = say("LAST CRASH")
        self.ws._dirty = True

    def _crash_page(self):
        """The record's lines, and one button back."""
        _x, _y, w, _h = _ui.page("")
        _ui.hints(("confirm", "Back"))
        _ui.group("Last crash")
        n = max(8, (w - 32) // max(1, self.ws.layout.font_w))
        for line in crash_lines(last_crash()):
            for part in _wrap(line, n):
                _ui.label(part)
        if _ui.button("Back"):
            self.close_crash()
        _ui.group()

    # -- the lent left zone (#46 zoned bar) -------------------------------------

    def draw_zone(self, cv, rect):
        """Where the bar places an X the zone stays empty; else the bar draws
        `back_title` there itself (runtime/bar_layer.py)."""

    def zone_tap(self, px, py, rect=None):
        """The zone past the chevron (the bar takes the chevron's tap,
        `back`), the title, knocks on the secret door (#21)."""
        if rect is None or not _ui.rect_in(px, py, rect):
            return False
        self.ws.ach_ui._tap_secret_door()
        return True

    # -- rows / actions -----------------------------------------------------------
    def _toggle_rows(self):
        """The SETTINGS_TOGGLES block for this board, in registry order, minus
        the entries whose capability gate says no.

        Memoized like _settings_rows below and for the same reason -- it runs
        INSIDE it, i.e. ~15x per frame. The gate answers live in a
        preallocated list compared element-wise, so re-asking every call
        allocates nothing and a seventh toggle needs no seventh attribute.
        `_settings_rows` then compares the RESULT by identity, which is why
        adding a toggle touches neither memo."""
        ws = self.ws
        seen = self._toggle_gates
        stale = self._toggle_cache is None
        for i in range(len(SETTINGS_TOGGLES)):
            gate = SETTINGS_TOGGLES[i][4]
            on = True if gate is None else bool(gate(ws))
            if on != seen[i]:
                seen[i] = on
                stale = True
        if stale:
            rows = ()
            for i in range(len(SETTINGS_TOGGLES)):
                if seen[i]:
                    t = SETTINGS_TOGGLES[i]
                    rows = rows + ((t[0], t[1], "diag"),)
            self._toggle_cache = rows
        return self._toggle_cache

    def _settings_rows(self):
        """The Settings rows for this session: the static set, the registry's
        ON/OFF gate block, plus "UPDATE FW" (install
        from SD) and "UPDATE ONLINE" (WiFi download, #53 Phase 3) action rows when the
        injected updater supports them. Built on demand so the rows appear/disappear with
        the updater without re-statting per draw.

        MEMOIZED on the service capability flags, the toggle block and the
        look's `layout_gen` (the LAYOUT row's presence), because "on
        demand" turned out to mean ~15 times per FRAME -- the draw loop asks once per
        ROW, and the pointer/scroll paths ask again -- and each call rebuilt the tuple
        through up to four
        concatenations. On P4 glass that was ~2.3KB of the ~4.5KB a Settings frame
        allocated, and since ~70KB of churn buys one 55ms mark-sweep there, it was the
        largest single contributor to the scroll hitches (measured with
        tools/p4_alloc.py, 2026-07-26). Each flag is a cached bool / getattr, so
        re-checking them per call stays free; only the rebuild is skipped. The flags
        are compared as separate attributes rather than a signature tuple, because
        building a tuple to test the cache would reintroduce ~40B of the churn the
        cache exists to remove -- and the toggle block by IDENTITY, for the same
        reason."""
        ws = self.ws
        bt = self._bt_service() is not None
        upd = ws._update_available()
        onl = ws._online_update_available()
        web = getattr(ws, "webhost", None) is not None
        c6u = getattr(ws, "c6_updater", None) is not None
        idle = getattr(ws, "idle_ladder", None)
        tog = self._toggle_rows()
        lgen = ws.look.layout_gen
        if (self._rows_cache is not None and lgen == self._rows_lgen
                and bt == self._rows_bt
                and upd == self._rows_upd and onl == self._rows_onl
                and web == self._rows_web and c6u == self._rows_c6
                and tog is self._rows_tog and idle is self._rows_idle):
            return self._rows_cache
        # The registry block sits directly after EDIT ICONS, which is where the
        # hand-spliced FRAMESKIP/PERF DIAG rows were: same order, same indices.
        rows = self._SETTINGS_ROWS + tog
        if ws.look.layout_choices() or ws.look.layout_theme() is not None:
            # LAYOUT (docs/shell_layout_2026-10.md §11): after FONT SIZE, and
            # only where a package's layout differs from the theme's.
            i = rows.index(("font_scale", "FONT SIZE", "font")) + 1
            rows = rows[:i] + (("layout_theme", "LAYOUT", "layout"),) + rows[i:]
        if bt:
            # Keep network/input together. Dynamic capability gating preserves
            # the non-P4 Settings row indices.
            rows = rows[:1] + (("bluetooth", "BLUETOOTH KEYBOARD", "bluetooth"),) \
                + rows[1:]
        if idle is not None:
            # The kernel's idle ladder (idle_ladder.py): DIM, SAVER and SCREEN
            # OFF as seconds of no input, OFF a value -- on a tier whose frames
            # are the kernel's, so every other tier's rows and pixels stay.
            rows = rows + idle.rows()
        if crash_available():
            rows = rows + (("crash", "LAST CRASH", "crash"),)
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
        if c6u:
            # UPGRADE C6 RADIO (#7/#58): the P4's radio is a second processor
            # with its own firmware, flashed FROM the console over SDIO. The
            # row is unconditional where the capability exists -- "is the shim
            # current" costs a 2s RPC timeout against a stock slave, which is
            # a price to pay on TAP (the flow's CHECKING screen), never per
            # frame or per Settings open. Capability-gated like the others so
            # every other board's rows and pixels stay frozen.
            rows = rows + (("update_c6", "UPGRADE C6 RADIO", "action"),)
        self._rows_bt = bt
        self._rows_upd = upd
        self._rows_onl = onl
        self._rows_web = web
        self._rows_c6 = c6u
        self._rows_idle = idle
        self._rows_lgen = lgen
        self._rows_tog = tog
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
        elif key == "update_c6":
            ws.update_ui.open_update_c6()
        else:
            ws.open_theme()       # EDIT ICONS (#52)

    def _toggle_diag_row(self, key):
        """Flip one of the "diag"-kind ON/OFF gate rows (any tap/step/A toggles).
        One dispatch shared by the step/key/tap paths, and it is now the
        REGISTRY's: read the flat mirror by name, call the verb the entry names.
        The six-branch `if key ==` chain this replaces was the fourth of the
        five sites a new toggle had to walk (#209 section 7)."""
        ws = self.ws
        for t in SETTINGS_TOGGLES:
            if t[0] == key:
                on = not getattr(ws, key, False)
                if on and key in TOGGLE_CONFIRMS:
                    self.open_confirm(key)
                    return
                getattr(ws, t[3])(on)
                return

    def row_index(self, key):
        """The index of the row `key` in this session's rows, or None."""
        rows = self._settings_rows()
        for i in range(len(rows)):
            if rows[i][0] == key:
                return i
        return None

    def step_row(self, key, d):
        """Step the row `key` by d, the cursor where it was (the power
        popover's rows)."""
        i = self.row_index(key)
        if i is not None:
            self.settings_adjust(d, i)

    def show_row(self, key):
        """Focus the row `key` next frame, scrolled into view (the battery
        widget's console-mode page)."""
        i = self.row_index(key)
        if i is not None:
            self.set_msel = i
    def settings_adjust(self, d, i=None):
        """Step the selected Settings row (or row `i`) by d. Font applies +
        persists; a level row (VOLUME, BRIGHTNESS) moves within its range
        (`set_level`); an "action" row (APPEARANCE / EDIT ICONS) fires its
        action regardless of direction; NAME is typed, not stepped."""
        ws = self.ws
        key, _label, kind = self._settings_rows()[self.set_msel if i is None else i]
        if kind == "wifi-net":                  # WIFI: any step/tap opens the panel (#38)
            self.open_wifi()
            return
        if kind == "bluetooth":
            self.open_bluetooth()
            return
        if kind == "action":                    # EDIT ICONS / UPDATE FW: open the tool
            self._activate_settings_action(key)
            return
        if kind == "diag":                      # the ON/OFF gates (#68 diag, #217 steady)
            self._toggle_diag_row(key)
            return
        if kind == "webhost":                   # WEB CONSOLE: serve / stop serving
            ws.toggle_webhost()
            return
        if kind == "crash":                     # LAST CRASH: any step/tap opens the record
            self.open_crash()
            return
        if kind == "idle":                      # the idle ladder's rungs: OFF, 30S .. 30M
            ws.idle_ladder.step(key, d)
            return
        if key == "ota_channel":                # OTA update channel STABLE <-> BETA
            ws._cycle_channel(d)
            return
        if key == "font_scale":                 # system-UI font size (#39): live + persisted
            ws.look.cycle_font_scale(d)
            return
        if kind == "layout":                    # LAYOUT: THEME / a package's bars
            ws.look.cycle_layout_theme(d)
            return
        if kind == "level":                     # VOLUME / BRIGHTNESS
            self.set_level(key, self.level(key)[0] + d)

    def commit_name(self):
        """Store the Name being typed, if it changed: Enter, focus leaving the
        field and every way out of Settings call this, so the store is written
        once an edit, never a keystroke."""
        got = self._name
        if got is None:
            return
        self._name = None
        if got != str(self.ws.system.get("name", "") or ""):
            self.ws.system.set("name", got)

    def leave(self):
        """Settings stopped holding the keyboard (the console's frame saw input
        focus move): the Name being typed is stored, and a text mode Settings
        set is handed back. On the desk the window manager has already put
        the focused window's own in place; elsewhere it goes to button mode
        unless another surface set it since."""
        self.commit_name()
        if self._typing:
            self._typing = False
            ws = self.ws
            if not ws.wm.desk_open() and ws._text_by == "settings":
                ws._set_text_mode(False)

    def level(self, key):
        """A level row's (value, lowest, highest): the stored value, else the
        row's default, held in its range."""
        lo, hi, dflt = LEVELS[key]
        try:
            v = int(self.ws.system.get(key, dflt))
        except (TypeError, ValueError):
            v = dflt
        return max(lo, min(hi, v)), lo, hi

    def set_level(self, key, v):
        """Store a level row's value, held in its range; VOLUME also sets the
        console's level, which every audio session mixes at."""
        lo, hi, _dflt = LEVELS[key]
        v = max(lo, min(hi, int(v)))
        self.ws.system.set(key, v)
        if key == "volume":
            console_volume(v)

    def rows_flinging(self):
        """False: the page scrolls by the engine's drag, which has no coast."""
        return False

    # -- About ----------------------------------------------------------------------

    def _read_about(self):
        """About's facts for this visit, as (label, value) rows: the firmware,
        the board, the store's free space (a board's), and nothing a tier
        cannot answer."""
        ws = self.ws
        out = []
        u = ws.updater
        if u is not None:
            try:
                out.append(("Firmware", str(u.version_label())))
            except Exception:  # noqa: BLE001
                pass
        board = None
        try:
            import moy_ota
            board = getattr(moy_ota, "BOARD", None)
        except ImportError:
            pass
        if board:
            out.append(("Board", _BOARDS.get(board, str(board))))
        if u is not None:
            free = self._free()
            if free is not None:
                out.append(("Storage", free))
        return tuple(out)

    def _free(self):
        """The store's free space as About shows it, or None."""
        ws = self.ws
        try:
            keep = getattr(ws, "cart_keep", None)
            if keep is not None:
                got = keep.free()
                b = got[0] if got else None
            else:
                import os
                st = os.statvfs(ws.carts_root)
                b = st[0] * st[4]
        except (ImportError, OSError, AttributeError, TypeError, IndexError):
            return None
        if b is None:
            return None
        if b >= 1 << 30:
            return "%d.%d GB free" % (b >> 30, ((b * 10) >> 30) % 10)
        if b >= 1 << 20:
            return "%d.%d MB free" % (b >> 20, ((b * 10) >> 20) % 10)
        return "%d KB free" % (b >> 10)

    # -- the rows page ----------------------------------------------------------------

    def _grouped(self):
        """The groups that have rows this session: (caption, rows), memoized
        on the rows tuple."""
        rows = self._settings_rows()
        if self._groups_of is rows:
            return self._groups
        out = []
        for caption, keys in _GROUPS:
            got = []
            for r in rows:
                if r[0] in keys or (caption == "Power" and r[2] == "idle"):
                    got.append(r)
            if got:
                out.append((caption, tuple(got)))
        self._groups = tuple(out)
        self._groups_of = rows
        return self._groups

    def _theme_title(self):
        look = self.ws.look
        name = str(look.theme_name or "")
        title = _capital(name.replace("_", " ").replace("-", " "))
        return title + (" Day" if look.theme_variant == "light" else " Night")

    def _row(self, key, label, kind):
        """One row of the page: its verb, and what its answer does."""
        ws = self.ws
        s = say(label)
        if kind == "wifi-net":
            self._kinds[s] = "link"
            ssid = None
            if ws.wifi is not None:
                try:
                    connected, ssid, _ip = ws.wifi.status()
                    ssid = ssid if connected else None
                except Exception:  # noqa: BLE001
                    ssid = None
            if _ui.link(s, str(ssid)[:16] if ssid else "Off"):
                self.open_wifi()
        elif kind == "bluetooth":
            self._kinds[s] = "link"
            try:
                enabled, state, name, _preferred, _error = \
                    self._bt_service().settings_status()
            except Exception:  # noqa: BLE001 -- a radio hiccup reads Off
                enabled, state, name = False, "error", None
            value = (str(name)[:12] if name else state[:1].upper() + state[1:]) \
                if enabled else "Off"
            if _ui.link(s, value):
                self.open_bluetooth()
        elif kind == "action":
            self._kinds[s] = "link"
            if _ui.link(s, self._theme_title() if key == "appearance" else ""):
                self._activate_settings_action(key)
        elif kind == "crash":
            self._kinds[s] = "link"
            rec = last_crash()
            v = str(rec.get("kind") or "?")[:8] if rec else "None"
            if _ui.link(s, v[:1].upper() + v[1:]):
                self.open_crash()
        elif kind == "webhost":
            self._kinds[s] = "link"
            if _ui.link(s, ws.webhost_label() if ws.webhost_serving() else "Off"):
                ws.toggle_webhost()
        elif kind == "diag":
            on = bool(getattr(ws, key, False))
            self._kinds[s] = "toggle_on" if on else "toggle_off"
            if _ui.toggle(s, on) != on:
                self._toggle_diag_row(key)
        elif kind == "font":
            self._kinds[s] = "cycle"
            scales = ws.look.FONT_SCALES
            fs = ws.look.font_scale
            cur = scales.index(fs) if fs in scales else 0
            words = self._words.get(scales)
            if words is None:
                words = self._words[scales] = [_SIZES.get(v, "%dx" % v) for v in scales]
            got = _ui.cycle(s, words, cur)
            if got != cur:
                ws.look.cycle_font_scale(got - cur)
        elif kind == "layout":
            self._kinds[s] = "cycle"
            pick = ws.look.layout_theme()
            vals = (None,) + tuple(ws.look.layout_choices())
            if pick is not None and pick not in vals:
                vals = vals + (pick,)
            cur = vals.index(pick)
            got = _ui.cycle(s, ["Theme" if v is None else _capital(str(v)) for v in vals], cur)
            if got != cur:
                ws.look.cycle_layout_theme(got - cur)
        elif kind == "idle":
            self._kinds[s] = "cycle"
            lad = ws.idle_ladder
            steps = lad.steps()
            v = lad.value(key)
            cur = steps.index(v) if v in steps else 0
            words = self._words.get(steps)
            if words is None:
                words = self._words[steps] = [_idle_word(t) for t in steps]
            got = _ui.cycle(s, words, cur)
            if got != cur:
                lad.set(key, steps[got])
        elif kind == "channel":
            self._kinds[s] = "seg"
            cur = 1 if ws._ota_channel() == "unstable" else 0
            got = _ui.seg(s, _STABLE_BETA, cur)
            if got != cur:
                ws._cycle_channel(got - cur)
        elif kind == "level":
            self._kinds[s] = "slider"
            cur, lo, hi = self.level(key)
            if key == "volume" and cur == lo:     # muted at 0
                _ui.icon("mute")
            got = _ui.slider(s, cur, lo, hi, _STEPPED)  # the power popover's gauge
            if got != cur:
                self.set_level(key, got)
        elif kind == "name" and not ws.can_type():
            # nothing in use can type (touch alone, a gamepad): the row says
            # so instead of a field no key reaches (#239 is the keyboard)
            self._kinds.pop(s, None)
            _ui.info(s, ws.no_keyboard_text)
        elif kind == "name":
            self._kinds[s] = "field"
            cur = self._name
            if cur is None:
                cur = str(ws.system.get(key, "") or "")
            got, done = _ui.field(s, cur, NAME_MAX, _NAME_HINT)
            if got != cur:
                self._name = got
            if done:
                self.commit_name()
                self._leave_field()

    def editing_field(self):
        """Whether a field on Settings is being typed into: the Name row
        focused, or the Wi-Fi password prompt."""
        if self._page_of() == "wifi":
            return self.wifi_pick is not None
        return self._page_of() is None and self._kinds.get(self._focused) == "field"

    def cancel_field(self):
        """Cancel the field being typed into (Esc, B, the T-Deck's Mic): the
        Name gets back the text it had when typing began and focus leaves
        it; the password prompt closes. False where no field is."""
        if not self.editing_field():
            return False
        if self._page_of() == "wifi":
            self._wifi_cancel()
            return True
        self._name = None                 # nothing typed is kept
        self._leave_field()
        return True

    def _leave_field(self):
        """Focus leaves the field for the row above it: typing ends."""
        i = self.row_index("name")
        if i:
            self.set_msel = i - 1

    def _rows_page(self):
        _ui.page("")
        _ui.hints(_HINTS.get(self._kinds.get(_ui.focus()), _HINT_OPEN))
        ws = self.ws
        sheet = ws.icon_spec() is None        # EDIT ICONS paints the IconSheet
        for caption, rows in self._grouped():
            _ui.group(caption)
            for key, label, kind in rows:
                if key != "icons" or sheet:
                    self._row(key, label, kind)
        _ui.group("About")
        for label, value in self._about:
            _ui.info(label, value)
        lvl = ws.battery_level()
        if lvl is not None:
            _ui.info("Battery", "%d%%" % lvl)
        self._kinds["Achievements"] = "link"
        if _ui.link("Achievements", str(ws.ach.count())):
            ws.show_achievements = True
            ws.ach_ui._secret_taps = 0
            ws._dirty = True
        _ui.group()

    # -- the frame --------------------------------------------------------------------

    def _page_of(self):
        if self.wifi_view:
            return "wifi"
        if self.bt_view:
            return "bt"
        if self.confirm_key is not None:
            return "confirm"
        if self.crash_view:
            return "crash"
        return None

    def _input_host(self):
        return self._host if self._page_of() is None else self._sub

    def handle_input(self, i):
        ws = self.ws
        view = self._page_of()
        if view == "wifi":
            return self.wifi_input(i, self._sub)
        if i.pressed("home"):
            if view == "confirm":
                self.close_confirm()
            ws.home_key()
            return True
        if i.pressed("b") or i.pressed("stop"):
            self.back()
            return True
        self._input_host().keys(i)
        return True

    def has_page(self):
        """Whether a page is open over Settings' rows (Wi-Fi, Bluetooth, a
        confirmation, a crash report), which `back` steps out of: a desk
        window shows the back chevron in its title strip then."""
        return self._page_of() is not None

    def back(self):
        """Back one level -- B, Esc and the bar's chevron: a page open over
        Settings closes, else Settings does (resuming the cart it was opened
        from). True: Settings took it either way."""
        view = self._page_of()
        if view is None and self.cancel_field():
            pass                          # the first back cancels the field
        elif view == "bt":
            self.close_bluetooth()
        elif view == "confirm":
            self.close_confirm()
        elif view == "crash":
            self.close_crash()
        elif view == "wifi":
            if self.wifi_pick is not None:
                self._wifi_cancel()
            else:
                self._wifi_back()
        else:
            self.ws._exit_settings()
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        # The achievements view is a modal overlay: while it's up, any tap
        # closes it (it has no controls of its own besides "tap to dismiss").
        if ws.show_achievements:
            if click:
                ws.show_achievements = False
            return True
        if click and ws.bar_layer.handle_bar_tap("settings", px, py):
            return True
        if click:
            ws.ach_ui._secret_taps = 0
        self._input_host().point(px, py, click, bool(getattr(ws.pointer, "down", False)))
        return True

    def draw(self, dt):
        """The Settings app: its page, the ground around it (the bar's band,
        wherever the bar sits), then the bar."""
        ws = self.ws
        cv = ws.sys_canvas
        lay = ws.layout
        g = ws.theme_colors["panel"]
        x, y, w, h = lay.cx, lay.cy, lay.cw, lay.ch
        if y > 0:
            cv.rect(0, 0, cv.w, y, g)
        if y + h < cv.h:
            cv.rect(0, y + h, cv.w, cv.h - y - h, g)
        if x > 0:
            cv.rect(0, y, x, h, g)
        if x + w < cv.w:
            cv.rect(x + w, y, cv.w - x - w, h, g)
        self._draw_page()
        ws.bar_layer._draw_status_strip("settings")

    def _draw_page(self):
        """The page the open panel is, else the rows, filling the content
        rect."""
        ws = self.ws
        lay = ws.layout
        view = self._page_of()
        if view != self._view:
            if self._view is None:
                self._host.clear()
            else:
                self._sub.clear()
            if view is not None:
                self._sub.reset()
            self._view = view
        host = self._host if view is None else self._sub
        want = self._want if view is None else None
        if want is not None:
            host.focus(want)          # before the frame: its keys act on it
            self._want = None
        host.begin(ws.sys_canvas, ws.theme_colors, (lay.cx, lay.cy, lay.cw, lay.ch),
                   _lb.metric_class() == _lb.DESK, ws.windowed_chrome)
        was = ws._dirty
        ws._dirty = False             # what the rows' answers change marks it again
        if view is None:
            if want is not None and _ui.focus() != want:
                _ui.focus(want)       # a page not drawn yet: from this frame on
                ws.request_frame()    # ... and the next scrolls to it
            self._rows_page()
            self._focused = _ui.focus()
            field = self._kinds.get(self._focused) == "field"
            if not field:
                self.commit_name()        # focus left the field
            # typed letters are the field's, never a button's alias -- while
            # Settings holds the keyboard (on the desk, its window has focus)
            typing = field and ws.wm.input_holder() == "settings"
            if typing != self._typing:
                self._typing = typing
                ws._set_text_mode(typing)
        else:
            self.commit_name()
            if view == "wifi":
                self.wifi_page()
            elif view == "bt":
                self._bt_page()
            elif view == "confirm":
                self._confirm_page()
            else:
                self._crash_page()
            self._sub_focused = _ui.focus()
        if ws._dirty or self._page_of() != view or host.owed or host.tap == 2:
            ws.request_frame()        # an answer changed what the page shows
        ws._dirty = was or ws._dirty


def _idle_word(secs):
    """An idle rung's value as its row says it: Off, 30 s, 5 min."""
    if not secs:
        return "Off"
    if secs % 60 == 0:
        return "%d min" % (secs // 60)
    return "%d s" % secs
