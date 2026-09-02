"""The Lua VM's small-object pool, and the integer formatting beside it (#66).

Two levers landed together because they answer the same measurement. On the
T-Deck (S3 at 240MHz, the Lua heap in PSRAM through moycore's `l_alloc`) one
malloc costs about 9us and one free about 2us, because the IDF allocator's
control structures live in PSRAM -- so a Lua `{}` is 9.7us against 0.15us for
an empty loop iteration -- and one snprintf costs 3-4us. A PICO-8 port builds
a table per vector operation and formats a number per tile, every tick, and
the S3 owes a frame every 33ms. The allocator and `%d` WERE the frame.

So:

  * `l_alloc` carves 1..256-byte requests out of PSRAM chunks through eight
    size-class free lists (`native/moycore/modmoycore.c`), and
  * `tostringbuff` converts integers -- and integral floats, which this VM
    already prints without a fraction -- by hand
    (`native/moy_lua/lua/lobject.c`, MODIFICATIONS.md item 3).

What this file watches, because neither lever fails loudly:

  * THE POOL GIVES EVERYTHING BACK. `alloc_stats()` reads all-zero after
    `close()` -- live bytes, pool bytes, and the chunk count -- so a cart that
    churned its way to a hundred chunks cannot keep them while the launcher is
    up, and the census cannot drift. It drifted the first time this was
    written: a grow or shrink INSIDE one size class returns the same pointer,
    and the bytes Lua asked for changed even though the block did not, so the
    matching free subtracted a number that had never been added. 45 bytes,
    invisible on glass, and exactly the kind of slow lie a leak check exists
    for.
  * ...AND IT GIVES THEM BACK WHILE THE CART RUNS, which is the harder half.
    A chunk serves one size class and counts its live blocks, so the free that
    empties one returns it to the heap; `pool_cap` is what says whether that
    is really happening. Before it did, moss moss's parse burst left the pool
    holding 1.47MB for 0.83MB live and the board sat with 3KB of PSRAM free --
    the slack was the difference between the cart loading and `not enough
    memory`. The burst scenario below is that shape in miniature.
  * THE MASK IS RIGHT. Block -> chunk is `p & ~(chunk - 1)`, which is only
    sound while every chunk is aligned to its own size; get it wrong and the
    decrement lands in a NEIGHBOUR's header, which corrupts silently rather
    than faulting. `moycore.pool_check()` walks the chunks and their free
    lists and answers a code (README.md lists them); every scenario here reads
    it, because the churn is what would break it.
  * THE CEILING IS CROSSED IN BOTH DIRECTIONS. A block growing past 256 bytes
    must move out to the heap and a block shrinking under it must move in, and
    each move is a copy whose length comes from Lua's realloc contract. A cart
    that only ever allocates small never exercises either.
  * THE DIGITS ARE THE SAME DIGITS. Lua's own `string.format("%d", v)` still
    goes through snprintf, so the cart can hold the fast path against the slow
    one INSIDE the VM; the sweep is then held against Python's `%d` on this
    side, which is the check that would catch both being wrong together.
    LUA_MININTEGER is in the sweep because a hand-rolled negation is where it
    would break, and 1e7 is in it because that is where `%.7g` stops printing
    plain digits and the fast path must stand down.

Needs the desktop MicroPython with moycore compiled in:

    make unix-micropython
"""

import os
import subprocess

from unix_mp import require_unix_mp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# alloc_stats() field order, mirrored from mod_alloc_stats.
SRAM, PSRAM, PEAK, DENIED, POOL_LIVE, POOL_CAP, POOL_CHUNKS = range(7)

# The formatting sweep. One list, formatted in Lua three ways by the driver and
# in Python once by the assertion.
SWEEP = [0, 1, -1, 7, 9, 10, 11, 99, 100, 101, 255, 256, 999, 1000, 1001,
         9999, 10000, 65535, 65536, 99999, 100000, 999999, 1000000,
         9999999, 10000000, 123456789, 2147483647, -2147483648,
         -9, -10, -99, -100, -12345, -1000000, -2147483647,
         -9999999, -10000000, -123456789]

# What the float row must print. `%.7g` with this VM's no-".0" edit: bare
# digits below 1e7, exponent form at and above it, and a signed zero that a
# cast to an integer would silently lose.
FLOAT_SRC = ("3.0, -3.0, 0.0, -0.0, 1.5, -1.5, 0.25, 1e7, -1e7, 9999999.0, "
             "-9999999.0, 1e8, 1e-5, 2.5, 1000000.0")
FLOAT_WANT = ["3", "-3", "0", "-0", "1.5", "-1.5", "0.25",
              "1e+07", "-1e+07", "9999999", "-9999999",
              "1e+08", "1e-05", "2.5", "1000000"]

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

# --------------------------------------------------------- the parse burst
# The shape a real load has: a transient tree far bigger than any frame that
# follows, built and dropped -- once at the top level, where the load-time
# collect has to catch it, and once inside a frame, where the chunks have to
# go back mid-run. moss moss is this with a 130KB source behind it.
begin()
print("BURSTLOAD", moycore.load(r"""
local t = {}
for i = 1, 12000 do t[i] = {x = i, y = i * 2, s = "key" .. i} end
BUILT = #t
t = nil
BIG = nil
N = 0
function _update(dt)
  N = N + 1
  if N == 1 then
    local u = {}
    for i = 1, 12000 do u[i] = {x = i, y = i * 2, s = "key" .. i} end
    BIG = u
  elseif N == 2 then
    BIG = nil
  end
end
function _draw() end
""", "@burst"))
print("BURSTBUILT", moycore.get_global("BUILT"))
print("BURSTIDLE", moycore.alloc_stats())
print("BURSTIDLECHECK", moycore.pool_check())
moycore.tick(0.03125)
print("BURSTPEAK", moycore.alloc_stats())
print("BURSTPEAKCHECK", moycore.pool_check())
moycore.tick(0.03125)
print("BURSTGCKB", moycore.gc())
print("BURSTAFTER", moycore.alloc_stats())
print("BURSTAFTERCHECK", moycore.pool_check())
moycore.close()
print("BURSTCLOSED", moycore.alloc_stats())

# ------------------------------------------------ churn across the chunks
# Frames that each cross several chunk boundaries in both directions: fill,
# empty, refill. The pool must reach a steady state rather than ratchet, and
# every round must still pass its own invariants -- a chunk freed while one of
# its blocks is still on a free list would show up here first.
begin()
print("CYCLELOAD", moycore.load(r"""
function _update(dt)
  local t = {}
  for i = 1, 4000 do t[i] = {i, i + 1, i + 2} end
  for i = 1, 4000 do t[i] = nil end
end
function _draw() end
""", "@cycle"))
_rows = []
for _r in range(6):
    moycore.tick(0.03125)
    _s = moycore.alloc_stats()
    _rows.append((_s[5], _s[6], moycore.pool_check()))
print("CYCLEROWS", _rows)
print("CYCLEGCKB", moycore.gc())
print("CYCLEAFTER", moycore.alloc_stats())
print("CYCLEAFTERCHECK", moycore.pool_check())
moycore.close()
print("CYCLECLOSED", moycore.alloc_stats())

# ------------------------------------------------------------- the formatter
# tostring() and `..` take the hand-rolled path; string.format("%d") is still
# snprintf, so the cart compares them itself and reports any disagreement.
begin()
print("FMTEXEC", moycore.exec(r"""
V = {@SWEEPVALS@}
BAD = ""
OUT = ""
for i = 1, #V do
  local v = V[i]
  local a, b, c = tostring(v), v .. "", string.format("%d", v)
  OUT = OUT .. c .. "\n"
  if a ~= c or b ~= c then
    BAD = BAD .. c .. "[" .. a .. "|" .. b .. "] "
  end
end
F = {@FLOATVALS@}
FOUT = ""
for i = 1, #F do FOUT = FOUT .. tostring(F[i]) .. "\n" end
""", "@fmt"))
print("FMTBAD", repr(moycore.get_global("BAD")))
print("FMTOUT")
print(moycore.get_global("OUT"), end="")
print("FMTEND")
print("FLTOUT")
print(moycore.get_global("FOUT"), end="")
print("FLTEND")
moycore.close()
print("FMTAFTER", moycore.alloc_stats())
'''

_OUT = []


def _run():
    if _OUT:
        return _OUT[0]
    exe = require_unix_mp(
        "moycore",
        why="Without it nothing checks that the Lua VM's small-object pool "
            "returns every chunk at close, nor that the hand-rolled integer "
            "formatter still prints what snprintf printed.")
    # LUA_MININTEGER cannot be written as a literal: 2147483648 does not fit a
    # 32-bit integer, so Lua reads it as a float and the unary minus keeps it
    # one. It goes in as arithmetic on a value that does fit.
    vals = ", ".join("%d - 1" % (v + 1) if v == -2147483648 else str(v)
                     for v in SWEEP)
    src = (DRIVER.replace("@SWEEPVALS@", vals)
                 .replace("@FLOATVALS@", FLOAT_SRC))
    p = subprocess.run([exe, "-c", src], capture_output=True, text=True,
                       timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    _OUT.append(p.stdout)
    return p.stdout


def _stats(out, tag):
    for line in out.splitlines():
        if line.startswith(tag + " "):
            return eval(line[len(tag) + 1:])            # a printed tuple
    raise AssertionError("no %s line in:\n%s" % (tag, out))


def _block(out, start, end):
    lines = out.splitlines()
    i = lines.index(start)
    return [l for l in lines[i + 1:lines.index(end, i)] if l]


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


def test_a_parse_burst_hands_its_chunks_back_instead_of_holding_them():
    """The measurement this exists for: on the T-Deck, moss moss's parse burst
    left the pool holding 1.47MB for 0.83MB live, and the run then sat with 3KB
    of PSRAM free. A chunk serves ONE size class and counts its live blocks, so
    the free that empties one returns it -- and `pool_cap` is the only number
    that says so, because `pool_live` was always going to fall when the garbage
    died."""
    out = _run()
    assert "BURSTERR" not in out, out
    assert "BURSTLOAD None" in out, out
    assert "BURSTBUILT 12000" in out, \
        "the burst cart never built its tree, so the rest proves nothing: %s" % out

    peak = _stats(out, "BURSTPEAK")
    after = _stats(out, "BURSTAFTER")
    assert peak[POOL_LIVE] > 1_000_000, \
        "a frame that builds 12000 tables held no memory -- the scenario is " \
        "not exercising the pool: %r" % (peak,)
    assert after[POOL_LIVE] * 8 < peak[POOL_LIVE], "the burst's garbage did " \
        "not die, so the chunks were never free to go: %r -> %r" % (peak, after)
    assert after[POOL_CAP] * 4 < peak[POOL_CAP], \
        "the blocks went free and the CHUNKS stayed -- this is the slack the " \
        "board runs out of memory on: %r -> %r" % (peak, after)
    assert after[POOL_CHUNKS] * 4 < peak[POOL_CHUNKS], (peak, after)
    assert after[POOL_CAP] < 512 * 1024, \
        "the pool settled far above its floor (one chunk per touched class): " \
        "%r" % (after,)
    assert _stats(out, "BURSTCLOSED") == (0, 0, _stats(out, "BURSTCLOSED")[PEAK],
                                          0, 0, 0, 0), out


def test_load_collects_the_parse_burst_before_the_first_frame():
    """`load()` runs a full collect once _init returns, because nothing else
    would: Lua's collector is incremental, so the burst would be walked out one
    step at a time WHILE the frame loop needed the memory. The cart above
    allocates 12000 tables at its top level and drops them, so the pool is
    holding megabytes when the chunk finishes -- and must not be by the time
    load() returns."""
    out = _run()
    idle = _stats(out, "BURSTIDLE")
    assert idle[POOL_LIVE] < 256 * 1024, \
        "load() returned with the parse burst still live: %r" % (idle,)
    assert idle[POOL_CAP] < 512 * 1024, \
        "load() returned holding the burst's chunks: %r" % (idle,)


def test_the_churn_reaches_a_steady_state_instead_of_ratcheting():
    """Frames that cross chunk boundaries in both directions, over and over.
    The pool may not grow round on round -- a chunk that goes back and one that
    comes out again must be the same arithmetic -- and every round has to pass
    pool_check(), which is where a chunk freed with one of its blocks still on
    a free list would surface."""
    out = _run()
    assert "CYCLEERR" not in out, out
    assert "CYCLELOAD None" in out, out
    rows = _stats(out, "CYCLEROWS")
    assert len(rows) == 6, rows
    assert all(r[2] == 0 for r in rows), \
        "pool_check() failed during the churn: %r" % (rows,)
    assert rows[0][1] > 1, "the churn never left its first chunk: %r" % (rows,)
    assert rows[-1][0] <= rows[0][0], \
        "the pool ratcheted: it must reuse what it already holds, not grow " \
        "every round: %r" % (rows,)

    after = _stats(out, "CYCLEAFTER")
    assert after[POOL_CAP] * 4 < rows[-1][0], \
        "the churn's chunks stayed after a full collect: %r vs %r" % (after, rows)
    assert _stats(out, "CYCLECLOSED")[POOL_CHUNKS] == 0, out


def test_every_chunk_stays_aligned_to_its_own_size():
    """block -> chunk is one AND, `p & ~(chunk - 1)`, which is sound only while
    every chunk is aligned to its own size. Nothing about that is visible from
    Python and it does not fault when it breaks -- the live-count decrement
    lands in a NEIGHBOURING chunk's header and the pool quietly frees a chunk
    that is still in use. pool_check() is the eye: it walks every chunk, every
    free list and both lists' links, and it runs at each point in the run where
    the shape has just changed."""
    out = _run()
    named = dict(l.split(" ", 1) for l in out.splitlines()
                 if l.split(" ", 1)[0].endswith("CHECK"))
    assert len(named) >= 4, "no pool_check() lines in:\n%s" % out
    assert set(named.values()) == {"0"}, \
        "pool_check() reported a broken invariant: %r" % (named,)

    rows = _stats(out, "CYCLEROWS")
    assert all(r[2] == 0 for r in rows), rows


def test_integers_format_exactly_as_snprintf_did():
    """`tostring(v)` and `v .. ""` skip snprintf now. They must still produce
    what `%d` produced -- checked against Lua's own string.format inside the
    VM, and against Python's %d out here, because the first check alone cannot
    see the two agreeing on something wrong."""
    out = _run()
    assert "FMTEXEC None" in out, out
    assert "FMTBAD ''" in out, \
        "tostring/.. disagreed with string.format inside the VM: %s" % out

    got = _block(out, "FMTOUT", "FMTEND")
    want = ["%d" % v for v in SWEEP]
    assert got == want, "the fast path changed the digits: %r" % (
        [(v, g, w) for v, g, w in zip(SWEEP, got, want) if g != w] or got,)


def test_an_integral_float_prints_bare_and_1e7_still_falls_back():
    """SPEC.md 4.2 already prints an integral float without a fraction, so the
    fast path takes those too -- but only below 1e7, which is where "%.7g"
    switches to exponent form. The boundary and the signed zero are the two
    values that would make the shortcut wrong."""
    out = _run()
    assert _block(out, "FLTOUT", "FLTEND") == FLOAT_WANT, \
        "the integral-float fast path changed what a float prints: %r" \
        % (_block(out, "FLTOUT", "FLTEND"),)

    after = _stats(out, "FMTAFTER")
    assert after[PSRAM] == 0 and after[POOL_CHUNKS] == 0, \
        "the formatting run leaked: %r" % (after,)
