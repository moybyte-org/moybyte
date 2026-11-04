"""The host's stand-in USB host (docs/os_2026-11/hardware.md section 1): the
C driver's stand-in (`moy_usb.sim`) with devices a test plugs in, so a test
sees what an app on a P4 would.

  install()                         the console has a USB host from now on
  plug(addr, cls, kind=0, vid=0, pid=0, name="")   a device enumerates
  unplug(addr)
  send(addr, data)                  the device sends: a HID report, serial
                                    bytes or USB-MIDI packets
  sent(addr) -> (bytes, baud)       what the console sent it, and its line
  remove()                          no USB host again"""

try:
    from . import moy_usb as _N
except ImportError:  # loaded flat
    import moy_usb as _N


def install():
    _N.sim(True)


def remove():
    _N.reset()
    _N.sim(False)


def plug(addr, cls, kind=0, vid=0, pid=0, name=""):
    return _N.sim_attach(addr, cls, kind, vid, pid, name)


def unplug(addr):
    _N.sim_detach(addr)


def send(addr, data):
    _N.sim_in(addr, data)


def sent(addr):
    return _N.sim_out(addr)
