"""The BLE HID keyboard central (native/moy_input/moy_hid.c), EXECUTED: the
report decode and keymap, the level state with its one-frame make edges, and
the scan / connect / pair / discover / subscribe machine, over a fake stack
through the host's binding (runtime/moy_input.py's HidMachine). A board runs
the same machine on NimBLE's host task (moy_ble_task.c); its wiring is pinned
at the end, and the radio itself on glass."""

import re
from pathlib import Path

from runtime import moy_input as mi
from tools import board_config

ROOT = Path(__file__).resolve().parents[1]
ADDR = (0, b"\x01\x02\x03\x04\x05\x06")
HID_ADV = bytes((3, 0x03, 0x12, 0x18))


class FakeStack:
    """NimBLE as the machine sees it: every request recorded, every one
    accepted, a clock the test moves."""

    def __init__(self):
        self.calls = []
        self.t = 1000
        self.saved = []

    def _rec(self, *call):
        self.calls.append(call)
        return 0

    def scan(self, picker):
        return self._rec("scan", picker)

    def scan_stop(self):
        return self._rec("scan_stop")

    def connect(self, addr):
        return self._rec("connect", addr)

    def disconnect(self, conn):
        return self._rec("disconnect", conn)

    def pair(self, conn):
        return self._rec("pair", conn)

    def disc_svcs(self, conn):
        return self._rec("services", conn)

    def disc_chrs(self, conn, start, end):
        return self._rec("chars", conn, start, end)

    def disc_dscs(self, conn, start, end):
        return self._rec("descriptors", conn, start, end)

    def write(self, conn, handle, data, response):
        return self._rec("write", conn, handle, data, response)

    def forget_bonds(self):
        return self._rec("forget")

    def save(self, machine):
        self.saved.append((machine.enabled, machine.preferred, machine.name))

    def ms(self):
        return self.t


def _machine(**kw):
    table = mi.InputTable()
    stack = FakeStack()
    m = mi.HidMachine(table, stack, **kw)
    return table, stack, m


def _ready(m, conn=3, handle=3):
    """Through scan, connect, discovery and the one subscription to ready,
    with a Report-only keyboard whose input report is `handle`."""
    m.started(True)
    m.scan_result(ADDR, -40, HID_ADV)
    m.on_scan_done()
    m.connected(conn, ADDR)
    m.on_svc(conn, 1, 12, mi.HID_SERVICE)
    m.on_svc_done(conn, 0)
    m.on_chr(conn, 2, handle, mi.HID_NOTIFY, mi.HID_REPORT)
    m.on_chr_done(conn, 0)
    m.on_dsc(conn, handle + 1, mi.HID_CCCD)
    m.on_dsc_done(conn, 0)
    m.on_write_done(conn, handle + 1, 0)
    assert m.state == "ready"


def _frame(m, table):
    m.frame()
    table.begin_frame()


# -- the pure parts --------------------------------------------------------------

def test_advertisement_recognises_hid_service_and_name():
    payload = bytes((2, 0x01, 0x06, 3, 0x03, 0x12, 0x18, 9, 0x09)) + b"Air Mini"
    assert mi.adv_has_hid(payload)
    assert mi.adv_name(payload) == "Air Mini"
    assert not mi.adv_has_hid(bytes((3, 0x03, 0x0F, 0x18)))
    assert mi.adv_has_hid(bytes((5, 0x16, 0x12, 0x18, 0, 0)))       # service data


def test_boot_report_and_ascii_mapping_cover_typing_shortcuts_and_symbols():
    assert mi.decode_report(b"\x02\x00\x04\x00\x00\x00\x00\x00") == (0x02, (0x04,))
    assert mi.decode_report(b"\x07\x02\x00\x04\x00\x00\x00\x00\x00") == (0x02, (0x04,))
    assert mi.decode_report(b"\x00\x01\x04\x00\x00\x00\x00\x00") is None
    assert mi.decode_report(b"\x00\x00\x01\x04\x04\x00\x00\x00") == (0, (0x04,))

    assert mi.usage_to_keycode(0x04) == ord("a")
    assert mi.usage_to_keycode(0x04, 0x02) == ord("A")
    assert mi.usage_to_keycode(0x04, 0, True) == ord("A")
    assert mi.usage_to_keycode(0x04, 0x02, True) == ord("a")
    assert mi.usage_to_keycode(0x1D, 0x01) == 0x1A              # Ctrl+Z
    assert mi.usage_to_keycode(0x1E, 0x02) == ord("!")
    assert mi.usage_to_keycode(0x2F, 0x02) == ord("{")
    assert mi.usage_to_keycode(0x28) == 0x0D
    assert mi.usage_to_keycode(0x2A) == 0x08


def test_the_arrow_host_scheme():
    """Arrows + Z/X, PICO-8's and every emulator's (owner call 2026-08-14);
    the T-Deck's L/K scheme is pinned in tests/test_tdeck_keymap.py."""
    assert mi.hid_buttons_for_key(ord("z")) == ("a",)
    assert mi.hid_buttons_for_key(ord(" ")) == ("a",)
    assert mi.hid_buttons_for_key(ord("x")) == ("b",)
    assert mi.hid_buttons_for_key(ord("W")) == ("up",)
    assert mi.hid_buttons_for_key(0x0D) == ("run",)
    assert mi.hid_buttons_for_key(0x08) == ("home",)
    assert mi.hid_buttons_for_key(ord("l")) == ()


# -- the level state ---------------------------------------------------------------

def test_report_level_state_gives_real_hold_edges_and_text_mode_suppression():
    table, _stack, m = _machine()
    _ready(m)
    m.notify(3, 3, b"\x00\x00\x1a\x00\x00\x00\x00\x00")      # W make
    _frame(m, table)
    assert table.last_key == ord("w")
    assert table.held("up") and table.pressed("up")
    _frame(m, table)
    assert table.held("up") and not table.pressed("up")
    m.notify(3, 3, bytes(8))                                  # W break
    _frame(m, table)
    assert table.last_key == 0 and table.released("up")
    # Text mode types W and fires no game alias; the arrows stay directional.
    table.text_mode = True
    m.notify(3, 3, b"\x00\x00\x1a\x00\x00\x00\x00\x00")
    _frame(m, table)
    assert table.last_key == ord("w") and not table.held("up")
    m.notify(3, 3, b"\x00\x00\x50\x00\x00\x00\x00\x00")
    _frame(m, table)
    assert table.last_key == 0 and table.held("left")


def test_make_and_break_between_frames_preserves_one_press_then_release():
    table, _stack, m = _machine()
    _ready(m)
    m.notify(3, 3, b"\x00\x00\x1a\x00\x00\x00\x00\x00")
    m.notify(3, 3, bytes(8))                    # the whole tap before a frame
    _frame(m, table)
    assert table.held("up") and table.pressed("up")
    _frame(m, table)
    assert not table.held("up") and table.released("up")


def test_an_idle_keyboard_writes_its_source_once_then_rests():
    """#220: with nothing held and nothing arriving, a frame writes nothing --
    and a key after any number of resting frames still lands, and so does its
    release."""
    table, _stack, m = _machine()
    _ready(m)
    for _ in range(5):
        _frame(m, table)
    assert not table.held("up")
    m.notify(3, 3, b"\x00\x00\x1a\x00\x00\x00\x00\x00")
    _frame(m, table)
    assert table.held("up") and table.pressed("up")
    m.notify(3, 3, bytes(8))
    _frame(m, table)
    assert not table.held("up") and table.last_key == 0


def test_caps_lock_toggles_on_its_make():
    table, _stack, m = _machine()
    _ready(m)
    m.notify(3, 3, b"\x00\x00\x39\x00\x00\x00\x00\x00")       # Caps make
    m.notify(3, 3, bytes(8))
    m.notify(3, 3, b"\x00\x00\x04\x00\x00\x00\x00\x00")       # a
    _frame(m, table)
    assert m.caps
    _frame(m, table)
    assert table.last_key == ord("A")


def test_a_report_on_another_handle_is_not_input():
    table, _stack, m = _machine()
    _ready(m)
    m.notify(3, 9, b"\x00\x00\x1a\x00\x00\x00\x00\x00")
    _frame(m, table)
    assert not table.held("up") and m.notify_count == 0


# -- the machine -------------------------------------------------------------------

def test_the_machine_scans_pairs_discovers_subscribes_and_feeds_input():
    table, stack, m = _machine()
    m.started(True)
    assert m.state == "scanning" and ("scan", False) in stack.calls
    m.scan_result(ADDR, -40, HID_ADV)
    assert m.state == "found" and ("scan_stop",) in stack.calls
    m.on_scan_done()
    assert ("connect", ADDR) in stack.calls
    m.connected(3, ADDR)
    assert ("pair", 3) in stack.calls and ("services", 3) in stack.calls
    m.on_svc(3, 1, 12, mi.HID_SERVICE)
    m.on_svc_done(3, 0)
    assert ("chars", 3, 1, 12) in stack.calls
    m.on_chr(3, 2, 3, mi.HID_NOTIFY, mi.HID_REPORT)
    m.on_chr_done(3, 0)
    m.on_dsc(3, 4, mi.HID_CCCD)
    m.on_dsc_done(3, 0)
    assert ("write", 3, 4, b"\x01\x00", True) in stack.calls
    m.on_write_done(3, 4, 0)
    assert m.state == "ready" and m.protocol == "report"
    # Left Shift + A -> upper-case A and the held `left` alias.
    m.notify(3, 3, b"\x02\x00\x04\x00\x00\x00\x00\x00")
    _frame(m, table)
    assert table.last_key == ord("A") and table.held("left") and table.pressed("left")
    m.notify(3, 3, bytes(8))
    _frame(m, table)
    assert table.last_key == 0 and table.released("left")
    m.tick()
    assert stack.saved[-1] == (True, ADDR, "BLE keyboard")


def test_boot_keyboard_is_selected_and_protocol_mode_is_written():
    _table, stack, m = _machine()
    m.started(True)
    m.scan_result(ADDR, -40, HID_ADV)
    m.on_scan_done()
    m.connected(4, ADDR)
    m.on_svc(4, 1, 12, mi.HID_SERVICE)
    m.on_svc_done(4, 0)
    m.on_chr(4, 2, 3, 0x04, mi.HID_PROTOCOL)
    m.on_chr(4, 4, 5, mi.HID_NOTIFY, mi.HID_BOOT_KBD)
    m.on_chr(4, 7, 8, mi.HID_NOTIFY, mi.HID_REPORT)
    m.on_chr_done(4, 0)
    m.on_dsc(4, 6, mi.HID_CCCD)
    m.on_dsc(4, 9, mi.HID_CCCD)
    m.on_dsc_done(4, 0)
    assert m.protocol == "boot"
    assert ("write", 4, 3, b"\x00", False) in stack.calls
    assert ("write", 4, 6, b"\x01\x00", True) in stack.calls
    assert not any(c[:3] == ("write", 4, 9) for c in stack.calls)


def test_security_gated_cccd_is_retried_after_encryption():
    _table, stack, m = _machine()
    m.started(True)
    m.scan_result(ADDR, -40, HID_ADV)
    m.on_scan_done()
    m.connected(9, ADDR)
    m.on_svc(9, 1, 50, mi.HID_SERVICE)
    m.on_svc_done(9, 0)
    m.on_chr(9, 42, 43, mi.HID_NOTIFY, mi.HID_REPORT)
    m.on_chr_done(9, 0)
    m.on_dsc(9, 44, mi.HID_CCCD)
    m.on_dsc_done(9, 0)
    stack.calls.clear()
    m.on_write_done(9, 44, 5)                   # rejected before encryption
    assert m.state == "subscribe-retry"
    assert not any(c[:3] == ("write", 9, 44) for c in stack.calls)
    m.on_enc_change(9, True, True)
    assert ("write", 9, 44, b"\x01\x00", True) in stack.calls
    m.on_write_done(9, 44, 0)
    assert m.state == "ready"


def test_picker_lists_all_hid_devices_and_connects_only_the_picked_one():
    _table, stack, m = _machine()
    m.started(True)
    m.discover()
    m.on_scan_done()                            # the boot scan ends, the picker starts
    assert m.state == "scanning" and ("scan", True) in stack.calls
    addr_b = (1, b"\x11\x12\x13\x14\x15\x16")
    m.scan_result(ADDR, -35, HID_ADV)
    m.scan_result(addr_b, -48, HID_ADV)
    m.on_scan_done()
    assert m.state == "choose"
    assert [d[0] for d in m.devices()] == [ADDR, addr_b]
    assert m.pick(addr_b)
    assert ("connect", addr_b) in stack.calls and ("connect", ADDR) not in stack.calls
    assert m.preferred == addr_b


def test_a_picked_keyboard_is_the_only_one_reconnected():
    _table, stack, m = _machine(preferred=(1, b"\x11\x12\x13\x14\x15\x16"), name="Air")
    m.started(True)
    m.scan_result(ADDR, -30, HID_ADV)           # another keyboard, closer
    assert m.state == "scanning"
    m.scan_result((1, b"\x11\x12\x13\x14\x15\x16"), -60, HID_ADV)
    assert m.state == "found"


def test_disable_disconnects_without_forgetting_and_enable_rescans():
    _table, stack, m = _machine()
    _ready(m, conn=7)
    m.set_enabled(False)
    assert ("disconnect", 7) in stack.calls
    assert (m.enabled, m.state) == (False, "disabled")
    assert m.preferred == ADDR and ("forget",) not in stack.calls
    m.on_disconnect(7)
    m.set_enabled(True)
    assert m.enabled and m.state == "scanning"


def test_forget_drops_the_keyboard_and_every_bond():
    _table, stack, m = _machine()
    _ready(m, conn=7)
    m.forget()
    assert ("forget",) in stack.calls and ("disconnect", 7) in stack.calls
    assert m.preferred is None and m.devices() == [] and m.state == "choose"


def test_timeouts_retry_and_a_stalled_discovery_disconnects():
    _table, stack, m = _machine()
    m.started(True)
    m.on_scan_done()                            # nothing found
    assert m.state == "idle"
    stack.t += 5000
    m.tick()
    assert m.state == "scanning"
    m.scan_result(ADDR, -40, HID_ADV)
    m.on_scan_done()
    m.connected(3, ADDR)
    stack.t += 15001
    m.tick()
    assert ("disconnect", 3) in stack.calls


def test_the_player_slot_is_held_only_while_connected():
    table, _stack, m = _machine()
    m.set_player(1)
    m.started(True)
    m.frame()
    assert m.src.player == 0
    m.scan_result(ADDR, -40, HID_ADV)
    m.on_scan_done()
    m.connected(3, ADDR)
    m.on_svc(3, 1, 12, mi.HID_SERVICE)
    m.on_svc_done(3, 0)
    m.on_chr(3, 2, 3, mi.HID_NOTIFY, mi.HID_REPORT)
    m.on_chr_done(3, 0)
    m.on_dsc(3, 4, mi.HID_CCCD)
    m.on_dsc_done(3, 0)
    m.on_write_done(3, 4, 0)
    m.frame()
    assert m.src.player == 1
    m.on_disconnect(3)
    m.frame()
    assert m.src.player == 0


def test_a_boot_mouse_is_subscribed_beside_the_keyboard_and_its_motion_taken():
    """#26 (owner 2026-10-07): a boot mouse is the second report shape over the
    same central -- buttons and deltas, taken by the frame for the pointer."""
    table, stack, m = _machine()
    m.started(True)
    m.scan_result(ADDR, -40, HID_ADV)
    m.on_scan_done()
    m.connected(5, ADDR)
    m.on_svc(5, 1, 20, mi.HID_SERVICE)
    m.on_svc_done(5, 0)
    m.on_chr(5, 2, 3, 0x04, mi.HID_PROTOCOL)
    m.on_chr(5, 4, 5, mi.HID_NOTIFY, mi.HID_BOOT_KBD)
    m.on_chr(5, 7, 8, mi.HID_NOTIFY, mi.HID_BOOT_MOUSE)
    m.on_chr_done(5, 0)
    m.on_dsc(5, 6, mi.HID_CCCD)
    m.on_dsc(5, 9, mi.HID_CCCD)
    m.on_dsc_done(5, 0)
    assert ("write", 5, 3, b"\x00", False) in stack.calls
    m.on_write_done(5, 6, 0)
    m.on_write_done(5, 9, 0)
    assert m.state == "ready"
    m.notify(5, 8, bytes((1, 5, 0xFE)))           # left down, +5, -2
    m.notify(5, 8, bytes((1, 3, 0x01)))
    assert m.take_mouse() == (8, -1, 1)
    assert m.take_mouse() == (0, 0, 1)            # taken once
    m.notify(5, 5, b"\x00\x00\x04\x00\x00\x00\x00\x00")
    _frame(m, table)
    assert table.last_key == ord("a")             # the keyboard beside it


def test_a_keyboard_with_no_mouse_has_no_mouse_to_take():
    _table, _stack, m = _machine()
    _ready(m)
    assert m.take_mouse() is None


# -- the boards' wiring --------------------------------------------------------------

def test_every_console_takes_the_kernels_central_and_no_bluetooth_module():
    for board in ("lilygo_t_deck_plus_mainline", "guition_jc3248w535",
                  "esp32_p4_wifi6_touch_lcd_7b", "guition_jc8012p4a1c"):
        d = ROOT / "firmware" / board
        header = next((d / "boards").glob("*/mpconfigboard.h")).read_text()
        cmake = next((d / "boards").glob("*/mpconfigboard.cmake")).read_text()
        sdk = next((d / "boards").glob("*/sdkconfig.board")).read_text()
        assert re.search(r"#define MOY_INPUT_BLE\s+\(1\)", header), board
        assert "MICROPY_PY_BLUETOOTH=1" not in cmake, board
        if "p4" in board:
            assert "MICROPY_PY_BLUETOOTH=0" in cmake, board
            assert re.search(r"#define MOY_INPUT_BLE_HOSTED\s+\(1\)", header), board
        else:
            assert re.search(r"#define MICROPY_PY_BLUETOOTH\s+\(0\)", header), board
        assert "CONFIG_BT_NIMBLE_NVS_PERSIST=y" in sdk, board
        required = [s.assignment for s in board_config.sdkconfig_required(d)]
        assert "CONFIG_BT_NIMBLE_NVS_PERSIST=y" in required, board


def test_the_p4_silicon_tier_carries_no_python_ble_fast_path():
    assert not (ROOT / "native" / "p4" / "moy_ble_hid").exists()
    assert not (ROOT / "patches" / "p4_modbluetooth_ble_hid_fastpath.patch").exists()
    lib = (ROOT / "tools" / "esp32_build_lib.sh").read_text()
    assert "ble_hid_fastpath" not in lib
    for board in ("esp32_p4_wifi6_touch_lcd_7b", "guition_jc8012p4a1c"):
        assert "ble_hid_fastpath" not in (ROOT / "firmware" / board / "build.sh").read_text()


def test_the_frame_half_polls_before_the_merge():
    """The central runs no Python (tests/test_no_vm_calls_in_drivers.py holds
    its sources); its frame half takes the reports into the source before the
    merge, or the table answers a frame late."""
    spine = (ROOT / "device" / "desktop_spine.py").read_text()
    body = spine[spine.index("    def poll_inputs("):spine.index("    def present(")]
    assert body.index("keyboard.poll()") < body.index("inp.begin_frame()")
