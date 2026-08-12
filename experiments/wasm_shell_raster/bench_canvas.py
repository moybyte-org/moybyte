"""Stage-4 spike (docs/moycore_plan_2026-08.md 3.2): price runtime/canvas.py as a
wasm-side SHELL raster at desktop size (1024x600).

Runs unmodified under BOTH CPython (the host-sim baseline) and the real dist
wasm MicroPython (driven by bench_wasm.mjs, which stages this file plus the
current runtime/canvas.py into /modules). Dependency-light on purpose: canvas
(+ its own font/palette/editors imports) and time only.

Workloads (ONE shared definition -- bench.c mirrors these op-for-op):
  desk   : cls wallpaper + 6 windows (420x320 rect fill + rectb frame + 18px
           title strip) + 300 glyphs of print (6x16 titles + 6x34 body lines)
           + 40 spr of a 16x16 Image.
  editor : full-canvas rect fill + 1600 glyphs (40 lines x 40 chars) + 58 thin
           row rects + scrollbar + cursor (60 rects).
  drag   : full-screen 1:1 backdrop restore via blit_strip(layer) -- the WM's
           actual full-restore idiom -- + one window restamped.
  micro  : per-verb table (cls / rect 420x320 / rectb / print 100 glyphs /
           spr 16x16 / 200px diagonal line).

Frame runs: 3 warmup + N timed (default 30), median + p90 in ms.
Micro: 7 batches of `iters` calls each, median batch -> us/op.
"""

import sys
import time

W = 1024
H = 600
FRAMES = 30
WARMUP = 3

# -- timing (MicroPython ticks_us, CPython perf_counter_ns) ------------------
if hasattr(time, "ticks_us"):
    _now = time.ticks_us
    _diff = time.ticks_diff          # wrap-safe

    def elapsed_us(a, b):
        return _diff(b, a)
else:
    _now = time.perf_counter_ns

    def elapsed_us(a, b):
        return (b - a) // 1000

# -- canvas import: repo package (CPython) or bare module (wasm /modules) ----
try:
    from runtime.canvas import Canvas, Image
    _flavor = "runtime.canvas"
except ImportError:
    from canvas import Canvas, Image
    _flavor = "canvas (staged/frozen)"


# -- fixtures -----------------------------------------------------------------

def make_icon():
    """16x16 icon, ~20% transparent (t=-1), same pattern as bench.c's sheet."""
    pix = []
    for y in range(16):
        for x in range(16):
            if (x * 7 + y * 3) % 5 == 0:
                pix.append(-1)
            else:
                pix.append((x + y) % 15 + 1)
    return Image(16, 16, pix, transparent=-1)


TITLE16 = "WINDOW TITLE 001"                                  # 16 glyphs
TEXT34 = "the quick brown fox jumps over 034"                 # 34 glyphs
LINE40 = "for i in range(40): draw(i, x, y) #c"               # pad to 40
LINE40 = LINE40 + " " * (40 - len(LINE40))
S100 = ("print one hundred glyphs of shell text " * 3)[:100]  # 100 glyphs


# -- workloads (mirrored in bench.c -- keep op-for-op identical) --------------

def desk_frame(c, icon):
    c.cls(1)
    for i in range(6):
        x = (i % 3) * 330 + 8
        y = (i // 3) * 260 + 20
        c.rect(x, y, 420, 320, 20 + i)      # panel fill
        c.rectb(x, y, 420, 320, 15)         # frame
        c.rect(x, y, 420, 18, 8)            # title strip
        c.print(TITLE16, x + 4, y + 5, 63)  # 16 glyphs
    for i in range(6):
        c.print(TEXT34, 16, 380 + i * 12, 60)   # 6 x 34 = 204 glyphs
    for i in range(40):
        c.spr(icon, (i * 97) % (W - 16), (i * 53) % (H - 16))


def editor_frame(c):
    c.rect(0, 0, W, H, 2)                   # full panel fill
    for i in range(40):
        c.print(LINE40, 8, 20 + i * 14, 62)  # 40 x 40 = 1600 glyphs
    for i in range(58):
        c.rect(4, 18 + i * 10, W - 24, 1, 3)  # thin row rules
    c.rect(W - 14, 20, 10, H - 40, 5)         # scrollbar
    c.rect(200, 188, 2, 12, 63)               # cursor


def drag_frame(c, backdrop, icon):
    c.blit_strip(backdrop, 0, 0)              # full-screen 1:1 restore
    x, y = 300, 140
    c.rect(x, y, 420, 320, 22)
    c.rectb(x, y, 420, 320, 15)
    c.rect(x, y, 420, 18, 8)
    c.print(TITLE16, x + 4, y + 5, 63)


# -- harness ------------------------------------------------------------------

def _median(xs):
    s = sorted(xs)
    n = len(s)
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2


def _p90(xs):
    s = sorted(xs)
    return s[int(0.9 * (len(s) - 1))]


def run_frames(name, fn, frames=FRAMES, reps=1):
    # `reps` calls per timing sample: the wasm port's ticks_us is ~1ms-granular,
    # so a sub-3ms frame (drag) is timed in batches of 10 and divided back.
    for _ in range(WARMUP):
        fn()
    times = []
    for _ in range(frames):
        t0 = _now()
        for _r in range(reps):
            fn()
        t1 = _now()
        times.append(elapsed_us(t0, t1) / 1000.0 / reps)
    print("RESULT frame %s median_ms=%.2f p90_ms=%.2f n=%d reps=%d"
          % (name, _median(times), _p90(times), frames, reps))


def run_micro(name, fn, iters, batches=7):
    fn()  # warm
    per = []
    for _ in range(batches):
        t0 = _now()
        for _i in range(iters):
            fn()
        t1 = _now()
        per.append(elapsed_us(t0, t1) / iters)
    print("RESULT micro %s us_per_op=%.1f iters=%d" % (name, _median(per), iters))


def main(frames=None):
    if frames is None:
        frames = FRAMES
        argv = getattr(sys, "argv", [])
        if len(argv) > 1:
            frames = int(argv[1])
    print("BENCH platform=%s canvas=%s size=%dx%d"
          % (sys.implementation.name, _flavor, W, H))

    c = Canvas(W, H)
    icon = make_icon()

    # Backdrop layer: painted once (a real desk frame) so the drag restore
    # copies real content, exactly like the WM's retained backdrop cache.
    backdrop = c.new_layer(W, H)
    desk_frame(backdrop, icon)

    run_frames("desk", lambda: desk_frame(c, icon), frames)
    run_frames("editor", lambda: editor_frame(c), frames)
    run_frames("drag", lambda: drag_frame(c, backdrop, icon), frames, reps=10)

    # iters sized so each batch is >=20ms: the wasm port's ticks_us has ~1ms
    # granularity, so a batch must be long enough that quantization is <5%.
    run_micro("cls", lambda: c.cls(5), 50)
    run_micro("rect_420x320", lambda: c.rect(30, 40, 420, 320, 9), 300)
    run_micro("rectb_420x320", lambda: c.rectb(30, 40, 420, 320, 9), 300)
    run_micro("print_100gl", lambda: c.print(S100, 4, 300, 61), 50)
    run_micro("spr_16x16", lambda: c.spr(icon, 500, 300), 300)
    run_micro("line_200px", lambda: c.line(100, 100, 300, 240, 7), 300)

    # keep the buffer live / prove work happened
    chk = 0
    for i in range(0, W * H, 80011):
        chk += c.buf[i]
    print("CHECK buf[0]=%d buf[-1]=%d sum8=%d" % (c.buf[0], c.buf[-1], chk))


if __name__ == "__main__":
    main()
