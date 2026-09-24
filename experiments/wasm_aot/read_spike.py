"""Read SPIKE lines off a board's serial console and convert them to rates.

    read_spike.py PORT            # P4 (CH343): pulse RTS to reset, read the boot
    read_spike.py PORT --attach   # S3 (USB-Serial/JTAG): attach with DTR/RTS
                                  # asserted and NEVER pulse -- board.toml
                                  # [serial] attach_only. Run it right after
                                  # esptool's hard reset; the app waits 3s.

Compares against the numbers already measured on this same board under Lua
(moy_lua) and on the host.
"""
import sys
import time

import serial

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT = ARGS[0] if ARGS else "/dev/ttyACM0"
ATTACH = "--attach" in sys.argv
ALL = "--all" in sys.argv          # print every line (panics, backtraces), not just SPIKE
NES_CYCLES_PER_FRAME = 1789773.0 / 60.0988

# already measured, same core, same board / same host
P4_LUA_IPS = 173_000.0
HOST_LUA_IPS = 7_920_000.0
HOST_WASM_IPS = 21_300_000.0


def open_port():
    if ATTACH:
        for _ in range(50):          # the node vanishes across a re-enumeration
            try:
                return serial.Serial(PORT, 115200, timeout=1, dsrdtr=False,
                                     rtscts=False)   # pyserial asserts DTR/RTS
            except serial.SerialException:
                time.sleep(0.2)
        raise SystemExit("port never came back: %s" % PORT)
    ser = serial.Serial(PORT, 115200, timeout=1)
    ser.setDTR(False)
    ser.setRTS(True)
    time.sleep(0.1)
    ser.setRTS(False)          # pulse reset so we catch the boot output
    return ser


def main():
    ser = open_port()
    deadline = time.time() + 90
    best = 0.0
    seen = []
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        if "SPIKE" not in line:
            if ALL:
                print("  :", line)
            continue
        print("  |", line)
        seen.append(line)
        if " step n=" in line:
            parts = dict(p.split("=") for p in line.split()[3:] if "=" in p)
            n = float(parts["n"])
            us = float(parts["us"])
            ips = n / (us / 1e6)
            best = max(best, ips)
            print("     -> %.3f M instr/s | %.2f M cyc/s | %.1f fps (CPU only)"
                  % (ips / 1e6, ips * 3.0 / 1e6,
                     ips * 3.0 / NES_CYCLES_PER_FRAME))
        if " spin n=" in line:
            parts = dict(p.split("=") for p in line.split()[3:] if "=" in p)
            ips2 = float(parts["n"]) / (float(parts["us"]) / 1e6)
            print("     -> %.2f M iter/s" % (ips2 / 1e6))
        if "SPIKE done" in line:
            break
    ser.close()

    if best:
        print("\n== best wasm rate seen (any mode) ==")
        print("  %.3f M instr/s" % (best / 1e6))
        print("  vs P4 Lua   %.3f M -> %.2fx" % (P4_LUA_IPS / 1e6, best / P4_LUA_IPS))
        print("  vs host wasm %.2f M -> host is %.1fx faster"
              % (HOST_WASM_IPS / 1e6, HOST_WASM_IPS / best))
        print("  NES CPU-only fps: %.1f (needs 60)"
              % (best * 3.0 / NES_CYCLES_PER_FRAME))
    elif not seen:
        print("no SPIKE lines seen -- wrong port, or the app did not boot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
