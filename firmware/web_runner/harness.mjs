// Moybyte web runner headless harness (#151): boot the console under node from
// dist/ (the exact bundle the page ships), run the shelf + every game cart for a
// few seconds each, and report frame stats. This is the pre-browser gate: any
// staged-module import error, cart crash, or protocol break surfaces here.
//
//   node harness.mjs             # full sweep
//   node harness.mjs star_catcher.moy   # one cart
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST = join(HERE, "dist");
const { loadMicroPython } = await import(join(DIST, "micropython.mjs"));

const mp = await loadMicroPython({ heapsize: 16 * 1024 * 1024,
    stdout: (l) => console.log("  [moy]", l) });

function mkdirs(p) {
    let cur = "";
    for (const part of p.split("/")) {
        if (!part) continue;
        cur += "/" + part;
        try { mp.FS.mkdir(cur); } catch (e) { /* exists */ }
    }
}

const mods = JSON.parse(readFileSync(join(DIST, "modules.json"), "utf-8"));
const carts = JSON.parse(readFileSync(join(DIST, "carts.json"), "utf-8"));
mkdirs("/modules");
for (const n in mods) mp.FS.writeFile("/modules/" + n, mods[n]);
mkdirs("/moy/carts");
for (const rel in carts) {
    const full = "/moy/carts/" + rel;
    mkdirs(full.slice(0, full.lastIndexOf("/")));
    mp.FS.writeFile(full, carts[rel]);
}

const t0 = performance.now();
mp.runPython("import sys\nsys.path.insert(0, '/modules')\nimport web_boot\n"
    + "web_boot.boot('/moy/carts')\n"
    + "from web_boot import assets_json, step_frame_json, apply_events_json, open_cart");
console.log("boot: console up in", (performance.now() - t0).toFixed(0), "ms");

const assets = mp.globals.get("assets_json");
const step = mp.globals.get("step_frame_json");
const events = mp.globals.get("apply_events_json");
const openCart = mp.globals.get("open_cart");

const a = JSON.parse(assets());
if (!a.palette || a.palette.length !== 64 || !a.font || a.w !== 320 || a.h !== 240) {
    throw new Error("bad assets payload: " + JSON.stringify(Object.keys(a)));
}
console.log("assets: ok (palette 64, font, " + a.w + "x" + a.h + ")");

function run(label, frames, dt = 1 / 60) {
    let drawn = 0, bytes = 0, cmds = 0, worst = 0;
    const t = performance.now();
    for (let i = 0; i < frames; i++) {
        const f0 = performance.now();
        const f = step(dt);
        const el = performance.now() - f0;
        if (el > worst) worst = el;
        if (f) {
            drawn++;
            bytes += f.length;
            cmds += JSON.parse(f).cmds.length;
        }
    }
    const ms = (performance.now() - t) / frames;
    console.log(`${label}: ${frames} frames, ${drawn} drawn, ` +
        `${(cmds / Math.max(1, drawn)).toFixed(0)} cmds/f, ` +
        `${(bytes / Math.max(1, drawn) / 1024).toFixed(1)} KB/f, ` +
        `${ms.toFixed(2)} ms/f avg, ${worst.toFixed(1)} worst`);
    return drawn;
}

// 1. The shelf (launcher home): first frame must be a keyframe.
if (run("shelf", 60) < 1) throw new Error("launcher drew nothing");

// 2. A pointer tap through the shared event decode (protocol smoke).
events(JSON.stringify({ events: [{ type: "down", x: 160, y: 120 }, { type: "up" }] }));
step(1 / 60);

// 3. Every game cart on the roster (arg = one cart), ~2s each + input pokes.
const roster = process.argv[2] ? [process.argv[2]]
    : [...new Set(Object.keys(carts).map((k) => k.split("/")[0]))]
        .filter((c) => {   // wallpapers are backdrop-only (excluded from the run grid)
            try { return JSON.parse(carts[c + "/manifest.json"]).type !== "wallpaper"; }
            catch (e) { return true; }
        });
let failed = 0;
for (const cart of roster) {
    try {
        if (!openCart(cart)) throw new Error("open_cart returned False");
        events(JSON.stringify({ events: [{ type: "hold", name: "right", down: true }] }));
        const drawn = run(cart, 120);
        events(JSON.stringify({ events: [{ type: "hold", name: "right", down: false },
            { type: "down", x: 160, y: 120 }, { type: "up" }] }));
        step(1 / 60);
        if (drawn < 1) throw new Error("cart drew nothing");
    } catch (e) {
        failed++;
        console.error(`FAIL ${cart}:`, e.message || e);
    }
}
mp.runPython("import gc; gc.collect(); print('heap used KB', gc.mem_alloc()//1024)");
if (failed) { console.error(failed + " cart(s) failed"); process.exit(1); }
console.log("harness: ALL OK");
