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
// CRASH, when set, counts every change the fake makes to its contents and
// throws once it has allowed `left` of them -- a reload at that exact point,
// with the store left as far as it got (truncate and write are two changes,
// as they are on a real sync access handle).
let CRASH = null;
function change() {
    if (CRASH && CRASH.left-- <= 0) throw new Error("the page went away here");
}

function makeDir(sync) {
    const children = new Map();
    const dir = {
        kind: "directory",
        async getDirectoryHandle(name, opts) {
            let c = children.get(name);
            if (!c) {
                if (!opts || !opts.create) throw new Error("NotFoundError: " + name);
                change();
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
                change();
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
            // A recursive removal is not atomic on a real filesystem: entry by
            // entry, so a reload can land half way through one.
            if (c.kind === "directory") {
                const inner = [];
                for await (const [k] of c.entries()) inner.push(k);
                for (const k of inner) await c.removeEntry(k, { recursive: true });
            }
            change();
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
            return { text: async () => dec.decode(fh.data),
                     arrayBuffer: async () => fh.data.slice().buffer };
        },
    };
    if (sync) {
        fh.createSyncAccessHandle = async () => ({
            truncate(n) { change(); fh.data = fh.data.slice(0, n); },
            write(bytes, o) {
                change();
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
            async write(bytes) { change(); fh.data = bytes; },
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

// ---- asking the browser to keep it ------------------------------------------
// Every shape a real navigator turns up in, because all of them are supported
// states and NONE of them may throw: an origin already durable, a browser that
// grants, one that refuses, a Worker (persisted/estimate but no persist -- the
// context worker.js actually runs in), an API that throws, and no storage at all.
const nav = (storage) => ({ storage });
const already = await store.requestPersistence(nav({
    persisted: async () => true,
    persist: async () => { throw new Error("must not be asked again"); },
    estimate: async () => ({ usage: 2097152, quota: 10737418240 }),
}));
ok("an origin already durable is not re-asked", already.state === "granted", already.state);
ok("the estimate rides along", already.usage === 2097152 && already.quota === 10737418240);
ok("a granted origin reads as kept, with its usage",
   store.storageNote(already) === "kept 2.0MB/10240MB", store.storageNote(already));

const granted = await store.requestPersistence(nav({
    persisted: async () => false, persist: async () => true }));
ok("a browser that grants says granted", granted.state === "granted");
ok("no estimate means no numbers, not zeroes", store.storageNote(granted) === "kept",
   store.storageNote(granted));

const denied = await store.requestPersistence(nav({
    persisted: async () => false, persist: async () => false,
    estimate: async () => ({ usage: 0, quota: 10737418240 }) }));
ok("a browser that refuses says denied", denied.state === "denied");
ok("a denied store is EVICTABLE in the page's words",
   store.storageNote(denied) === "evictable 0.0MB/10240MB", store.storageNote(denied));

// The one that costs a session if it is read wrong: storage.persist() is
// [Exposed=Window], so the worker's own navigator has no `persist` at all.
// That is "cannot ask here", NOT "was refused" -- the page is asked instead.
const inWorker = await store.requestPersistence(nav({
    persisted: async () => false, getDirectory: async () => null,
    estimate: async () => ({ usage: 0, quota: 1073741824 }) }));
ok("a context with no persist() is unsupported, not denied",
   inWorker.state === "unsupported", inWorker.state);
ok("...and says so rather than claiming a refusal",
   store.storageNote(inWorker) === "evictable (cannot ask here) 0.0MB/1024MB",
   store.storageNote(inWorker));

const angry = await store.requestPersistence(nav({
    persisted: async () => { throw new Error("site data blocked"); },
    persist: async () => { throw new Error("site data blocked"); },
    estimate: async () => { throw new Error("site data blocked"); } }));
ok("an API that throws is a state, not a crash", angry.state === "unsupported");
ok("no storage manager at all is a state too",
   (await store.requestPersistence({})).state === "unsupported"
   && (await store.requestPersistence(null)).state === "unsupported");

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

    // Nothing that escapes the store, and nothing crash-safety leaves behind.
    const bad = await store.applyOps(s, [
        { p: "../escape.py", t: "x" },
        { p: "a.moy/main.py.bak", t: "x" },
        { p: "a.moy/main.py.tmp", t: "x" },
        { p: "system.json", t: "x" },
    ]);
    ok("traversal, .bak/.tmp and top-level files are all refused (" + label + ")",
       bad.applied === 0 && bad.errors.length === 4, JSON.stringify(bad.errors));

    // ...but THE JOURNAL LANDS (2026-08-25). In mode 1 this store is of record,
    // so the kid's undo history has to live here or die at the next reload --
    // the whole of #193's "with its undo history". It used to be refused, on
    // the wire's rule, which is a different question with a different answer.
    const jr = await store.applyOps(s, [
        { p: "a.moy/journal/journal.jsonl", t: '{"seq": 1}\n' },
        { p: "a.moy/journal/s/0001-main.py", t: "print(1)\n" },
        { p: "a.moy/journal/cursor.json", t: '{"seq": 1}' },
    ]);
    const back = await store.readAll(s);
    ok("the journal lands in the local store and reads back (" + label + ")",
       jr.applied === 3 && !jr.errors.length
       && back["a.moy/journal/journal.jsonl"] === '{"seq": 1}\n'
       && back["a.moy/journal/s/0001-main.py"] === "print(1)\n",
       JSON.stringify(jr.errors) + " " + JSON.stringify(Object.keys(back)));
}

// ---- a cart's cover crosses as BYTES (SPEC.md 3.6) ---------------------------
{
    const s = await store.openStore(fakeNav(true));
    const png = new Uint8Array(40000);
    for (let i = 0; i < png.length; i++) png[i] = (i * 37 + (i >> 9)) & 255;
    const b64 = store.toBase64(png);
    await store.seed(s, { "c.moy/manifest.json": "{}", "c.moy/cover.png": { b: b64 } });
    let all = await store.readAll(s);
    ok("a seeded cover reads back as {b: base64} of the same bytes",
       all["c.moy/cover.png"] && all["c.moy/cover.png"].b === b64);
    ok("fileData turns that back into the bytes",
       store.fileData(all["c.moy/cover.png"]).length === png.length
       && store.fileData(all["c.moy/cover.png"]).every((v, i) => v === png[i]));
    const third = Math.ceil(png.length / 3 / 3) * 3;
    const r = await store.applyOps(s, [
        { p: "c.moy/cover.png", b: store.toBase64(png.subarray(0, third)), part: 0 },
        { p: "c.moy/cover.png", b: store.toBase64(png.subarray(third, 2 * third)), part: 1 },
        { p: "c.moy/cover.png", b: store.toBase64(png.subarray(2 * third)), part: 2 },
        { p: "c.moy/cover.png", pub: 1 },
        { p: "c.moy/main.py", b: "AAAA" },
    ]);
    all = await store.readAll(s);
    ok("a chunked cover publishes whole, and only a cover takes bytes",
       r.applied === 4 && r.errors.length === 1 && r.errors[0][1] === "not a binary file"
       && all["c.moy/cover.png"].b === b64, JSON.stringify(r.errors));
}

// ---- Get Carts' installs: old or new, at every point a reload can land --------
{
    const bin = (n, seed) => {
        const b = new Uint8Array(n);
        for (let i = 0; i < n; i++) b[i] = (i * seed + (i >> 7)) & 255;
        return b;
    };
    const OLD = { "manifest.json": enc.encode('{"title":"Jet","v":1}'),
                  "main.wasm": bin(3000, 7), "pmem.json": enc.encode('{"0": 5}') };
    const NEW = { "manifest.json": enc.encode('{"title":"Jet","v":2}'),
                  "main.wasm": bin(5000, 11), "game.wad": bin(9000, 13),
                  "pmem.json": enc.encode('{"0": 5}') };
    const OLD_REC = '{"version": 1, "carts": {"jet.moy": {"version": 1}}}';
    const NEW_REC = '{"version": 1, "carts": {"jet.moy": {"version": 2}}}';
    const asFiles = (set) => Object.keys(set).map((name) => ({ name, data: set[name] }));
    const same = (all, set) => {
        const keys = Object.keys(all).filter((k) => k.startsWith("jet.moy/")).sort();
        const want = Object.keys(set).map((k) => "jet.moy/" + k).sort();
        if (keys.join() !== want.join()) return false;
        return want.every((k) => {
            const v = store.fileData(all[k]);
            const b = typeof v === "string" ? enc.encode(v) : v;
            const w = set[k.slice(8)];
            return b.length === w.length && b.every((x, i) => x === w[i]);
        });
    };
    const fresh = async () => {
        const s = await store.openStore(fakeNav(true));
        await store.seed(s, { "other.moy/main.lua": "x = 1" });
        await store.commitInstall(s, "jet.moy", asFiles(OLD), OLD_REC);
        return s;
    };
    const s0 = await fresh();
    let all = await store.readAll(s0);
    ok("an install lands whole, binary files as bytes", same(all, OLD)
       && all["jet.moy/main.wasm"] instanceof Uint8Array, JSON.stringify(Object.keys(all)));
    ok("...with its record", await store.readRecord(s0) === OLD_REC);
    ok("...and leaves no staging", (await (async () => {
        const st = await s0.dir.getDirectoryHandle(store.STAGE_DIR);
        for await (const _e of st.entries()) return false;
        return true; })()));
    ok("a text file still reads back as text", typeof all["jet.moy/manifest.json"] === "string");

    // How many changes a whole update makes, then a reload after each one.
    const probe = await fresh();
    CRASH = { left: 1e9 };
    await store.commitInstall(probe, "jet.moy", asFiles(NEW), NEW_REC);
    const total = 1e9 - CRASH.left;
    CRASH = null;
    let olds = 0, news = 0, bad = [];
    for (let k = 0; k < total; k++) {
        const s = await fresh();
        CRASH = { left: k };
        let threw = false;
        try { await store.commitInstall(s, "jet.moy", asFiles(NEW), NEW_REC); }
        catch (e) { threw = true; }
        CRASH = null;
        if (!threw) { bad.push(k + ": no crash"); continue; }
        await store.recoverInstalls(s);
        all = await store.readAll(s);
        const rec = await store.readRecord(s);
        const left = [];
        for await (const [n] of (await s.dir.getDirectoryHandle(store.STAGE_DIR)).entries())
            left.push(n);
        if (same(all, OLD) && rec === OLD_REC) olds++;
        else if (same(all, NEW) && rec === NEW_REC) news++;
        else bad.push(k + ": " + JSON.stringify(Object.keys(all)) + " " + rec);
        if (left.length) bad.push(k + ": staging left " + left.join());
        if (all["other.moy/main.lua"] !== "x = 1") bad.push(k + ": another cart changed");
    }
    ok("a reload at any of an update's " + total + " changes leaves the old cart or the new",
       !bad.length && olds > 0 && news > 0,
       bad.slice(0, 4).join(" | ") + " old=" + olds + " new=" + news);

    // A recovery that is itself interrupted still finishes on the next boot.
    let twice = [];
    for (let k = 0; k < total; k++) {
        const s = await fresh();
        CRASH = { left: k };
        try { await store.commitInstall(s, "jet.moy", asFiles(NEW), NEW_REC); } catch (e) { }
        for (let j = 0; j < 40; j++) {
            CRASH = { left: j };
            try { await store.recoverInstalls(s); CRASH = null; break; } catch (e) { }
        }
        CRASH = null;
        await store.recoverInstalls(s);
        all = await store.readAll(s);
        const rec = await store.readRecord(s);
        if (!((same(all, OLD) && rec === OLD_REC) || (same(all, NEW) && rec === NEW_REC)))
            twice.push(k);
    }
    ok("an interrupted recovery is finished by the next one", !twice.length,
       twice.join(","));

    const s1 = await fresh();
    let refused = false;
    try { await store.commitInstall(s1, "../evil", asFiles(NEW), NEW_REC); }
    catch (e) { refused = true; }
    ok("a folder that is not a plain name is refused", refused);
    await store.writeRecord(s1, '{"version": 1, "carts": {}}');
    ok("a removal's record is written alone",
       await store.readRecord(s1) === '{"version": 1, "carts": {}}');
}

// ---- a returning browser still gets a system cart it never had ---------------
{
    const local = { "star.moy/manifest.json": '{"title":"Star","moybyte":{"system":true}}',
                    "mine.moy/manifest.json": '{"title":"Mine"}' };
    const bundle = {
        "star.moy/manifest.json": '{"title":"Star","moybyte":{"system":true},"version":9}',
        "star.moy/main.py": "new code",
        "moybyte.get_carts.moy/manifest.json": '{"title":"Get Carts","moybyte":{"system":true}}',
        "moybyte.get_carts.moy/main.py": "pass",
        "moybyte.get_carts.moy/cover.png": { b: "AAAA" },
        "demo.moy/manifest.json": '{"title":"Demo"}',
        "demo.moy/main.py": "x",
        "loose.moy/manifest.json": '{"title":"Loose","system":true}',
        "loose.moy/main.py": "x",
    };
    const got = store.missingSystemCarts(local, bundle);
    ok("a system cart the store lacks comes from the bundle, whole",
       Object.keys(got).sort().join() ===
       "moybyte.get_carts.moy/cover.png,moybyte.get_carts.moy/main.py,moybyte.get_carts.moy/manifest.json",
       JSON.stringify(Object.keys(got)));
    ok("one the store has is never touched, and a non-system cart never added",
       !("star.moy/main.py" in got) && !("demo.moy/main.py" in got));
    ok("a top-level \"system\" is not Moybyte's field and reads as absent",
       !("loose.moy/main.py" in got));
}

// The two predicates are two QUESTIONS, and the answers differ on exactly one
// thing. Pinned here because a single predicate is what this used to be, and
// collapsing them again would either ship a board somebody else's history or
// drop the kid's on the next reload -- neither of which fails loudly.
ok("the wire refuses a journal, the local store keeps it",
   store.skipName("journal") && store.skipName("journal.jsonl")
   && !store.skipLocal("journal") && !store.skipLocal("journal.jsonl"));
ok("both still refuse thumbs and the crash-safety artifacts",
   store.skipName("thumbs") && store.skipLocal("thumbs")
   && store.skipName("x.bak") && store.skipLocal("x.bak")
   && store.skipName("x.tmp") && store.skipLocal("x.tmp"));
ok("safeSegments takes the predicate it is given",
   store.safeSegments("a.moy/journal/journal.jsonl") === null
   && (store.safeSegments("a.moy/journal/journal.jsonl", store.skipLocal) || []).length === 3);

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

// ---- a compiled cart's written files (moy-spec SPEC.md 16.12) -----------------
{
    const OLD = enc.encode("old save"), NEW = enc.encode("the new, longer save");
    const w = await store.openWritten(fakeNav(true));
    await store.commitWritten(w, "doom", "doomsav0.dsg", OLD);
    let got = await store.readWritten(w);
    ok("a written file reads back as it was written",
       Object.keys(got).join() === "doom/doomsav0.dsg" && dec.decode(got["doom/doomsav0.dsg"]) === "old save",
       JSON.stringify(Object.keys(got)));
    // A reload at every change commitWritten makes: the file is the old save or
    // the new one, whole, and nothing of the attempt is left behind.
    let probe = await store.openWritten(fakeNav(true));
    await store.commitWritten(probe, "doom", "doomsav0.dsg", OLD);
    CRASH = { left: 1e9 };
    await store.commitWritten(probe, "doom", "doomsav0.dsg", NEW);
    const steps = 1e9 - CRASH.left;
    CRASH = null;
    let torn = 0, newer = 0;
    for (let k = 0; k < steps; k++) {
        const s = await store.openWritten(fakeNav(true));
        await store.commitWritten(s, "doom", "doomsav0.dsg", OLD);
        CRASH = { left: k };
        try { await store.commitWritten(s, "doom", "doomsav0.dsg", NEW); } catch (e) { }
        CRASH = null;
        const after = await store.readWritten(s);
        const text = after["doom/doomsav0.dsg"] ? dec.decode(after["doom/doomsav0.dsg"]) : null;
        if (text !== "old save" && text !== "the new, longer save") torn++;
        if (text === "the new, longer save") newer++;
        const d = await s.dir.getDirectoryHandle("doom");
        for await (const [n] of d.entries()) if (n.includes("~")) torn++;
    }
    ok("a reload at any of " + steps + " steps of a write leaves the old save or the new, whole",
       torn === 0 && newer > 0, "torn " + torn + ", new " + newer);
    await store.commitWritten(w, "doom", "default.cfg", NEW);
    await store.commitWritten(w, "jet", "options.cfg", OLD);
    await store.dropWritten(w, "doom", "doomsav0.dsg");
    got = await store.readWritten(w);
    ok("an erase takes one file", Object.keys(got).sort().join() === "doom/default.cfg,jet/options.cfg",
       Object.keys(got).join());
    await store.dropWritten(w, "doom", null);
    got = await store.readWritten(w);
    ok("a removed cart's files go, and another cart's stay", Object.keys(got).join() === "jet/options.cfg",
       Object.keys(got).join());
    let refused = false;
    try { await store.commitWritten(w, "..", "x", OLD); } catch (e) { refused = true; }
    ok("a written path that is not one name in one folder is refused", refused);
}

// ---- naming -----------------------------------------------------------------
const have = { "star.moy": 1, "star_2.moy": 1 };
ok("an imported cart takes the store's duplicate-naming rule",
   store.uniqueCartDir((n) => n in have, "star") === "star_3.moy",
   store.uniqueCartDir((n) => n in have, "star"));
ok("a free name is taken as is", store.uniqueCartDir(() => false, "star") === "star.moy");
ok("a zip file name becomes a legal folder base, under the local author",
   store.cartBase("My Game!.moy.zip") === "local.my_game", store.cartBase("My Game!.moy.zip"));
ok("a zip named for a cart's id keeps it",
   store.cartBase("kenny.Star Catcher.moy.zip") === "kenny.star_catcher",
   store.cartBase("kenny.Star Catcher.moy.zip"));

console.log("\n" + (fail ? fail + " FAILED" : "all store checks passed"));
process.exit(fail ? 1 : 0);
