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
    frame_rows = None
    frame_tic = 0
    n_frames = 0
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").rstrip()
        if frame_rows is not None:
            if line.startswith("FRAMEEND"):
                save_frame(frame_rows, frame_tic, n_frames)
                n_frames += 1
                frame_rows = None
            elif line and all(c in "0123456789abcdef" for c in line):
                frame_rows.append(bytes.fromhex(line))
            continue
        if line.startswith("FRAME gametic="):
            frame_tic = int(line.split("=")[1])
            frame_rows = []
            continue
        if line:
            print("  |", line, flush=True)
        if "DOOM exit" in line or "DOOM ERR" in line:
            deadline = min(deadline, time.time() + 3)
    ser.close()


def save_frame(rows, tic, n):
    """200 rows of 320 palette indices + 4 rows of the BGRA palette -> PNG,
    beside a raw copy, under FRAMES_DIR (default /tmp/doomframes)."""
    import os
    import struct
    import zlib
    d = os.environ.get("FRAMES_DIR", "/tmp/doomframes")
    os.makedirs(d, exist_ok=True)
    if len(rows) != 204:
        print("  ! frame %d at gametic %d: %d rows" % (n, tic, len(rows)), flush=True)
        return
    frame = b"".join(rows[:200])
    pal = b"".join(rows[200:])
    out = []
    for y in range(200):
        row = bytearray([0])
        for x in range(320):
            c = frame[y * 320 + x]
            row += bytes((pal[c * 4 + 2], pal[c * 4 + 1], pal[c * 4]))
        out.append(bytes(row))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 320, 200, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(out))) + chunk(b"IEND", b""))
    path = "%s/frame_%02d_tic%d.png" % (d, n, tic)
    open(path, "wb").write(png)
    print("  * saved", path, flush=True)


if __name__ == "__main__":
    main()
