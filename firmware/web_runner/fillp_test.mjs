// A fill-patterned rect / rectb through the canvas the BROWSER draws on, in the
// wasm VM from dist/ (the bytes the page ships): both of a pattern's colours
// must land, and the pattern must end where the cart ends it.
//
// Why it is a wasm test and not a host one: the wasm's moy_gfx installs the
// native rect/rectb DRAW GATES on every canvas (`make_draw_ctx`), which fill
// solid, and the host's ctypes kernel has none -- so a host canvas draws the
// pattern correctly while the browser and the boards did not (Moy's night
// wallpaper drew its dithered ground as one navy band, 2026-10-18). The first
// check below fails the test if the canvas it draws on is not gated, so it
// cannot pass vacuously on a build that lost the gates.
//
// Run from firmware/web_runner, against dist/ (a build: it boots the frozen
// console). tests/test_web_fillp.py runs it.
import { pathToFileURL } from "node:url";

const { loadMicroPython } = await import(
    pathToFileURL(process.cwd() + "/dist/micropython.mjs").href);
const mp = await loadMicroPython({ heapsize: 16 * 1024 * 1024,
    stdout: (l) => console.log("  " + l) });

mp.runPython(`
import web_canvas

W, H = 64, 16
NAVY, BLACK, CREAM = 60, 0, 7

def make():
    c = web_canvas.make_canvas(W, H)
    c.cls(BLACK)
    return c

def count(c):
    buf = c._buf
    n = {}
    for i in range(0, W * H * 2, 2):
        v = buf[i] | (buf[i + 1] << 8)
        n[v] = n.get(v, 0) + 1
    return n

def colours(c):
    return c._wire[NAVY], c._wire[BLACK], c._wire[CREAM]

def check(name, cond):
    if not cond:
        raise AssertionError(name)
    print("ok   " + name)

c = make()
check("the canvas is drawn through the native gates",
      "draw_gate" in repr(c.rect) and "draw_gate" in repr(c.rectb))
navy, black, cream = colours(c)

# Holes untouched: half the pixels are the pattern's colour, half the ground's.
c.fillp(0x5A5A)
c.rect(0, 0, W, 8, NAVY)
n = count(c)
check("rect: both the pattern's colour and the ground show",
      n.get(navy, 0) == W * 4 and n.get(black, 0) == W * H - W * 4)

# Holes take a second colour: the pattern's two colours and nothing else.
c = make()
c.fillp(0x5A5A, CREAM)
c.rect(0, 0, W, 8, NAVY)
n = count(c)
check("rect: a hole colour lands beside the pattern's",
      n.get(navy, 0) == W * 4 and n.get(cream, 0) == W * 4
      and n.get(black, 0) == W * H - W * 8)

# rectb is a shape verb too: a patterned outline is not a solid one.
c = make()
c.fillp(0x5A5A)
c.rectb(0, 0, W, H, NAVY)
n = count(c)
check("rectb: the outline is dithered",
      0 < n.get(navy, 0) < 2 * (W + H - 2) and n.get(black, 0) > W * H - 2 * (W + H))

# Solid again after fillp(), and after the frame's reset_state.
c = make()
c.fillp(0x5A5A)
c.fillp()
c.rect(0, 0, W, 8, NAVY)
check("fillp() returns the gate to a solid fill", count(c).get(navy, 0) == W * 8)
c = make()
c.fillp(0x5A5A)
c.reset_state()
c.rect(0, 0, W, 8, NAVY)
check("a frame's reset_state returns the gate to a solid fill",
      count(c).get(navy, 0) == W * 8)
`);
console.log("fillp_test: ALL OK");
