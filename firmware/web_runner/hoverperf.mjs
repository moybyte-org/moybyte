// Tier-0 hover-flip baseline on the wasm desktop (the ui_widgets_2026-08 W1
// number, measured early per the perf review's B3): a hover flip == ws._dirty
// + a full repaint, so forced-dirty frames time the exact cost class without
// coordinate archaeology. Console half = step_frame_json (record + JSON);
// page half = df() replay into the retained buffer (putImageData floor ~2ms
// per surface_model 5.4 is on top, fixed).
import { readFileSync } from "node:fs";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const RUNNER = dirname(fileURLToPath(import.meta.url));
const { loadConsole } = await import(join(RUNNER, "mpboot.mjs"));
const { mp, boot } = await loadConsole();

// page replayer slice (same markers pageshot uses)
const REPO = resolve(RUNNER, "../..");
function pageModuleSource() {
    const py = readFileSync(join(REPO, "runtime/web_view_page.py"), "utf-8");
    const lines = py.split("\n");
    const from = lines.findIndex((l) => l.startsWith("var caX=0,caY=0"));
    const to = lines.findIndex((l) => l.startsWith("function blit()"));
    const slice = lines.slice(from, to).join("\n");
    const genWipe = lines.find((l) => l.startsWith("if(f.gen!==curGen)"));
    const surfStart = lines.findIndex((l) => l.startsWith("if(f.surfaces){for(var si=0"));
    const surfLoop = lines.slice(surfStart, surfStart + 3).join("\n");
    return `
let W = 320, H = 240, idx = new Uint8Array(W * H);
let ATL = [], SURF = {}, IMG = {}, SHEET = null, TM = null, FONT = null, PAL = null;
let imgWant = false, curGen = -1;
const HUD = { unknown: 0 };
function atob(s) { return Buffer.from(s, "base64").toString("binary"); }
${slice}
export function applyAssets(a) {
    W = a.w; H = a.h; PAL = a.palette; FONT = a.font; SHEET = a.sheet || null;
    TM = a.tilemap ? { w: a.tilemap.w, h: a.tilemap.h, cells: a.tilemap.cells.slice() } : null;
    if (!a.partial) IMG = {};
    if (a.images) for (const nm in a.images) {
        const gi = a.images[nm], bs = atob(gi.b64), bp = new Uint8Array(bs.length);
        for (let k = 0; k < bs.length; k++) bp[k] = bs.charCodeAt(k);
        IMG[nm] = { w: gi.w, h: gi.h, px: bp };
    }
    if (idx.length !== W * H) { idx = new Uint8Array(W * H); }
}
export function df(f) {
    ${genWipe}
    ${surfLoop}
}
export function wantsImages() { const w = imgWant; imgWant = false; return w; }
`;
}
const page = await import("data:text/javascript;base64," + Buffer.from(pageModuleSource(), "utf8").toString("base64"));

mp.runPython(boot + `
import json
import web_boot
web_boot.boot('/moy/carts', None, 1024, 600, True)
from web_boot import assets_json, step_frame_json, apply_events_json
_ws = web_boot._S["ws"]
`);
const pyJson = (expr) => { mp.runPython(`_pv = ${expr}`); return JSON.parse(mp.globals.get("_pv")); };
page.applyAssets(pyJson("assets_json()"));

function bench(label, n, { dirty = false, pre = null } = {}) {
    const conMs = [], repMs = [], bytes = [];
    let drawn = 0, skipped = 0;
    for (let i = 0; i < n; i++) {
        if (pre) mp.runPython(pre);
        if (dirty) mp.runPython(`_ws._dirty = True`);
        const t0 = process.hrtime.bigint();
        mp.runPython(`_f = step_frame_json(1/60, -1.0)`);
        const t1 = process.hrtime.bigint();
        const f = mp.globals.get("_f");
        conMs.push(Number(t1 - t0) / 1e6);
        if (!f) { skipped++; continue; }
        drawn++;
        bytes.push(f.length);
        const p = JSON.parse(f);
        const r0 = process.hrtime.bigint();
        page.df(p);
        const r1 = process.hrtime.bigint();
        repMs.push(Number(r1 - r0) / 1e6);
        if (page.wantsImages()) page.applyAssets(pyJson("assets_json()"));
    }
    const stat = (a) => {
        if (!a.length) return "n/a";
        const s = [...a].sort((x, y) => x - y);
        const mean = a.reduce((x, y) => x + y, 0) / a.length;
        return `mean ${mean.toFixed(2)} p50 ${s[(s.length / 2) | 0].toFixed(2)} p95 ${s[Math.min(s.length - 1, (s.length * 0.95) | 0)].toFixed(2)}`;
    };
    console.log(`${label}: drawn ${drawn} skipped ${skipped}`
        + `\n  console(ms) ${stat(conMs)}`
        + `\n  replay(ms)  ${stat(repMs)}`
        + `\n  bytes/frame ${bytes.length ? Math.round(bytes.reduce((x, y) => x + y, 0) / bytes.length) : 0}`);
}

// settle the desk
for (let i = 0; i < 90; i++) { mp.runPython(`_f = step_frame_json(1/60, -1.0)`); const f = mp.globals.get("_f"); if (f) page.df(JSON.parse(f)); }

bench("desk quiet", 60);
bench("desk forced-dirty (hover-flip class, desk only)", 60, { dirty: true });

// open the Editor on the first editable cart, Code tab (the surface the owner felt)
mp.runPython(`
for _i, _c in enumerate(_ws.launcher.items):
    if _c.get("path"):
        _ws.launcher.sel = _i
        break
_ws.open_in_editor()
_ws.set_menu_view("code")
`);
for (let i = 0; i < 90; i++) { mp.runPython(`_f = step_frame_json(1/60, -1.0)`); const f = mp.globals.get("_f"); if (f) page.df(JSON.parse(f)); }
console.log("editor state:", JSON.stringify(pyJson(`json.dumps({"view": getattr(_ws, "menu_view", None), "editor": _ws.editor is not None})`)));

bench("editor quiet", 60);
bench("editor forced-dirty (hover-flip class, code tab)", 60, { dirty: true });

// heaviest tabs: sprites + map
for (const tab of ["sprites", "map"]) {
    mp.runPython(`_ws.set_menu_view(${JSON.stringify(tab)})`);
    for (let i = 0; i < 60; i++) { mp.runPython(`_f = step_frame_json(1/60, -1.0)`); const f = mp.globals.get("_f"); if (f) page.df(JSON.parse(f)); }
    bench(`editor ${tab} forced-dirty`, 60, { dirty: true });
}
