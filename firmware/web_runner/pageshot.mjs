// Moybyte web runner SCREENSHOT harness: what the BROWSER actually shows.
//
// The other probes (harness.mjs, the scratchpad one-offs) inspect the wasm side
// -- frame counts, timings, state. That is the emitter, not the picture, and a
// whole class of owner-visible bugs lives strictly in the pixels: a panel drawn
// off-screen, a chip in the wrong corner, a window whose content never
// repainted. Those are invisible in a numbers dump and obvious in a PNG.
//
// Since moycore stage 4 this is much simpler than it was. The console
// rasterizes, so there is no page-side replayer to reproduce: this boots the
// real wasm console from dist/, drives it, and decodes the SAME RGB565
// framebuffer the browser blits. Previously it had to slice the replayer out of
// the page source and replay draw commands into a matching index buffer -- and
// that reconstruction was itself a thing that could disagree with the browser.
// Now the only page-side logic left is the 565->RGBA expansion, and it is four
// lines.
//
//   node pageshot.mjs scenario.json [outdir]
//   node pageshot.mjs --scenario '{"steps":[{"frames":40},{"shot":"desk"}]}'
//
// Scenario: {"size":[1024,600], "windowed":true, "steps":[...]}, each step one of
//   {"frames":N}                 advance N frames
//   {"shot":"name"}              write <outdir>/name.png + a stats line
//   {"tap":[x,y]}                down, frame, up, frame  (a real click)
//   {"hover":[x,y]}              pointer move with no button
//   {"drag":[x0,y0,x1,y1,N]}     press, N interpolated moves, release
//   {"key":"a"} / {"key":13}     a typed key
//   {"py":"..."}                 exec against the live console (`_ws` is bound)
//   {"note":"..."}               printed, for readable transcripts
//   {"stain":c} / {"unpainted":c}  fill the compare buffer with a sentinel 565
//                                value, then report how much of it survived --
//                                how you tell a screen that repainted itself
//                                from one still showing the last screen.
//
// Exit code is non-zero if any step raised, so it works as a CI gate too.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { loadConsole } from "./mpboot.mjs";
import { deflateSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));

// -- PNG out (no deps) -------------------------------------------------------
function crc32(buf) {
    let c, table = crc32.t;
    if (!table) {
        table = crc32.t = new Int32Array(256);
        for (let n = 0; n < 256; n++) { c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; table[n] = c; }
    }
    c = -1;
    for (let i = 0; i < buf.length; i++) c = table[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
    return (c ^ -1) >>> 0;
}
function chunk(type, data) {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
    return Buffer.concat([len, td, crc]);
}
// RGB565 -> 8/8/8, the same high-bit replication the page's LUT does, so a
// screenshot and the browser agree on every channel.
function rgb(p) {
    const r = (p >> 11) & 31, g = (p >> 5) & 63, b = p & 31;
    return [(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)];
}
function writePng(path, px, w, h) {
    const raw = Buffer.alloc((w * 3 + 1) * h);
    let o = 0;
    for (let y = 0; y < h; y++) {
        raw[o++] = 0;                                   // filter: none
        for (let x = 0; x < w; x++) {
            const c = rgb(px[y * w + x]);
            raw[o++] = c[0]; raw[o++] = c[1]; raw[o++] = c[2];
        }
    }
    const ihdr = Buffer.alloc(13);
    ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4);
    ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;   // 8-bit RGB
    writeFileSync(path, Buffer.concat([
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
        chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw, { level: 6 })), chunk("IEND", Buffer.alloc(0)),
    ]));
}

// -- scenario ----------------------------------------------------------------
const argv = process.argv.slice(2);
let scenario = null, outdir = null;
for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--scenario") scenario = JSON.parse(argv[++i]);
    else if (scenario === null) scenario = JSON.parse(readFileSync(argv[i], "utf-8"));
    else outdir = argv[i];
}
if (!scenario) throw new Error("usage: node pageshot.mjs <scenario.json|--scenario JSON> [outdir]");
outdir = resolve(outdir || scenario.out || join(HERE, "shots"));
mkdirSync(outdir, { recursive: true });

const { mp, boot } = await loadConsole();
const [SW, SH] = scenario.size || [1024, 600];
const windowed = scenario.windowed !== false;

mp.runPython(boot + `
import json
import web_boot
web_boot.boot('/moy/carts', None, ${SW}, ${SH}, ${windowed ? "True" : "False"})
from web_boot import assets_json, step_frame_json, apply_events_json, fb_addr, fb_len
_ws = web_boot._S["ws"]
`);
const stepFrame = mp.globals.get("step_frame_json");
const assetsJson = mp.globals.get("assets_json");
const fbAddr = mp.globals.get("fb_addr"), fbLen = mp.globals.get("fb_len");
const meta = JSON.parse(assetsJson());
const SWAP = !!meta.swap;

function events(list) { mp.runPython(`apply_events_json(json.dumps(${JSON.stringify(list)}))`); }

// The framebuffer as 16-bit pixels in CANONICAL order, whatever order the
// console wrote (device_canvas picks its palette table from the panel it thinks
// it is talking to; web_boot reports which). Read fresh every time: HEAPU8 is
// replaced when the wasm heap grows.
function pixels() {
    const heap = mp._module.HEAPU8, a = fbAddr(), n = fbLen();
    const out = new Uint16Array(n >> 1);
    for (let i = 0; i < out.length; i++) {
        const lo = heap[a + i * 2], hi = heap[a + i * 2 + 1];
        out[i] = SWAP ? ((lo << 8) | hi) : ((hi << 8) | lo);
    }
    return out;
}
// The sentinel written by {"stain":c} -- in the CONSOLE's byte order, since it
// goes straight into the framebuffer.
function stain(c) {
    const heap = mp._module.HEAPU8, a = fbAddr(), n = fbLen();
    const v = SWAP ? (((c & 255) << 8) | (c >> 8)) : c;
    for (let i = 0; i < n; i += 2) { heap[a + i] = v & 255; heap[a + i + 1] = v >> 8; }
}
function stainLeft(c) {
    const px = pixels();
    let n = 0;
    for (const v of px) if (v === c) n++;
    return n;
}

let drawn = 0, skipped = 0;
function frames(n) {
    for (let i = 0; i < n; i++) {
        const f = stepFrame(1 / 60, -1.0);
        if (!f) { skipped++; continue; }
        drawn += JSON.parse(f).paint ? 1 : 0;
    }
}

let failed = 0;
for (const step of scenario.steps || []) {
    try {
        if (step.note != null) console.log("--", step.note);
        if (step.stain != null) stain(step.stain);
        if (step.unpainted != null) {
            const left = stainLeft(step.unpainted);
            const n = fbLen() >> 1;
            console.log(`unpainted: ${left}/${n} px still the sentinel`
                + ` (${(100 * left / n).toFixed(1)}%)`);
        }
        if (step.frames != null) frames(step.frames);
        if (step.hover) { events([{ type: "hover", x: step.hover[0], y: step.hover[1] }]); frames(1); }
        if (step.tap) {
            events([{ type: "down", x: step.tap[0], y: step.tap[1] }]); frames(1);
            events([{ type: "up" }]); frames(1);
        }
        if (step.drag) {
            const [x0, y0, x1, y1, n = 8] = step.drag;
            events([{ type: "down", x: x0, y: y0 }]); frames(1);
            for (let i = 1; i <= n; i++) {
                events([{ type: "move", x: Math.round(x0 + (x1 - x0) * i / n), y: Math.round(y0 + (y1 - y0) * i / n) }]);
                frames(1);
            }
            events([{ type: "up" }]); frames(1);
        }
        if (step.key != null) {
            const code = typeof step.key === "number" ? step.key : step.key.charCodeAt(0);
            events([{ type: "key", code }]); frames(1);
        }
        if (step.py) mp.runPython(step.py);
        if (step.shot) {
            const m = JSON.parse(assetsJson());
            const path = join(outdir, step.shot + ".png");
            writePng(path, pixels(), m.w, m.h);
            if (step.dump) {
                const heap = mp._module.HEAPU8, a = fbAddr(), n = fbLen();
                writeFileSync(join(outdir, step.shot + ".fb"),
                    Buffer.from(heap.subarray(a, a + n)));
            }
            console.log(`shot ${step.shot}: ${m.w}x${m.h} -> ${path}`
                + `  [painted ${drawn} skipped ${skipped} cart ${m.cart || "-"}]`);
        }
    } catch (e) {
        failed++;
        console.log("STEP FAILED", JSON.stringify(step), "\n  ", e && e.message ? e.message : e);
    }
}
if (failed) { console.log(`${failed} step(s) failed`); process.exit(1); }
