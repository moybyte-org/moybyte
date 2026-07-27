"""#158 spike, host half: run spike6502.lua under the SAME Lua 5.4 the console
pins (lupa.lua54) and report emulated 6502 instructions/sec.

Reference point for the device run -- and the number that decides whether the
host/Anbernic/browser tiers could carry a Lua emulator even if the ESP32s can't.
"""
import os
import time

from lupa import lua54

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "spike6502.lua")).read()

# NES: 1.789773 MHz, 60.0988 fps -> cycles per frame; our loop averages 3.0
# cycles/instruction (see the lua source header).
NES_CYCLES_PER_FRAME = 1789773.0 / 60.0988


def bench(fn, n, warm=True):
    if warm:
        fn(n // 10 or 1)
    t0 = time.perf_counter()
    r = fn(n)
    return time.perf_counter() - t0, r


def main():
    lua = lua54.LuaRuntime(register_eval=False, unpack_returned_tuples=True)
    lua.execute(SRC)
    g = lua.globals()

    n = 2_000_000
    dt, cyc = bench(g.step, n)
    ips = n / dt
    cps = cyc / dt
    print("host  Lua 5.4 (lupa %s)" % __import__("lupa").__version__)
    print("  6502: %.2fs for %d instr -> %.2f M instr/s, %.2f M cycles/s"
          % (dt, n, ips / 1e6, cps / 1e6))
    print("  emulated speed: %.2fx the NES's 1.79MHz CPU" % (cps / 1789773.0))
    print("  => CPU-only frame rate: %.1f fps" % (cps / NES_CYCLES_PER_FRAME))

    dtS, _ = bench(g.spin, 5_000_000)
    print("  spin: %.2f M iterations/s (raw VM reference)"
          % (5.0 / dtS))


if __name__ == "__main__":
    main()
