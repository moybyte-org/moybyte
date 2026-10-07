"""The Guition S3's input: the AXS15231 touch as the pointer and a BLE HID
keyboard on the S3's own radio, the P4's shape (touch-only, no poller thread,
no keyboard modes). `device/wire_input.py` constructs the shared half; what is
this board's is its bond store and its touch driver.
"""

import wire_input

# The bond store is device identity: the internal VFS, never the card.
BLE_STORE = "/moy/ble_keyboard.json"


def make_input():
    """(inp, keyboard): the merged InputState and the BLE HID keyboard. The
    spine starts the keyboard's radio after the Workstation's boot
    allocations. A paired keyboard's hold-BACKSPACE is also this board's
    game-exit path, through the shared console unmodified."""
    return wire_input.ble_keyboard_input(BLE_STORE)


def make_touch(w, h):
    """The AXS15231's touch, mapped onto a w x h system canvas."""
    from axs_touch import Touch
    return Touch(w, h)
