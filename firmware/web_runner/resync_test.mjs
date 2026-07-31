// Dropped-frame RECOVERY (the 2026-07-31 tablet bug), at the protocol level.
//
// page_tail's rAF loop keeps only the newest frame, so a main thread that misses
// a beat DISCARDS one -- which the #76 delta does not tolerate: it ships
// {"same":1} for surfaces it believes the client holds, so the frame that
// carried a surface in full is the only chance the page gets. Lose it and every
// later frame says "same" while the page replays a stale cache. On a desktop the
// moving mouse hides this (pointer motion keeps dirtying the console); on a
// touch tablet nothing moves, so the screen just stays wrong until you drag.
//
// The contract this pins: after the page reports a drop (worker `resync` ->
// web_boot.request_keyframe), the very next frame must ship every surface in
// FULL. And without that call it must NOT -- otherwise the test proves nothing.
import { loadConsole } from "./mpboot.mjs";

const { mp, boot } = await loadConsole();

mp.runPython(boot + `
import json, web_boot
web_boot.boot('/moy/carts', None, 1024, 600, True)
from web_boot import assets_json, step_frame_json
_ws = web_boot._S["ws"]
assets_json()
for _ in range(40): step_frame_json(1/60, -1.0)
`);

function frame() {
    mp.runPython(`_f = step_frame_json(1/60, -1.0)`);
    const f = mp.globals.get("_f");
    return f ? JSON.parse(f) : null;
}
function surfaces(f) {
    return (f && f.surfaces ? f.surfaces : []).filter((s) => s.id !== "_defs");
}
const settle = (n) => { for (let i = 0; i < n; i++) frame(); };

let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
    if (cond) { pass++; console.log("  ok   " + name); }
    else { fail++; console.log("  FAIL " + name + (extra ? "  " + extra : "")); }
};

// 1. A world flip ships the new world's surfaces in FULL (the frame a slow page
//    is most likely to drop -- it is the big one).
mp.runPython(`_ws.open_library()`);
let flip = null;
for (let i = 0; i < 6 && !flip; i++) { const f = frame(); if (f && surfaces(f).some((s) => !s.same)) flip = f; }
ok("the world flip ships full surface streams", flip !== null
    && surfaces(flip).filter((s) => !s.same).length >= 2,
    flip ? JSON.stringify(surfaces(flip).map((s) => s.id + (s.same ? "=" : ":full"))) : "no flip frame");
settle(8);

// 2. DROP the flip: go back, flip again, and throw the frames away like the page
//    does. Without a resync the console keeps saying "same" -- the stranded state.
mp.runPython(`_ws.open_desk()`);
settle(8);
mp.runPython(`_ws.open_library()`);
for (let i = 0; i < 3; i++) frame();                  // dropped by the page
let refreshed = 0, pushed = 0;
for (let i = 0; i < 6; i++) {
    const f = frame();
    if (!f) continue;                       // the redraw gate: nothing pushed at all
    pushed++;
    if (surfaces(f).some((s) => !s.same)) refreshed++;
}
// Nothing comes back on its own: the console either pushes nothing (a settled
// screen) or pushes "same" for everything. Either way the page is stuck showing
// the world it was in -- which is the bug as the owner sees it.
ok("without a resync nothing re-ships (the page stays stranded)", refreshed === 0,
    `pushed ${pushed} frame(s), ${refreshed} carried a full surface`);

// 3. Now the recovery the page requests on a drop: the NEXT frame is full again.
mp.runPython(`web_boot.request_keyframe()`);
let reseed = null;
for (let i = 0; i < 4 && !reseed; i++) { const f = frame(); if (f && surfaces(f).some((s) => !s.same)) reseed = f; }
ok("request_keyframe re-ships every surface in full", reseed !== null
    && surfaces(reseed).length > 0 && surfaces(reseed).every((s) => !s.same),
    reseed ? JSON.stringify(surfaces(reseed).map((s) => s.id + (s.same ? "=" : ":full"))) : "no keyframe");

// 4. ... and delta encoding resumes right after (the recovery is one frame, not a
//    permanent full-frame mode -- that would undo #76's whole point).
settle(2);
const quiet = surfaces(frame() || {});
ok("delta encoding resumes after the keyframe",
    quiet.length === 0 || quiet.every((s) => s.same),
    JSON.stringify(quiet.map((s) => s.id + (s.same ? "=" : ":full"))));

console.log(fail ? `resync: ${fail} FAILED, ${pass} ok` : `resync: ALL OK (${pass})`);
process.exit(fail ? 1 : 0);
