// The /assets payload must never be STALE in its non-image fields.
//
// assets_json memoises the serialised payload (json.dumps over ~644KB of images
// is the whole cost) and ships images incrementally. It also used to shortcut
// "no new images" straight to the previous payload -- which quietly shipped that
// payload's cart title, palette, sheet, tilemap and INPUT HINT too.
//
// The visible symptom (owner, 2026-07-31, phone, Brick Siege): the page
// re-fetches assets while a cart plays, gets a payload whose input hint is the
// LAUNCHER's (null), and null means "show every control" -- so the ⌨ button
// appears; the next frame carries the cart's real hint (buttons-only) and hides
// it again. A button blinking every couple of seconds.
import { loadConsole } from "./mpboot.mjs";

const { mp, py, boot } = await loadConsole();

let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
    if (cond) { pass++; console.log("  ok   " + name); }
    else { fail++; console.log("  FAIL " + name + (extra ? "  " + extra : "")); }
};

// Boot on the LAUNCHER (no cart): the payload that used to get stuck.
mp.runPython(boot + `
import json, web_boot
web_boot.boot('/moy/carts', None, 320, 240, False)
from web_boot import assets_json, step_frame_json, open_cart
_ws = web_boot._S["ws"]
`);
py(`json.dumps(json.loads(assets_json()).get("input"))`);   // seed the memo
ok("on the launcher every control is offered (hint null)",
    py(`json.dumps(json.loads(assets_json()).get("input"))`) === null);

// Now play a BUTTONS-ONLY cart. Its frames carry ["buttons"]; the assets payload
// the page re-fetches must agree, or the ⌨ button blinks between the two.
mp.runPython(`open_cart("brick_siege.moy")`);
for (let i = 0; i < 8; i++) mp.runPython(`step_frame_json(1/60, -1.0)`);
const frameHint = py(`json.dumps(json.loads(step_frame_json(1/60, -1.0) or "{}").get("input"))`);
ok("the running cart's FRAMES carry its manifest hint",
    JSON.stringify(frameHint) === JSON.stringify(["buttons"]), JSON.stringify(frameHint));

const assetHint = py(`json.dumps(json.loads(assets_json()).get("input"))`);
ok("... and an asset RE-FETCH agrees (no stale payload)",
    JSON.stringify(assetHint) === JSON.stringify(frameHint),
    `assets ${JSON.stringify(assetHint)} vs frames ${JSON.stringify(frameHint)}`);

// The other fields the shortcut could strand: the title must follow the cart.
ok("the payload's cart title follows the running cart",
    py(`json.dumps(json.loads(assets_json()).get("cart"))`) === "Brick Siege",
    JSON.stringify(py(`json.dumps(json.loads(assets_json()).get("cart"))`)));

// Repeated re-fetches (the imgWant latch does this every second) stay stable --
// that steady state is what the blinking button actually violated.
let hints = new Set();
for (let i = 0; i < 5; i++) {
    for (let k = 0; k < 4; k++) mp.runPython(`step_frame_json(1/60, -1.0)`);
    hints.add(JSON.stringify(py(`json.dumps(json.loads(assets_json()).get("input"))`)));
}
ok("repeated re-fetches never flip the hint", hints.size === 1,
    JSON.stringify([...hints]));

console.log(fail ? `assets hint: ${fail} FAILED, ${pass} ok` : `assets hint: ALL OK (${pass})`);
process.exit(fail ? 1 : 0);
