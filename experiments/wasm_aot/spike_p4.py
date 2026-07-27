"""#158 spike, device half: run spike6502.lua under the P4's `moy_lua` VM,
on glass, via the #156 serial harness. Reports emulated 6502 instructions/sec
and what that means for a Lua NES core's CPU-only frame rate.

    MOYBYTE_P4_PORT=/dev/ttyACM0 .venv/bin/python spike_p4.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from p4_autotest import P4Board  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LUA_SRC = open(os.path.join(HERE, "spike6502.lua")).read()

NES_CYCLES_PER_FRAME = 1789773.0 / 60.0988
CYCLES_PER_INSTR = 3.0          # measured exactly on host: 4611 cyc / 1537 instr

SETUP = """
import time as _t, moy_lua as _ml
_LUA = %r
_arr = bytearray(128)
_ml.init(None, None, _arr, 0x7A11)
_ml.exec(_LUA)
def _bench(n):
    t0 = _t.ticks_ms()
    _ml.call('step', n)
    return _t.ticks_diff(_t.ticks_ms(), t0)
def _spin(n):
    t0 = _t.ticks_ms()
    _ml.call('spin', n)
    return _t.ticks_diff(_t.ticks_ms(), t0)
ws._bench = _bench
ws._spin = _spin
ws._ml = _ml
"""


def main():
    port = os.environ.get("MOYBYTE_P4_PORT", "/dev/ttyACM0")
    board = P4Board(port, log=lambda s: print("  |", s.rstrip()))
    try:
        print("== resetting board ==")
        board.reset()
        print("== uploading lua core (%d bytes) ==" % len(LUA_SRC))
        t0 = time.time()
        ok = board.pyexec(SETUP % LUA_SRC, timeout=60.0)
        print("   upload %s in %.1fs" % ("ok" if ok else "FAILED", time.time() - t0))
        if not ok:
            return 1

        print("== 6502 core ==")
        best = 0.0
        for n in (20_000, 100_000, 400_000):
            ms = board.pyval("ws._bench(%d)" % n, timeout=120.0)
            if ms is None:
                print("   n=%-7d FAILED" % n)
                continue
            ips = n / (ms / 1000.0)
            best = max(best, ips)
            print("   n=%-7d %6d ms -> %.3f M instr/s | %.2f M cyc/s "
                  "| %.1fx NES | %.1f fps (CPU only)"
                  % (n, ms, ips / 1e6, ips * CYCLES_PER_INSTR / 1e6,
                     ips * CYCLES_PER_INSTR / 1789773.0,
                     ips * CYCLES_PER_INSTR / NES_CYCLES_PER_FRAME))

        print("== raw VM reference ==")
        for n in (200_000, 1_000_000):
            ms = board.pyval("ws._spin(%d)" % n, timeout=120.0)
            if ms is not None:
                print("   spin n=%-9d %6d ms -> %.2f M iter/s"
                      % (n, ms, (n / (ms / 1000.0)) / 1e6))

        mem = board.pyval("ws._ml.mem_kb()")
        peak = board.pyval("ws._ml.peak_kb()")
        print("== lua heap: live %s KB, peak %s KB ==" % (mem, peak))

        board.pyval("ws._ml.close() or 1")     # leave the board clean
        print("\n== summary ==")
        if best:
            print("P4 Lua 6502: %.3f M instr/s -> %.1f fps CPU-only "
                  "(NES needs 60)" % (best / 1e6,
                                      best * CYCLES_PER_INSTR / NES_CYCLES_PER_FRAME))
    finally:
        board.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
