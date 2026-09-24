"""Attach to the T-Deck (DTR/RTS asserted, never pulsed -- board.toml
[serial]) right after a flash and print the Doom spike's lines for a while.

    read_doom.py /dev/ttyACM0 [seconds]
"""
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 60


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
