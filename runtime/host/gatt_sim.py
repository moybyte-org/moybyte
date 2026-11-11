"""The host's stand-in Bluetooth LE (docs/os_2026-11/hardware.md section 1):
the `ble` driver's stand-in ops (native/moy_input/moy_gatt.c) over
peripherals declared here, which answer a scan, a connection, a read, a write
and a subscription at once.

  install() / remove()          the console has BLE, or not
  device(addr, name, rssi=-60)  a peripheral on the air ("aa:bb:cc:dd:ee:ff")
  chr(addr, uuid, handle, value)
                                one of its characteristics (uuid an int for a
                                16-bit one, else the 128-bit string)
  written(addr, handle)         what the console last wrote to it
  notify(addr, handle, data)    it notifies: whether a subscriber heard
  advertised()                  what the console advertises"""

try:
    from . import moy_gatt as _N
except ImportError:  # loaded flat
    import moy_gatt as _N


def addr_bytes(s):
    """"aa:bb:cc:dd:ee:ff" as NimBLE holds it: little-endian."""
    return bytes(int(p, 16) for p in s.split(":"))[::-1]


def uuid_bytes(u):
    if isinstance(u, int):
        return bytes([u & 0xFF, u >> 8])
    return bytes.fromhex(u.replace("-", ""))[::-1]


def install():
    _N.sim(True)


def remove():
    _N.reset()


def device(addr, name, rssi=-60):
    _N.sim_device(addr_bytes(addr), rssi, name)


def chr(addr, uuid, handle, value):  # noqa: A001 -- GATT's word
    _N.sim_chr(addr_bytes(addr), uuid_bytes(uuid), handle, value)


def written(addr, handle):
    return _N.sim_written(addr_bytes(addr), handle)


def notify(addr, handle, data):
    return _N.sim_notify(addr_bytes(addr), handle, data)


def advertised():
    return _N.sim_adv()
