"""#158 spike, host half: run spike6502.lua under the SAME Lua 5.4 the console
pins -- runtime/lua_binding, the boards' vendored VM built for the host,
LUA_32BITS and all -- and report emulated 6502 instructions/sec.

Reference point for the device run -- and the number that decides whether the
host/Anbernic/browser tiers could carry a Lua emulator even if the ESP32s can't.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from runtime.lua_binding import HostLuaRun       # noqa: E402

SRC = open(os.path.join(HERE, "spike6502.lua")).read()

# NES: 1.789773 MHz, 60.0988 fps -> cycles per frame; our loop averages 3.0
# cycles/instruction (see the lua source header).
NES_CYCLES_PER_FRAME = 1789773.0 / 60.0988


def _call(lua, fn, n):
    """`fn(n)` in the VM, its number back through a global: the binding runs
    chunks and reads number globals, and that is all a benchmark needs."""
    err = lua.exec("__r = %s(%d)" % (fn, n), fn)
    if err:
        raise RuntimeError(err)
    return lua.get_global("__r")


def bench(lua, fn, n, warm=True):
    if warm:
        _call(lua, fn, n // 10 or 1)
    t0 = time.perf_counter()
    r = _call(lua, fn, n)
    return time.perf_counter() - t0, r


def main():
    lua = HostLuaRun(bytearray(8 * 8 * 2), 8, 8)
    err = lua.exec(SRC, "spike6502")
    if err:
        raise RuntimeError(err)

    n = 2_000_000
    dt, cyc = bench(lua, "step", n)
    ips = n / dt
    cps = cyc / dt
    print("host  Lua 5.4 (the boards' VM, runtime/lua_binding)")
    print("  6502: %.2fs for %d instr -> %.2f M instr/s, %.2f M cycles/s"
          % (dt, n, ips / 1e6, cps / 1e6))
    print("  emulated speed: %.2fx the NES's 1.79MHz CPU" % (cps / 1789773.0))
    print("  => CPU-only frame rate: %.1f fps" % (cps / NES_CYCLES_PER_FRAME))

    dtS, _ = bench(lua, "spin", 5_000_000)
    print("  spin: %.2f M iterations/s (raw VM reference)"
          % (5.0 / dtS))
    lua.close()


if __name__ == "__main__":
    main()
