"""Bluetooth LE HID keyboard input -- the shared device driver (#202 Phase C).

Born as the P4's `p4_ble_keyboard.py` and PROMOTED to the shared device tree
on 2026-08-19, the day the rule's second consumer arrived (the Guition S3
wants the same cheap HOGP keyboards). Everything here is written against
MicroPython's standard `bluetooth` (NimBLE) API, so where the radio LIVES is
each board's business and invisible to this file: the P4 reaches a companion
ESP32-C6 over ESP-Hosted SDIO, the S3 boards use the radio on the chip. The
`moy_ble_hid` native notification fast-path is a guarded OPTIONAL (a P4-local
usermod + a modbluetooth patch, built because the P4's synchronous NimBLE
IRQ/GIL hop measured too slow for steady-state play input THERE) -- a board
without it runs the ordinary Python notification path, and whether that path
is fast enough is a per-board measurement, not an assumption. The keyboard
path itself:

    scan for HID service 0x1812 -> connect/bond -> discover Report or Boot
    Keyboard Input characteristics -> enable their CCCDs -> consume 8-byte HID
    keyboard reports.

This first slice deliberately targets standard BLE keyboards, not arbitrary HID
report maps.  It accepts the ordinary boot-keyboard-shaped report used by cheap
HOGP keyboards (modifier, reserved, six key usages), including the occasional
leading report-id byte.  Mouse, consumer-control and NKRO report parsing remain
separate follow-ups.

The class owns only its BLE connection and writes into the existing InputState:
real report make/break state becomes held buttons, while ``last_key`` carries the
held ASCII/control byte.  That gives games true hold-to-move and lets the shared
editors keep their existing edge detector.  Every radio/storage operation is
best-effort; a missing/broken Bluetooth stack leaves the board touch-only.
"""


try:
    from micropython import const
except ImportError:  # CPython host tests
    def const(value):
        return value


# MicroPython bluetooth IRQ numbers.
_IRQ_SCAN_RESULT = const(5)
_IRQ_SCAN_DONE = const(6)
_IRQ_PERIPHERAL_CONNECT = const(7)
_IRQ_PERIPHERAL_DISCONNECT = const(8)
_IRQ_GATTC_SERVICE_RESULT = const(9)
_IRQ_GATTC_SERVICE_DONE = const(10)
_IRQ_GATTC_CHARACTERISTIC_RESULT = const(11)
_IRQ_GATTC_CHARACTERISTIC_DONE = const(12)
_IRQ_GATTC_DESCRIPTOR_RESULT = const(13)
_IRQ_GATTC_DESCRIPTOR_DONE = const(14)
_IRQ_GATTC_WRITE_DONE = const(17)
_IRQ_GATTC_NOTIFY = const(18)
_IRQ_CONNECTION_UPDATE = const(27)
_IRQ_ENCRYPTION_UPDATE = const(28)
_IRQ_GET_SECRET = const(29)
_IRQ_SET_SECRET = const(30)
_IRQ_PASSKEY_ACTION = const(31)

_PASSKEY_ACTION_INPUT = const(2)
_PASSKEY_ACTION_DISPLAY = const(3)
_PASSKEY_ACTION_NUMCMP = const(4)

_IO_CAPABILITY_NO_INPUT_OUTPUT = const(3)

_FLAG_NOTIFY = const(0x0010)

_HID_SERVICE = const(0x1812)
_BOOT_KEYBOARD_INPUT = const(0x2A22)
_REPORT = const(0x2A4D)
_PROTOCOL_MODE = const(0x2A4E)
_CCCD = const(0x2902)

_ADV_IND = const(0x00)
_ADV_DIRECT_IND = const(0x01)

_STORE_VERSION = const(2)


# Clock shims: ONE body, runtime/ticks.py, via the device tier's leaf module
# (this file carried its own getattr-flavoured variant until 2026-08-18).
try:
    from device_util import _ticks_ms, _ticks_diff
except ImportError:  # loaded by path outside pytest's device finder
    from runtime.ticks import _ticks_ms, _ticks_diff


def _adv_fields(payload):
    """Yield ``(type, value-memoryview)`` fields from BLE advertising bytes."""
    data = bytes(payload)
    i = 0
    n = len(data)
    while i < n:
        size = data[i]
        if size == 0:
            return
        end = i + 1 + size
        if end > n or size < 1:
            return
        yield data[i + 1], data[i + 2:end]
        i = end


def adv_has_hid(payload):
    """True when an advertisement identifies a BLE HID/HOGP peripheral."""
    for kind, value in _adv_fields(payload):
        # Incomplete/complete 16-bit service UUID lists.
        if kind in (0x02, 0x03):
            for i in range(0, len(value) - 1, 2):
                if value[i] | (value[i + 1] << 8) == _HID_SERVICE:
                    return True
        # 16-bit service data begins with its UUID.
        elif kind == 0x16 and len(value) >= 2:
            if value[0] | (value[1] << 8) == _HID_SERVICE:
                return True
    return False


def adv_name(payload):
    """Best-effort local name from an advertisement/scan response."""
    short = None
    for kind, value in _adv_fields(payload):
        if kind in (0x08, 0x09):
            try:
                name = bytes(value).decode("utf-8")
            except Exception:
                name = "?"
            if kind == 0x09:
                return name
            short = name
    return short


def decode_keyboard_report(report):
    """Return ``(modifiers, usages_tuple)`` for a boot-shaped HID report.

    BLE Report characteristics normally omit a report id and deliver eight
    bytes.  A few inexpensive devices include the id anyway; accept their
    nine-byte ``id + boot report`` shape.  Other layouts (notably NKRO bitmaps)
    are ignored instead of being misread as keys.
    """
    data = bytes(report)
    if len(data) == 9 and data[2] == 0:
        data = data[1:]
    if len(data) != 8 or data[1] != 0:
        return None
    keys = []
    for usage in data[2:8]:
        # 0 = empty; 1..3 are HID error sentinels, not real keys.
        if usage > 3 and usage not in keys:
            keys.append(usage)
    return data[0], tuple(keys)


_PLAIN = {
    0x28: "\r", 0x29: "\x1b", 0x2A: "\x08", 0x2B: "\t", 0x2C: " ",
    0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\",
    0x32: "#", 0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",",
    0x37: ".", 0x38: "/", 0x4C: "\x7f",
}
_SHIFT = {
    0x1E: "!", 0x1F: "@", 0x20: "#", 0x21: "$", 0x22: "%",
    0x23: "^", 0x24: "&", 0x25: "*", 0x26: "(", 0x27: ")",
    0x2D: "_", 0x2E: "+", 0x2F: "{", 0x30: "}", 0x31: "|",
    0x32: "~", 0x33: ":", 0x34: '"', 0x35: "~", 0x36: "<",
    0x37: ">", 0x38: "?",
}
_DIGITS = "1234567890"


def usage_to_keycode(usage, modifiers=0, caps=False):
    """Translate one USB-HID keyboard usage into Moybyte's byte key contract."""
    usage = int(usage)
    shift = bool(modifiers & 0x22)  # left/right shift
    ctrl = bool(modifiers & 0x11)   # left/right control
    if 0x04 <= usage <= 0x1D:
        letter = usage - 0x04
        if ctrl:
            return letter + 1       # Ctrl+A..Z -> 0x01..0x1a
        ch = chr(ord("a") + letter)
        if shift != bool(caps):
            ch = ch.upper()
        return ord(ch)
    if 0x1E <= usage <= 0x27:
        ch = _SHIFT.get(usage) if shift else _DIGITS[usage - 0x1E]
        return ord(ch) if ch else 0
    ch = _SHIFT.get(usage) if shift else None
    if ch is None:
        ch = _PLAIN.get(usage)
    return ord(ch) if ch else 0


# THE ARROW-KEY HOST SCHEME (owner call, 2026-08-14): arrows + Z/X, which is
# PICO-8's and every emulator's. A BLE keyboard HAS arrows (see _DIRECT_BUTTON
# below, the HID usages), so this board belongs with the pygame sim and the
# browser page -- NOT with the T-Deck, whose keyboard has no arrows at all and
# whose Z/X sit under the same thumb WASD needs, and which therefore uses L/K.
#
# This docstring used to say "shared with the T-Deck keyboard's game-button
# mapping". It was never shared -- it was a fourth hand-written copy, and by the
# time anyone looked it still had hjkl as a d-pad and R as `run`, both of which
# the other tiers had moved on from.
BUTTON_FOR_KEY = {
    ord("w"): "up",
    ord("s"): "down",
    ord("a"): "left",
    ord("d"): "right",
    ord("z"): "a",
    ord(" "): "a",          # SPACE = jump, every tier -- the one alias
    ord("x"): "b",
    0x0D: "run",            # ENTER, also the launcher's "open this cart"
    0x1B: "stop",
    0x08: "home",           # BACKSPACE: THE console key / hold to exit
}


def buttons_for_key(key):
    """A typed ASCII byte -> the buttons it fires on a keyboard WITH arrows."""
    if 65 <= key <= 90:                      # uppercase -> the same button
        key += 32
    b = BUTTON_FOR_KEY.get(key)
    return (b,) if b is not None else ()


_DIRECT_BUTTON = {
    0x4F: "right",
    0x50: "left",
    0x51: "down",
    0x52: "up",
}


class BleHidKeyboard:
    """One auto-discovered BLE HID keyboard feeding a Moybyte InputState."""

    # Settings uses this explicit capability instead of guessing from the
    # keyboard object.  The T-Deck keyboard is local hardware and therefore
    # never grows a Bluetooth row merely because it also has set_game_mode().
    settings_capable = True

    SCAN_MS = 5000
    RETRY_MS = 5000       # don't keep the shared C6 radio in near-continuous scan
    # The scan's RADIO DUTY, not its cadence, was the espnow link's biggest
    # enemy (measured 2026-08-24, #7): interval == window == 30ms is a
    # CONTINUOUS scan, and with no keyboard connected the 5s-scan/5s-idle
    # retry loop kept the shared radio deaf HALF the time -- the P4's C6
    # dropped ~40% of inbound espnow packets at an idle desk (19.2/s received
    # of 32.5 offered; 29.5/s the moment the scan stopped), which was most of
    # the P4<->T-Deck lockstep stall rate. Background rescans now scan 30ms in
    # every 300ms (10% duty, PASSIVE -- reconnect matches the bonded address
    # from the ADV itself, so scan responses are not needed); an advertising
    # keyboard is still found in about a second. The PICKER keeps the
    # continuous ACTIVE scan: user-facing, brief, and it wants names.
    BG_INTERVAL_US = 300000
    BG_WINDOW_US = 30000
    PICKER_INTERVAL_US = 30000
    PICKER_WINDOW_US = 30000
    CONNECT_TIMEOUT_MS = 12000
    DISCOVERY_TIMEOUT_MS = 15000

    def __init__(self, input_state, ble=None, store_path="/moy/ble_keyboard.json",
                 auto_start=True):
        self.input = input_state
        # This keyboard is ONE SOURCE among however many the board has (#26):
        # on the T-Deck it shares the console with the physical C3 keyboard,
        # which used to assert full authority over the shared InputState every
        # poll and erase every BLE press within a frame. Writing a named
        # source is what makes both keyboards work at once. (`getattr` because
        # a host test fake may still be the flat pre-source shape.)
        _src = getattr(input_state, "source", None)
        self.src = _src("ble") if _src is not None else input_state
        # #65: the player slot this keyboard is MEANT to drive. None = nobody
        # has asked, so the source is left entirely alone; a number is honoured
        # only while connected.
        self._want_player = None
        self.store_path = store_path
        self.ble = ble
        self.available = False
        self.state = "off"
        self.name = None
        self.passkey = None
        self.error = None

        self._bt = None
        self._uuid_hid = None
        self._uuid_report = None
        self._uuid_boot = None
        self._uuid_protocol = None
        self._uuid_cccd = None
        self._conn = None
        self._candidate = None
        self._candidate_name = None
        self._hid_range = None
        self._protocol_handle = None
        self._chars = []
        self._descriptors = []
        self._input_handles = set()
        self._reports = {}
        # Preserve make edges that are followed by a release notification before
        # the next display frame.  BLE input and the 60 Hz render loop are
        # asynchronous; keeping only the newest level can otherwise lose a fast
        # tap completely.
        self._pending_usages = set()
        self._pending_modifiers = 0
        self._notify_count = 0
        self._trace_on = False
        self._trace_events = []
        self._trace_input = None
        self.protocol = None
        self._subscribe_queue = []
        self._subscribe_all = []
        self._write_pending = None
        self._subscribe_retries = 0
        self._encrypted = False
        self._caps = False
        self._secrets = {}
        self._store_dirty = False
        # User-facing Bluetooth settings.  Version-1 stores contained only the
        # bond keys + display name; they migrate with enabled=True and learn the
        # exact preferred address on their next successful connection.
        self._enabled = True
        self._preferred = None       # (addr_type, six address bytes)
        self._devices = {}           # address tuple -> [display name, RSSI]
        self._device_order = []
        self._scan_picker = False
        self._pending_scan = None    # None or bool picker mode after stop/disconnect
        self._pending_connect = None # (address tuple, display name)
        self._manual_hold = False    # picker/forget waits for an explicit choice
        self._fast = None
        self._fast_active = False
        self._conn_interval_ms = None
        # The ESP32 port invokes BLE IRQ handlers on NimBLE's core-0 task.
        # Never print there: MicroPython's print path is stack-heavy and serial
        # output can overlap the core-1 desktop.  IRQ-side code only appends
        # small tuples; poll() emits them later on the main VM task.
        self._log_queue = []
        self._state_at = _ticks_ms()
        self._retry_at = self._state_at

        try:
            import moy_ble_hid
            self._fast = moy_ble_hid
        except ImportError:
            pass

        self._load_store()
        if auto_start:
            self.start()

    # -- lifecycle / public diagnostics ---------------------------------

    def start(self):
        if self.available:
            if self._enabled and self.state == "disabled":
                self._manual_hold = False
                self._start_scan()
            return True
        try:
            import bluetooth
            self._bt = bluetooth
            if self.ble is None:
                self.ble = bluetooth.BLE()
            self._uuid_hid = bluetooth.UUID(_HID_SERVICE)
            self._uuid_report = bluetooth.UUID(_REPORT)
            self._uuid_boot = bluetooth.UUID(_BOOT_KEYBOARD_INPUT)
            self._uuid_protocol = bluetooth.UUID(_PROTOCOL_MODE)
            self._uuid_cccd = bluetooth.UUID(_CCCD)
            self.ble.irq(self._irq)
            # Just Works bonding is the broadest first-cut compatibility for
            # inexpensive keyboards and needs no passkey UI.  The IRQ handler
            # still reports/responds if a peripheral requests a passkey action.
            self.ble.config(bond=True)
            self.ble.config(mitm=False)
            self.ble.config(le_secure=False)
            self.ble.config(io=_IO_CAPABILITY_NO_INPUT_OUTPUT)
            try:
                self.ble.config(rxbuf=512)
            except Exception:
                pass
            try:
                # ESP-Hosted boards only (the P4): since hosted ~2.8 the
                # co-processor's BT controller is not initialised or enabled
                # by default -- the HOST does both before NimBLE's first HCI
                # command (esp-hosted-mcu#212; without it active(True) panics
                # the interrupt watchdog). Absent module = radio-on-SoC board.
                import moy_c6
                moy_c6.bt_up()
            except ImportError:
                pass
            self.ble.active(True)
            self.available = True
            if self._enabled:
                self._start_scan()
            else:
                self.state = "disabled"
            return True
        except Exception as exc:
            self.error = str(exc)
            self.state = "off"
            self._log("Moybyte BLE keyboard unavailable:", exc)
            return False

    def status(self):
        return self.state, self.name, self.passkey

    def settings_status(self):
        """Stable, allocation-small status contract consumed by Settings."""
        return (self._enabled, self.state, self.name, self._preferred, self.error)

    def settings_devices(self):
        """Return discovered keyboards in display order.

        Each row is ``(opaque_address, name, rssi, preferred, connected)``.
        The address tuple is deliberately opaque to the shared UI; only this
        service interprets it, while persistence serializes it as hex.
        """
        out = []
        preferred = self._preferred
        if preferred is not None and preferred not in self._devices:
            self._remember_device(preferred[0], preferred[1], self.name, -127)
        for key in self._device_order:
            item = self._devices.get(key)
            if item is None:
                continue
            out.append((key, item[0], item[1], key == preferred,
                        self._conn is not None and key == self._candidate))
        return tuple(out)

    def set_enabled(self, on):
        """Enable/disable Bluetooth keyboard input without forgetting its bond."""
        on = bool(on)
        if on == self._enabled:
            return on
        self._enabled = on
        self._store_dirty = True
        self._pending_scan = None
        self._pending_connect = None
        self._manual_hold = False
        if not on:
            self._clear_reports()
            if self.state in ("scanning", "found"):
                try:
                    self.ble.gap_scan(None)
                except Exception:
                    pass
            if self._conn is not None:
                try:
                    self.ble.gap_disconnect(self._conn)
                except Exception:
                    self._reset_connection()
            else:
                self._reset_connection()
            self.state = "disabled"
            return False
        if not self.available:
            self.start()
        else:
            self.state = "idle"
            self._retry_at = _ticks_ms()
            self._start_scan()
        return True

    def discover_devices(self):
        """Scan for every nearby HOGP keyboard and wait for an explicit pick."""
        if not self._enabled:
            return False
        self._manual_hold = True
        return self._request_scan(True)

    def connect_device(self, address):
        """Persist + connect the opaque address returned by settings_devices()."""
        try:
            key = (int(address[0]), bytes(address[1]))
        except Exception:
            return False
        item = self._devices.get(key)
        if item is None:
            return False
        self._preferred = key
        self.name = item[0] or self.name or "BLE keyboard"
        self._store_dirty = True
        self._manual_hold = False
        self._pending_scan = None
        self._pending_connect = (key, self.name)
        if self._conn is not None:
            if key == self._candidate and self.state == "ready":
                self._pending_connect = None
                return True
            try:
                self.ble.gap_disconnect(self._conn)
                return True
            except Exception:
                self._reset_connection()
        if self.state in ("scanning", "found"):
            try:
                self.ble.gap_scan(None)
                return True
            except Exception:
                pass
        self._begin_pending_connect()
        return True

    def fast_status(self):
        if self._fast is None:
            return None
        try:
            return self._fast.stats()
        except Exception:
            return None

    def _log(self, *parts):
        if len(self._log_queue) < 32:
            self._log_queue.append(parts)

    def trace(self, on=True):
        """Toggle raw notification diagnostics, printed safely from poll()."""
        self._trace_on = bool(on)
        self._trace_events = []
        self._trace_input = None
        return self._trace_on

    def set_game_mode(self, _on):
        """Compatibility with Workstation's T-Deck keyboard mode hook.

        BLE HID reports always carry clean text plus real make/break state, so
        there is no hardware ASCII/raw mode to flip.  ``poll`` reads
        ``input.text_mode`` and suppresses letter-to-game aliases there.
        """
        return

    def forget(self):
        """Forget the selected keyboard and local bond keys (REPL affordance)."""
        conn = self._conn
        self._secrets = {}
        self._preferred = None
        self._devices = {}
        self._device_order = []
        self.name = None
        self._store_dirty = True
        self._manual_hold = True
        self._pending_scan = None
        self._pending_connect = None
        self._clear_reports()
        if conn is not None:
            try:
                self.ble.gap_disconnect(conn)
            except Exception:
                pass
        self._reset_connection()
        self.state = "choose" if self._enabled else "disabled"

    def scan(self):
        """Restart discovery now (REPL affordance)."""
        if not self.available or not self._enabled:
            return False
        self._manual_hold = False
        return self._request_scan(False)

    # -- per-frame bridge -------------------------------------------------

    def set_player(self, slot):
        """Which PLAYER this keyboard drives (#65 Phase 1).

        On a board that already HAS a keyboard, a paired Bluetooth one is the
        natural second controller -- two kids, two real keyboards, one screen,
        and no radio between consoles. Assigning it a player is the entire
        mechanism: a source carries a player (#26), and two sources disagreeing
        IS multiplayer, so players() reports 2 with nothing else wired.

        Stored as an INTENT rather than applied straight through, because a
        keyboard that is not connected must not hold a player slot: the cart
        would field a second character nobody could move. _sync_player resolves
        the intent against the live connection every frame.
        """
        self._want_player = int(slot)
        self._sync_player()

    def _sync_player(self):
        """Own the player slot only while actually connected. One int compare
        per frame, on a path that already runs per frame.

        A no-op until somebody ASKS for a slot: managing it unconditionally
        would stomp a direct `src.player = n` every poll, and that assignment is
        the documented one-attribute way to make this keyboard a player."""
        want = self._want_player
        if want is None:
            return
        if self.state != "ready":
            want = 0
        src = self.src
        if getattr(src, "_player", None) == want:
            return
        try:
            src.player = want
        except AttributeError:      # a pre-source fake: no players for it
            pass

    def poll(self):
        """Apply latest report level-state before InputState.begin_frame()."""
        self._sync_player()
        logs = self._log_queue
        self._log_queue = []
        for parts in logs:
            try:
                print(*parts)
            except Exception:
                pass

        if self._store_dirty:
            self._save_store()

        now = _ticks_ms()
        if self.available and self._enabled and not self._manual_hold \
                and self._conn is None and self.state == "idle" \
                and _ticks_diff(now, self._retry_at) >= 0:
            self._start_scan()
        elif self._conn is None and self.state == "connecting" \
                and _ticks_diff(now, self._state_at) > self.CONNECT_TIMEOUT_MS:
            self.state = "idle"
            self._retry_at = now + self.RETRY_MS
        elif self._conn is not None and self.state in ("pairing", "discovering",
                                                        "subscribe-retry") \
                and _ticks_diff(now, self._state_at) > self.DISCOVERY_TIMEOUT_MS:
            self._disconnect("discovery timeout")

        self._drain_fastpath()

        modifiers = 0
        usages = set()
        for report in self._reports.values():
            modifiers |= report[0]
            for usage in report[1]:
                usages.add(usage)
        # A complete make+break can land between two polls. Surface that make for
        # one frame, then the already-stored empty level produces a clean release
        # on the following frame.
        modifiers |= self._pending_modifiers
        for usage in self._pending_usages:
            usages.add(usage)
        self._pending_modifiers = 0
        self._pending_usages.clear()

        # PER-SOURCE "I hold nothing", not the shared "everybody let go".
        src = self.src
        src.release_all()
        text_mode = bool(getattr(self.input, "text_mode", False))
        buttons = set()
        for usage in usages:
            direct = _DIRECT_BUTTON.get(usage)
            if direct is not None:
                buttons.add(direct)
            if not text_mode:
                key = usage_to_keycode(usage, modifiers, self._caps)
                for button in buttons_for_key(key):
                    buttons.add(button)
        for button in buttons:
            try:
                src.set_button(button, True)
            except ValueError:
                pass

        # One representative held byte, exactly like the T-Deck raw keyboard.
        # Editors edge-detect it; carts can read it as level state with key().
        key_out = 0
        for usage in sorted(usages):
            key_out = usage_to_keycode(usage, modifiers, self._caps)
            if key_out:
                break
        src.last_key = key_out

        if self._trace_on:
            try:
                import binascii
                events = self._trace_events
                self._trace_events = []
                for seq, handle, payload, decoded, age_us in events:
                    print("BTKEY notify=%d handle=%d len=%d age_us=%d "
                          "raw=%s decoded=%s"
                          % (seq, handle, len(payload),
                             age_us, binascii.hexlify(payload).decode(), decoded))
                current = (tuple(sorted(usages)), tuple(sorted(buttons)), key_out)
                if current != self._trace_input:
                    print("BTKEY input usages=%s buttons=%s key=%d protocol=%s"
                          % (current[0], current[1], key_out, self.protocol))
                    self._trace_input = current
            except Exception as exc:
                print("BTKEY trace failed:", exc)

    # -- BLE state machine ------------------------------------------------

    def _set_state(self, state):
        self.state = state
        self._state_at = _ticks_ms()

    def _remember_device(self, addr_type, addr, name=None, rssi=-127):
        key = (int(addr_type), bytes(addr))
        item = self._devices.get(key)
        if item is None:
            item = [name or "BLE keyboard", int(rssi)]
            self._devices[key] = item
            # Keep the saved keyboard at the top; append all newly-seen peers.
            if key == self._preferred:
                self._device_order.insert(0, key)
            else:
                self._device_order.append(key)
        else:
            if name and name != "?":
                item[0] = name
            item[1] = int(rssi)
        return key

    def _request_scan(self, picker):
        if not self.available or not self._enabled:
            return False
        self._pending_connect = None
        if self._conn is not None:
            self._pending_scan = bool(picker)
            try:
                self.ble.gap_disconnect(self._conn)
                return True
            except Exception:
                self._reset_connection()
        if self.state in ("scanning", "found"):
            self._pending_scan = bool(picker)
            try:
                self.ble.gap_scan(None)
                return True
            except Exception:
                self._pending_scan = None
        self._reset_connection()
        return self._start_scan(picker)

    def _begin_pending_connect(self):
        pending = self._pending_connect
        self._pending_connect = None
        if pending is None or not self._enabled:
            return False
        self._candidate, self._candidate_name = pending
        self._connect_candidate()
        return True

    def _start_scan(self, picker=False):
        if not self.available or not self._enabled or self._conn is not None:
            return False
        self._candidate = None
        self._candidate_name = None
        self._scan_picker = bool(picker)
        if picker:
            # Retain only the saved identity while fresh results arrive. This
            # gives the panel immediate context without presenting stale peers.
            self._devices = {}
            self._device_order = []
            if self._preferred is not None:
                self._remember_device(self._preferred[0], self._preferred[1],
                                      self.name, -127)
        self.error = None
        self._set_state("scanning")
        if picker:
            iv, win, act = self.PICKER_INTERVAL_US, self.PICKER_WINDOW_US, True
        else:
            iv, win, act = self.BG_INTERVAL_US, self.BG_WINDOW_US, False
        try:
            try:
                self.ble.gap_scan(self.SCAN_MS, iv, win, act)
            except TypeError:
                self.ble.gap_scan(self.SCAN_MS, iv, win)
            self._log("Moybyte BLE keyboard: scanning%s"
                      % (" for devices" if picker else ""))
            return True
        except Exception as exc:
            self.error = str(exc)
            self.state = "idle"
            self._retry_at = _ticks_ms() + self.RETRY_MS
            self._log("Moybyte BLE keyboard scan failed:", exc)
            return False

    def _connect_candidate(self):
        candidate = self._candidate
        if candidate is None or not self._enabled:
            self.state = "idle"
            self._retry_at = _ticks_ms() + self.RETRY_MS
            return
        self._set_state("connecting")
        try:
            try:
                # Request the low-latency range allowed by BLE. The peripheral
                # may negotiate a different interval; _IRQ_CONNECTION_UPDATE
                # records the resulting value for `bt status` diagnostics.
                self.ble.gap_connect(candidate[0], candidate[1],
                                     self.CONNECT_TIMEOUT_MS, 7500, 15000)
            except TypeError:
                self.ble.gap_connect(candidate[0], candidate[1])
        except Exception as exc:
            self.error = str(exc)
            self.state = "idle"
            self._retry_at = _ticks_ms() + self.RETRY_MS

    def _discover(self):
        if self._conn is None:
            return
        self._hid_range = None
        self._chars = []
        self._descriptors = []
        self._input_handles = set()
        self._reports = {}
        self._set_state("discovering")
        try:
            self.ble.gattc_discover_services(self._conn, self._uuid_hid)
        except TypeError:
            self.ble.gattc_discover_services(self._conn)
        except Exception as exc:
            self._disconnect("service discovery: %s" % (exc,))

    def _discover_descriptors(self):
        if self._conn is None or self._hid_range is None:
            return
        try:
            self.ble.gattc_discover_descriptors(
                self._conn, self._hid_range[0], self._hid_range[1])
        except Exception as exc:
            self._disconnect("descriptor discovery: %s" % (exc,))

    def _prepare_subscriptions(self):
        boot_candidates = []
        report_candidates = []
        self._protocol_handle = None
        for item in self._chars:
            _def_handle, value_handle, props, uuid = item
            if uuid == self._uuid_protocol:
                self._protocol_handle = value_handle
            elif uuid == self._uuid_boot and props & _FLAG_NOTIFY:
                boot_candidates.append(item)
            elif uuid == self._uuid_report and props & _FLAG_NOTIFY:
                report_candidates.append(item)

        # HOGP defines two distinct host roles. Prefer Boot Host whenever the
        # device exposes it: explicitly write Protocol Mode=0, then consume only
        # the fixed boot-keyboard report. Mixing Boot and Report characteristics
        # (the first implementation did this) leaves the payload layout ambiguous
        # and is contrary to the profile's setup procedure.
        if boot_candidates:
            candidates = boot_candidates
            self.protocol = "boot"
            self._set_boot_protocol()
        else:
            # Report-only devices remain connected for diagnostics. Their Report
            # Map/Report Reference can describe layouts other than the standard
            # eight-byte keyboard shape; trace exposes those raw values until the
            # full descriptor parser is needed by a real keyboard.
            candidates = report_candidates
            self.protocol = "report"

        queue = []
        for item in candidates:
            def_handle, value_handle, _props, _uuid = item
            next_def = self._hid_range[1] + 1
            for other in self._chars:
                if def_handle < other[0] < next_def:
                    next_def = other[0]
            cccd = None
            for handle, uuid in self._descriptors:
                if value_handle < handle < next_def and uuid == self._uuid_cccd:
                    cccd = handle
                    break
            if cccd is not None:
                self._input_handles.add(value_handle)
                queue.append(cccd)

        self._subscribe_all = list(queue)
        self._subscribe_queue = queue
        self._write_pending = None
        self._subscribe_retries = 0
        if not queue:
            self._disconnect("no keyboard input report CCCD")
            return
        self._write_next()

    def _set_boot_protocol(self):
        if self._conn is None or self._protocol_handle is None:
            return False
        try:
            # Protocol Mode is a Write Without Response characteristic in HIDS.
            self.ble.gattc_write(self._conn, self._protocol_handle, b"\x00", 0)
            self._log("Moybyte BLE keyboard: boot protocol")
            return True
        except Exception as exc:
            self._log("Moybyte BLE keyboard boot protocol failed:", exc)
            return False

    def _write_next(self):
        if self._conn is None:
            return
        if not self._subscribe_queue:
            self._write_pending = None
            self._enable_fastpath()
            self._set_state("ready")
            self._log("Moybyte BLE keyboard ready:", self.name or "?")
            return
        handle = self._subscribe_queue.pop(0)
        self._write_pending = handle
        try:
            self.ble.gattc_write(self._conn, handle, b"\x01\x00", 1)
        except Exception as exc:
            self._disconnect("subscribe: %s" % (exc,))

    def _retry_subscriptions(self):
        if self._conn is None or not self._subscribe_all:
            return
        if self.protocol == "boot":
            # The first write may have happened before pairing established ATT
            # encryption. Reassert boot mode on the encrypted retry path.
            self._set_boot_protocol()
        self._subscribe_queue = list(self._subscribe_all)
        self._write_pending = None
        self._write_next()

    def _disconnect(self, reason=None):
        if reason:
            self.error = reason
            self._log("Moybyte BLE keyboard:", reason)
        conn = self._conn
        if conn is not None:
            try:
                self.ble.gap_disconnect(conn)
                return
            except Exception:
                pass
        self._reset_connection()
        if self._enabled:
            self.state = "idle"
            self._retry_at = _ticks_ms() + self.RETRY_MS
        else:
            self.state = "disabled"

    def _reset_connection(self):
        self._disable_fastpath()
        self._conn = None
        self._candidate = None
        self._candidate_name = None
        self._hid_range = None
        self._protocol_handle = None
        self._chars = []
        self._descriptors = []
        self._input_handles = set()
        self._subscribe_queue = []
        self._subscribe_all = []
        self._write_pending = None
        self._subscribe_retries = 0
        self._encrypted = False
        self._conn_interval_ms = None
        self.passkey = None
        self._clear_reports()

    def _clear_reports(self):
        self._reports = {}
        self._pending_usages.clear()
        self._pending_modifiers = 0
        try:
            self.src.release_all()      # this keyboard's buttons only
            self.src.last_key = 0
        except Exception:
            pass

    # -- native notification fast path ----------------------------------

    def _enable_fastpath(self):
        if self._fast is None or self._conn is None or not self._input_handles:
            return False
        try:
            self._fast.configure(self._conn, tuple(self._input_handles))
            self._fast_active = True
            self._log("Moybyte BLE keyboard: native input queue")
            return True
        except Exception as exc:
            self._fast_active = False
            self._log("Moybyte BLE keyboard native queue unavailable:", exc)
            return False

    def _disable_fastpath(self):
        if self._fast is not None:
            try:
                self._fast.disable()
            except Exception:
                pass
        self._fast_active = False

    def _drain_fastpath(self):
        if not self._fast_active:
            return
        try:
            while True:
                event = self._fast.read()
                if event is None:
                    break
                value_handle, payload, age_us = event
                self._consume_report(value_handle, payload, age_us)
        except Exception as exc:
            # Disable interception so subsequent reports fall back to the
            # ordinary MicroPython IRQ rather than leaving input stuck.
            self._log("Moybyte BLE keyboard native queue failed:", exc)
            self._disable_fastpath()

    def _consume_report(self, value_handle, payload, age_us=-1):
        decoded = decode_keyboard_report(payload)
        self._notify_count += 1
        if self._trace_on and len(self._trace_events) < 24:
            self._trace_events.append((self._notify_count, value_handle,
                                       bytes(payload), decoded, int(age_us)))
        if decoded is None:
            return
        old = self._reports.get(value_handle, (0, ()))
        old_keys = old[1]
        new_keys = decoded[1]
        for usage in new_keys:
            if usage not in old_keys:
                self._pending_usages.add(usage)
                self._pending_modifiers |= decoded[0]
        if 0x39 in new_keys and 0x39 not in old_keys:  # Caps Lock make edge
            self._caps = not self._caps
        self._reports[value_handle] = decoded

    # -- IRQ handler ------------------------------------------------------

    def _irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            addr_type, addr, adv_type, rssi, payload = data
            if self.state != "scanning":
                return
            key = (int(addr_type), bytes(addr))
            has_hid = adv_has_hid(payload)
            known = key in self._devices
            preferred = key == self._preferred
            # A scan response often carries only the name, after the preceding
            # advertisement established HIDS. Remember/update that same peer.
            if not has_hid and not known and not preferred:
                return
            name = adv_name(payload)
            key = self._remember_device(addr_type, addr, name, rssi)
            item = self._devices[key]
            if self._scan_picker:
                return
            # Once a keyboard has been picked, reconnect ONLY that address.
            # A version-1 store has no preferred address, so its first HOGP
            # connection migrates naturally and becomes the saved identity.
            if (self._preferred is None and has_hid) or preferred:
                self._candidate = key
                self._candidate_name = item[0]
                self._set_state("found")
                try:
                    self.ble.gap_scan(None)
                except Exception:
                    self._connect_candidate()

        elif event == _IRQ_SCAN_DONE:
            if not self._enabled:
                self.state = "disabled"
            elif self._pending_connect is not None:
                self._begin_pending_connect()
            elif self._pending_scan is not None:
                picker = self._pending_scan
                self._pending_scan = None
                self._start_scan(picker)
            elif self._scan_picker:
                self.state = "choose"
                self._manual_hold = True
            elif self.state == "found":
                self._connect_candidate()
            elif self.state == "scanning":
                self.state = "idle"
                self._retry_at = _ticks_ms() + self.RETRY_MS

        elif event == _IRQ_PERIPHERAL_CONNECT:
            conn_handle, addr_type, addr = data
            if not self._enabled:
                try:
                    self.ble.gap_disconnect(conn_handle)
                except Exception:
                    pass
                return
            candidate = self._candidate
            if candidate is not None \
                    and (addr_type != candidate[0] or bytes(addr) != candidate[1]):
                return
            self._conn = conn_handle
            self._encrypted = False
            self.name = self._candidate_name or self.name or "BLE keyboard"
            self._preferred = (int(addr_type), bytes(addr))
            self._remember_device(addr_type, addr, self.name, -127)
            self._store_dirty = True
            self._set_state("pairing")
            self._log("Moybyte BLE keyboard connected:", self.name)
            # Pairing and ATT discovery can proceed together.  Cheap keyboards
            # commonly use unauthenticated (Just Works) encryption; encrypted
            # CCCD writes complete once SMP has established the link keys.
            try:
                self.ble.gap_pair(conn_handle)
            except Exception as exc:
                self._log("Moybyte BLE keyboard pair start:", exc)
            self._discover()

        elif event == _IRQ_PERIPHERAL_DISCONNECT:
            conn_handle, _addr_type, _addr = data
            if conn_handle == self._conn:
                self._log("Moybyte BLE keyboard disconnected")
                self._reset_connection()
                if not self._enabled:
                    self.state = "disabled"
                elif self._pending_connect is not None:
                    self._begin_pending_connect()
                elif self._pending_scan is not None:
                    picker = self._pending_scan
                    self._pending_scan = None
                    self._start_scan(picker)
                elif self._manual_hold:
                    self.state = "choose"
                else:
                    self.state = "idle"
                    self._retry_at = _ticks_ms() + self.RETRY_MS

        elif event == _IRQ_GATTC_SERVICE_RESULT:
            conn_handle, start_handle, end_handle, uuid = data
            if conn_handle == self._conn and uuid == self._uuid_hid:
                self._hid_range = (start_handle, end_handle)

        elif event == _IRQ_GATTC_SERVICE_DONE:
            conn_handle, status = data
            if conn_handle != self._conn:
                return
            if status or self._hid_range is None:
                self._disconnect("HID service not found")
            else:
                try:
                    self.ble.gattc_discover_characteristics(
                        conn_handle, self._hid_range[0], self._hid_range[1])
                except Exception as exc:
                    self._disconnect("characteristic discovery: %s" % (exc,))

        elif event == _IRQ_GATTC_CHARACTERISTIC_RESULT:
            conn_handle, def_handle, value_handle, props, uuid = data
            if conn_handle == self._conn:
                # IRQ-owned UUID buffers must be copied before the handler returns.
                self._chars.append((def_handle, value_handle, props,
                                    self._bt.UUID(uuid)))

        elif event == _IRQ_GATTC_CHARACTERISTIC_DONE:
            conn_handle, status = data
            if conn_handle == self._conn:
                if status:
                    self._disconnect("characteristic discovery failed")
                else:
                    self._discover_descriptors()

        elif event == _IRQ_GATTC_DESCRIPTOR_RESULT:
            conn_handle, handle, uuid = data
            if conn_handle == self._conn:
                self._descriptors.append((handle, self._bt.UUID(uuid)))

        elif event == _IRQ_GATTC_DESCRIPTOR_DONE:
            conn_handle, status = data
            if conn_handle == self._conn:
                if status:
                    self._disconnect("descriptor discovery failed")
                else:
                    self._prepare_subscriptions()

        elif event == _IRQ_GATTC_WRITE_DONE:
            conn_handle, value_handle, status = data
            if conn_handle == self._conn and value_handle == self._write_pending:
                if status:
                    # Security-gated HOGP CCCDs commonly reject the first write
                    # while SMP is still establishing encryption. Keep the link,
                    # then retry the complete subscription set once encryption is
                    # reported. A second failure is a real incompatibility.
                    self._write_pending = None
                    if self._subscribe_retries < 1:
                        self._subscribe_retries += 1
                        self._set_state("subscribe-retry")
                        if self._encrypted:
                            self._retry_subscriptions()
                    else:
                        self._disconnect(
                            "keyboard notification subscribe failed (%s)" % status)
                else:
                    self._write_pending = None
                    self._write_next()

        elif event == _IRQ_GATTC_NOTIFY:
            conn_handle, value_handle, payload = data
            if conn_handle != self._conn or value_handle not in self._input_handles:
                return
            # Fallback for builds without the native fast path (and for any
            # notification that arrived just before interception was enabled).
            self._consume_report(value_handle, payload)

        elif event == _IRQ_CONNECTION_UPDATE:
            conn_handle, interval, _latency, _timeout, status = data
            if conn_handle == self._conn and status == 0:
                # BLE interval units are 1.25ms.
                self._conn_interval_ms = interval * 1.25

        elif event == _IRQ_ENCRYPTION_UPDATE:
            conn_handle, encrypted, _authenticated, bonded, _key_size = data
            if conn_handle == self._conn:
                self._encrypted = bool(encrypted)
                self._log("Moybyte BLE keyboard security: encrypted=%s bonded=%s"
                          % (encrypted, bonded))
                if bonded:
                    self._store_dirty = True
                # A security-gated CCCD may have rejected the first write.  The
                # normal path reaches ready before this branch needs to do work;
                # retain the hook for a manual/on-glass retry.
                if encrypted and self.state == "subscribe-retry":
                    self._retry_subscriptions()

        elif event == _IRQ_PASSKEY_ACTION:
            conn_handle, action, passkey = data
            if conn_handle != self._conn:
                return
            if action == _PASSKEY_ACTION_DISPLAY:
                # This should not occur with the default no-I/O/Just-Works policy,
                # but keep a deterministic response + serial diagnostic for a
                # peripheral that insists on passkey entry.
                self.passkey = (_ticks_ms() ^ (conn_handle << 8)) % 1000000
                self._log("Moybyte BLE keyboard passkey: %06d" % self.passkey)
                self.ble.gap_passkey(conn_handle, action, self.passkey)
            elif action == _PASSKEY_ACTION_NUMCMP:
                self.passkey = passkey
                self._log("Moybyte BLE keyboard compare: %06d (accepted)" % passkey)
                self.ble.gap_passkey(conn_handle, action, 1)
            elif action == _PASSKEY_ACTION_INPUT:
                self.error = "keyboard asks console to enter a passkey"
                self._log("Moybyte BLE keyboard cannot enter remote passkey")

        elif event == _IRQ_SET_SECRET:
            sec_type, key, value = data
            skey = (sec_type, bytes(key))
            if value is None:
                if skey in self._secrets:
                    del self._secrets[skey]
                    self._store_dirty = True
                    return True
                return False
            self._secrets[skey] = bytes(value)
            self._store_dirty = True
            return True

        elif event == _IRQ_GET_SECRET:
            sec_type, index, key = data
            if key is not None:
                return self._secrets.get((sec_type, bytes(key)))
            i = 0
            for (stored_type, _stored_key), value in self._secrets.items():
                if stored_type == sec_type:
                    if i == index:
                        return value
                    i += 1
            return None

    # -- persistent NimBLE bond-key store --------------------------------

    def _load_store(self):
        if not self.store_path:
            return
        try:
            import binascii
            import json
            with open(self.store_path, "r") as src:
                data = json.load(src)
            # v1 stored only name + NimBLE secrets. Keep those bonds and learn
            # the preferred address on the next connection instead of forcing
            # an already-working keyboard through pairing again.
            if data.get("version") not in (1, _STORE_VERSION):
                return
            self._enabled = bool(data.get("enabled", True))
            self.name = data.get("name") or None
            preferred = data.get("preferred")
            if preferred and len(preferred) == 2:
                try:
                    self._preferred = (int(preferred[0]),
                                       binascii.unhexlify(preferred[1]))
                    self._remember_device(self._preferred[0], self._preferred[1],
                                          self.name, -127)
                except Exception:
                    self._preferred = None
            for item in data.get("secrets", ()):
                if len(item) != 3:
                    continue
                sec_type = int(item[0])
                key = binascii.unhexlify(item[1])
                value = binascii.unhexlify(item[2])
                self._secrets[(sec_type, key)] = value
        except Exception:
            self._secrets = {}

    def _save_store(self):
        if not self.store_path:
            self._store_dirty = False
            return
        try:
            import binascii
            import json
            import os
            secrets = []
            for (sec_type, key), value in self._secrets.items():
                secrets.append((sec_type,
                                binascii.hexlify(key).decode(),
                                binascii.hexlify(value).decode()))
            preferred = None
            if self._preferred is not None:
                preferred = (self._preferred[0],
                             binascii.hexlify(self._preferred[1]).decode())
            data = {"version": _STORE_VERSION, "enabled": self._enabled,
                    "name": self.name, "preferred": preferred,
                    "secrets": secrets}
            tmp = self.store_path + ".tmp"
            with open(tmp, "w") as dst:
                json.dump(data, dst)
            try:
                os.remove(self.store_path)
            except OSError:
                pass
            os.rename(tmp, self.store_path)
            self._store_dirty = False
        except Exception as exc:
            # Do not retry every 16ms if storage is unavailable; the live bond
            # still works for this boot and the next key change must stay cheap.
            self._store_dirty = False
            self._log("Moybyte BLE keyboard bond save failed:", exc)
