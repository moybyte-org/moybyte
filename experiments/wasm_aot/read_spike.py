"""Read SPIKE lines off the P4 serial console and convert them to rates.

Compares against the numbers already measured on this same board under Lua
(moy_lua) and on the host.
"""
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
NES_CYCLES_PER_FRAME = 1789773.0 / 60.0988

# already measured, same core, same board / same host
P4_LUA_IPS = 173_000.0
HOST_LUA_IPS = 7_920_000.0
HOST_WASM_IPS = 21_300_000.0


def main():
    ser = serial.Serial(PORT, 115200, timeout=1)
    ser.setDTR(False)
    ser.setRTS(True)
    time.sleep(0.1)
    ser.setRTS(False)          # pulse reset so we catch the boot output
    deadline = time.time() + 90
    best = 0.0
    seen = []
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        if "SPIKE" not in line:
            continue
        print("  |", line)
        seen.append(line)
        if line.startswith("SPIKE step"):
            parts = dict(p.split("=") for p in line.split()[2:] if "=" in p)
            n = float(parts["n"])
            us = float(parts["us"])
            ips = n / (us / 1e6)
            best = max(best, ips)
            print("     -> %.3f M instr/s | %.2f M cyc/s | %.1f fps (CPU only)"
                  % (ips / 1e6, ips * 3.0 / 1e6,
                     ips * 3.0 / NES_CYCLES_PER_FRAME))
        if line.startswith("SPIKE spin"):
            parts = dict(p.split("=") for p in line.split()[2:] if "=" in p)
            ips2 = float(parts["n"]) / (float(parts["us"]) / 1e6)
            print("     -> %.2f M iter/s" % (ips2 / 1e6))
        if "SPIKE done" in line or "SPIKE ERR" in line:
            break
    ser.close()

    if best:
        print("\n== P4 wasm (WAMR fast-interp) ==")
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
