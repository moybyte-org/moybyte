"""Run doom.wasm on the host under wasmtime with the same six imports the
board provides -- the reference run: if it traps here, the fault is the
module's; if it only traps on the board, the fault is the port's.

    toolchain/.venv/bin/python doom/doom_host.py [ticks] [--png frame.png]
"""
import struct
import sys
import time

from wasmtime import (Config, Engine, FuncType, Linker, Module, Store,
                      ValType, WasiConfig)

HERE = __file__.rsplit("/", 1)[0]
WAD = open(HERE + "/../doom1.wad", "rb").read()
TICKS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 200
PNG = sys.argv[sys.argv.index("--png") + 1] if "--png" in sys.argv else None
EVERY = int(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 0

cfg = Config()
cfg.wasm_backtrace_details = True
engine = Engine(cfg)
store = Store(engine)
wasi = WasiConfig()
wasi.inherit_stdout()
wasi.inherit_stderr()
store.set_wasi(wasi)
linker = Linker(engine)
linker.define_wasi()
module = Module.from_file(engine, HERE + "/doom.wasm")

state = {"mem": None, "t0": time.time(), "frames": 0, "keys": [], "last": None}


def mem():
    return state["mem"]


def ticks_ms():
    return int((time.time() - state["t0"]) * 1000) & 0xFFFFFFFF


def sleep_ms(ms):
    time.sleep(ms / 1000.0)


def get_key():
    return state["keys"].pop(0) if state["keys"] else 0


def draw(frame_off, pal_off):
    state["frames"] += 1
    m = mem()
    state["last"] = (bytes(m.read(store, frame_off, frame_off + 320 * 200)),
                     bytes(m.read(store, pal_off, pal_off + 1024)))


def wad_size():
    return len(WAD)


def wad_read(off, dst, n):
    chunk = WAD[off:off + n]
    mem().write(store, chunk, dst)
    return len(chunk)


i32 = ValType.i32()
linker.define_func("env", "moy_ticks_ms", FuncType([], [i32]), ticks_ms)
linker.define_func("env", "moy_sleep_ms", FuncType([i32], []), sleep_ms)
linker.define_func("env", "moy_get_key", FuncType([], [i32]), get_key)
linker.define_func("env", "moy_draw", FuncType([i32, i32], []), draw)
linker.define_func("env", "moy_wad_size", FuncType([], [i32]), wad_size)
linker.define_func("env", "moy_wad_read", FuncType([i32, i32, i32], [i32]), wad_read)

inst = linker.instantiate(store, module)
exports = inst.exports(store)
state["mem"] = exports["memory"]
exports["_initialize"](store)
t = time.time()
exports["dg_start"](store, 3)
print("== dg_start ok in %.2fs, memory %d pages" % (time.time() - t, state["mem"].size(store)))
t = time.time()


def dump(path):
    import zlib
    frame, pal = state["last"]
    rows = []
    for y in range(200):
        row = bytearray([0])
        for x in range(320):
            c = frame[y * 320 + x]
            b, g, r = pal[c * 4], pal[c * 4 + 1], pal[c * 4 + 2]
            row += bytes((r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 320, 200, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    open(path, "wb").write(png)
    print("== wrote", path, flush=True)


import zlib as _zlib
last_crc_tic = -1
for i in range(TICKS):
    exports["dg_tick"](store)
    if "dg_gametic" in exports and state["last"]:
        tic = exports["dg_gametic"](store)
        if tic % 500 == 0 and tic != last_crc_tic:
            last_crc_tic = tic
            frame, pal = state["last"]
            print("FRAMECRC gametic=%d crc=%08x" % (tic, _zlib.crc32(pal, _zlib.crc32(frame)) & 0xFFFFFFFF),
                  flush=True)
    if EVERY and PNG and (i + 1) % EVERY == 0 and state["last"]:
        dump(PNG.replace(".png", "_%05d.png" % (i + 1)))
dt = time.time() - t
print("== %d ticks in %.2fs (%.1f ticks/s), %d frames drawn" % (TICKS, dt, TICKS / dt, state["frames"]))

if PNG and state["last"]:
    dump(PNG)
