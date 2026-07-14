"""P4 BLE-HID keyboard: report mapping + low-level GATT wiring.

The real transport is MicroPython NimBLE over the ESP32-C6 hosted controller.
These tests keep the device module importable on CPython, drive its IRQ state
machine with a fake BLE object, and verify the exact shared InputState contract.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"
          / "p4_ble_keyboard.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("p4_ble_keyboard_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


blekbd = _load_module()


class InputState:
    BUTTONS = {
        "up", "down", "left", "right", "a", "b", "run", "stop", "home",
    }

    def __init__(self):
        self._held = set()
        self._last = set()
        self._pressed = set()
        self._released = set()
        self.last_key = 0
        self.text_mode = False

    def release_all(self):
        self._held.clear()

    def set_button(self, name, held):
        if name not in self.BUTTONS:
            raise ValueError(name)
        if held:
            self._held.add(name)
        else:
            self._held.discard(name)

    def begin_frame(self):
        self._pressed = self._held - self._last
        self._released = self._last - self._held
        self._last = set(self._held)

    def held(self, name):
        return name in self._held

    def pressed(self, name):
        return name in self._pressed

    def released(self, name):
        return name in self._released


def test_advertisement_recognises_hid_service_and_name():
    # Flags + complete 16-bit service list (0x1812 LE) + complete local name.
    payload = bytes((2, 0x01, 0x06,
                     3, 0x03, 0x12, 0x18,
                     9, 0x09)) + b"Air Mini"
    assert blekbd.adv_has_hid(payload)
    assert blekbd.adv_name(payload) == "Air Mini"
    assert not blekbd.adv_has_hid(bytes((3, 0x03, 0x0F, 0x18)))


def test_boot_report_and_ascii_mapping_cover_typing_shortcuts_and_symbols():
    assert blekbd.decode_keyboard_report(b"\x02\x00\x04\x00\x00\x00\x00\x00") \
        == (0x02, (0x04,))
    assert blekbd.decode_keyboard_report(b"\x07\x02\x00\x04\x00\x00\x00\x00\x00") \
        == (0x02, (0x04,))
    assert blekbd.decode_keyboard_report(b"\x00\x01\x04\x00\x00\x00\x00\x00") is None

    assert blekbd.usage_to_keycode(0x04) == ord("a")
    assert blekbd.usage_to_keycode(0x04, modifiers=0x02) == ord("A")
    assert blekbd.usage_to_keycode(0x04, caps=True) == ord("A")
    assert blekbd.usage_to_keycode(0x04, modifiers=0x02, caps=True) == ord("a")
    assert blekbd.usage_to_keycode(0x1D, modifiers=0x01) == 0x1A  # Ctrl+Z
    assert blekbd.usage_to_keycode(0x1E, modifiers=0x02) == ord("!")
    assert blekbd.usage_to_keycode(0x2F, modifiers=0x02) == ord("{")
    assert blekbd.usage_to_keycode(0x28) == 0x0D
    assert blekbd.usage_to_keycode(0x2A) == 0x08


def test_report_level_state_gives_real_hold_edges_and_text_mode_suppression():
    inp = InputState()
    keyboard = blekbd.BleHidKeyboard(inp, store_path=None, auto_start=False)

    # W make -> held Up and one clean press edge; held report stays held.
    keyboard._reports[7] = (0, (0x1A,))
    keyboard.poll()
    inp.begin_frame()
    assert inp.last_key == ord("w")
    assert inp.held("up") and inp.pressed("up")

    keyboard.poll()
    inp.begin_frame()
    assert inp.held("up") and not inp.pressed("up")

    # W break -> release edge.
    keyboard._reports[7] = (0, ())
    keyboard.poll()
    inp.begin_frame()
    assert inp.last_key == 0
    assert inp.released("up")

    # Text mode types W but must not also fire its game alias. Physical arrows
    # remain directional so a desktop keyboard can navigate the editor.
    inp.text_mode = True
    keyboard._reports[7] = (0, (0x1A,))
    keyboard.poll()
    assert inp.last_key == ord("w")
    assert not inp.held("up")
    keyboard._reports[7] = (0, (0x50,))
    keyboard.poll()
    assert inp.last_key == 0
    assert inp.held("left")


def test_make_and_break_between_frames_preserves_one_press_then_release():
    inp = InputState()
    keyboard = blekbd.BleHidKeyboard(inp, store_path=None, auto_start=False)
    keyboard._conn = 1
    keyboard._input_handles = {7}

    # A quick tap can deliver both notifications before the render loop polls.
    keyboard._irq(blekbd._IRQ_GATTC_NOTIFY,
                  (1, 7, b"\x00\x00\x1a\x00\x00\x00\x00\x00"))
    keyboard._irq(blekbd._IRQ_GATTC_NOTIFY,
                  (1, 7, b"\x00\x00\x00\x00\x00\x00\x00\x00"))
    keyboard.poll()
    inp.begin_frame()
    assert inp.held("up") and inp.pressed("up")

    keyboard.poll()
    inp.begin_frame()
    assert not inp.held("up") and inp.released("up")


class FakeUUID:
    def __init__(self, value):
        self.value = value.value if isinstance(value, FakeUUID) else int(value)

    def __eq__(self, other):
        return isinstance(other, FakeUUID) and self.value == other.value

    def __hash__(self):
        return self.value


class FakeBLE:
    def __init__(self):
        self.handler = None
        self.calls = []

    def irq(self, handler):
        self.handler = handler

    def config(self, **kwargs):
        self.calls.append(("config", kwargs))

    def active(self, on):
        self.calls.append(("active", on))

    def gap_scan(self, *args):
        self.calls.append(("scan", args))

    def gap_connect(self, addr_type, addr):
        self.calls.append(("connect", addr_type, bytes(addr)))

    def gap_pair(self, conn):
        self.calls.append(("pair", conn))

    def gap_disconnect(self, conn):
        self.calls.append(("disconnect", conn))

    def gattc_discover_services(self, *args):
        self.calls.append(("services", args))

    def gattc_discover_characteristics(self, *args):
        self.calls.append(("chars", args))

    def gattc_discover_descriptors(self, *args):
        self.calls.append(("descriptors", args))

    def gattc_write(self, conn, handle, value, mode):
        self.calls.append(("write", conn, handle, bytes(value), mode))


class FakeFastQueue:
    def __init__(self):
        self.events = []
        self.configured = None
        self.disabled = False

    def configure(self, conn, handles):
        self.configured = (conn, tuple(handles))
        self.disabled = False

    def disable(self):
        self.disabled = True

    def read(self):
        return self.events.pop(0) if self.events else None

    def stats(self):
        return (2, 0, len(self.events), 2, not self.disabled)


def test_irq_state_machine_scans_pairs_discovers_subscribes_and_feeds_input(monkeypatch):
    fake_module = types.SimpleNamespace(UUID=FakeUUID, BLE=FakeBLE)
    monkeypatch.setitem(sys.modules, "bluetooth", fake_module)
    radio = FakeBLE()
    inp = InputState()
    keyboard = blekbd.BleHidKeyboard(inp, ble=radio, store_path=None)

    assert keyboard.state == "scanning"
    addr = b"\x01\x02\x03\x04\x05\x06"
    hid_adv = bytes((3, 0x03, 0x12, 0x18))
    keyboard._irq(blekbd._IRQ_SCAN_RESULT, (0, addr, 0, -40, hid_adv))
    assert keyboard.state == "found"
    assert ("scan", (None,)) in radio.calls
    keyboard._irq(blekbd._IRQ_SCAN_DONE, None)
    assert ("connect", 0, addr) in radio.calls

    keyboard._irq(blekbd._IRQ_PERIPHERAL_CONNECT, (3, 0, addr))
    assert ("pair", 3) in radio.calls
    assert any(call[0] == "services" for call in radio.calls)

    keyboard._irq(blekbd._IRQ_GATTC_SERVICE_RESULT,
                  (3, 1, 12, FakeUUID(blekbd._HID_SERVICE)))
    keyboard._irq(blekbd._IRQ_GATTC_SERVICE_DONE, (3, 0))
    keyboard._irq(blekbd._IRQ_GATTC_CHARACTERISTIC_RESULT,
                  (3, 2, 3, blekbd._FLAG_NOTIFY, FakeUUID(blekbd._REPORT)))
    keyboard._irq(blekbd._IRQ_GATTC_CHARACTERISTIC_DONE, (3, 0))
    keyboard._irq(blekbd._IRQ_GATTC_DESCRIPTOR_RESULT,
                  (3, 4, FakeUUID(blekbd._CCCD)))
    keyboard._irq(blekbd._IRQ_GATTC_DESCRIPTOR_DONE, (3, 0))
    assert ("write", 3, 4, b"\x01\x00", 1) in radio.calls
    keyboard._irq(blekbd._IRQ_GATTC_WRITE_DONE, (3, 4, 0))
    assert keyboard.state == "ready"

    # Left Shift + A report -> uppercase A and held left game alias.
    keyboard._irq(blekbd._IRQ_GATTC_NOTIFY,
                  (3, 3, b"\x02\x00\x04\x00\x00\x00\x00\x00"))
    keyboard.poll()
    inp.begin_frame()
    assert inp.last_key == ord("A")
    assert inp.held("left") and inp.pressed("left")

    keyboard._irq(blekbd._IRQ_GATTC_NOTIFY,
                  (3, 3, b"\x00\x00\x00\x00\x00\x00\x00\x00"))
    keyboard.poll()
    inp.begin_frame()
    assert inp.last_key == 0
    assert inp.released("left")


def test_native_queue_is_drained_without_synchronous_python_ble_irq():
    inp = InputState()
    keyboard = blekbd.BleHidKeyboard(inp, store_path=None, auto_start=False)
    fast = FakeFastQueue()
    keyboard._fast = fast
    keyboard._conn = 6
    keyboard._input_handles = {11}

    assert keyboard._enable_fastpath()
    assert fast.configured == (6, (11,))
    # Complete W make+break arrived on the NimBLE task while Python was drawing.
    fast.events.extend([
        (11, b"\x00\x00\x1a\x00\x00\x00\x00\x00", 700),
        (11, b"\x00\x00\x00\x00\x00\x00\x00\x00", 450),
    ])
    keyboard.poll()
    inp.begin_frame()
    assert inp.held("up") and inp.pressed("up")
    assert keyboard.fast_status() == (2, 0, 0, 2, True)

    keyboard.poll()
    inp.begin_frame()
    assert not inp.held("up") and inp.released("up")
    keyboard._reset_connection()
    assert fast.disabled


def test_security_gated_cccd_is_retried_after_encryption(monkeypatch):
    fake_module = types.SimpleNamespace(UUID=FakeUUID, BLE=FakeBLE)
    monkeypatch.setitem(sys.modules, "bluetooth", fake_module)
    radio = FakeBLE()
    keyboard = blekbd.BleHidKeyboard(InputState(), ble=radio, store_path=None)
    keyboard._conn = 9
    keyboard._subscribe_all = [44]
    keyboard._subscribe_queue = []
    keyboard._write_pending = 44

    keyboard._irq(blekbd._IRQ_GATTC_WRITE_DONE, (9, 44, 5))
    assert keyboard.state == "subscribe-retry"
    assert not any(call[:3] == ("write", 9, 44) for call in radio.calls)

    keyboard._irq(blekbd._IRQ_ENCRYPTION_UPDATE, (9, True, False, True, 16))
    assert ("write", 9, 44, b"\x01\x00", 1) in radio.calls


def test_boot_keyboard_is_selected_and_protocol_mode_is_written(monkeypatch):
    fake_module = types.SimpleNamespace(UUID=FakeUUID, BLE=FakeBLE)
    monkeypatch.setitem(sys.modules, "bluetooth", fake_module)
    radio = FakeBLE()
    keyboard = blekbd.BleHidKeyboard(InputState(), ble=radio, store_path=None)
    keyboard._conn = 4
    keyboard._hid_range = (1, 12)
    keyboard._chars = [
        (2, 3, 0x04, FakeUUID(blekbd._PROTOCOL_MODE)),
        (4, 5, blekbd._FLAG_NOTIFY, FakeUUID(blekbd._BOOT_KEYBOARD_INPUT)),
        (7, 8, blekbd._FLAG_NOTIFY, FakeUUID(blekbd._REPORT)),
    ]
    keyboard._descriptors = [
        (6, FakeUUID(blekbd._CCCD)),
        (9, FakeUUID(blekbd._CCCD)),
    ]

    keyboard._prepare_subscriptions()

    assert keyboard.protocol == "boot"
    assert keyboard._input_handles == {5}
    assert ("write", 4, 3, b"\x00", 0) in radio.calls
    assert ("write", 4, 6, b"\x01\x00", 1) in radio.calls
    assert not any(call[:3] == ("write", 4, 9) for call in radio.calls)


def test_picker_lists_all_hid_devices_and_connects_only_the_picked_one(monkeypatch):
    fake_module = types.SimpleNamespace(UUID=FakeUUID, BLE=FakeBLE)
    monkeypatch.setitem(sys.modules, "bluetooth", fake_module)
    radio = FakeBLE()
    keyboard = blekbd.BleHidKeyboard(InputState(), ble=radio, store_path=None)

    # Replace the boot auto-scan with the user-facing full picker scan.
    assert keyboard.discover_devices()
    keyboard._irq(blekbd._IRQ_SCAN_DONE, None)
    assert keyboard.state == "scanning"
    addr_a = b"\x01\x02\x03\x04\x05\x06"
    addr_b = b"\x11\x12\x13\x14\x15\x16"
    hid = bytes((3, 0x03, 0x12, 0x18))
    keyboard._irq(blekbd._IRQ_SCAN_RESULT, (0, addr_a, 0, -35, hid))
    keyboard._irq(blekbd._IRQ_SCAN_RESULT, (1, addr_b, 0, -48, hid))
    keyboard._irq(blekbd._IRQ_SCAN_DONE, None)
    rows = keyboard.settings_devices()
    assert keyboard.state == "choose"
    assert [row[0] for row in rows] == [(0, addr_a), (1, addr_b)]

    assert keyboard.connect_device(rows[1][0])
    assert ("connect", 1, addr_b) in radio.calls
    assert not any(call == ("connect", 0, addr_a) for call in radio.calls)
    assert keyboard.settings_status()[3] == (1, addr_b)


def test_saved_enabled_preferred_address_and_bond_round_trip(tmp_path):
    store = tmp_path / "ble_keyboard.json"
    keyboard = blekbd.BleHidKeyboard(
        InputState(), store_path=str(store), auto_start=False)
    keyboard.name = "Pocket Keys"
    keyboard._preferred = (1, b"\xaa\xbb\xcc\xdd\xee\xff")
    keyboard._secrets[(2, b"peer")] = b"bond-key"
    keyboard._enabled = False
    keyboard._store_dirty = True
    keyboard.poll()

    raw = json.loads(store.read_text())
    assert raw["version"] == 2
    assert raw["enabled"] is False
    assert raw["preferred"] == [1, "aabbccddeeff"]

    restored = blekbd.BleHidKeyboard(
        InputState(), store_path=str(store), auto_start=False)
    assert restored.settings_status()[:4] == (
        False, "off", "Pocket Keys", (1, b"\xaa\xbb\xcc\xdd\xee\xff"))
    assert restored._secrets[(2, b"peer")] == b"bond-key"
    rows = restored.settings_devices()
    assert rows[0][:4] == ((1, b"\xaa\xbb\xcc\xdd\xee\xff"),
                           "Pocket Keys", -127, True)


def test_version_one_bond_store_migrates_without_forgetting_keys(tmp_path):
    store = tmp_path / "ble_keyboard.json"
    store.write_text(json.dumps({
        "version": 1,
        "name": "Old Bond",
        "secrets": [[3, "70656572", "736563726574"]],
    }))
    keyboard = blekbd.BleHidKeyboard(
        InputState(), store_path=str(store), auto_start=False)
    assert keyboard.settings_status()[:4] == (True, "off", "Old Bond", None)
    assert keyboard._secrets[(3, b"peer")] == b"secret"


def test_disable_disconnects_without_forgetting_and_enable_rescans(monkeypatch):
    fake_module = types.SimpleNamespace(UUID=FakeUUID, BLE=FakeBLE)
    monkeypatch.setitem(sys.modules, "bluetooth", fake_module)
    radio = FakeBLE()
    keyboard = blekbd.BleHidKeyboard(InputState(), ble=radio, store_path=None)
    keyboard._conn = 7
    keyboard._candidate = (0, b"\x01\x02\x03\x04\x05\x06")
    keyboard._preferred = keyboard._candidate
    keyboard._secrets[(1, b"peer")] = b"bond"

    assert keyboard.set_enabled(False) is False
    assert ("disconnect", 7) in radio.calls
    assert keyboard.settings_status()[0:2] == (False, "disabled")
    assert keyboard._preferred is not None and keyboard._secrets
    keyboard._irq(blekbd._IRQ_PERIPHERAL_DISCONNECT,
                  (7, 0, b"\x01\x02\x03\x04\x05\x06"))

    assert keyboard.set_enabled(True) is True
    assert keyboard.settings_status()[0] is True
    assert keyboard.state == "scanning"


def test_p4_board_enables_hosted_ble_and_runtime_polls_before_edge_snapshot():
    driver = SOURCE.read_text()
    board_cmake = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "boards"
                   / "MOYBYTE_P4" / "mpconfigboard.cmake").read_text()
    board_sdkconfig = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "boards"
                       / "MOYBYTE_P4" / "sdkconfig.board").read_text()
    build_script = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b"
                    / "build.sh").read_text()
    native_cmake = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "native"
                    / "micropython.cmake").read_text()
    native_queue = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "native"
                    / "moy_ble_hid" / "modmoy_ble_hid.c").read_text()
    bt_patch = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "patches"
                / "modbluetooth_ble_hid_fastpath.patch").read_text()
    runtime = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / "modules"
               / "moy_runtime.py").read_text()
    sdkconfig = (ROOT / "firmware" / "esp32_p4_wifi6_touch_lcd_7b" / ".build"
                 / "micropython" / "ports" / "esp32" / "boards"
                 / "sdkconfig.p4_wifi_common")

    assert "MICROPY_PY_BLUETOOTH=1" in board_cmake
    assert "MICROPY_HW_MOYBYTE_P4_BLE_HID_QUEUE=1" in board_cmake
    assert "CONFIG_BT_NIMBLE_TRANSPORT_ACL_FROM_LL_COUNT=64" in board_sdkconfig
    assert "CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=12288" in board_sdkconfig
    assert "CONFIG_BT_NIMBLE_TRANSPORT_ACL_FROM_LL_COUNT=64" in build_script
    assert "CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=12288" in build_script
    assert "moy_ble_hid/micropython.cmake" in native_cmake
    assert "moy_ble_hid_queue_on_notify" in native_queue
    assert "moy_ble_hid_queue_on_notify" in bt_patch
    assert "modbluetooth_ble_hid_fastpath.patch" in build_script
    # BLE IRQs execute on NimBLE's core-0 task. Serial output must be deferred
    # to poll() on the main task; print() here caused hardware stack panics.
    assert "print(" not in driver[driver.index("    def _irq("):]
    # The upstream fragment may not exist in a clean checkout, so the durable
    # contract is the selected C6_WIFI variant + Bluetooth module define. If the
    # checkout is present, pin the hosted VHCI setting too.
    assert "boards/sdkconfig.p4_wifi_c6" in board_cmake
    if sdkconfig.exists():
        text = sdkconfig.read_text()
        assert "CONFIG_ESP_HOSTED_NIMBLE_HCI_VHCI=y" in text

    assert "from p4_ble_keyboard import BleHidKeyboard" in runtime
    assert "ws.keyboard = keyboard" in runtime
    assert runtime.index("keyboard.poll()") < runtime.index("inp.begin_frame()")
