// The browser-local cart store (#193 mode 1) and the .moy zip codec.
//
// TWO WEB MODES, TOTAL, NO CROSSOVER (owner call 2026-08-25). A page served
// FROM a board edits the BOARD's store: the sweep's batches go out as
// POST /sync and nothing is kept locally. A page on a static host
// (moybyte.com, an export, file://) keeps the carts HERE, and this module is
// where they live.
//
// The engine is the same one either way: runtime/moy_sync's StoreWatcher
// already sweeps the wasm VFS ~1/s and hands out commit-shaped op batches, so
// mode 1 is not a second persistence design -- it is a second DELIVERY TARGET
// for the batch the sweep already built. Writes, deletes and cart-deletes all
// arrive in the one op vocabulary.
//
// AND THE UNDO HISTORY COMES WITH THEM (2026-08-25). The journal lives with the
// store of record: board mode leaves it on the board (its `apply_ops` writes
// one), and site mode keeps it HERE, because here is where the cart durably
// lives. The mechanism is not a second path -- web_boot hands its site-mode
// watcher `skip_keep_journal` and the journal files ride the same sweep as
// everything else. What that costs is disk: a cart's `journal/s/` holds full
// snapshots, capped by moy_journal at 64 entries / 512KB per cart.
//
// SUBSTRATE: OPFS, not IndexedDB (moycore plan 9's open question, closed here).
// The ops ARE file writes at paths, so OPFS applies them 1:1 -- a cart folder
// in OPFS is a cart folder, the same shape moy_carts already speaks on every
// other tier. IndexedDB would mean inventing a path keyspace and a blob schema
// to store a filesystem inside a database, and then keeping that schema honest
// against a store that grows new file kinds (scenes, tables, docs) whenever the
// console does. The cost of the choice is reach, and it is small: OPFS ships in
// every browser this build already needs for wasm + AudioWorklet. Where it is
// missing the page says so and runs in memory, which is the pre-#193 behaviour.
//
// Everything here is deliberately free of the VM and of the Worker globals, so
// node can drive it directly (worker_persist_test.mjs) against a fake OPFS.

// What never crosses THE WIRE or goes into a zip -- the JS mirror of
// runtime/moy_sync's `_skip`, which is the ONE predicate for what stays home.
// journal/ is the durable undo history, thumbs/ a regenerable cache, .bak/.tmp
// moy_fs's crash-safety artifacts.
const SKIP_DIRS = ["thumbs", "__pycache__", "journal"];
const SKIP_FILES = ["journal.jsonl"];
const SKIP_SUFFIXES = [".bak", ".tmp"];

// ...and what never reaches THE LOCAL STORE, which since 2026-08-25 is a
// SHORTER list: in mode 1 this OPFS store is the store of record, so the undo
// history belongs in it (moy_sync's "the journal lives with the store of
// record"; #193's "with its undo history"). The mirror of moy_sync's
// SITE_SKIP_*. A zip and a wire batch keep the longer list: a board has its own
// journal and must never be handed somebody else's.
const SITE_SKIP_DIRS = ["thumbs", "__pycache__"];
const SITE_SKIP_FILES = [];

function skipIn(name, dirs, files) {
    if (dirs.indexOf(name) >= 0 || files.indexOf(name) >= 0) return true;
    return SKIP_SUFFIXES.some((s) => name.endsWith(s));
}

export function skipName(name) { return skipIn(name, SKIP_DIRS, SKIP_FILES); }

export function skipLocal(name) {
    return skipIn(name, SITE_SKIP_DIRS, SITE_SKIP_FILES);
}

// The SYNC ROOT REGISTRY -- the JS MIRROR of runtime/moy_sync.SYNC_ROOTS, so the
// worker and this store iterate roots instead of naming carts/files. A static
// host has no server to ask for this, so it is a constant, pinned against the
// Python registry by tests/test_web_store.py (a drift there fails the build).
// The only per-root behaviour the browser store needs is the whole-folder
// delete ARITY: a cart is one segment, a files item is a kind/name or a folder
// below it. Kinds are not validated here -- these ops are the browser's own
// sweep of its own store, and `.history`/`trash` persisting to a SITE-mode OPFS
// is the kid's own undo/recovery surviving the tab, which is correct.
export const ROOTS = [
    { id: "carts", vfs: "/moy/carts", endpoint: "carts.json", dcMin: 1, dcMax: 1 },
    { id: "files", vfs: "/moy/files", endpoint: "files.json", dcMin: 2, dcMax: null },
];

export function rootById(id) {
    for (const r of ROOTS) if (r.id === id) return r;
    return null;
}

// A path the local store will accept: the JS half of moy_sync.safe_segments.
// An allowlist of shape, not a blocklist of tricks -- the store is a real
// filesystem and `..` in a cart name must never resolve. `skip` defaults to the
// WIRE's rule; every store-side caller here passes `skipLocal`, so a journal
// path lands locally and still cannot be shipped.
export function safeSegments(rel, skip = skipName) {
    if (typeof rel !== "string" || !rel || rel.length > 256) return null;
    const parts = rel.split("/");
    for (const seg of parts) {
        if (!seg || seg === "." || seg === "..") return null;
        if (/[\\\0\r\n]/.test(seg)) return null;
        if (skip(seg)) return null;
    }
    return parts;
}

// ---------------------------------------------------------------------------
// Mode detection.
// ---------------------------------------------------------------------------

// Which world is this page in? Answered BEFORE anything is written, because
// the answer decides whether the VFS is seeded from the host or from OPFS.
//
// TWO PROBES, deliberately. `GET /sync` is the cheap marker a board serves to
// say "I have the push half"; but a board running firmware older than that
// marker answers 404 to the GET while still accepting the POST, and reading
// that as "static host" would quietly strand a kid's edits in a browser
// instead of writing them to the console they are sitting at. So a GET miss
// falls through to an EMPTY batch POST -- zero ops, nothing applied, and the
// status code is the same evidence the old lazy probe collected on its first
// real batch.
export async function probeMode(fetchFn) {
    const noPush = (s) => s === 404 || s === 405 || s === 501;
    try {
        const r = await fetchFn("sync", { method: "GET" });
        if (r && r.ok) return "board";
        if (r && !noPush(r.status)) return "board";
    } catch (e) { /* file://, offline, CSP: fall through to the POST probe */ }
    try {
        const r = await fetchFn("sync", {
            method: "POST", body: JSON.stringify({ v: 1, ops: [] }),
            headers: { "Content-Type": "application/json" },
        });
        if (r && r.ok) return "board";
        // 403 is a board that wants a ?pin= this page was not opened with. It
        // is still a board, and site mode there would edit a phantom store.
        if (r && r.status === 403) return "board";
    } catch (e) { /* no host at all */ }
    return "site";
}

// ---------------------------------------------------------------------------
// The OPFS store.
// ---------------------------------------------------------------------------

const enc = new TextEncoder();
const dec = new TextDecoder();

// null when the browser has no OPFS (or refuses it -- a private window, a
// file:// origin, site data blocked). The caller must treat null as "run in
// memory and SAY so", never as an empty store.
export async function openStore(nav, rootId = "carts") {
    const desc = rootById(rootId);
    if (!desc) return null;
    const st = nav && nav.storage;
    if (!st || typeof st.getDirectory !== "function") return null;
    try {
        const dir = await st.getDirectory();
        // The OPFS directory is named by the root id, so the carts and files
        // stores are siblings under the origin's OPFS, never one blob.
        const carts = await dir.getDirectoryHandle(rootId, { create: true });
        return { dir, carts, parts: new Map(), root: desc };
    } catch (e) {
        return null;
    }
}

async function dirFor(store, segs, create) {
    let d = store.carts;
    for (const seg of segs) d = await d.getDirectoryHandle(seg, { create: !!create });
    return d;
}

async function writeText(store, parts, text) {
    const dir = await dirFor(store, parts.slice(0, -1), true);
    const fh = await dir.getFileHandle(parts[parts.length - 1], { create: true });
    const bytes = enc.encode(text);
    // Sync access handles are the worker-only fast path AND the widest-support
    // one (they landed in OPFS before createWritable did); createWritable is
    // the fallback for a main-thread caller or a browser without them.
    if (typeof fh.createSyncAccessHandle === "function") {
        const h = await fh.createSyncAccessHandle();
        try {
            h.truncate(0);
            h.write(bytes, { at: 0 });
            h.flush();
        } finally { h.close(); }
        return;
    }
    const w = await fh.createWritable();
    await w.write(bytes);
    await w.close();
}

async function removeAt(store, parts, recursive) {
    const dir = await dirFor(store, parts.slice(0, -1), false);
    await dir.removeEntry(parts[parts.length - 1], { recursive: !!recursive });
}

// Apply one wire batch. The op vocabulary is moy_sync's, verbatim, so this and
// the board's `apply_ops` cannot drift about what a batch means:
//   {p, t}            whole-file write
//   {p, t, part: n}   chunk n of a big file (parts buffer until `pub`)
//   {p, pub: 1}       publish the buffered chunks
//   {p, d: 1}         delete one file
//   {p, dc: 1}        delete a whole cart folder
// A bad op SKIPS; it never aborts the batch, because the client would only
// replay the same poison forever.
export async function applyOps(store, ops) {
    let applied = 0;
    const errors = [];
    for (let i = 0; i < ops.length; i++) {
        const op = ops[i];
        try {
            const reason = await applyOne(store, op);
            if (reason) errors.push([i, reason]); else applied++;
        } catch (e) {
            errors.push([i, String((e && e.message) || e)]);
        }
    }
    return { applied, errors };
}

async function applyOne(store, op) {
    if (!op || typeof op !== "object") return "not an op";
    // skipLocal, not skipName: in site mode the sweep ships this store's own
    // journal to this store, and the wire predicate would refuse every line of
    // it -- silently, as "bad path" errors nobody reads.
    const parts = safeSegments(op.p || "", skipLocal);
    if (!parts) return "bad path";
    if (op.dc) {
        // Whole-folder delete arity from the root descriptor -- a cart is one
        // segment, a files item a kind/name or a recording folder below it.
        const n = parts.length, r = store.root;
        if (n < r.dcMin || (r.dcMax !== null && n > r.dcMax)) return "bad dc target";
        try { await removeAt(store, parts, true); } catch (e) { /* already gone */ }
        return null;
    }
    // Never a top-level file: system.json / wifi.json are system state beside
    // the carts, not the kid's work (moy_sync draws the same line).
    if (parts.length < 2) return "not a cart file";
    const key = parts.join("/");
    if (op.d) {
        try { await removeAt(store, parts, false); } catch (e) { /* already gone */ }
        store.parts.delete(key);
        return null;
    }
    if (op.pub) {
        const buf = store.parts.get(key);
        if (buf === undefined) return "no staged parts";
        store.parts.delete(key);
        await writeText(store, parts, buf);
        return null;
    }
    if (typeof op.t !== "string") return "no text";
    if (op.part === undefined || op.part === null) {
        await writeText(store, parts, op.t);
        return null;
    }
    // Chunks accumulate in RAM and land in ONE write at `pub`, so a batch that
    // dies mid-file leaves the previous good copy untouched -- the same
    // guarantee the board gets from its .tmp staging, without a stray .tmp the
    // next sweep would have to learn to ignore.
    store.parts.set(key, op.part === 0 ? op.t : (store.parts.get(key) || "") + op.t);
    return null;
}

// Every syncable file in the local store as {rel: text}. This is what a site-mode
// boot writes into the VFS INSTEAD of the served carts.json.
export async function readAll(store) {
    const out = {};
    await walk(store.carts, "", out, 0);
    return out;
}

async function walk(dir, prefix, out, depth) {
    if (depth > 6) return;
    const entries = [];
    for await (const [name, handle] of dir.entries()) entries.push([name, handle]);
    entries.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    for (const [name, handle] of entries) {
        if (skipLocal(name)) continue;      // the journal comes BACK too
        const rel = prefix ? prefix + "/" + name : name;
        if (handle.kind === "directory") {
            await walk(handle, rel, out, depth + 1);
            continue;
        }
        // Top-level files are not cart files; the sweep never ships them and
        // the VFS must not be seeded with them either.
        if (rel.indexOf("/") < 0) continue;
        const f = await handle.getFile();
        out[rel] = await f.text();
    }
}

export async function isEmpty(store) {
    for await (const [name, handle] of store.carts.entries()) {
        if (handle.kind === "directory" && !skipLocal(name)) return false;
    }
    return true;
}

// First visit: adopt the served carts.json as the local baseline.
export async function seed(store, carts) {
    let n = 0;
    for (const rel in carts) {
        const parts = safeSegments(rel, skipLocal);
        if (!parts || parts.length < 2) continue;
        await writeText(store, parts, carts[rel]);
        n++;
    }
    return n;
}

// ---------------------------------------------------------------------------
// The .moy zip -- the no-account escape hatch (#193).
//
// A zip carries NO journal, deliberately: it is built with `skipName` (the wire
// rule) because a .moy is meant to drop into somebody else's board store, and a
// history of edits made on another machine is neither useful there nor theirs.
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
        let c = i;
        for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
        t[i] = c >>> 0;
    }
    return t;
})();

export function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
}

// STORED (method 0) entries only. A cart is a handful of small text files, so
// compressing them buys little and costs a DEFLATE implementation; every
// unzipper reads stored entries, which is what "it drops into a board store"
// requires. Reading, by contrast, must handle deflated input -- see unzip().
export function zipStore(files) {
    const parts = [];
    const central = [];
    let offset = 0;
    for (const f of files) {
        const name = enc.encode(f.name);
        const data = f.data;
        const crc = crc32(data);
        const local = new Uint8Array(30 + name.length);
        const lv = new DataView(local.buffer);
        lv.setUint32(0, 0x04034b50, true);
        lv.setUint16(4, 20, true);          // version needed
        lv.setUint16(6, 0, true);           // flags
        lv.setUint16(8, 0, true);           // method: stored
        lv.setUint16(10, 0, true);          // mod time
        lv.setUint16(12, 0x21, true);       // mod date: 1980-01-01, deterministic
        lv.setUint32(14, crc, true);
        lv.setUint32(18, data.length, true);
        lv.setUint32(22, data.length, true);
        lv.setUint16(26, name.length, true);
        lv.setUint16(28, 0, true);
        local.set(name, 30);
        parts.push(local, data);

        const cen = new Uint8Array(46 + name.length);
        const cv = new DataView(cen.buffer);
        cv.setUint32(0, 0x02014b50, true);
        cv.setUint16(4, 20, true);          // version made by
        cv.setUint16(6, 20, true);          // version needed
        cv.setUint16(8, 0, true);
        cv.setUint16(10, 0, true);
        cv.setUint16(12, 0, true);
        cv.setUint16(14, 0x21, true);
        cv.setUint32(16, crc, true);
        cv.setUint32(20, data.length, true);
        cv.setUint32(24, data.length, true);
        cv.setUint16(28, name.length, true);
        cv.setUint16(30, 0, true);
        cv.setUint16(32, 0, true);
        cv.setUint16(34, 0, true);
        cv.setUint16(36, 0, true);
        cv.setUint32(38, 0, true);
        cv.setUint32(42, offset, true);
        cen.set(name, 46);
        central.push(cen);
        offset += local.length + data.length;
    }
    const cenStart = offset;
    let cenLen = 0;
    for (const c of central) cenLen += c.length;
    const end = new Uint8Array(22);
    const ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(8, central.length, true);
    ev.setUint16(10, central.length, true);
    ev.setUint32(12, cenLen, true);
    ev.setUint32(16, cenStart, true);
    const all = parts.concat(central, [end]);
    let total = 0;
    for (const p of all) total += p.length;
    const out = new Uint8Array(total);
    let at = 0;
    for (const p of all) { out.set(p, at); at += p.length; }
    return out;
}

async function inflateRaw(bytes) {
    if (typeof DecompressionStream !== "function")
        throw new Error("this browser cannot read compressed zips");
    const ds = new DecompressionStream("deflate-raw");
    const stream = new Blob([bytes]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
}

// -> [{name, data}]. Sizes and offsets come from the CENTRAL DIRECTORY, never
// from the local headers: an entry written with a data descriptor (flag bit 3,
// what most streaming zippers emit) carries zeroes for crc/sizes locally, and
// trusting those reads every file as empty.
export async function unzip(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let eocd = -1;
    for (let i = bytes.length - 22; i >= 0 && i >= bytes.length - 66000; i--) {
        if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error("not a zip file");
    const count = view.getUint16(eocd + 10, true);
    let at = view.getUint32(eocd + 16, true);
    const out = [];
    for (let i = 0; i < count; i++) {
        if (at + 46 > bytes.length || view.getUint32(at, true) !== 0x02014b50) break;
        const method = view.getUint16(at + 10, true);
        const csize = view.getUint32(at + 20, true);
        const nameLen = view.getUint16(at + 28, true);
        const extraLen = view.getUint16(at + 30, true);
        const commentLen = view.getUint16(at + 32, true);
        const local = view.getUint32(at + 42, true);
        const name = dec.decode(bytes.subarray(at + 46, at + 46 + nameLen));
        at += 46 + nameLen + extraLen + commentLen;
        if (name.endsWith("/")) continue;                 // a directory entry
        if (view.getUint32(local, true) !== 0x04034b50) continue;
        const lNameLen = view.getUint16(local + 26, true);
        const lExtraLen = view.getUint16(local + 28, true);
        const start = local + 30 + lNameLen + lExtraLen;
        const raw = bytes.subarray(start, start + csize);
        if (method === 0) out.push({ name, data: raw });
        else if (method === 8) out.push({ name, data: await inflateRaw(raw) });
        else throw new Error("unsupported zip compression (" + method + ")");
    }
    return out;
}

// The single top-level directory every entry sits under, or null. A zip made
// by zipStore() carries one `<cart>.moy/`; one made by hand from a cart's
// CONTENTS has manifest.json at the root and needs no stripping.
export function zipTopDir(entries) {
    let top = null;
    for (const e of entries) {
        const slash = e.name.indexOf("/");
        if (slash < 0) return null;
        const seg = e.name.slice(0, slash);
        if (top === null) top = seg;
        else if (top !== seg) return null;
    }
    return top;
}

// A cart folder base from a zip's top directory or its file name -- the same
// character class moy_carts.slug() lands on, so an imported folder is one a
// board's store would have created itself.
export function cartBase(name) {
    let n = String(name || "cart").replace(/\.zip$/i, "").replace(/\.moy$/i, "");
    n = n.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
    return n || "cart";
}

// moy_carts._unique_dir's rule, so an imported cart is named the way a
// duplicated one is: `base.moy`, then `base_2.moy`, `base_3.moy`...
export function uniqueCartDir(exists, base) {
    if (!exists(base + ".moy")) return base + ".moy";
    let i = 2;
    while (exists(base + "_" + i + ".moy")) i++;
    return base + "_" + i + ".moy";
}
