// The browser-local store (#193) driven in node: the mode decision, the op
// apply against a FAKE OPFS, and the .moy zip both ways.
//
// node has no OPFS, which is exactly why moy_store.mjs takes the storage
// manager as an argument instead of reaching for `navigator`: the whole apply
// path -- writes, chunked parts, deletes, cart deletes, the path guard -- runs
// here, and Chrome only has to prove the real substrate behaves like the fake
// (tests/test_web_persist_e2e.py). Both write paths are exercised: Chrome uses
// the sync access handle, a browser without one falls back to createWritable.
import { deflateRawSync } from "node:zlib";
import * as store from "./moy_store.mjs";

let fail = 0;
function ok(name, cond, extra = "") {
    if (cond) console.log("  ok   " + name);
    else { fail++; console.log("  FAIL " + name + (extra ? "  " + extra : "")); }
}

const enc = new TextEncoder(), dec = new TextDecoder();

// ---- a fake OPFS ------------------------------------------------------------
function makeDir(sync) {
    const children = new Map();
    const dir = {
        kind: "directory",
        async getDirectoryHandle(name, opts) {
            let c = children.get(name);
            if (!c) {
                if (!opts || !opts.create) throw new Error("NotFoundError: " + name);
                c = makeDir(sync);
                children.set(name, c);
            }
            if (c.kind !== "directory") throw new Error("TypeMismatchError: " + name);
            return c;
        },
        async getFileHandle(name, opts) {
            let c = children.get(name);
            if (!c) {
                if (!opts || !opts.create) throw new Error("NotFoundError: " + name);
                c = makeFile(sync);
                children.set(name, c);
            }
            if (c.kind !== "file") throw new Error("TypeMismatchError: " + name);
            return c;
        },
        async removeEntry(name, opts) {
            const c = children.get(name);
            if (!c) throw new Error("NotFoundError: " + name);
            if (c.kind === "directory" && !(opts && opts.recursive) && c._size())
                throw new Error("InvalidModificationError");
            children.delete(name);
        },
        async *entries() { for (const [k, v] of children) yield [k, v]; },
        _size() { return children.size; },
    };
    return dir;
}

function makeFile(sync) {
    const fh = {
        kind: "file",
        data: new Uint8Array(0),
        async getFile() {
            return { text: async () => dec.decode(fh.data) };
        },
    };
    if (sync) {
        fh.createSyncAccessHandle = async () => ({
            truncate(n) { fh.data = fh.data.slice(0, n); },
            write(bytes, o) {
                const at = (o && o.at) || 0;
                const out = new Uint8Array(Math.max(fh.data.length, at + bytes.length));
                out.set(fh.data, 0);
                out.set(bytes, at);
                fh.data = out;
            },
            flush() { }, close() { },
        });
    } else {
        fh.createWritable = async () => ({
            async write(bytes) { fh.data = bytes; },
            async close() { },
        });
    }
    return fh;
}

const fakeNav = (sync) => {
    const root = makeDir(sync);
    return { storage: { getDirectory: async () => root } };
};

// ---- mode detection ---------------------------------------------------------
const reply = (status) => async () => ({ ok: status >= 200 && status < 300, status });
const boom = async () => { throw new Error("no host"); };

ok("a host that answers GET /sync owns the carts", await store.probeMode(reply(200)) === "board");
ok("a static host gets the browser store",
   await store.probeMode(async (u, o) => ({ ok: false, status: 404 })) === "site");
ok("file:// (no host at all) gets the browser store", await store.probeMode(boom) === "site");
// The compatibility case the second probe exists for: a board whose firmware
// predates the GET marker still ACCEPTS the batch, and must not be read as a
// static host -- that would strand a kid's edits in a browser.
ok("a board with no GET marker is still a board", await store.probeMode(
    async (u, o) => (o && o.method === "POST") ? { ok: true, status: 200 }
                                               : { ok: false, status: 404 }) === "board");
ok("a board that wants a pin is still a board", await store.probeMode(
    async (u, o) => (o && o.method === "POST") ? { ok: false, status: 403 }
                                               : { ok: false, status: 404 }) === "board");

// ---- the store --------------------------------------------------------------
for (const sync of [true, false]) {
    const label = sync ? "sync handle" : "createWritable";
    const s = await store.openStore(fakeNav(sync));
    ok("openStore works (" + label + ")", !!s);
    ok("a fresh store is empty (" + label + ")", await store.isEmpty(s));

    await store.seed(s, {
        "a.moy/manifest.json": '{"title":"A"}',
        "a.moy/main.py": "print(1)",
        "b.moy/manifest.json": '{"title":"B"}',
        // Never seeded: a top-level file is system state, not the kid's work.
        "system.json": "{}",
    });
    ok("seeded store is not empty (" + label + ")", !(await store.isEmpty(s)));
    let all = await store.readAll(s);
    ok("seed round-trips its cart files (" + label + ")",
       all["a.moy/main.py"] === "print(1)" && all["b.moy/manifest.json"] === '{"title":"B"}',
       JSON.stringify(Object.keys(all)));
    ok("a top-level file is never stored (" + label + ")", !("system.json" in all));

    // The op vocabulary, in one batch: an overwrite, a chunked file, a file
    // delete and a whole-cart delete.
    const r = await store.applyOps(s, [
        { p: "a.moy/main.py", t: "print(2)" },
        { p: "a.moy/big.lua", t: "xxx", part: 0 },
        { p: "a.moy/big.lua", t: "yyy", part: 1 },
        { p: "a.moy/big.lua", pub: 1 },
        { p: "a.moy/manifest.json", d: 1 },
        { p: "b.moy", dc: 1 },
    ]);
    ok("every op applied (" + label + ")", r.applied === 6 && !r.errors.length,
       JSON.stringify(r.errors));
    all = await store.readAll(s);
    ok("a write overwrites (" + label + ")", all["a.moy/main.py"] === "print(2)");
    ok("chunks publish as ONE file (" + label + ")", all["a.moy/big.lua"] === "xxxyyy",
       String(all["a.moy/big.lua"]));
    ok("a file delete lands (" + label + ")", !("a.moy/manifest.json" in all));
    ok("a cart delete takes the folder (" + label + ")",
       !Object.keys(all).some((k) => k.startsWith("b.moy/")), JSON.stringify(Object.keys(all)));

    // Nothing that must stay home, and nothing that escapes the store.
    const bad = await store.applyOps(s, [
        { p: "../escape.py", t: "x" },
        { p: "a.moy/journal/1.json", t: "x" },
        { p: "a.moy/journal.jsonl", t: "x" },
        { p: "a.moy/main.py.bak", t: "x" },
        { p: "system.json", t: "x" },
        { p: "a.moy", dc: 1, p2: 0 },
    ]);
    ok("traversal, journals, .bak and top-level files are all refused (" + label + ")",
       bad.applied === 1 && bad.errors.length === 5, JSON.stringify(bad.errors));
}

// ---- the .moy zip -----------------------------------------------------------
const files = [
    { name: "star.moy/manifest.json", data: enc.encode('{"title":"Star"}') },
    { name: "star.moy/main.py", data: enc.encode("def _draw():\n    cls(0)\n") },
];
const zip = store.zipStore(files);
const back = await store.unzip(zip);
ok("a stored zip round-trips its names", back.map((f) => f.name).join(",")
   === files.map((f) => f.name).join(","), back.map((f) => f.name).join(","));
ok("a stored zip round-trips its bytes",
   dec.decode(back[1].data) === "def _draw():\n    cls(0)\n", dec.decode(back[1].data));
ok("the top directory is the cart folder", store.zipTopDir(back) === "star.moy");
ok("a flat zip has no top directory",
   store.zipTopDir([{ name: "manifest.json" }, { name: "main.py" }]) === null);

// DEFLATED input: most real zips are, so read support is not optional even
// though we only ever WRITE stored entries.
function deflatedZip(name, text) {
    const nameB = enc.encode(name), data = deflateRawSync(Buffer.from(text));
    const crc = store.crc32(enc.encode(text)), usize = enc.encode(text).length;
    const local = new Uint8Array(30 + nameB.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true); lv.setUint16(4, 20, true);
    lv.setUint16(8, 8, true); lv.setUint32(14, crc, true);
    lv.setUint32(18, data.length, true); lv.setUint32(22, usize, true);
    lv.setUint16(26, nameB.length, true);
    local.set(nameB, 30);
    const cen = new Uint8Array(46 + nameB.length);
    const cv = new DataView(cen.buffer);
    cv.setUint32(0, 0x02014b50, true); cv.setUint16(6, 20, true);
    cv.setUint16(10, 8, true); cv.setUint32(16, crc, true);
    cv.setUint32(20, data.length, true); cv.setUint32(24, usize, true);
    cv.setUint16(28, nameB.length, true); cv.setUint32(42, 0, true);
    cen.set(nameB, 46);
    const end = new Uint8Array(22);
    const ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, 1, true); ev.setUint16(10, 1, true);
    ev.setUint32(12, cen.length, true);
    ev.setUint32(16, local.length + data.length, true);
    const out = new Uint8Array(local.length + data.length + cen.length + end.length);
    let at = 0;
    for (const p of [local, data, cen, end]) { out.set(p, at); at += p.length; }
    return out;
}
const defl = await store.unzip(deflatedZip("x.moy/main.py", "hello deflate\n"));
ok("a DEFLATED zip reads too", defl.length === 1 && dec.decode(defl[0].data) === "hello deflate\n",
   defl.length ? dec.decode(defl[0].data) : "no entries");

// ---- naming -----------------------------------------------------------------
const have = { "star.moy": 1, "star_2.moy": 1 };
ok("an imported cart takes the store's duplicate-naming rule",
   store.uniqueCartDir((n) => n in have, "star") === "star_3.moy",
   store.uniqueCartDir((n) => n in have, "star"));
ok("a free name is taken as is", store.uniqueCartDir(() => false, "star") === "star.moy");
ok("a zip file name becomes a legal folder base",
   store.cartBase("My Game!.moy.zip") === "My_Game");

console.log("\n" + (fail ? fail + " FAILED" : "all store checks passed"));
process.exit(fail ? 1 : 0);
