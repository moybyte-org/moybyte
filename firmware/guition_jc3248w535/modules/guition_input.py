"""The Guition S3's input: the AXS15231 touch as the pointer and a BLE HID
keyboard on the S3's own radio, the P4's shape (touch-only, no input task, no
keyboard modes). `device/wire_input.py` constructs the shared half; what is
this board's is its touch (the kernel's driver).
"""

import wire_input

# The bond store is device identity: the internal VFS, never the card.

def make_input():
    """(inp, keyboard): the merged InputState and the BLE HID keyboard. The
    spine starts the keyboard's radio after the Workstation's boot
    allocations. A paired keyboard's hold-BACKSPACE is also this board's
    game-exit path, through the shared console unmodified."""
    return wire_input.ble_keyboard_input()


def make_touch(w, h):
    """The AXS15231's touch (the kernel's driver), mapped onto a w x h system
    canvas."""
    import moy_input
    return moy_input.touch(w, h)
