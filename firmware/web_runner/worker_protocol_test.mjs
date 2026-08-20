// Drive dist/worker.js through its REAL message protocol by faking the Web Worker
// globals (self + fetch) in node. Verifies boot -> assets -> run -> frames, plus
// input and the dev reload path. Run from firmware/web_runner.
import { readFileSync, existsSync } from "node:fs";
import { pathToFileURL } from "node:url";

const posted = [];
let onmsg = null;
globalThis.self = {
    postMessage: (m) => posted.push(m),
    set onmessage(f) { onmsg = f; },
    get onmessage() { return onmsg; },
};
globalThis.fetch = async (url) => {
    const p = "dist/" + url;
    if (!existsSync(p)) return { ok: false, json: async () => { throw new Error("404 " + url); } };
    const txt = readFileSync(p, "utf-8");
    return { ok: true, json: async () => JSON.parse(txt), text: async () => txt };
};

// worker.js resolves ./micropython.mjs relative to itself, so import it from dist/.
await import(pathToFileURL(process.cwd() + "/dist/worker.js").href);
if (!onmsg) { console.log("FAIL: worker never installed an onmessage handler"); process.exit(1); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const seen = (t) => posted.filter((m) => m.t === t);

let fail = 0;
function ok(name, cond, extra = "") {
    if (cond) console.log("  ok   " + name);
    else { fail++; console.log("  FAIL " + name + (extra ? "  " + extra : "")); }
}

const search = process.argv[2] || "?desktop=1";
await onmsg({ data: { t: "init", search } });

ok("boot produced status lines", seen("status").length >= 3,
   JSON.stringify(seen("status").map((m) => m.s)));
ok("boot reached 'live'", seen("status").some((m) => m.s === "live"));
ok("no error during boot", seen("error").length === 0,
   JSON.stringify(seen("error").map((m) => m.s)));
const assets = seen("assets");
ok("assets pushed exactly once", assets.length === 1);
if (assets.length) {
    const a = JSON.parse(assets[0].json);
    // METADATA only since moycore stage 4: the palette, the font, the cart's
    // sheet/tilemap/images and every shelf cover used to ride here so the page
    // could replay draw commands. The page blits a framebuffer now, so the only
    // things it cannot derive are the surface size, the title, the audio rate,
    // the input hint and the framebuffer's byte order.
    ok("assets carry the page's metadata, and no pixels",
       a.w > 0 && a.h > 0 && typeof a.audio_rate === "number"
       && "swap" in a && !a.palette && !a.font && !a.sheet && !a.images,
       "w=" + a.w + " h=" + a.h + " keys=" + Object.keys(a).join(","));
    ok("desktop tier reports its panel size",
       search.includes("desktop") ? (a.w === 1024 && a.h === 600) : (a.w === 320),
       a.w + "x" + a.h);
}

// Frames must NOT flow before "run" (the page's play gesture gates it).
const before = seen("frame").length;
await sleep(120);
ok("no frames before run", seen("frame").length === before);

await onmsg({ data: { t: "ahead", v: -1 } });
await onmsg({ data: { t: "run" } });
await sleep(600);
const frames = seen("frame");
if (search.includes("cart=")) {
    ok("frames flow after run", frames.length > 10,
       "got " + frames.length + " in 600ms");
}
ok("a frame is parseable metadata + a transferred framebuffer", (() => {
    if (!frames.length) return false;
    const m = frames[frames.length - 1];
    const d = JSON.parse(m.s);
    if (typeof d.paint !== "number") return false;
    // Since moycore stage 4 the pixels ride as a transferred ArrayBuffer beside
    // the metadata; a painted frame must carry one, a skipped one must not.
    return d.paint ? (m.fb instanceof ArrayBuffer && m.fb.byteLength > 0)
                   : !m.fb;
})());
const rate = frames.length / 0.6;
if (search.includes("cart=")) {
    ok("pacing is in the 60fps ballpark, self-driven", rate > 25 && rate < 130,
       rate.toFixed(0) + " frames/s");
} else {
    // No cart: the #44 redraw gate records NOTHING on a static screen, so an idle
    // desk must cost ~no frames. (Asserting 60fps here was a bug in this test.)
    ok("an IDLE desk emits almost no frames (redraw gate)", frames.length <= 4,
       "got " + frames.length + " in 600ms");
}

// Input goes in without throwing, and the console keeps producing frames.
const n0 = seen("frame").length;
await onmsg({ data: { t: "input", json: JSON.stringify({ events: [
    { type: "down", x: 60, y: 120 }, { type: "up" }] }) } });
await sleep(200);
ok("input is accepted without error",
   seen("error").length === 0 && (!search.includes("cart=")
       || seen("frame").length > n0));
ok("still no errors", seen("error").length === 0,
   JSON.stringify(seen("error").map((m) => m.s)));

// Dev reload: refetch carts, restart, push assets again.
await onmsg({ data: { t: "reload" } });
await sleep(200);
ok("reload pushes assets a second time", seen("assets").length === 2,
   "assets pushes: " + seen("assets").length);
ok("no errors after reload", seen("error").length === 0,
   JSON.stringify(seen("error").map((m) => m.s)));

// PACING: simulated time must track the wall clock. A wrong dt changes game SPEED
// without changing the frame RATE, so frame counts cannot catch it -- this is the
// check that would have caught dt being measured from the frame deadline instead of
// from the previous step (owner: "the games run sped up vs webview").
if (search.includes("cart=")) {
    // Sample TWICE and compare the deltas: measuring from the first step folds in
    // warm-up, where a long stall gets clamped by dt's Math.min(0.1, ...) and makes
    // sim legitimately lag wall. Steady-state pacing is what must be 1:1.
    const i0 = seen("clock").length;
    await onmsg({ data: { t: "clock" } });
    await sleep(700);
    await onmsg({ data: { t: "clock" } });
    await sleep(20);
    const a = seen("clock")[i0], b = seen("clock")[i0 + 1];
    const dsim = a && b ? b.sim - a.sim : null;
    const dwall = a && b ? b.wall - a.wall : null;
    const ratio = dwall > 0.3 ? dsim / dwall : null;
    ok("simulated time tracks the wall clock (no speed-up/slow-down)",
       ratio !== null && ratio > 0.95 && ratio < 1.05,
       ratio === null ? "no clock replies" : "sim/wall = " + ratio.toFixed(3)
           + " over " + (dwall || 0).toFixed(2) + "s");
}

console.log("\n" + (fail ? fail + " FAILED" : "all worker protocol checks passed"));
process.exit(fail ? 1 : 0);
