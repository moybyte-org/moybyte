// Execute the REAL replayer slice (web_view_page.py lines 246..386 -- the draw
// state, primitives and rep() dispatch) in node with browser globals stubbed, and
// assert the #175 `view` op places, clips and composes correctly. This is the half
// the node/wasm probes never touch: they exercise the Python emitter, not the JS.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

// Resolved from THIS file, like every other harness here (pageshot/browsershot/
// mpboot). An absolute path shipped here once and passed on every machine that
// had the repo at that path -- i.e. mine -- and failed on the first CI run.
const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const py = readFileSync(join(REPO, "runtime/web_view_page.py"), "utf-8");
const lines = py.split("\n");
const from = lines.findIndex((l) => l.startsWith("var caX=0,caY=0"));
const to = lines.findIndex((l) => l.startsWith("function blit()"));
if (from < 0 || to < 0) throw new Error("slice markers not found");
const js = lines.slice(from, to).join("\n");

const W0 = 64, H0 = 32;                       // small canvas: easy to reason about
const harness = `
let W = ${W0}, H = ${H0};
let idx = new Uint8Array(W * H);
const ATL = {}, IMG = {};        // LAY is declared inside the slice itself
let SHEET = null, TM = null, imgWant = false;
const HUD = { unknown: 0 };
const PAL = []; for (let i = 0; i < 64; i++) PAL.push([i, i, i]);
const FONT = null;
function atob(s) { return Buffer.from(s, "base64").toString("binary"); }
${js}
export { rep, idx, W, H, put, fr };
export function reset() { idx = new Uint8Array(W * H); rs(); }
export function get(x, y) { return idx[y * W + x]; }
export function count(c) { let n = 0; for (const v of idx) if (v === c) n++; return n; }
export function viewOn() { return vOn; }
export function bounds() { return [vOX, vOY, vS, vCW, vCH, vOn]; }
// getA() grows W/H from assets AFTER this script loads (320x240 -> 1024x600), and
// dfl() changes them per layer. Anything that CACHED identity bounds at load time
// is stale from here on -- that shipped once and pinned the whole desktop into its
// top-left 320x240. This is how the test sees it.
export function resize(w, h) { W = w; H = h; idx = new Uint8Array(w * h); rs(); }
`;
const mod = await import("data:text/javascript;base64," +
    Buffer.from(harness, "utf8").toString("base64"));

let pass = 0, fail = 0;
function ok(name, cond, extra = "") {
    if (cond) { pass++; console.log("  ok   " + name); }
    else { fail++; console.log("  FAIL " + name + (extra ? "  " + extra : "")); }
}

// 1. No view: a rect lands where it is asked to, cls fills everything.
mod.reset();
mod.rep([["cls", 3]]);
ok("cls with no view fills the whole canvas", mod.count(3) === W0 * H0,
   "got " + mod.count(3) + "/" + W0 * H0);
mod.reset();
mod.rep([["rect", 2, 3, 4, 5, 9]]);
ok("rect unshifted at (2,3)", mod.get(2, 3) === 9 && mod.get(1, 3) === 0);

// 2. View: the SAME rect shifts by the view origin.
mod.reset();
mod.rep([["view", 10, 4, 1, 20, 10], ["rect", 2, 3, 4, 5, 9], ["view"]]);
ok("view shifts rect to (12,7)", mod.get(12, 7) === 9, "got " + mod.get(12, 7));
ok("view leaves origin untouched", mod.get(2, 3) === 0);

// 3. cls under a view fills ONLY the view rect (this is the desk-bleed guard).
mod.reset();
mod.rep([["view", 10, 4, 1, 20, 10], ["cls", 5], ["view"]]);
ok("cls under view fills exactly 20x10", mod.count(5) === 200, "got " + mod.count(5));
ok("cls under view starts at the view origin", mod.get(10, 4) === 5 && mod.get(9, 4) === 0);

// 4. A cart drawing OUTSIDE its window is clipped away, not bled onto the desk.
mod.reset();
mod.rep([["view", 10, 4, 1, 20, 10], ["rect", 0, 0, 60, 30, 7], ["view"]]);
ok("oversized rect clipped to the window", mod.count(7) === 200, "got " + mod.count(7));

// 5. The cart's own camera COMPOSES with the view base (does not clobber it).
mod.reset();
mod.rep([["view", 10, 4, 1, 20, 10], ["camera", 2, 1], ["rect", 2, 3, 2, 2, 6], ["view"]]);
ok("cart camera composes: (2,3)-cam(2,1)+view(10,4) -> (10,6)",
   mod.get(10, 6) === 6, "got " + mod.get(10, 6));

// 6. A cart's clip INTERSECTS the view, it cannot widen it.
mod.reset();
mod.rep([["view", 10, 4, 1, 20, 10], ["clip", 0, 0, 60, 30],
         ["rect", 0, 0, 60, 30, 4], ["view"]]);
ok("cart clip cannot widen the view", mod.count(4) === 200, "got " + mod.count(4));

// 7. reset_state must NOT clear the view (it is WM-owned, outside cart state).
mod.reset();
mod.rep([["view", 10, 4, 1, 20, 10], ["reset_state"], ["rect", 0, 0, 2, 2, 8], ["view"]]);
ok("reset_state keeps the view", mod.get(10, 4) === 8 && mod.get(0, 0) === 0,
   "at(10,4)=" + mod.get(10, 4) + " at(0,0)=" + mod.get(0, 0));

// 8. Identity restore.
mod.reset();
mod.rep([["view", 10, 4, 1, 20, 10], ["view"], ["rect", 2, 3, 1, 1, 2]]);
ok("view() restores identity", mod.get(2, 3) === 2 && !mod.viewOn());

// 9. A deflayer inside a view renders in its OWN space and restores the view.
mod.reset();
mod.rep([["view", 10, 4, 1, 20, 10],
         ["deflayer", 1, 8, 8, [["cls", 1]]],
         ["rect", 0, 0, 2, 2, 8], ["view"]]);
ok("deflayer restores the view afterwards", mod.get(10, 4) === 8,
   "got " + mod.get(10, 4));

// 10. REGRESSION: identity bounds must follow the CURRENT canvas, never the W/H
// captured when the script loaded. getA() grows the canvas from 320x240 to the
// desktop's 1024x600 after load; caching vR/vB at load time pinned every draw into
// the top-left 320x240 of the desktop (owner-visible: "all bunched up in a small
// upper left corner"). The earlier tests could not see it because this harness sets
// W/H BEFORE loading the slice.
mod.resize(128, 96);
mod.rep([["rect", 100, 80, 10, 10, 5]]);
ok("draw beyond the load-time bounds after a resize", mod.get(104, 84) === 5,
   "got " + mod.get(104, 84));
mod.rep([["cls", 2]]);
ok("cls after a resize fills the NEW canvas", mod.count(2) === 128 * 96,
   "got " + mod.count(2) + "/" + 128 * 96);
mod.rep([["clip", 0, 0, 128, 96], ["rect", 100, 80, 4, 4, 6]]);
ok("clip after a resize does not clamp to the old bounds", mod.get(101, 81) === 6);

// 11. INTEGER SCALE: a view with scale S magnifies the whole span, so a windowed
// game FILLS its window instead of sitting 1:1 in a corner (owner: "zoom is still
// wrong on the desktop view").
mod.resize(64, 32);
mod.rep([["view", 4, 2, 3, 8, 6], ["rect", 0, 0, 8, 6, 5], ["view"]]);
ok("scale 3 fills 8x6 -> 24x18 px", mod.count(5) === 24 * 18,
   "got " + mod.count(5) + "/" + 24 * 18);
ok("scaled rect starts at the view origin",
   mod.get(4, 2) === 5 && mod.get(3, 2) === 0 && mod.get(4, 1) === 0);
mod.reset();
mod.rep([["view", 4, 2, 3, 8, 6], ["pix", 1, 1, 6], ["view"]]);
ok("a scaled cart pixel is a 3x3 block at (7,5)",
   mod.count(6) === 9 && mod.get(7, 5) === 6, "count " + mod.count(6));
mod.reset();
mod.rep([["view", 4, 2, 3, 8, 6], ["cls", 4], ["view"]]);
ok("cls under a scaled view fills the scaled surface", mod.count(4) === 24 * 18,
   "got " + mod.count(4));
mod.reset();
mod.rep([["view", 4, 2, 3, 8, 6], ["rect", 0, 0, 99, 99, 7], ["view"]]);
ok("scaled view still clips to its surface", mod.count(7) === 24 * 18,
   "got " + mod.count(7));

// 12. LAYER BLITS UNDER A VIEW (the owner's 2026-07-31 screenshot: sakura /
// Sky Run / Hop Quest / Letter Blitz -- every cart that uses make_layer --
// smeared across the whole desktop). draw_layer/blit_strip copy already-mapped
// layer pixels; they must still PLACE, SCALE and CLIP like every primitive.
// A 8x6 layer, each pixel = colour 9.
const LCMDS = [["rect", 0, 0, 8, 6, 9]];
mod.resize(64, 32);
mod.rep([["deflayer", 7, 8, 6, LCMDS]]);          // define layer id 7

// blit_strip (the "full" form) at cart (0,0) inside a 1:1 view.
mod.reset();
mod.rep([["view", 10, 4, 1, 8, 6], ["blit_layer", 7, 0, 0, "full"], ["view"]]);
ok("blit_strip lands at the view origin",
   mod.get(10, 4) === 9 && mod.get(9, 4) === 0 && mod.get(10, 3) === 0);
ok("blit_strip covers exactly the layer", mod.count(9) === 48,
   "got " + mod.count(9) + "/48");

// ...and must not escape the view surface.
mod.reset();
mod.rep([["view", 10, 4, 1, 4, 3], ["blit_layer", 7, 0, 0, "full"], ["view"]]);
ok("blit_strip clips to the cart surface", mod.count(9) === 12,
   "got " + mod.count(9) + "/12");

// draw_layer's window copy fills the cart surface, placed at the view origin.
mod.reset();
mod.rep([["view", 20, 8, 1, 8, 6], ["blit_layer", 7, 0, 0], ["view"]]);
ok("draw_layer window lands at the view origin",
   mod.get(20, 8) === 9 && mod.get(19, 8) === 0);
ok("draw_layer window fills the cart surface only", mod.count(9) === 48,
   "got " + mod.count(9) + "/48");

// SCALED: the whole layer magnifies with the view, like every other verb.
mod.reset();
mod.rep([["view", 4, 2, 3, 8, 6], ["blit_layer", 7, 0, 0, "full"], ["view"]]);
ok("scaled blit_strip fills 24x18", mod.count(9) === 24 * 18,
   "got " + mod.count(9) + "/" + 24 * 18);
ok("scaled blit_strip starts at the view origin",
   mod.get(4, 2) === 9 && mod.get(3, 2) === 0);
mod.reset();
mod.rep([["view", 4, 2, 3, 8, 6], ["blit_layer", 7, 0, 0], ["view"]]);
ok("scaled draw_layer window fills 24x18", mod.count(9) === 24 * 18,
   "got " + mod.count(9) + "/" + 24 * 18);

// No view: identity behaviour is byte-identical to before the fix (the handheld
// tier and the device path must not move).
mod.reset();
mod.rep([["blit_layer", 7, 0, 0, "full"]]);
ok("no view: blit_strip still lands at the origin",
   mod.get(0, 0) === 9 && mod.count(9) === 48, "count " + mod.count(9));
mod.reset();
mod.rep([["blit_layer", 7, 2, 1, "full"]]);
ok("no view: blit_strip honours its offset",
   mod.get(2, 1) === 9 && mod.get(1, 1) === 0 && mod.count(9) === 48);

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
