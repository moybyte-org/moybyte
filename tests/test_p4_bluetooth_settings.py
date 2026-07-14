"""Capability-gated Bluetooth keyboard Settings panel (P4)."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class FakeBluetoothKeyboard:
    settings_capable = True

    def __init__(self):
        self.enabled = True
        self.state = "choose"
        self.name = "Pocket Keys"
        self.preferred = (0, b"\x01\x02\x03\x04\x05\x06")
        self.error = None
        self.rows = [
            (self.preferred, "Pocket Keys", -36, True, False),
            ((1, b"\x11\x12\x13\x14\x15\x16"), "Tiny Board", -51,
             False, False),
        ]
        self.calls = []

    def settings_status(self):
        return self.enabled, self.state, self.name, self.preferred, self.error

    def set_game_mode(self, _on):
        return

    def settings_devices(self):
        return tuple(self.rows)

    def set_enabled(self, on):
        self.enabled = bool(on)
        self.state = "idle" if self.enabled else "disabled"
        self.calls.append(("enabled", self.enabled))
        return self.enabled

    def discover_devices(self):
        self.state = "scanning"
        self.calls.append(("scan",))
        return True

    def connect_device(self, address):
        self.preferred = address
        self.name = next(row[1] for row in self.rows if row[0] == address)
        self.state = "connecting"
        self.calls.append(("connect", address))
        return True

    def forget(self):
        self.preferred = None
        self.name = None
        self.rows = []
        self.state = "choose"
        self.calls.append(("forget",))


def _ws(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"),
                                    sys_size=(1024, 600), font_scale=2,
                                    windowed=True)
    ws.keyboard = FakeBluetoothKeyboard()
    ws.open_settings()
    return ws


def test_bluetooth_row_is_capability_gated(tmp_path):
    from runtime import host_app
    ordinary = host_app.build_workstation(str(tmp_path / "ordinary"))
    assert "bluetooth" not in [row[0] for row in ordinary.settings_layer._settings_rows()]

    ws = _ws(tmp_path)
    rows = ws.settings_layer._settings_rows()
    keys = [row[0] for row in rows]
    assert keys[:3] == ["wifi", "bluetooth", "wallpaper"]
    assert rows[1][1:] == ("BLUETOOTH KEYBOARD", "bluetooth")


def test_panel_uses_draw_registered_device_and_semantic_action_hits(tmp_path):
    from runtime import host_app
    ws = _ws(tmp_path)
    drv = host_app.ConsoleDriver(ws)
    sl = ws.settings_layer
    sl.open_bluetooth()
    drv.frame(1 / 30)

    hits = [(verb, arg) for _rect, verb, arg in sl._bt_hits._items]
    assert [verb for verb, _arg in hits[:2]] == ["device", "device"]
    assert [verb for verb, _arg in hits[-5:]] == [
        "toggle", "connect", "scan", "forget", "back"]
    assert sl.bt_msg == "PICK A KEYBOARD"


def test_picker_actions_toggle_scan_select_save_and_forget(tmp_path):
    ws = _ws(tmp_path)
    sl = ws.settings_layer
    svc = ws.keyboard
    sl.open_bluetooth()

    sl._bt_action("toggle")
    assert svc.calls[-1] == ("enabled", False)
    sl._bt_action("toggle")
    assert svc.calls[-1] == ("enabled", True)
    sl._bt_action("scan")
    assert svc.calls[-1] == ("scan",)
    sl.bt_sel = 1
    sl._bt_action("connect")
    assert svc.calls[-1] == ("connect", svc.rows[1][0])
    assert svc.preferred == svc.rows[1][0]
    assert sl.bt_msg == "CONNECTING + SAVING..."
    sl._bt_action("forget")
    assert svc.calls[-1] == ("forget",)
    assert sl.bt_devices == []
