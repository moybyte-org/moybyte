// Moybyte web runner SCREENSHOT harness: what the BROWSER actually shows.
//
// The other probes (harness.mjs, the scratchpad one-offs) inspect the wasm side
// -- frames, surfaces, byte counts. That is the emitter, not the picture, and a
// whole class of owner-visible bugs lives strictly on the other side of the wire:
// a surface shipped as {"same":1} the page has no cache for, a placement the
// replayer puts somewhere else, a retained buffer nobody repainted. Those are
// invisible to a protocol dump and obvious in a PNG.
//
// So this runs BOTH halves in node: the real wasm console from dist/ produces
// frames, and the REAL page replayer -- sliced out of runtime/web_view_page.py,
// not a reimplementation -- replays them into the same retained index buffer the
// browser keeps, which we then write out as a PNG.
//
//   node pageshot.mjs scenario.json [outdir]
//   node pageshot.mjs --scenario '{"steps":[{"frames":40},{"shot":"desk"}]}'
//
// Scenario: {"size":[1024,600], "windowed":true, "steps":[...]}, each step one of
//   {"frames":N}                 advance N frames (skipped frames replay nothing)
//   {"shot":"name"}              write <outdir>/name.png + a stats line
//   {"tap":[x,y]}                down, frame, up, frame  (a real click)
//   {"hover":[x,y]}              pointer move with no button
//   {"drag":[x0,y0,x1,y1,N]}     press, N interpolated moves, release
//   {"key":"a"} / {"key":13}     a typed key
//   {"py":"..."}                 exec against the live console (`_ws` is bound)
//   {"note":"..."}               printed, for readable transcripts
//
// Exit code is non-zero if any step raised, so it works as a CI gate too.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { loadConsole } from "./mpboot.mjs";
import { deflateSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../..");

// -- the page half: the REAL replayer, sliced from the page source -----------
// Same slice markers replayer_view_test.mjs uses (the draw state + primitives +
// rep()), plus df()'s two frame-level lines lifted verbatim so the surface cache
// and the atlas-gen wipe behave exactly as they do in the browser.
function pageModuleSource() {
    const py = readFileSync(join(REPO, "runtime/web_view_page.py"), "utf-8");
    const lines = py.split("\n");
    const from = lines.findIndex((l) => l.startsWith("var caX=0,caY=0"));
    const to = lines.findIndex((l) => l.startsWith("function blit()"));
    if (from < 0 || to < 0) throw new Error("replayer slice markers not found in web_view_page.py");
    const slice = lines.slice(from, to).join("\n");

    const genWipe = lines.find((l) => l.startsWith("if(f.gen!==curGen)"));
    const surfStart = lines.findIndex((l) => l.startsWith("if(f.surfaces){for(var si=0"));
    if (!genWipe || surfStart < 0) throw new Error("df() frame markers not found in web_view_page.py");
    const surfLoop = lines.slice(surfStart, surfStart + 3).join("\n");  // if/else over f.surfaces

    return `
let W = 320, H = 240, idx = new Uint8Array(W * H);
let ATL = [], SURF = {}, IMG = {}, SHEET = null, TM = null, FONT = null, PAL = null;
let imgWant = false, curGen = -1;
const HUD = { unknown: 0 };
function atob(s) { return Buffer.from(s, "base64").toString("binary"); }
${slice}

// getA()'s asset merge (page lines 243..257), minus the DOM/audio plumbing.
export function applyAssets(a) {
    W = a.w; H = a.h; PAL = a.palette; FONT = a.font; SHEET = a.sheet || null;
    TM = a.tilemap ? { w: a.tilemap.w, h: a.tilemap.h, cells: a.tilemap.cells.slice() } : null;
    if (!a.partial) IMG = {};
    if (a.images) for (const nm in a.images) {
        const gi = a.images[nm], bs = atob(gi.b64), bp = new Uint8Array(bs.length);
        for (let k = 0; k < bs.length; k++) bp[k] = bs.charCodeAt(k);
        IMG[nm] = { w: gi.w, h: gi.h, px: bp };
    }
    if (idx.length !== W * H) { idx = new Uint8Array(W * H); rs(); }   // alloc()
}
export function df(f) {
    ${genWipe}
    ${surfLoop}
}
export function stats() { return { W, H, unknown: HUD.unknown, surfaces: Object.keys(SURF).length,
                                   atlas: ATL.filter(Boolean).length, imgWant }; }
// Paint the retained buffer a sentinel colour: any pixel still holding it after
// the next frame was NOT painted by that frame. This is how you tell a screen
// that drew itself from one that is showing the previous screen's pixels --
// the distinction a PNG alone can't make when both screens share a wallpaper.
export function stain(c) { idx.fill(c); }
export function stainLeft(c) { let n = 0; for (const v of idx) if (v === c) n++; return n; }
export function pixels() { return { idx, W, H, PAL }; }
export function wantsImages() { const w = imgWant; imgWant = false; return w; }
`;
}

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
function writePng(path, idx, w, h, pal) {
    const raw = Buffer.alloc((w * 3 + 1) * h);
    let o = 0;
    for (let y = 0; y < h; y++) {
        raw[o++] = 0;                                   // filter: none
        for (let x = 0; x < w; x++) {
            const p = pal[idx[y * w + x]] || [255, 0, 255];
            raw[o++] = p[0]; raw[o++] = p[1]; raw[o++] = p[2];
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

// -- the wasm half (shared loader: mpboot.mjs) -------------------------------
const { mp, boot } = await loadConsole();

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

const [SW, SH] = scenario.size || [1024, 600];
const windowed = scenario.windowed !== false;
const page = await import("data:text/javascript;base64," + Buffer.from(pageModuleSource(), "utf8").toString("base64"));

mp.runPython(boot + `
import json
import web_boot
web_boot.boot('/moy/carts', None, ${SW}, ${SH}, ${windowed ? "True" : "False"})
from web_boot import assets_json, step_frame_json, apply_events_json
_ws = web_boot._S["ws"]
`);

const pyJson = (expr) => { mp.runPython(`_pv = ${expr}`); return JSON.parse(mp.globals.get("_pv")); };
function pullAssets() { page.applyAssets(pyJson("assets_json()")); }
function events(list) { mp.runPython(`apply_events_json(json.dumps(${JSON.stringify(list)}))`); }

// getA() is ASYNC in the browser: df() kicks the fetch and keeps replaying with
// the caches it has, so a cart change lands its new assets some frames later.
// `assetDelay` emulates that gap (0 = the idealized synchronous case).
const assetDelay = Number(process.env.MOY_ASSET_DELAY || scenario.asset_delay || 0);
let drawn = 0, skipped = 0, lastCart = null, assetsDue = -1;
const recent = [];
function frames(n) {
    for (let i = 0; i < n; i++) {
        mp.runPython(`_f = step_frame_json(1/60, -1.0)`);
        const f = mp.globals.get("_f");
        if (assetsDue === 0) { assetsDue = -1; pullAssets(); }
        else if (assetsDue > 0) assetsDue--;
        if (!f) { skipped++; continue; }
        drawn++;
        const p = JSON.parse(f);
        // What the page RECEIVED, per frame: "<id>:<n>" for a real stream,
        // "<id>=" for a {"same":1} stub. A screen that should have changed and
        // is all "=" is the console telling the page to keep what it has.
        recent.push((p.surfaces || []).map((s) => s.id + (s.same ? "=" : ":" + ((s.cmds || []).length))).join(","));
        if (recent.length > 12) recent.shift();
        // The page refetches assets on a cart change, and again whenever an
        // imgref missed its cache -- both are load-bearing for what appears.
        if (p.cart !== lastCart) {
            lastCart = p.cart;
            if (assetDelay) assetsDue = assetDelay; else pullAssets();
        }
        page.df(p);
        if (page.wantsImages()) { if (assetDelay) assetsDue = assetDelay; else pullAssets(); }
    }
}

pullAssets();
let failed = 0;
for (const step of scenario.steps || []) {
    try {
        if (step.note != null) console.log("--", step.note);
        if (step.stain != null) page.stain(step.stain);
        if (step.unpainted != null) {
            const left = page.stainLeft(step.unpainted);
            const { W, H } = page.stats();
            console.log(`unpainted: ${left}/${W * H} px still the sentinel`
                + ` (${(100 * left / (W * H)).toFixed(1)}%)`);
        }
        // DROP frames the way the browser does (page_tail keeps only the newest
        // frame per rAF): step the console but never replay the payload. Unless
        // `noresync` is set, the page's recovery request follows -- which is the
        // whole point of the step: with it the screen must come back, without it
        // the delta strands every surface the lost frame carried.
        if (step.drop != null) {
            for (let i = 0; i < step.drop; i++) mp.runPython(`_f = step_frame_json(1/60, -1.0)`);
            if (!step.noresync) mp.runPython(`web_boot.request_keyframe()`);
        }
        if (step.frames != null) frames(step.frames);
        if (step.stream) { console.log("   last frames received by the page:"); for (const r of recent) console.log("     " + r); }
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
            const { idx, W, H, PAL } = page.pixels();
            const path = join(outdir, step.shot + ".png");
            writePng(path, idx, W, H, PAL || []);
            // The raw palette-index buffer too, when asked: byte-comparing two
            // shots is how you tell "this screen painted these pixels" from
            // "nobody painted them and the retained buffer showed through".
            if (step.dump) writeFileSync(join(outdir, step.shot + ".idx"), Buffer.from(idx));
            const s = page.stats();
            console.log(`shot ${step.shot}: ${W}x${H} -> ${path}`
                + `  [drawn ${drawn} skipped ${skipped} surfaces ${s.surfaces}`
                + ` atlas ${s.atlas} unknown-sprite ${s.unknown}]`);
        }
    } catch (e) {
        failed++;
        console.log("STEP FAILED", JSON.stringify(step), "\n  ", e && e.message ? e.message : e);
    }
}
if (failed) { console.log(`${failed} step(s) failed`); process.exit(1); }
