"""Attach to the T-Deck (DTR/RTS asserted, never pulsed -- board.toml
[serial]) right after a flash and print the Doom spike's lines for a while.

    read_doom.py /dev/ttyACM0 [seconds]
"""
import sys
import time

import serial

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT = ARGS[0] if ARGS else "/dev/ttyACM0"
SECS = float(ARGS[1]) if len(ARGS) > 1 else 60
PULSE = "--pulse" in sys.argv    # the P4's CH343: DTR low, pulse RTS to reset (board.toml [serial])


def main():
    ser = None
    for _ in range(50):
        try:
            ser = serial.Serial(PORT, 115200, timeout=1)
            break
        except serial.SerialException:
            time.sleep(0.2)
    if not ser:
        raise SystemExit("port never came back")
    if PULSE:
        ser.setDTR(False)
        ser.setRTS(True)
        time.sleep(0.1)
        ser.setRTS(False)
    deadline = time.time() + SECS
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            print("  |", line, flush=True)
        if "DOOM exit" in line or "DOOM ERR" in line:
            deadline = min(deadline, time.time() + 3)
    ser.close()


if __name__ == "__main__":
    main()
