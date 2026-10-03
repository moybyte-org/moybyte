// installCartEngine's open(), against a FAKE VM that grows on its first
// malloc -- the exact shape of 2026-10-03's bug: `bytes` (web_open's
// HEAPU8.subarray, a view into the VM's own memory) must still be the one
// WebAssembly.Module() compiles, even though the FIRST open ever also builds
// the adapter table (adapters(): C.malloc/C.natives/C.free), and building it
// can grow the VM's memory. Growing a WebAssembly.Memory DETACHES its old
// ArrayBuffer, so a view taken before the growth reads back length 0 --
// "BufferSource argument is empty" -- on whichever cart happens to be first
// through this path when the real VM's heap sits close enough to its
// boundary (which is a question of overall layout, not of this cart: it was
// lost to an unrelated size change elsewhere in the frozen console, on a
// fixture that happened to need its very first write-path turn before
// anything else in that worker had opened a cart).
//
// `ArrayBuffer.prototype.transfer()` (Node 22+) is the stand-in for that
// detach: it moves the content to a new buffer and leaves the old one at
// byteLength 0, precisely what a real WebAssembly.Memory.grow() does to
// every view still pointing at the memory it replaced. Run from
// firmware/web_runner, against dist/worker.js (needs a build: worker.js
// resolves ./micropython.mjs relative to itself, so it only loads from
// dist/, the same reason worker_protocol_test.mjs does).
import { readFileSync, existsSync } from "node:fs";
import { pathToFileURL } from "node:url";

globalThis.self = { postMessage() {}, set onmessage(f) {}, get onmessage() { return null; } };
globalThis.fetch = async (url) => {
    const p = "dist/" + url;
    if (!existsSync(p)) return { ok: false, json: async () => { throw new Error("404 " + url); } };
    const txt = readFileSync(p, "utf-8");
    return { ok: true, json: async () => JSON.parse(txt), text: async () => txt };
};

const { installCartEngine } = await import(pathToFileURL(process.cwd() + "/dist/worker.js").href);

let fail = 0;
function ok(name, cond, extra = "") {
    if (cond) console.log("  ok   " + name);
    else { fail++; console.log("  FAIL " + name + (extra ? "  " + extra : "")); }
}

// A minimal valid module -- no imports (so an empty adapter table satisfies
// it), one exported memory (open() reads instance.exports.memory). Hand-
// assembled: \0asm, version 1, a memory section (one memory, min 1 page) and
// an export section ("m" -> memory 0). What this test is pinning does not
// need _init/_update/_draw -- those are the Player's concern once open()
// has already returned "".
const MODULE_BYTES = new Uint8Array([
    0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,       // \0asm, version 1
    0x05, 0x03, 0x01, 0x00, 0x01,                          // memory section: 1 memory, min 1
    0x07, 0x05, 0x01, 0x01, 0x6d, 0x02, 0x00,              // export section: "m" -> memory 0
]);

function freshEngine() {
    // One page of VM memory, the module bytes written in around the middle --
    // like web_open's malloc'd file buffer, somewhere past address 0.
    const M = { HEAPU8: new Uint8Array(new ArrayBuffer(65536)) };
    M.HEAPU8.set(MODULE_BYTES, 256);
    const bytes = M.HEAPU8.subarray(256, 256 + MODULE_BYTES.length);   // web_open's view
    installCartEngine(M);

    let grown = false;
    const vm = {
        // The FIRST allocation the engine's own boot makes (adapters()'s
        // "how many natives" probe). Simulate a VM whose heap had no more
        // room: grow it, which detaches every view -- including `bytes` --
        // still pointing at the buffer M.HEAPU8 used to hold.
        malloc(n) {
            if (!grown) {
                grown = true;
                M.HEAPU8 = new Uint8Array(M.HEAPU8.buffer.transfer(131072));
            }
            return 0;
        },
        natives(cp) {
            // Zero native symbols: adapters()'s own loop then does nothing,
            // so this fake needs no C.fn/C.trapped/C.itemTrap at all.
            new Uint32Array(M.HEAPU8.buffer)[cp >> 2] = 0;
            return 0;
        },
        free(cp) { },
    };
    return { M, bytes, vm };
}

{
    const { M, bytes, vm } = freshEngine();
    ok("the fixture's view is the module bytes before anything runs",
       bytes.byteLength === MODULE_BYTES.length, "byteLength=" + bytes.byteLength);
    const err = M.moyEngine.open(bytes, vm);
    // The fixture's own malloc really did detach `bytes`'s buffer -- if this
    // reads nonzero, the test exercised nothing and the pass above means
    // nothing either.
    ok("the adapter-table build did grow the VM (the hazard actually fired)",
       bytes.byteLength === 0, "byteLength=" + bytes.byteLength);
    ok("open() still compiled and instantiated across that growth",
       err === "", "err=" + JSON.stringify(err));
    ok("the success path ran all the way through (moyCart bound)",
       M.moyCart != null, "err=" + JSON.stringify(err));
}

console.log("\n" + (fail ? fail + " FAILED" : "all cart engine checks passed"));
process.exit(fail ? 1 : 0);
