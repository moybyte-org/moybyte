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
import { crash, fakeNav } from "./fake_opfs.mjs";

let fail = 0;
function ok(name, cond, extra = "") {
    if (cond) console.log("  ok   " + name);
    else { fail++; console.log("  FAIL " + name + (extra ? "  " + extra : "")); }
}

const enc = new TextEncoder(), dec = new TextDecoder();

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
        // Never seeded here: a top-level file is the console's own, and the
        // console's own store keeps it (the system root, below).
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

// ---- the console's own documents: system.json and its siblings ---------------
// A site-mode browser is its own console, so it keeps what a board keeps
// beside its carts -- the Settings rows, the badges, the shared sheet -- in an
// OPFS store of its own, and takes nothing else into it.
const dirNames = async (d) => {
    const out = [];
    for await (const [n] of d.entries()) out.push(n);
    return out.sort();
};
for (const sync of [true, false]) {
    const label = sync ? "sync handle" : "createWritable";
    const nav = fakeNav(sync);
    const carts = await store.openStore(nav);
    const s = await store.openStore(nav, "system");
    ok("the console's store opens beside the carts (" + label + ")",
       !!s && s.root.id === "system" && s.root.endpoint === "console.json");
    ok("a fresh console store is empty (" + label + ")", await store.isEmpty(s));
    const sheet = new Uint8Array(300).map((_, i) => (i * 7) & 255);
    const SYS = '{"theme": "dusk", "show_fps": false, "volume": 3}';
    const ACH = '{"unlocked": ["first_open", "first_run"]}';
    const r = await store.applyOps(s, [
        { p: "system.json", t: SYS },
        { p: "achievements.json", t: ACH },
        { p: "shared.moygfx", b: store.toBase64(sheet) },
    ]);
    ok("its documents land (" + label + ")", r.applied === 3 && !r.errors.length,
       JSON.stringify(r.errors));
    let all = await store.readAll(s);
    ok("...and read back, the sheet as bytes (" + label + ")",
       all["system.json"] === SYS && all["achievements.json"] === ACH
       && store.fileData(all["shared.moygfx"]).every((x, i) => x === sheet[i]),
       JSON.stringify(Object.keys(all)));
    ok("a committed write leaves nothing beside the file (" + label + ")",
       (await dirNames(s.carts)).join() ===
       "achievements.json,shared.moygfx,system.json",
       (await dirNames(s.carts)).join());
    ok("the console store is not empty now (" + label + ")", !(await store.isEmpty(s)));
    ok("...and the carts store never saw any of it (" + label + ")",
       !Object.keys(await store.readAll(carts)).length && await store.isEmpty(carts));

    const big = "{\"name\": \"" + "z".repeat(40) + "\"}";
    const chunked = await store.applyOps(s, [
        { p: "system.json", t: big.slice(0, 20), part: 0 },
        { p: "system.json", t: big.slice(20), part: 1 },
        { p: "system.json", pub: 1 },
        { p: "achievements.json", d: 1 },
    ]);
    all = await store.readAll(s);
    ok("chunks publish whole and a delete lands (" + label + ")",
       chunked.applied === 4 && all["system.json"] === big && !("achievements.json" in all),
       JSON.stringify(chunked.errors) + " " + JSON.stringify(Object.keys(all)));

    const bad = await store.applyOps(s, [
        { p: "wifi.json", t: "[]" },
        { p: "installed.json", t: "{}" },
        { p: "indexes.json", t: "{}" },
        { p: "a.moy/main.py", t: "x" },
        { p: "system.json.bak", t: "x" },
        { p: "system.json~part", t: "x" },
        { p: "../system.json", t: "x" },
        { p: "system.json", dc: 1 },
        { p: "carts/a.moy", dc: 1 },
        { p: "system.json", b: store.toBase64(sheet) },
    ]);
    ok("a secret, another sibling, a cart path, a folder delete and bytes for a"
       + " text file are all refused (" + label + ")",
       bad.applied === 0 && bad.errors.length === 10, JSON.stringify(bad.errors));
    ok("...and nothing they named was written (" + label + ")",
       (await dirNames(s.carts)).join() ===
       "shared.moygfx,system.json",
       (await dirNames(s.carts)).join());
}

// ---- an interrupted write never corrupts system.json -------------------------
// A reload can land between any two changes the store makes. Whatever it
// interrupted, the next boot reads the old document or the new one, whole,
// and finds nothing else beside them.
{
    const OLD = '{"theme": "dusk", "show_fps": false}';
    const NEW = '{"theme": "night", "show_fps": true, "name": "nikola", "volume": 5}';
    const fresh = async () => {
        const s = await store.openStore(fakeNav(true), "system");
        await store.applyOps(s, [{ p: "system.json", t: OLD }]);
        return s;
    };
    const probe = await fresh();
    crash.at = { left: 1e9 };
    await store.applyOps(probe, [{ p: "system.json", t: NEW }]);
    const total = 1e9 - crash.at.left;
    crash.at = null;
    let olds = 0, news = 0, bad = [];
    for (let k = 0; k < total; k++) {
        const s = await fresh();
        crash.at = { left: k };
        const r = await store.applyOps(s, [{ p: "system.json", t: NEW }]);
        crash.at = null;
        if (!r.errors.length) { bad.push(k + ": no crash"); continue; }
        const all = await store.readAll(s);           // the next boot
        if (all["system.json"] === OLD) olds++;
        else if (all["system.json"] === NEW) news++;
        else bad.push(k + ": " + JSON.stringify(all["system.json"]));
        const left = await dirNames(s.carts);
        if (left.join() !== "system.json") bad.push(k + ": left " + left.join());
    }
    ok("a reload at any of a Settings write's " + total + " changes leaves the old"
       + " system.json or the new", !bad.length && olds > 0 && news > 0,
       bad.slice(0, 4).join(" | ") + " old=" + olds + " new=" + news);

    // A boot whose own settle is interrupted is finished by the boot after it.
    let twice = [];
    for (let k = 0; k < total; k++) {
        const s = await fresh();
        crash.at = { left: k };
        await store.applyOps(s, [{ p: "system.json", t: NEW }]);
        for (let j = 0; j < 12; j++) {
            crash.at = { left: j };
            try { await store.readAll(s); crash.at = null; break; } catch (e) { }
        }
        crash.at = null;
        const v = (await store.readAll(s))["system.json"];
        if (v !== OLD && v !== NEW) twice.push(k + ":" + v);
    }
    ok("an interrupted settle is finished by the next one", !twice.length, twice.join(","));

    // No reload at all: a write that failed part way (a quota error) and the
    // next write after it, in the same session.
    let next = [];
    const NEWER = '{"theme": "sakura"}';
    for (let k = 0; k < total; k++) {
        const s = await fresh();
        crash.at = { left: k };
        await store.applyOps(s, [{ p: "system.json", t: NEW }]);
        crash.at = null;
        const r = await store.applyOps(s, [{ p: "system.json", t: NEWER }]);
        const v = (await store.readAll(s))["system.json"];
        if (r.errors.length || v !== NEWER) next.push(k + ":" + v);
    }
    ok("the write after a failed one lands whole", !next.length, next.join(","));
}

// ---- a theme package's fonts and sheets cross as BYTES -----------------------
{
    ok("a theme package's font and sheet are binary, its theme.json is not",
       store.isBinary("dusk.moy/ui.fnt") && store.isBinary("dusk.moy/parts.moygfx")
       && !store.isBinary("dusk.moy/theme.json") && !store.isBinary(".fnt"));
    const s = await store.openStore(fakeNav(true));
    const fnt = new Uint8Array(700);
    for (let i = 0; i < fnt.length; i++) fnt[i] = (i * 29 + 3) & 255;
    await store.seed(s, { "dusk.moy/theme.json": "{}", "dusk.moy/ui.fnt": { b: store.toBase64(fnt) } });
    const all = await store.readAll(s);
    ok("a seeded font reads back byte-equal",
       store.fileData(all["dusk.moy/ui.fnt"]).every((v, i) => v === fnt[i])
       && all["dusk.moy/theme.json"] === "{}");
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
    crash.at = { left: 1e9 };
    await store.commitInstall(probe, "jet.moy", asFiles(NEW), NEW_REC);
    const total = 1e9 - crash.at.left;
    crash.at = null;
    let olds = 0, news = 0, bad = [];
    for (let k = 0; k < total; k++) {
        const s = await fresh();
        crash.at = { left: k };
        let threw = false;
        try { await store.commitInstall(s, "jet.moy", asFiles(NEW), NEW_REC); }
        catch (e) { threw = true; }
        crash.at = null;
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
        crash.at = { left: k };
        try { await store.commitInstall(s, "jet.moy", asFiles(NEW), NEW_REC); } catch (e) { }
        for (let j = 0; j < 40; j++) {
            crash.at = { left: j };
            try { await store.recoverInstalls(s); crash.at = null; break; } catch (e) { }
        }
        crash.at = null;
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
    crash.at = { left: 1e9 };
    await store.commitWritten(probe, "doom", "doomsav0.dsg", NEW);
    const steps = 1e9 - crash.at.left;
    crash.at = null;
    let torn = 0, newer = 0;
    for (let k = 0; k < steps; k++) {
        const s = await store.openWritten(fakeNav(true));
        await store.commitWritten(s, "doom", "doomsav0.dsg", OLD);
        crash.at = { left: k };
        try { await store.commitWritten(s, "doom", "doomsav0.dsg", NEW); } catch (e) { }
        crash.at = null;
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

// ---- a theme lands under its own name (theming phase 6 step 8) --------------
{
    const enc = (o) => new TextEncoder().encode(JSON.stringify(o));
    const pkg = (top, name) => [
        { name: top + "/manifest.json", data: enc({ format: "moy-1", title: "Sunset",
            runtime: "theme", main: "theme.json", moybyte: { type: "theme" } }) },
        { name: top + "/theme.json", data: enc({ format: 1, name }) }];
    const t = store.themeOf(pkg("sunset.moy", "sunset"), "sunset.moy");
    ok("a theme archive is read as a theme", t && t.name === "sunset" && t.file === "theme.json",
       JSON.stringify(t));
    ok("a cart archive is no theme", store.themeOf([{ name: "x.moy/manifest.json",
        data: enc({ moybyte: { type: "game" } }) }], "x.moy") === null);
    ok("a theme whose name is not the grammar takes its folder's",
       store.themeOf(pkg("Night Owl.moy", "Bad Name"), "Night Owl.moy").name === "night_owl");
    ok("a free theme name is its folder",
       store.uniqueThemeDir(() => false, "sunset").dir === "sunset.moy");
    const taken = { "sunset.moy": 1 };
    const u = store.uniqueThemeDir((n) => n in taken, "sunset");
    ok("a taken theme name takes the next _2 stem", u.dir === "sunset_2.moy" && u.name === "sunset_2",
       JSON.stringify(u));
    const long = "a".repeat(24);
    const l = store.uniqueThemeDir((n) => n === long + ".moy", long);
    ok("the _2 stem stays inside the name's 24", l.name.length === 24 && store.themeName(l.name),
       l.name);
    const back = JSON.parse(new TextDecoder().decode(
        store.renamedTheme(enc({ format: 1, name: "sunset", colors: {} }), "sunset_2")));
    ok("theme.json's name is rewritten to the folder's", back.name === "sunset_2" && back.format === 1);
    const pretty = new TextEncoder().encode(
        JSON.stringify({ format: 1, name: "dusk", colors: {} }, null, 2) + "\n");
    const dusk = store.themeOf([pkg("dusk.moy", "dusk")[0],
        { name: "dusk.moy/theme.json", data: pretty }], "dusk.moy");
    ok("a theme landing under its own name keeps theme.json byte for byte",
       store.landedTheme(pretty, dusk, "dusk") === pretty && !dusk.renamed, JSON.stringify(dusk));
    ok("a theme landing under a _2 stem has theme.json rewritten",
       JSON.parse(new TextDecoder().decode(store.landedTheme(pretty, dusk, "dusk_2"))).name
           === "dusk_2");
    const owl = store.themeOf(pkg("night_owl.moy", "Bad Name"), "night_owl.moy");
    ok("a theme named for its folder has theme.json rewritten, even unmoved",
       owl.renamed && JSON.parse(new TextDecoder().decode(
           store.landedTheme(pkg("x", "Bad Name")[1].data, owl, "night_owl"))).name === "night_owl");
}

// ---- a channel's site keeps its own storage ---------------------------------
// The stable site and the dev site share an origin, so they share its OPFS. A
// page published for a channel opens every store through channelNav, and what
// one site writes the other never reads.
{
    const nav = fakeNav(true);
    ok("the stable site opens the origin's own storage", store.channelNav(nav, "") === nav);
    const devNav = store.channelNav(nav, "dev");
    const stable = await store.openStore(nav, "carts");
    const dev = await store.openStore(devNav, "carts");
    await store.seed(stable, { "a.moy/main.py": "print('stable')" });
    await store.seed(dev, { "a.moy/main.py": "print('dev')", "d.moy/main.py": "x" });
    const sAll = await store.readAll(await store.openStore(nav, "carts"));
    const dAll = await store.readAll(await store.openStore(devNav, "carts"));
    ok("a dev write never reaches the stable carts",
       sAll["a.moy/main.py"] === "print('stable')" && !("d.moy/main.py" in sAll),
       JSON.stringify(sAll));
    ok("the dev site reads its own carts back",
       dAll["a.moy/main.py"] === "print('dev')" && "d.moy/main.py" in dAll, JSON.stringify(dAll));
    await store.writeRecord(dev, '{"dev":1}');
    ok("the install record is the channel's too", (await store.readRecord(stable)) === null
       && (await store.readRecord(dev)) === '{"dev":1}');
    await store.commitWritten(await store.openWritten(devNav), "c", "k", new Uint8Array([1]));
    ok("a compiled cart's written files are the channel's too",
       !Object.keys(await store.readWritten(await store.openWritten(nav))).length &&
       Object.keys(await store.readWritten(await store.openWritten(devNav))).length === 1);
    const root = await nav.storage.getDirectory();
    const top = [];
    for await (const [name] of root.entries()) top.push(name);
    ok("the channel lives in one directory of the origin root",
       top.includes(store.CHANNEL_DIR_PREFIX + "dev") && top.includes("carts")
       && !top.includes("dev"), JSON.stringify(top));
    ok("a channel name that is not a word gets no storage, never the origin's",
       (await store.openStore(store.channelNav(nav, "../carts"), "carts")) === null);
    ok("no OPFS stays no OPFS under a channel",
       (await store.openStore(store.channelNav({ storage: null }, "dev"), "carts")) === null);
}

console.log("\n" + (fail ? fail + " FAILED" : "all store checks passed"));
process.exit(fail ? 1 : 0);
