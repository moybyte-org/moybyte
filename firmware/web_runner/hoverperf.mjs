// Tier-0 hover-flip baseline on the wasm desktop (the ui_widgets_2026-08 W1
// number, measured early per the perf review's B3): a hover flip == ws._dirty
// + a full repaint, so forced-dirty frames time the exact cost class without
// coordinate archaeology.
//
// REBASED at moycore stage 4. It used to report three columns -- console
// (record + JSON), page replay, and bytes/frame -- because the console shipped
// draw commands a JS replayer drew. The wasm rasterizes now, so there is no
// replay and no per-frame byte count: what a frame costs is the console
// drawing it, plus a fixed present (one heap copy + one 565->RGBA pass over the
// framebuffer, both independent of what changed). Only the console column
// survives, and it is the one the design argument rests on.
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const RUNNER = dirname(fileURLToPath(import.meta.url));
const { loadConsole } = await import(join(RUNNER, "mpboot.mjs"));
const { mp, boot } = await loadConsole();

mp.runPython(boot + `
import json
import web_boot
web_boot.boot('/moy/carts', None, 1024, 600, True)
from web_boot import assets_json, step_frame_json
_ws = web_boot._S["ws"]
`);

const pyJson = (expr) => { mp.runPython(`_pv = ${expr}`); return JSON.parse(mp.globals.get("_pv")); };

function bench(label, n, { dirty = false, pre = null } = {}) {
    const conMs = [];
    let drawn = 0, skipped = 0;
    for (let i = 0; i < n; i++) {
        if (pre) mp.runPython(pre);
        if (dirty) mp.runPython(`_ws._dirty = True`);
        const t0 = process.hrtime.bigint();
        mp.runPython(`_f = step_frame_json(1/60, -1.0)`);
        const t1 = process.hrtime.bigint();
        const f = mp.globals.get("_f");
        if (!f || !JSON.parse(f).paint) { skipped++; continue; }
        drawn++;
        conMs.push(Number(t1 - t0) / 1e6);
    }
    const stat = (a) => {
        if (!a.length) return "n/a";
        const s = [...a].sort((x, y) => x - y);
        const mean = a.reduce((x, y) => x + y, 0) / a.length;
        return `mean ${mean.toFixed(2)} p50 ${s[(s.length / 2) | 0].toFixed(2)} p95 ${s[Math.min(s.length - 1, (s.length * 0.95) | 0)].toFixed(2)}`;
    };
    console.log(`${label}: painted ${drawn} skipped ${skipped}`
        + `\n  console(ms) ${stat(conMs)}`);
}

// settle the desk
for (let i = 0; i < 90; i++) { mp.runPython(`_f = step_frame_json(1/60, -1.0)`); const f = mp.globals.get("_f"); /* present is fixed-cost; not timed here */ }

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
for (let i = 0; i < 90; i++) { mp.runPython(`_f = step_frame_json(1/60, -1.0)`); const f = mp.globals.get("_f"); /* present is fixed-cost; not timed here */ }
console.log("editor state:", JSON.stringify(pyJson(`json.dumps({"view": getattr(_ws, "menu_view", None), "editor": _ws.editor is not None})`)));

bench("editor quiet", 60);
bench("editor forced-dirty (hover-flip class, code tab)", 60, { dirty: true });

// heaviest tabs: sprites + map
for (const tab of ["sprites", "map"]) {
    mp.runPython(`_ws.set_menu_view(${JSON.stringify(tab)})`);
    for (let i = 0; i < 60; i++) { mp.runPython(`_f = step_frame_json(1/60, -1.0)`); const f = mp.globals.get("_f"); /* present is fixed-cost; not timed here */ }
    bench(`editor ${tab} forced-dirty`, 60, { dirty: true });
}
