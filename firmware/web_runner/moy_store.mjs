// The browser-local cart store (#193 mode 1), Get Carts' installs into it
// (#124, `commitInstall` below), and the .moy zip codec.
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

// The binary files that cross by name -- a cart's cover (SPEC.md 3.6) -- the
// JS mirror of runtime/moy_sync's BINARY_FILES. They travel as base64: `b`
// where text rides as `t` in a batch op, `{b: ...}` as a value in a served
// bundle, and as BYTES once they are in the VFS or this store.
const BINARY_FILES = ["cover.png"];

export function isBinary(rel) {
    return BINARY_FILES.indexOf(String(rel).slice(String(rel).lastIndexOf("/") + 1)) >= 0;
}

export function fromBase64(b) {
    const s = atob(b);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
}

export function toBase64(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000)
        s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
}

// One bundle value as what a file holds: a string for text, the bytes of a
// `{b: base64}` value, raw bytes as they are (`readAll`'s form for a file that
// is not text -- an installed cart's module or game data), or null for a
// value that is none of those.
export function fileData(v) {
    if (typeof v === "string") return v;
    if (v instanceof Uint8Array) return v;
    if (v && typeof v.b === "string") return fromBase64(v.b);
    return null;
}

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
// Asking the browser to KEEP it.
// ---------------------------------------------------------------------------

// OPFS is BEST-EFFORT storage: a browser under disk pressure evicts an origin
// WHOLE, and the only thing this build did about that was advise "export your
// cart" -- after a write had already failed. `storage.persist()` asks for the
// durable bucket instead, and SITE MODE IS THE ONLY MODE THAT ASKS: a
// board-served page keeps nothing here, so asking there would raise a
// permission prompt for a store that is never written.
//
// THE ASK ONLY EXISTS ON THE MAIN THREAD. `persist()` is `[Exposed=Window]`;
// inside a Worker `navigator.storage` carries `persisted()`, `estimate()` and
// `getDirectory()` but no `persist` at all (measured in Chrome, 2026-08-29).
// So this one body does both halves: the worker calls it to LEARN the state,
// the page calls it to CHANGE it, and a context that cannot ask says
// "unsupported" rather than pretending it was refused.
//
// Three outcomes, all of them normal states:
//   granted      the origin is durable; only the user can clear it
//   denied       the browser said no -- Chrome asks a site-engagement
//                heuristic and mostly answers no on a first visit. The carts
//                are still saved; they are merely evictable, and the page must
//                SAY that rather than promise more than it was given.
//   unsupported  no persist() in reach (Safari, a Worker, a locked-down profile)
//
// It must never throw. A private window, blocked site data and file:// all
// arrive here, and every one of them is a supported way to run.
export async function requestPersistence(nav) {
    const st = nav && nav.storage;
    const out = { state: "unsupported", usage: null, quota: null };
    if (!st) return out;
    try {
        // persisted() FIRST: an origin already granted must not be re-asked,
        // because Firefox raises its permission prompt on every persist().
        if (typeof st.persisted === "function" && await st.persisted())
            out.state = "granted";
        else if (typeof st.persist === "function")
            out.state = (await st.persist()) ? "granted" : "denied";
    } catch (e) { /* an ask that throws is an ask that was never answered */ }
    if (typeof st.estimate === "function") {
        try {
            const e = await st.estimate();
            if (e && typeof e.usage === "number") out.usage = e.usage;
            if (e && typeof e.quota === "number") out.quota = e.quota;
        } catch (e) { /* the estimate is evidence, never a boot condition */ }
    }
    return out;
}

function mbytes(n) {
    const mb = n / 1048576;
    return (mb < 10 ? mb.toFixed(1) : mb.toFixed(0)) + "MB";
}

// The answer in the page's words -- the tail of the persist chip's detail line,
// which is where the E2E (and a curious owner in devtools) reads it back. The
// estimate rides along because #193's failure mode is SILENT eviction, and a
// store's distance from its quota is the one number that sees it coming.
export function storageNote(p) {
    if (!p) return "";
    const word = p.state === "granted" ? "kept"
               : p.state === "denied" ? "evictable"
                                      : "evictable (cannot ask here)";
    if (p.usage === null || p.quota === null) return word;
    return word + " " + mbytes(p.usage) + "/" + mbytes(p.quota);
}

// ---------------------------------------------------------------------------
// The OPFS store.
// ---------------------------------------------------------------------------

const enc = new TextEncoder();
const dec = new TextDecoder();
// A file read back is TEXT only when it is UTF-8 throughout, byte for byte:
// fatal, so a module or a WAD is never mangled into replacement characters,
// and the BOM kept, so writing the string back gives the same bytes.
const strict = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

// The record of what Get Carts installed, and the folder an install is built
// in, as siblings of the carts store -- runtime/cart_index.py's RECORD_NAME
// and STAGE_DIR, the same layout as beside a board's carts folder. Pinned
// against the Python by tests/test_web_store.py.
export const RECORD_NAME = "installed.json";
export const STAGE_DIR = "install";

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
    await writeIn(dir, parts[parts.length - 1], text);
}

async function writeIn(dir, name, text) {
    const fh = await dir.getFileHandle(name, { create: true });
    const bytes = typeof text === "string" ? enc.encode(text) : text;
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
//   {p, b}            the same two for a binary file (a cover), in base64
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
    if (op.b !== undefined) {
        if (!isBinary(key)) return "not a binary file";
        if (typeof op.b !== "string") return "no bytes";
        const bytes = fromBase64(op.b);
        if (op.part === undefined || op.part === null) {
            await writeText(store, parts, bytes);
            return null;
        }
        const had = op.part === 0 ? new Uint8Array(0) : (store.parts.get(key) || new Uint8Array(0));
        const joined = new Uint8Array(had.length + bytes.length);
        joined.set(had, 0);
        joined.set(bytes, had.length);
        store.parts.set(key, joined);
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

// Every syncable file in the local store as {rel: text}, and {rel: {b: base64}}
// for a binary one -- the served bundle's shape. This is what a site-mode boot
// writes into the VFS INSTEAD of the served carts.json.
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
        const bytes = new Uint8Array(await (await handle.getFile()).arrayBuffer());
        if (isBinary(rel)) { out[rel] = { b: toBase64(bytes) }; continue; }
        try { out[rel] = strict.decode(bytes); } catch (e) { out[rel] = bytes; }
    }
}

export async function isEmpty(store) {
    for await (const [name, handle] of store.carts.entries()) {
        if (handle.kind === "directory" && !skipLocal(name)) return false;
    }
    return true;
}

// The served bundle's SYSTEM carts (manifest "system": true) that the local
// store lacks, as bundle entries. The local store wins over the bundle -- it is
// the kid's work -- but a console that ships a system cart the store has never
// had (a new app like Get Carts, a new seed game) must still reach a browser
// that has kept its shelf since an older visit, the way a board seeds a
// built-in it does not have (runtime/moy_seed.py). A cart the store has is
// never touched.
export function missingSystemCarts(local, bundle) {
    const have = new Set(Object.keys(local).map((k) => k.split("/")[0]));
    const system = new Set();
    for (const rel in bundle) {
        const cut = rel.indexOf("/");
        if (cut < 0 || rel.slice(cut + 1) !== "manifest.json") continue;
        const top = rel.slice(0, cut);
        if (have.has(top) || typeof bundle[rel] !== "string") continue;
        try {
            const m = JSON.parse(bundle[rel]);
            if (m && m.moybyte && m.moybyte.system === true) system.add(top);
        } catch (e) { /* not a manifest anything reads: not a cart to add */ }
    }
    const out = {};
    for (const rel in bundle) if (system.has(rel.split("/")[0])) out[rel] = bundle[rel];
    return out;
}

// First visit: adopt the served carts.json as the local baseline.
export async function seed(store, carts) {
    let n = 0;
    for (const rel in carts) {
        const parts = safeSegments(rel, skipLocal);
        const data = fileData(carts[rel]);
        if (!parts || parts.length < 2 || data === null) continue;
        await writeText(store, parts, data);
        n++;
    }
    return n;
}

// ---------------------------------------------------------------------------
// Get Carts' installs (#124): a cart folder and the record, durable together.
//
// The console checks every byte of an install in its VFS staging folder and
// hands the result here; what comes back must be the guarantee a board's one
// rename gives -- a reload at ANY moment finds the old cart or the new one,
// never half of either, and never a record that disagrees with the folder.
// OPFS has no rename for a directory, so the commit is a MARKER instead:
//
//   1. the files go into install/<folder>/ beside the carts store (a reload
//      here leaves a build with no marker, which recovery removes);
//   2. install/<folder>.commit is written, holding the new record -- the
//      commit point;
//   3. the roll forward: carts/<folder> is replaced by a copy of the staged
//      files, the record is written, the marker goes, then the staging.
//
// A reload anywhere in 3 rolls forward again at the next boot
// (`recoverInstalls`, before the store is read), and every step of 3 is safe
// to repeat because the staging stays whole until the marker is gone. A
// marker that never finished writing does not parse, and counts as none.
// ---------------------------------------------------------------------------

function plainName(name) {
    return typeof name === "string" && !!name && name.indexOf("/") < 0
        && name.indexOf("\\") < 0 && name.indexOf("\0") < 0 && name[0] !== ".";
}

// `store` is the carts store (openStore(nav, "carts")); `files` is
// [{name, data: Uint8Array}], the staged folder's whole contents; `record` is
// the record's new text. Resolves once the install is durable; throws, having
// changed nothing on the shelf, when it could not be made so.
export async function commitInstall(store, folder, files, record) {
    if (!plainName(folder)) throw new Error("bad folder " + folder);
    for (const f of files) if (!plainName(f.name)) throw new Error("bad file " + f.name);
    const stage = await store.dir.getDirectoryHandle(STAGE_DIR, { create: true });
    try { await stage.removeEntry(folder + ".commit"); } catch (e) { /* none */ }
    try { await stage.removeEntry(folder, { recursive: true }); } catch (e) { /* none */ }
    try {
        const into = await stage.getDirectoryHandle(folder, { create: true });
        for (const f of files) await writeIn(into, f.name, f.data);
        await writeIn(stage, folder + ".commit", JSON.stringify({ folder, record }));
    } catch (e) {
        try { await stage.removeEntry(folder + ".commit"); } catch (e2) { /* none */ }
        try { await stage.removeEntry(folder, { recursive: true }); } catch (e2) { }
        throw e;
    }
    await rollForward(store, stage, folder);
}

async function readMarker(stage, folder) {
    try {
        const fh = await stage.getFileHandle(folder + ".commit");
        const m = JSON.parse(await (await fh.getFile()).text());
        return (m && m.folder === folder && typeof m.record === "string") ? m : null;
    } catch (e) {
        return null;
    }
}

async function rollForward(store, stage, folder) {
    const m = await readMarker(stage, folder);
    if (!m) return false;
    let from = null;
    try { from = await stage.getDirectoryHandle(folder); } catch (e) { from = null; }
    if (from) {
        try { await store.carts.removeEntry(folder, { recursive: true }); } catch (e) { }
        const into = await store.carts.getDirectoryHandle(folder, { create: true });
        for await (const [name, h] of from.entries()) {
            if (h.kind !== "file") continue;
            await writeIn(into, name, new Uint8Array(await (await h.getFile()).arrayBuffer()));
        }
        await writeIn(store.dir, RECORD_NAME, m.record);
    }
    await stage.removeEntry(folder + ".commit");
    if (from) await stage.removeEntry(folder, { recursive: true });
    return true;
}

// Finish what a reload interrupted, before anything reads the store: every
// committed install rolls forward, and everything else in install/ (a build
// that never reached its marker) goes. Returns how many rolled forward.
export async function recoverInstalls(store) {
    let stage;
    try { stage = await store.dir.getDirectoryHandle(STAGE_DIR); } catch (e) { return 0; }
    const names = [];
    for await (const [name, h] of stage.entries()) names.push([name, h.kind]);
    let n = 0;
    for (const [name, kind] of names) {
        if (kind === "file" && name.endsWith(".commit")
                && await rollForward(store, stage, name.slice(0, -".commit".length))) n++;
    }
    const left = [];
    for await (const [name] of stage.entries()) left.push(name);
    for (const name of left) {
        try { await stage.removeEntry(name, { recursive: true }); } catch (e) { }
    }
    return n;
}

// The record alone (a removal), or null when there is none.
export async function writeRecord(store, text) {
    await writeIn(store.dir, RECORD_NAME, text);
}

export async function readRecord(store) {
    try {
        const fh = await store.dir.getFileHandle(RECORD_NAME);
        return await (await fh.getFile()).text();
    } catch (e) {
        return null;
    }
}

// ---------------------------------------------------------------------------
// A compiled cart's written files (moy-spec SPEC.md 16.12), in OPFS beside the
// carts: written/<cart>/<key>, runtime/cart_files.py's layout, so the VFS the
// console reads and this store of record hold the same names. The console
// writes the VFS and queues the file here (carts_link's WebCartKeep), and a
// write lands with an install's crash-safety: the bytes go to "<key>~part",
// an empty "<key>~done" then says they are whole, and only then do they
// replace "<key>" -- so a reload at any moment finds the old copy or the new
// one, and readWritten finishes or discards what one left.
// ---------------------------------------------------------------------------

export const WRITTEN_ROOT = "written";
const PART = "~part", DONE = "~done";

// The written store, or null with no OPFS (the console then keeps written
// files in this tab alone, like every other edit there).
export async function openWritten(nav) {
    const st = nav && nav.storage;
    if (!st || typeof st.getDirectory !== "function") return null;
    try {
        const dir = await st.getDirectory();
        return { dir: await dir.getDirectoryHandle(WRITTEN_ROOT, { create: true }) };
    } catch (e) {
        return null;
    }
}

// A cart id or a key as a name in one folder: what cart_files.py writes, and
// never a path.
function writtenName(n) {
    return typeof n === "string" && !!n && n !== "." && n !== ".." && !/[\/\\\0]/.test(n);
}

async function rollWritten(dir, key) {
    const part = await dir.getFileHandle(key + PART);
    await writeIn(dir, key, new Uint8Array(await (await part.getFile()).arrayBuffer()));
    await dir.removeEntry(key + DONE);
    await dir.removeEntry(key + PART);
}

// Make one written file durable. Throws, with the old copy in place, when it
// could not be.
export async function commitWritten(w, cart, key, bytes) {
    if (!writtenName(cart) || !writtenName(key) || key.includes("~"))
        throw new Error("bad written file " + cart + "/" + key);
    const dir = await w.dir.getDirectoryHandle(cart, { create: true });
    try { await dir.removeEntry(key + DONE); } catch (e) { /* none */ }
    await writeIn(dir, key + PART, bytes);
    await writeIn(dir, key + DONE, new Uint8Array(0));
    await rollWritten(dir, key);
}

// One written file gone, or with `key` null every file the cart wrote.
export async function dropWritten(w, cart, key) {
    if (!writtenName(cart)) return;
    if (key === null || key === undefined) {
        try { await w.dir.removeEntry(cart, { recursive: true }); } catch (e) { /* none */ }
        return;
    }
    if (!writtenName(key)) return;
    let dir;
    try { dir = await w.dir.getDirectoryHandle(cart); } catch (e) { return; }
    try { await dir.removeEntry(key); } catch (e) { /* none */ }
}

// Every written file as {"<cart>/<key>": bytes}, after finishing each write a
// reload interrupted: one with its marker rolls forward, one without goes.
export async function readWritten(w) {
    const out = {};
    const carts = [];
    for await (const [name, h] of w.dir.entries()) if (h.kind === "directory") carts.push(name);
    for (const cart of carts) {
        const dir = await w.dir.getDirectoryHandle(cart);
        const names = [];
        for await (const [name, h] of dir.entries()) if (h.kind === "file") names.push(name);
        for (const n of names)
            if (n.endsWith(DONE) && names.includes(n.slice(0, -DONE.length) + PART))
                await rollWritten(dir, n.slice(0, -DONE.length));
        const left = [];
        for await (const [name] of dir.entries()) left.push(name);
        for (const n of left) {
            if (n.endsWith(PART) || n.endsWith(DONE)) {
                try { await dir.removeEntry(n); } catch (e) { /* gone */ }
                continue;
            }
            const fh = await dir.getFileHandle(n);
            out[cart + "/" + n] = new Uint8Array(await (await fh.getFile()).arrayBuffer());
        }
    }
    return out;
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

// A cart folder base from a zip's top directory or its file name: the cart's
// id, `<author>.<name>`, each part in the id's characters (lowercase a-z, 0-9
// and _, as moy_carts.slug() lands on), so an imported folder is one a board's
// store would have created itself. A name with no author part is the local
// author's (moy_store_base.USER_NS).
export function cartBase(name, author = "local") {
    let n = String(name || "cart").replace(/\.zip$/i, "").replace(/\.moy$/i, "");
    const part = (s) => s.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
    const dot = n.indexOf(".");
    const who = dot < 0 ? author : part(n.slice(0, dot)) || author;
    return part(who) + "." + (part(dot < 0 ? n : n.slice(dot + 1)) || "cart");
}

// moy_carts._unique_dir's rule, so an imported cart is named the way a
// duplicated one is: `base.moy`, then `base_2.moy`, `base_3.moy`...
export function uniqueCartDir(exists, base) {
    if (!exists(base + ".moy")) return base + ".moy";
    let i = 2;
    while (exists(base + "_" + i + ".moy")) i++;
    return base + "_" + i + ".moy";
}
