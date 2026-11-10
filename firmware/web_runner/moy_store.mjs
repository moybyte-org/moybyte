// The browser-local cart store (#193 mode 1) and Get Carts' installs into it
// (#124, `commitInstall` below). Its I/O only: what is seeded, skipped and
// named, and the .moy archive, are the VM's (docs/moycore_direction.md
// section 3; tests/test_web_io_ratchet.py).
//
// TWO WEB MODES, TOTAL, NO CROSSOVER (owner call 2026-08-25). A page served
// FROM a board edits the BOARD's store: the sweep's batches go out as
// POST /sync and nothing is kept locally. A page on a static host
// (moybyte.com, an export, file://) keeps the carts HERE, and this module is
// where they live.
//
// The engine is the same one either way: runtime/carts/moy_sync's StoreWatcher
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

// THE VM'S RULES, handed in by the worker's `bind` before any store is
// opened: every decision about what a path, a name or a root is.
//   roots           the sync roots (moy_sync.roots_json): each root's id, VFS
//                   path and served endpoint, the named files a root of them
//                   IS (`top`), whether every file is bytes (`binary`) and
//                   whether a site-mode page leaves it to another keeper
//                   (`site: false`, the apps' folders: commitWritten below);
//                   the files root names its folder of `songs`
//   skip(name, history)
//                   what never crosses THE WIRE (false) and what never reaches
//                   THE LOCAL STORE (true, which keeps the journal: in mode 1
//                   this store is of record, and the undo history lives with
//                   it): the native store's moy_store.skip (moy_fs.h)
//   path(root id, rel, dc)
//                   how many segments a root's store keeps `rel` as, 0 for
//                   none (moy_sync.local_segs: the receiving C's path rule and
//                   the root's shape)
//   folder(root id, rel)
//                   whether a root's store reads folder `rel` back, one a
//                   sweep walks (moy_sync.local_folder)
//   song(rel)       a files store's song (moy_sync.local_song)
//   plain(name)     a name an install may write (cart_index.plain_name)
//   record, stage   the names of Get Carts' record and of the folder an
//                   install is built in, beside the carts store
//                   (cart_index.RECORD_NAME, STAGE_DIR)
let rules = null;

export function bind(r) { rules = r; }

export function skipName(name) { return !!rules.skip(name, false); }

export function skipLocal(name) { return !!rules.skip(name, true); }

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

export function roots() { return rules.roots; }

export function rootById(id) {
    for (const r of rules.roots) if (r.id === id) return r;
    return null;
}

// The segments `rel` is in a store of `rootId`, or null when the VM's rule
// keeps no such path there.
function partsIn(rootId, rel, dc) {
    if (typeof rel !== "string" || !rel || !rules.path(rootId, rel, !!dc)) return null;
    return rel.split("/");
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
// A file read back is TEXT only when it is UTF-8 throughout, byte for byte:
// fatal, so a module or a WAD is never mangled into replacement characters,
// and the BOM kept, so writing the string back gives the same bytes.
const strict = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

// THE CHANNEL'S STORAGE ROOT. Pages that share an origin share its OPFS: the
// stable site (/moybyte/) and the dev site (/moybyte/dev/) both live on
// moybyte-org.github.io. A page published for a channel (site/build.py
// --channel, which stamps page_tail.js's MOY_CHANNEL) keeps every store under
// its own top-level directory, so a dev store format never reads or writes the
// stable site's carts. The worker opens every store through what this returns;
// the empty channel is the origin root, which is where the stable site, a
// board-served page and a local build keep theirs. A channel name that is not
// a plain word gets no storage at all (the console runs in memory and says so)
// rather than the origin root.
export const CHANNEL_DIR_PREFIX = "channel-";

export function channelNav(nav, channel) {
    if (!channel) return nav;
    if (!/^[a-z][a-z0-9-]{0,31}$/.test(channel)) return { storage: null };
    const st = nav && nav.storage;
    if (!st || typeof st.getDirectory !== "function") return { storage: null };
    return { storage: { getDirectory: async () =>
        (await st.getDirectory()).getDirectoryHandle(CHANNEL_DIR_PREFIX + channel,
                                                     { create: true }) } };
}

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

// A named file of a root of named files: the directory it sits in (made when
// `create`) and its name there.
async function namedAt(store, key, create) {
    const parts = key.split("/");
    return [await dirFor(store, parts.slice(0, -1), create), parts[parts.length - 1]];
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
    // The local rule, not the wire's: in site mode the sweep ships this
    // store's own journal to this store.
    const r = store.root;
    if (op.dc) {
        // A whole folder within the root's arity: a cart is one segment, a
        // files item a kind/name or a recording folder below it.
        const parts = partsIn(r.id, op.p, true);
        if (!parts) return "bad dc target";
        try { await removeAt(store, parts, true); } catch (e) { /* already gone */ }
        return null;
    }
    const parts = partsIn(r.id, op.p);
    if (!parts) return "bad path";
    const key = parts.join("/");
    // The console's own documents are written so a reload at any moment finds
    // the old copy or the new one (`commitFile`): a torn settings file would boot
    // the console on its defaults and lose every Setting. Cart files keep the
    // plain write: a cart's undo journal is the record of its last commits.
    const put = r.top ? async (data) => commitFile(...await namedAt(store, key, true), data)
                      : (data) => writeText(store, parts, data);
    if (op.d) {
        if (r.top) {
            try { await dropFile(...await namedAt(store, key, false)); } catch (e) { /* none */ }
        }
        else try { await removeAt(store, parts, false); } catch (e) { /* already gone */ }
        store.parts.delete(key);
        return null;
    }
    if (op.pub) {
        const buf = store.parts.get(key);
        if (buf === undefined) return "no staged parts";
        store.parts.delete(key);
        await put(buf);
        return null;
    }
    if (op.b !== undefined) {
        if (typeof op.b !== "string") return "no bytes";
        const bytes = fromBase64(op.b);
        if (op.part === undefined || op.part === null) {
            await put(bytes);
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
        await put(op.t);
        return null;
    }
    // Chunks accumulate in RAM and land in ONE write at `pub`, so a batch that
    // dies mid-file leaves the previous good copy untouched -- the same
    // guarantee the board gets from its .tmp staging, without a stray .tmp the
    // next sweep would have to learn to ignore.
    store.parts.set(key, op.part === 0 ? op.t : (store.parts.get(key) || "") + op.t);
    return null;
}

// Every syncable file in the local store as {rel: text, or its bytes when it
// is not UTF-8}: what a site-mode boot writes into the VFS before the VM
// decides what of the served bundle to add.
export async function readAll(store) {
    const out = {};
    if (store.root.top) {
        // A write a reload interrupted is finished or discarded first, in
        // each folder a named file sits in.
        const settled = new Map();
        for (const name of store.root.top) {
            const cut = name.lastIndexOf("/");
            const head = cut < 0 ? "" : name.slice(0, cut);
            if (!settled.has(head)) {
                let got = null;
                try {
                    const dir = await dirFor(store, head ? head.split("/") : [], false);
                    got = [dir, await settleFiles(dir)];
                } catch (e) { /* no such folder yet */ }
                settled.set(head, got);
            }
            const at = settled.get(head);
            const leaf = name.slice(cut + 1);
            if (!at || !at[1].includes(leaf)) continue;
            const fh = await at[0].getFileHandle(leaf);
            out[name] = asValue(new Uint8Array(await (await fh.getFile()).arrayBuffer()));
        }
        return out;
    }
    await walk(store.root.id, store.carts, "", out, store.root.songs ? rules.song : null);
    return out;
}

// A file's bytes as the value readAll hands out: its text when it is UTF-8
// throughout, else the bytes.
function asValue(bytes) {
    try { return strict.decode(bytes); } catch (e) { return bytes; }
}

// Each file of root `rootId`'s store under `dir` the VM's rules keep: a
// folder `rules.folder` reads, a file `rules.path` keeps (the journal comes
// BACK too), none `leave` names.
async function walk(rootId, dir, prefix, out, leave) {
    const entries = [];
    for await (const [name, handle] of dir.entries()) entries.push([name, handle]);
    entries.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    for (const [name, handle] of entries) {
        const rel = prefix ? prefix + "/" + name : name;
        if (handle.kind === "directory") {
            if (rules.folder(rootId, rel)) await walk(rootId, handle, rel, out, leave);
            continue;
        }
        if ((leave && leave(rel)) || !partsIn(rootId, rel)) continue;
        out[rel] = asValue(new Uint8Array(await (await handle.getFile()).arrayBuffer()));
    }
}

// THE SONGS: a files store's music folder keeps MP3s the VFS never holds
// (readAll leaves them out); Music reads one a range at a time
// (media_link.py), so a library never sits in the page's memory.

// {name: size} of the store's songs.
export async function songs(store) {
    const out = {};
    const music = store.root.songs;
    let dir;
    try { dir = await store.carts.getDirectoryHandle(music); } catch (e) { return out; }
    for await (const [name, handle] of dir.entries()) {
        if (handle.kind !== "file" || !rules.song(music + "/" + name)) continue;
        out[name] = (await handle.getFile()).size;
    }
    return out;
}

// A song's File (a Blob to slice), or null.
export async function songFile(store, name) {
    const music = store.root.songs;
    if (typeof name !== "string" || !rules.song(music + "/" + name)) return null;
    try {
        const dir = await store.carts.getDirectoryHandle(music);
        return await (await dir.getFileHandle(name)).getFile();
    } catch (e) { return null; }
}

// A dropped song kept: its name, or null for one that is not a song.
export async function keepSong(store, name, bytes) {
    const base = String(name).slice(String(name).lastIndexOf("/") + 1);
    if (!rules.song(store.root.songs + "/" + base)) return null;
    await writeText(store, [store.root.songs, base], bytes);
    return base;
}

// One file of a cart store removed; one already gone is no error.
export async function removeFile(store, rel) {
    const parts = partsIn(store.root.id, rel);
    if (!parts) return;
    try { await removeAt(store, parts, false); } catch (e) { /* already gone */ }
}

// First visit: adopt the served carts.json as the local baseline.
export async function seed(store, carts) {
    let n = 0;
    for (const rel in carts) {
        const parts = partsIn(store.root.id, rel);
        const data = fileData(carts[rel]);
        if (!parts || data === null) continue;
        if (store.root.top) await commitFile(...await namedAt(store, rel, true), data);
        else await writeText(store, parts, data);
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

// `store` is the carts store (openStore(nav, "carts")); `files` is
// [{name, data: Uint8Array}], the staged folder's whole contents; `record` is
// the record's new text. Resolves once the install is durable; throws, having
// changed nothing on the shelf, when it could not be made so.
export async function commitInstall(store, folder, files, record) {
    if (!rules.plain(folder)) throw new Error("bad folder " + folder);
    for (const f of files) if (!rules.plain(f.name)) throw new Error("bad file " + f.name);
    const stage = await store.dir.getDirectoryHandle(rules.stage, { create: true });
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
        await writeIn(store.dir, rules.record, m.record);
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
    try { stage = await store.dir.getDirectoryHandle(rules.stage); } catch (e) { return 0; }
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
    await writeIn(store.dir, rules.record, text);
}

export async function readRecord(store) {
    try {
        const fh = await store.dir.getFileHandle(rules.record);
        return await (await fh.getFile()).text();
    } catch (e) {
        return null;
    }
}

// ---------------------------------------------------------------------------
// One file, old or new: the bytes go to "<key>~part", an empty "<key>~done"
// then says they are whole, and only then do they replace "<key>" -- so a
// reload at any moment finds the old copy or the new one, and `settleFiles`
// finishes or discards what one left. A compiled cart's written files and the
// console's own documents (system/settings and its siblings) are kept this way.
// ---------------------------------------------------------------------------

const PART = "~part", DONE = "~done";

async function rollFile(dir, key) {
    const part = await dir.getFileHandle(key + PART);
    await writeIn(dir, key, new Uint8Array(await (await part.getFile()).arrayBuffer()));
    await dir.removeEntry(key + DONE);
    await dir.removeEntry(key + PART);
}

async function hasFile(dir, name) {
    try { await dir.getFileHandle(name); return true; } catch (e) { return false; }
}

// Make `key` in `dir` hold `data`. Throws, with a whole copy in place (the old
// one, or a committed one the next settle rolls forward), when it could not.
async function commitFile(dir, key, data) {
    // A commit that failed in this session after its marker landed (a quota
    // error mid-roll, not a reload) finishes first: dropping its marker now
    // would leave "<key>" as torn as that roll left it.
    if (await hasFile(dir, key + DONE)) {
        if (await hasFile(dir, key + PART)) await rollFile(dir, key);
        else await dir.removeEntry(key + DONE);
    }
    await writeIn(dir, key + PART, data);
    await writeIn(dir, key + DONE, new Uint8Array(0));
    await rollFile(dir, key);
}

// `key` gone, with any commit of it a reload left.
async function dropFile(dir, key) {
    for (const n of [key + DONE, key, key + PART]) {
        try { await dir.removeEntry(n); } catch (e) { /* none */ }
    }
}

// Finish every commit in `dir` whose marker landed, discard every other
// "~part"/"~done", and return the names of the files left.
async function settleFiles(dir) {
    const names = [];
    for await (const [name, h] of dir.entries()) if (h.kind === "file") names.push(name);
    for (const n of names)
        if (n.endsWith(DONE) && names.includes(n.slice(0, -DONE.length) + PART))
            await rollFile(dir, n.slice(0, -DONE.length));
    const left = [];
    for await (const [name, h] of dir.entries()) if (h.kind === "file") left.push(name);
    const out = [];
    for (const n of left) {
        if (n.endsWith(PART) || n.endsWith(DONE)) {
            try { await dir.removeEntry(n); } catch (e) { /* gone */ }
            continue;
        }
        out.push(n);
    }
    return out;
}

// ---------------------------------------------------------------------------
// A compiled cart's written files (moy-spec SPEC.md 16.12), in OPFS beside the
// carts: apps/<id>/<key>, runtime/carts/cart_files.py's layout, so the VFS the
// console reads and this store of record hold the same names. The console
// writes the VFS and queues the file here (carts_link's WebCartKeep), and a
// write lands with `commitFile`'s crash-safety, so readWritten finds the old
// copy or the new one.
// ---------------------------------------------------------------------------

// The written store is the apps root's (openStore(nav, "apps")).

// Make one written file durable. Throws, with the old copy in place, when it
// could not be.
export async function commitWritten(w, cart, key, bytes) {
    if (typeof cart !== "string" || !partsIn(w.root.id, cart + "/" + key))
        throw new Error("bad written file " + cart + "/" + key);
    const dir = await w.carts.getDirectoryHandle(cart, { create: true });
    await commitFile(dir, key, bytes);
}

// One written file gone, or with `key` null every file the cart wrote.
export async function dropWritten(w, cart, key) {
    if (key === null || key === undefined) {
        if (!partsIn(w.root.id, cart, true)) return;
        try { await w.carts.removeEntry(cart, { recursive: true }); } catch (e) { /* none */ }
        return;
    }
    if (typeof cart !== "string" || !partsIn(w.root.id, cart + "/" + key)) return;
    let dir;
    try { dir = await w.carts.getDirectoryHandle(cart); } catch (e) { return; }
    try { await dir.removeEntry(key); } catch (e) { /* none */ }
}

// Every written file as {"<cart>/<key>": bytes}, after finishing each write a
// reload interrupted: one with its marker rolls forward, one without goes.
export async function readWritten(w) {
    const out = {};
    const carts = [];
    for await (const [name, h] of w.carts.entries()) if (h.kind === "directory") carts.push(name);
    for (const cart of carts) {
        const dir = await w.carts.getDirectoryHandle(cart);
        for (const n of await settleFiles(dir)) {
            const fh = await dir.getFileHandle(n);
            out[cart + "/" + n] = new Uint8Array(await (await fh.getFile()).arrayBuffer());
        }
    }
    return out;
}

