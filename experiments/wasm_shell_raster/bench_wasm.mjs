// Stage-4 spike: run bench_canvas.py inside the REAL dist wasm MicroPython
// (firmware/web_runner/dist) via mpboot.loadConsole -- option (a) priced on
// the actual shipping VM, not a proxy.
//
//   node bench_wasm.mjs [frames]
//
// The CURRENT runtime/canvas.py is staged into /modules so it shadows the
// frozen copy (its font/palette/editors imports resolve to the frozen twins,
// exactly like a --stage-only dev dist). bench_canvas.py is staged beside it.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadConsole } from "../../firmware/web_runner/mpboot.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");
const frames = parseInt(process.argv[2] || "30", 10);

const { mp, mkdirs } = await loadConsole({
    heapMB: 96,
    stdout: (l) => console.log(l),   // raw lines: RESULT ... parse as-is
});

mkdirs("/modules");
mp.FS.writeFile("/modules/canvas.py",
    readFileSync(join(REPO, "runtime", "canvas.py"), "utf-8"));
mp.FS.writeFile("/modules/bench_canvas.py",
    readFileSync(join(HERE, "bench_canvas.py"), "utf-8"));

const t0 = performance.now();
mp.runPython(`
import sys
sys.path.insert(0, '/modules')
import canvas
print("STAGED canvas shadows frozen:", canvas.__file__ if hasattr(canvas, '__file__') else '?')
import bench_canvas
bench_canvas.main(${frames})
`);
console.log(`TOTAL wall ${(performance.now() - t0).toFixed(0)} ms`);

// Option (a)'s PRESENT cost, priced as a PROXY: a Python-rastered shell still
// has to turn its 1024x600 index buffer into RGBA for a page canvas. In the
// realistic shape that loop is page-side JS over the wasm heap (a Python loop
// over 614k pixels would be seconds). The plumbing to export the buffer is
// NOT built -- this times the identical JS LUT loop over a same-sized
// Uint8Array, nothing more.
{
    const N = 1024 * 600;
    const idx = new Uint8Array(N);
    for (let i = 0; i < N; i++) idx[i] = i % 64;
    const lut = new Uint32Array(64);
    for (let i = 0; i < 64; i++) lut[i] = 0xff000000 | (i * 0x030201);
    const out = new Uint32Array(N);
    const times = [];
    for (let r = 0; r < 33; r++) {
        const a = performance.now();
        for (let i = 0; i < N; i++) out[i] = lut[idx[i]];
        const b = performance.now();
        if (r >= 3) times.push(b - a);
    }
    times.sort((x, y) => x - y);
    console.log(`RESULT frame present_js_lut_proxy median_ms=${times[15].toFixed(2)} ` +
        `p90_ms=${times[Math.floor(0.9 * (times.length - 1))].toFixed(2)} n=30 (out[7]=${out[7]})`);
}
