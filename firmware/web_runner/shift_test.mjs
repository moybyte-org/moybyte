// shift_last through the canvas the BROWSER draws on, in the wasm VM from
// dist/ (the bytes the page ships): the browser's canvas is one persistent
// buffer, so the shift is in place, and it must move the pixels the boards'
// and the host's do -- every sign of the move, a rect clipped at each edge --
// held to the per-pixel reference tests/test_shift_last.py states
// (docs/theme_and_shell_2026-11.md §9.6). The web canvas is the one-buffer
// compositor's (runtime/toolkit/one_buffer.py): its one buffer is the last
// frame's, so copy_last copies nothing and copy_last_into reads that buffer.
//
// Run from firmware/web_runner, against dist/. tests/test_web_shift.py runs it.
import { pathToFileURL } from "node:url";

const { loadMicroPython } = await import(
    pathToFileURL(process.cwd() + "/dist/micropython.mjs").href);
const mp = await loadMicroPython({ heapsize: 16 * 1024 * 1024,
    stdout: (l) => console.log("  " + l) });

mp.runPython(`
import web_canvas

W, H = 40, 24

def noise(n, seed):
    out = bytearray(n)
    s = seed * 2654435761 % 4294967296 or 1
    for i in range(n):
        s = (s * 1103515245 + 12345) % 2147483648
        out[i] = (s >> 16) & 255
    return out

def ref(before, x, y, w, h, dx, dy):
    out = bytearray(before)
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    for py in range(y0, y1):
        for px in range(x0, x1):
            sx, sy = px - dx, py - dy
            if x0 <= sx < x1 and y0 <= sy < y1:
                i, j = (py * W + px) * 2, (sy * W + sx) * 2
                out[i:i + 2] = before[j:j + 2]
    return out

c = web_canvas.make_canvas(W, H)
c._buf[:] = noise(W * H * 2, 99)
before = bytes(c._buf)
if c.copy_last(3, 3, 20, 10) is not True or bytes(c._buf) != before:
    raise AssertionError("copy_last on the one buffer moved pixels")
lay = c.new_layer(8, 6)
lay._buf[:] = bytes(8 * 6 * 2)
if c.copy_last_into(lay, 5, 2, 8, 6) is not True:
    raise AssertionError("copy_last_into refused")
for r in range(6):
    a = ((2 + r) * W + 5) * 2
    if bytes(lay._buf[r * 16:(r + 1) * 16]) != before[a:a + 16]:
        raise AssertionError("copy_last_into row %d is not the one buffer's" % r)
k = 0
for rect in ((5, 4, 20, 12), (-3, -2, 15, 10), (30, 15, 20, 15), (0, 0, 40, 24)):
    for d in ((0, 1), (0, -1), (0, 7), (0, -7), (3, 0), (-3, 0), (2, -5), (-4, 6),
              (0, 30), (0, 0)):
        k += 1
        c._buf[:] = noise(W * H * 2, k)
        before = bytes(c._buf)
        if c.shift_last(*(rect + d)) is not True:
            raise AssertionError("shift_last refused %r" % ((rect, d),))
        c.shift_fence()
        if bytes(c._buf) != ref(before, *(rect + d)):
            raise AssertionError("shift_last %r moved the wrong pixels" % ((rect, d),))
print("ok   %d shifts in place" % k)
`);
console.log("shift_test: ALL OK");
