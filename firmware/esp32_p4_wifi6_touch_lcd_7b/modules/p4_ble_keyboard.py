"""Bluetooth LE HID keyboard input for the P4 desktop tier.

The ESP32-P4 has no radio of its own.  On the Waveshare 7B, mainline
MicroPython's C6_WIFI variant exposes the companion ESP32-C6 as a BLE controller
over the same ESP-Hosted SDIO transport used by ``network.WLAN``.  The board
definition already enables MicroPython's NimBLE central/GATT-client bindings, so
the keyboard path can stay in Python:

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
best-effort; a missing/broken Bluetooth stack leaves the P4 touch-only.
"""

import time

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

_STORE_VERSION = const(1)


def _ticks_ms():
    fn = getattr(time, "ticks_ms", None)
    if fn is not None:
        return fn()
    return int(time.monotonic() * 1000)


def _ticks_diff(a, b):
    fn = getattr(time, "ticks_diff", None)
    if fn is not None:
        return fn(a, b)
    return a - b


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


def buttons_for_key(key):
    """ASCII aliases shared with the T-Deck keyboard's game-button mapping."""
    if key in (ord("a"), ord("A"), ord("h"), ord("H")):
        return ("left",)
    if key in (ord("d"), ord("D"), ord("l"), ord("L")):
        return ("right",)
    if key in (ord("w"), ord("W"), ord("k"), ord("K")):
        return ("up",)
    if key in (ord("s"), ord("S"), ord("j"), ord("J")):
        return ("down",)
    if key in (ord("z"), ord("Z"), ord(" "), 0x0D):
        return ("a",)
    if key in (ord("x"), ord("X")):
        return ("b",)
    if key in (ord("r"), ord("R")):
        return ("run",)
    if key == 0x1B:
        return ("stop",)
    if key == 0x08:
        return ("home",)
    return ()


_DIRECT_BUTTON = {
    0x4F: "right",
    0x50: "left",
    0x51: "down",
    0x52: "up",
}


class BleHidKeyboard:
    """One auto-discovered BLE HID keyboard feeding a Moybyte InputState."""

    SCAN_MS = 5000
    RETRY_MS = 5000       # don't keep the shared C6 radio in near-continuous scan
    CONNECT_TIMEOUT_MS = 12000
    DISCOVERY_TIMEOUT_MS = 15000

    def __init__(self, input_state, ble=None, store_path="/moy/ble_keyboard.json",
                 auto_start=True):
        self.input = input_state
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
        self._fast = None
        self._fast_active = False
        self._conn_interval_ms = None
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
            self.ble.active(True)
            self.available = True
            self._start_scan()
            return True
        except Exception as exc:
            self.error = str(exc)
            self.state = "off"
            print("Moybyte P4 BLE keyboard unavailable:", exc)
            return False

    def status(self):
        return self.state, self.name, self.passkey

    def fast_status(self):
        if self._fast is None:
            return None
        try:
            return self._fast.stats()
        except Exception:
            return None

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
        self.name = None
        self._store_dirty = True
        self._clear_reports()
        if conn is not None:
            try:
                self.ble.gap_disconnect(conn)
            except Exception:
                pass
        self._reset_connection()
        self.state = "idle"
        self._retry_at = _ticks_ms()

    def scan(self):
        """Restart discovery now (REPL affordance)."""
        if not self.available:
            return False
        if self._conn is not None:
            try:
                self.ble.gap_disconnect(self._conn)
            except Exception:
                pass
        self._reset_connection()
        return self._start_scan()

    # -- per-frame bridge -------------------------------------------------

    def poll(self):
        """Apply latest report level-state before InputState.begin_frame()."""
        if self._store_dirty:
            self._save_store()

        now = _ticks_ms()
        if self.available and self._conn is None and self.state == "idle" \
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

        self.input.release_all()
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
                self.input.set_button(button, True)
            except ValueError:
                pass

        # One representative held byte, exactly like the T-Deck raw keyboard.
        # Editors edge-detect it; carts can read it as level state with key().
        key_out = 0
        for usage in sorted(usages):
            key_out = usage_to_keycode(usage, modifiers, self._caps)
            if key_out:
                break
        self.input.last_key = key_out

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

    def _start_scan(self):
        if not self.available or self._conn is not None:
            return False
        self._candidate = None
        self._candidate_name = None
        self.error = None
        self._set_state("scanning")
        try:
            try:
                self.ble.gap_scan(self.SCAN_MS, 30000, 30000, True)
            except TypeError:
                self.ble.gap_scan(self.SCAN_MS, 30000, 30000)
            print("Moybyte P4 BLE keyboard: scanning")
            return True
        except Exception as exc:
            self.error = str(exc)
            self.state = "idle"
            self._retry_at = _ticks_ms() + self.RETRY_MS
            print("Moybyte P4 BLE keyboard scan failed:", exc)
            return False

    def _connect_candidate(self):
        candidate = self._candidate
        if candidate is None:
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
            print("Moybyte P4 BLE keyboard: boot protocol")
            return True
        except Exception as exc:
            print("Moybyte P4 BLE keyboard boot protocol failed:", exc)
            return False

    def _write_next(self):
        if self._conn is None:
            return
        if not self._subscribe_queue:
            self._write_pending = None
            self._enable_fastpath()
            self._set_state("ready")
            print("Moybyte P4 BLE keyboard ready:", self.name or "?")
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
            print("Moybyte P4 BLE keyboard:", reason)
        conn = self._conn
        if conn is not None:
            try:
                self.ble.gap_disconnect(conn)
                return
            except Exception:
                pass
        self._reset_connection()
        self.state = "idle"
        self._retry_at = _ticks_ms() + self.RETRY_MS

    def _reset_connection(self):
        self._disable_fastpath()
        self._conn = None
        self._candidate = None
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
            self.input.release_all()
            self.input.last_key = 0
        except Exception:
            pass

    # -- native notification fast path ----------------------------------

    def _enable_fastpath(self):
        if self._fast is None or self._conn is None or not self._input_handles:
            return False
        try:
            self._fast.configure(self._conn, tuple(self._input_handles))
            self._fast_active = True
            print("Moybyte P4 BLE keyboard: native input queue")
            return True
        except Exception as exc:
            self._fast_active = False
            print("Moybyte P4 BLE keyboard native queue unavailable:", exc)
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
            print("Moybyte P4 BLE keyboard native queue failed:", exc)
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
            addr_type, addr, adv_type, _rssi, payload = data
            if self.state != "scanning":
                return
            if adv_type not in (_ADV_IND, _ADV_DIRECT_IND) and not adv_has_hid(payload):
                return
            name = adv_name(payload)
            if name:
                self._candidate_name = name
            if adv_has_hid(payload):
                self._candidate = (addr_type, bytes(addr))
                self._set_state("found")
                try:
                    self.ble.gap_scan(None)
                except Exception:
                    self._connect_candidate()

        elif event == _IRQ_SCAN_DONE:
            if self.state == "found":
                self._connect_candidate()
            elif self.state == "scanning":
                self.state = "idle"
                self._retry_at = _ticks_ms() + self.RETRY_MS

        elif event == _IRQ_PERIPHERAL_CONNECT:
            conn_handle, addr_type, addr = data
            candidate = self._candidate
            if candidate is not None \
                    and (addr_type != candidate[0] or bytes(addr) != candidate[1]):
                return
            self._conn = conn_handle
            self._encrypted = False
            self.name = self._candidate_name or self.name or "BLE keyboard"
            self._store_dirty = True
            self._set_state("pairing")
            print("Moybyte P4 BLE keyboard connected:", self.name)
            # Pairing and ATT discovery can proceed together.  Cheap keyboards
            # commonly use unauthenticated (Just Works) encryption; encrypted
            # CCCD writes complete once SMP has established the link keys.
            try:
                self.ble.gap_pair(conn_handle)
            except Exception as exc:
                print("Moybyte P4 BLE keyboard pair start:", exc)
            self._discover()

        elif event == _IRQ_PERIPHERAL_DISCONNECT:
            conn_handle, _addr_type, _addr = data
            if conn_handle == self._conn:
                print("Moybyte P4 BLE keyboard disconnected")
                self._reset_connection()
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
                print("Moybyte P4 BLE keyboard security: encrypted=%s bonded=%s"
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
                print("Moybyte P4 BLE keyboard passkey: %06d" % self.passkey)
                self.ble.gap_passkey(conn_handle, action, self.passkey)
            elif action == _PASSKEY_ACTION_NUMCMP:
                self.passkey = passkey
                print("Moybyte P4 BLE keyboard compare: %06d (accepted)" % passkey)
                self.ble.gap_passkey(conn_handle, action, 1)
            elif action == _PASSKEY_ACTION_INPUT:
                self.error = "keyboard asks console to enter a passkey"
                print("Moybyte P4 BLE keyboard cannot enter remote passkey")

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
            if data.get("version") != _STORE_VERSION:
                return
            self.name = data.get("name") or None
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
            data = {"version": _STORE_VERSION, "name": self.name,
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
            print("Moybyte P4 BLE keyboard bond save failed:", exc)
