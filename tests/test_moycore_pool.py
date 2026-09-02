"""The Lua VM's small-object pool (#66).

Measured on the T-Deck (S3 at 240MHz, the Lua heap in PSRAM through moycore's
`l_alloc`): one malloc costs about 9us and one free about 2us, because the IDF
allocator's control structures live in PSRAM -- so a Lua `{}` is 9.7us against
0.15us for an empty loop iteration, and a two-field constructor 21us. A PICO-8
port builds a table per vector operation, every tick, and the S3 owes a frame
every 33ms. The ALLOCATOR was the frame.

So `l_alloc` carves 1..256-byte requests out of PSRAM chunks through eight
size-class free lists (`native/moycore/modmoycore.c`).

What this file watches, because the pool does not fail loudly:

  * THE POOL GIVES EVERYTHING BACK. `alloc_stats()` reads all-zero after
    `close()` -- live bytes, pool bytes, and the chunk count -- so a cart that
    churned its way to a dozen 32KB chunks cannot keep them while the launcher
    is up, and the census cannot drift. It drifted the first time this was
    written: a grow or shrink INSIDE one size class returns the same pointer,
    and the bytes Lua asked for changed even though the block did not, so the
    matching free subtracted a number that had never been added. 45 bytes,
    invisible on glass, and exactly the kind of slow lie a leak check exists
    for.
  * THE CEILING IS CROSSED IN BOTH DIRECTIONS. A block growing past 256 bytes
    must move out to the heap and a block shrinking under it must move in, and
    each move is a copy whose length comes from Lua's realloc contract. A cart
    that only ever allocates small never exercises either.

Needs the desktop MicroPython with moycore compiled in:

    make unix-micropython
"""

import os
import subprocess

from unix_mp import require_unix_mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# alloc_stats() field order, mirrored from mod_alloc_stats.
SRAM, PSRAM, PEAK, DENIED, POOL_LIVE, POOL_CAP, POOL_CHUNKS = range(7)

DRIVER = r'''
import moycore
from array import array

W, H = 64, 48
fb = bytearray(W * H * 2)
snap = array("i", bytearray(4 * moycore.SNAP_LEN))
aq = array("h", bytearray(2 * (1 + 4 * 32)))


def begin():
    moycore.run_begin(fb, W, H, None, None, None, 0, 0, snap, aq,
                      None, None, None)


def run(tag, src, frames):
    print(tag + "LOAD", moycore.load(src, "@" + tag))
    for _f in range(frames):
        err = moycore.tick(0.03125)
        if err is not None:
            print(tag + "ERR", err)
            return


print("BOOT", moycore.alloc_stats())

# ---------------------------------------------------------------- the churn
# Small objects only, in the shapes the VM actually allocates: a table header
# and its node array, a short interned string, and a closure over an upvalue.
# Thousands per frame, all garbage by the end of it.
begin()
run("CHURN", r"""
local keep = {}
local sink = 0
function _update(dt)
  for i = 1, 700 do
    keep[i] = {x = i, y = i * 2, s = "k" .. (i % 13)}
  end
  for i = 1, 700 do keep[i] = nil end
  for i = 1, 300 do
    local n = i
    local f = function() return n + 1 end
    sink = sink + f()
  end
end
function _draw() end
""", 12)
print("CHURNLIVE", moycore.alloc_stats())
moycore.close()
print("CHURNAFTER", moycore.alloc_stats())

# ------------------------------------------------------- across the ceiling
# A string and a table grown well past 256 bytes and then dropped, so a block
# leaves the pool for the heap, and a hash part built up and torn down so the
# reverse shrink runs too.
begin()
run("EDGE", r"""
function _update(dt)
  local s = ""
  for i = 1, 120 do s = s .. "0123456789" end
  BIGLEN = #s
  local t = {}
  for i = 1, 900 do t[i] = i end
  for i = 900, 1, -1 do t[i] = nil end
  local u = {}
  for i = 1, 64 do u["key" .. i] = i end
  for i = 1, 64 do u["key" .. i] = nil end
end
function _draw() end
""", 4)
print("EDGELEN", moycore.get_global("BIGLEN"))
print("EDGELIVE", moycore.alloc_stats())
moycore.close()
print("EDGEAFTER", moycore.alloc_stats())
'''

_OUT = []


def _run():
    if _OUT:
        return _OUT[0]
    exe = require_unix_mp(
        "moycore",
        why="Without it nothing checks that the Lua VM's small-object pool "
            "returns every chunk at close.")
    p = subprocess.run([exe, "-c", DRIVER], capture_output=True, text=True,
                       timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    _OUT.append(p.stdout)
    return p.stdout


def _stats(out, tag):
    for line in out.splitlines():
        if line.startswith(tag + " "):
            return eval(line[len(tag) + 1:])            # a printed tuple
    raise AssertionError("no %s line in:\n%s" % (tag, out))


def test_the_pool_hands_back_every_chunk_when_the_vm_closes():
    """Nothing may survive a run. All seven counters read zero after close --
    the requested bytes, the pool's own bytes, and the chunk list -- while the
    peak survives, which is what proves the census was running at all."""
    out = _run()
    assert "CHURNERR" not in out, out
    assert "CHURNLOAD None" in out, "the churn cart did not load: %s" % out

    boot = _stats(out, "BOOT")
    assert boot == (0, 0, 0, 0, 0, 0, 0), \
        "a fresh process is already holding bytes: %r" % (boot,)

    live = _stats(out, "CHURNLIVE")
    assert live[POOL_CHUNKS] >= 1, \
        "thousands of small tables took no pool chunk -- the pool is not on " \
        "this path: %r" % (live,)
    assert 0 < live[POOL_LIVE] <= live[POOL_CAP], \
        "pool live bytes outside the pool's own capacity: %r" % (live,)
    assert live[PSRAM] > 0, "the census reported no live bytes mid-run: %r" % (live,)
    # Off-board there is one region and mc_region calls it region 1, so the
    # SRAM fields are structurally zero here. Said out loud because a board
    # reading zero there would mean something entirely different.
    assert live[SRAM] == 0 and live[DENIED] == 0, live

    after = _stats(out, "CHURNAFTER")
    assert after[PSRAM] == 0 and after[SRAM] == 0, \
        "the VM closed and the census still holds bytes -- the allocator's " \
        "accounting drifted, or a block leaked: %r" % (after,)
    assert after[POOL_LIVE] == 0, "a pool block outlived the VM: %r" % (after,)
    assert after[POOL_CHUNKS] == 0 and after[POOL_CAP] == 0, \
        "close() left chunks allocated -- they leak across runs: %r" % (after,)
    assert after[PEAK] > 0, \
        "the peak is a high-water mark and must survive close: %r" % (after,)


def test_a_block_crossing_the_256_byte_ceiling_moves_and_still_balances():
    """The pool's whole safety argument is that a request of 1..256 bytes is
    ALWAYS a pool block, so a block growing past the ceiling has to move out to
    the heap and one shrinking under it has to move in. Both moves are copies
    sized from Lua's realloc contract, and getting either length wrong is a
    corrupted string or a lost table -- so the cart's own result is checked
    too, not only the counters."""
    out = _run()
    assert "EDGEERR" not in out, out
    assert "EDGELOAD None" in out, out
    assert "EDGELEN 1200" in out, \
        "a string grown past the pool ceiling came back the wrong length: %s" % out

    live = _stats(out, "EDGELIVE")
    assert live[PSRAM] > live[POOL_LIVE], \
        "nothing lives outside the pool -- the >256 path never ran: %r" % (live,)
    after = _stats(out, "EDGEAFTER")
    assert after[SRAM] == 0 and after[PSRAM] == 0 and after[POOL_LIVE] == 0 \
        and after[POOL_CHUNKS] == 0, \
        "crossing the ceiling left the census unbalanced: %r" % (after,)


