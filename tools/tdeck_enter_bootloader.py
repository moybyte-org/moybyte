#!/usr/bin/env python3
"""Put a RUNNING T-Deck into the ROM bootloader over serial -- no buttons.

The T-Deck's native-USB esptool auto-reset is unreliable (the reset dance is
firmware-mediated on TinyUSB, unlike the P4's CH343), so cable flashing always
needed the BOOT/RST finger dance. But the console firmware's Ctrl-C -> REPL
path works (glass-verified 2026-07-10; the takeover-starves-USB lore is dead),
and from the REPL `machine.bootloader()` enters the ROM downloader in software.
This script does that dance; the Makefile target then flashes with
`--before no_reset` because the chip is already sitting in the bootloader.

Usage: tdeck_enter_bootloader.py [PORT]        (default /dev/ttyACM0)
Exits 0 once the ROM is (very likely) up; the caller runs esptool next.
"""
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"


def main():
    sp = serial.Serial(PORT, 115200, timeout=0.3)
    time.sleep(0.2)
    # Interrupt whatever runs (the desktop loop, a cart, an existing REPL).
    # Two Ctrl-Cs with a settle: the first lands mid-frame, the second is
    # belt-and-braces; a quiet board just gets prompts.
    sp.write(b"\x03")
    time.sleep(0.6)
    sp.write(b"\x03")
    time.sleep(0.6)
    sp.reset_input_buffer()
    sp.write(b"\r\n")
    time.sleep(0.4)
    banner = sp.read(4096).decode("utf-8", "replace")
    if ">>>" not in banner:
        print("tdeck_enter_bootloader: no REPL prompt (is the console build "
              "running? tail: %r)" % banner[-120:])
        sp.close()
        return 1
    sp.write(b"import machine; machine.bootloader()\r\n")
    sp.flush()
    time.sleep(0.3)
    sp.close()
    # The port re-enumerates as the ROM's USB-JTAG/serial unit; give it a beat.
    time.sleep(2.0)
    print("tdeck_enter_bootloader: ROM downloader requested on %s" % PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
