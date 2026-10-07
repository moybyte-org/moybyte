"""Input's provider: the console's input sources and its pointer, constructed
in one place for every console board (docs/kernel_survival_2026-10.md section
2 item 1).

`build_desktop` (device/desktop_spine.py) calls `wire_pointer` behind the
splash and `start_keyboards` once the presentation tier is up; a board whose
keyboard is a BLE HID one (the P4 desk, the Guition S3) builds its input state
with `ble_keyboard_input`. What is a board's own hardware -- the T-Deck's
keyboard matrix, trackball and poller thread -- is its board module's
(`modules/tdeck_input.py`).
"""

from console import Pointer


def ble_keyboard_input(store_path):
    """(inp, keyboard): the merged InputState and a BLE HID keyboard writing
    into it, its bonds kept at `store_path`. The keyboard's radio is started
    by `start_keyboards`, after the Workstation's boot allocations."""
    from ble_keyboard import BleHidKeyboard
    from moybyte.input import InputState
    inp = InputState()
    return inp, BleHidKeyboard(inp, store_path=store_path, auto_start=False)


def wire_pointer(inp, inputs, w, h):
    """(touch, pointer): the board's pointer source, built by its `inputs()`,
    and the screen-space pointer it feeds, published on `inp` so a
    touch-driven cart reads it through the api's touch()."""
    touch = inputs()
    pointer = Pointer(w, h)
    inp.pointer = pointer
    return touch, pointer


def start_keyboards(ws, keyboard, ble_keyboard, _census):
    """The second keyboard attached, and the console's keyboard started."""
    if ble_keyboard is not None:
        # A second keyboard beside the physical one (#26). Both write into the
        # same InputState, so the console never asks which one a key came
        # from; Settings finds it because _bt_service() checks ws.ble_keyboard
        # before ws.keyboard.
        ws.ble_keyboard = ble_keyboard
    if keyboard is not None and getattr(keyboard, "start", None) is not None:
        # A BLE keyboard starts its radio only now, after the Workstation's
        # boot allocations; a keyboard that answers from __init__ has no
        # start(). Failure is touch-only, never a boot failure.
        keyboard.start()
        _census("keyboard")
