// The console's own documents across a reload, on the REAL console: dist/
// worker.js and its wasm, a static host (no /sync), and a fake OPFS that
// outlives the process the way OPFS outlives a tab. Run from
// firmware/web_runner, once per page load, by tests/test_web_system_persist.py:
//
//   node system_persist_test.mjs <search> <tree.json>
//
// The tree file is the origin's OPFS before the load (moy_store's layout:
// carts/, files/, system/); the load's own writes are put back into it once
// the sweep has persisted them, and the console's `state` is printed as
// `STATE <json>` for the driver to read.
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { dumpTree, loadTree, fakeNav } from "./fake_opfs.mjs";

const [search, treePath] = process.argv.slice(2);
const root = await loadTree(JSON.parse(readFileSync(treePath, "utf-8")));
Object.defineProperty(globalThis, "navigator",
                      { value: fakeNav(true, root), configurable: true });

const posted = [];
let onmsg = null;
globalThis.self = {
    postMessage: (m) => posted.push(m),
    set onmessage(f) { onmsg = f; },
    get onmessage() { return onmsg; },
};
// A static host: the bundle's files, and a 404 for everything else -- /sync
// included, which is what puts the page in site mode.
globalThis.fetch = async (url) => {
    const p = "dist/" + String(url).split("?")[0];
    if (!existsSync(p)) return { ok: false, status: 404, json: async () => null };
    const txt = readFileSync(p, "utf-8");
    return { ok: true, status: 200, json: async () => JSON.parse(txt), text: async () => txt };
};

await import(pathToFileURL(process.cwd() + "/dist/worker.js").href);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const seen = (t) => posted.filter((m) => m.t === t);
const fail = (why) => { console.log("FAIL " + why); process.exit(1); };

await onmsg({ data: { t: "init", search } });
if (seen("error").length) fail(JSON.stringify(seen("error").map((m) => m.s)));
const said = seen("persist")[0];
if (!said || said.mode !== "site") fail("not in site mode: " + JSON.stringify(said));
console.log("PERSIST " + JSON.stringify(said));
await onmsg({ data: { t: "run" } });

// Let the sweep (~1/s) persist what the load wrote, until two pumps in a row
// have nothing left to say.
let quiet = 0, last = -1;
for (let i = 0; i < 40 && quiet < 3; i++) {
    await sleep(500);
    const n = seen("persist").length;
    quiet = n === last ? quiet + 1 : 0;
    last = n;
}
if (seen("error").length) fail(JSON.stringify(seen("error").map((m) => m.s)));
console.log("BATCHES " + JSON.stringify(seen("persist").map((m) => m.d)));
await onmsg({ data: { t: "state" } });
const st = seen("state");
if (!st.length) fail("no state");
console.log("STATE " + st[st.length - 1].json);
writeFileSync(treePath, JSON.stringify(await dumpTree(root)));
process.exit(0);
