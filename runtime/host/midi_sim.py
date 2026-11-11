"""The host's stand-in MIDI wire (docs/os_2026-11/hardware.md section 1): the
bytes a leased UART would carry into a `midi` port, fed to the C driver the
way its board reader feeds them. USB-MIDI arrives through usb_sim's device
and BLE-MIDI through gatt_sim's notifications.

  wire(port, data)      MIDI bytes on a UART port's line"""

try:
    from . import moy_midi as _N
except ImportError:  # loaded flat
    import moy_midi as _N


def wire(port, data):
    _N.feed(port, data)


def remove():
    _N.reset()
