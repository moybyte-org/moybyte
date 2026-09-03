#!/usr/bin/env python3
"""Which serial port is which board -- the answer `PORT is not set` points at.

The Makefile's PORT hint said "try: make device-port" and there was no such
target, so the one message a person sees at exactly the moment they do not know
the answer sent them somewhere that did not exist.

`p4_autotest.find_port` already does the hard part, and does it carefully: it
matches a board's declared `[serial] usb` id, PROBES to tell twins apart where
opening a port is side-effect free, and refuses to guess rather than hand back a
plausible wrong port. This is a listing around it.

THE ONE THING IT HAS TO SAY THAT find_port CANNOT. A board in the ROM loader
does not carry its running USB id -- the Zero runs as TinyUSB CDC `303a:4001`
and appears in the loader as `303a:1001` -- so `find_port` fails for that board
at precisely the moment you want to flash it. An unclaimed `303a:1001` port is
therefore reported as a likely loader, because that is the flashable state and a
person staring at "no serial port matches" would otherwise conclude the board
was not plugged in.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import p4_autotest as pa                                       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Board dir -> the make target that flashes it, so the output is something to
# paste rather than something to look up.
BOARDS = [
    ("firmware/lilygo_t_deck_plus_mainline", "firmware-flash-tdeck-mainline"),
    ("firmware/esp32_p4_wifi6_touch_lcd_7b", "firmware-flash-p4"),
    ("firmware/guition_jc3248w535", "firmware-flash-guition-s3"),
    ("firmware/seeed_xiao_esp32s3_zero", "firmware-flash-zero"),
]

# The S3 ROM loader's id. Shared with the console boards' running USB-Serial/JTAG,
# which is why an unclaimed one is a GUESS and is printed as such.
ROM_LOADER_USB = "303a:1001"


# A board's USB SERIAL number, when `board.toml` names one. The Zero needs this:
# since it took the USB-Serial/JTAG promotion it shares 303a:1001 with the two
# console boards, and `find_port` tells same-id boards apart by ASKING them --
# over the dev channel, which a headless board does not have. Its serial number
# is stable, printed by `udevadm`, and settles it without a round trip.
def _serial_of(port):
    try:
        out = subprocess.run(["udevadm", "info", "-q", "property", "-n", port],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:                       # noqa: BLE001 -- no udevadm, no hint
        return None
    for line in out.splitlines():
        if line.startswith("ID_SERIAL_SHORT="):
            return line.split("=", 1)[1].strip()
    return None


def _resolve(board_dir, ports):
    """`find_port`, with a serial-number answer for a board that declares one."""
    want = pa.declared_serial(board_dir).get("serial_number")
    if want:
        hits = [p for p in ports if _serial_of(p) == want]
        if len(hits) == 1:
            return hits[0]
        raise RuntimeError(
            "no port has usb serial %s (this board is identified by serial "
            "number, not by asking -- it has no dev channel)" % want)
    return pa.find_port(board_dir)


def main(argv):
    ports = pa.serial_ports()
    if not ports:
        print("no serial ports found -- is a board plugged in?")
        return 1

    print("ports:")
    for p in ports:
        print("  %-16s %s" % (p, pa.usb_id_of(p) or "?"))

    print("\nboards:")
    claimed = set()
    unresolved = []
    for rel, target in BOARDS:
        board_dir = os.path.join(ROOT, rel)
        name = os.path.basename(rel)
        try:
            port = _resolve(board_dir, ports)
        except Exception as exc:                # noqa: BLE001 -- the reason IS the answer
            unresolved.append((name, target, str(exc).split(" (saw:")[0]))
            continue
        claimed.add(port)
        print("  %-32s %s" % (name, port))
        print("  %-32s   make %s PORT=%s" % ("", target, port))

    for name, target, why in unresolved:
        print("  %-32s not found: %s" % (name, why))

    loose = [p for p in ports
             if p not in claimed and pa.usb_id_of(p) == ROM_LOADER_USB]
    if loose and unresolved:
        print("\n%s is also the S3 ROM LOADER's id, and %s %s unclaimed."
              % (ROM_LOADER_USB, ", ".join(loose),
                 "is" if len(loose) == 1 else "are"))
        print("A board held in the loader does not answer as itself, so if you "
              "just put one there\nto flash it, that is very likely the port -- "
              "pass it explicitly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
