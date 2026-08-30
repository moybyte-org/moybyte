// The console, off the main thread (#176 smoothness).
//
// WHY: the browser's main thread must only REPLAY and BLIT. When the wasm console
// stepped inside the rAF callback, a frame cost ~7ms of console work + ~2ms of blit
// on a 16.7ms budget, so any worst-case frame (a full repaint, a GC sweep) missed
// vsync and dropped -- visible judder, even though total frame cost was LOWER than
// the host web console's. That transport looks smoother precisely because its
// console runs in another process and the page only replays. This worker gives the
// wasm the same division of labour, minus the network.
//
// The worker owns the VM and self-drives the frame loop, POSTING frames as they are
// ready (the push model the WebSocket transport uses). The page keeps only the most
// recent frame, so a slow main thread drops stale frames instead of queueing them.
//
// SINCE MOYCORE STAGE 4 the worker ships PIXELS, not draw commands: the wasm
// rasterizes into an RGB565 framebuffer (moy_gfx, the boards' own kernel) and
// this loop hands the finished bytes to the page, which blits them. The page's
// JS replayer, the wire protocol and the per-surface delta are gone.
//
// Protocol -- main -> worker:
//   {t:"init", search}   boot the console; `search` is location.search (tier + cart)
//   {t:"input", json}    an {"events":[...]} batch, applied before the next step
//   {t:"ahead", v}       the page's scheduled-ahead audio depth, seconds (-1 = none)
//   {t:"run"}            start stepping (the page's play-button gesture)
//   {t:"fbret", b}       a framebuffer being handed BACK for reuse (see below)
//   {t:"reload"}         dev hot-reload: re-read carts.json/files.json, restart
//   {t:"kept", state}    the page's answer to {t:"keep"}: "granted" | "denied"
//   {t:"carts"}          list the cart folders (the export picker)
//   {t:"export", cart}   zip one cart out of the VFS
//   {t:"import", name, buf}  a dropped file into the store: a .moy zip, or a
//                        PICO-8 .p8 / .p8.png, which is CONVERTED first (#194)
//   {t:"edit", cart, tab}  open a cart in the Editor (the import report's
//                        action); `tab` optionally lands on paint/map/music
// worker -> main:
//   {t:"status", s}      boot progress text
//   {t:"assets", json}   the page's metadata payload (size/title/audio/input)
//   {t:"frame", s, fb}   frame metadata + the RGB565 framebuffer, TRANSFERRED
//   {t:"error", s}       fatal: the page shows it and stops
//   {t:"persist", mode, s}   where carts are being kept, in the page's words
//   {t:"keep"}           site mode: ask the browser to make this origin
//                        durable. Sent to the PAGE because storage.persist()
//                        is window-only -- a Worker cannot ask, only look.
//   {t:"pin", tried}     this board is PINNED and this page cannot read it, so
//                        the boot stopped before the VM: the page prompts.
//                        `tried` = a pin WAS offered and refused (a wrong one),
//                        which is the difference between "type it" and "that
//                        one did not work".
//   {t:"carts", names}   the shelf's folder names
//   {t:"exported", name, buf}  a .moy zip, TRANSFERRED
//   {t:"imported", s, ok, report, dir}  the result of an import, in the page's
//                        words; `report` is the p8 compatibility summary (the
//                        shared writer's own lines) when there was one
//   {t:"edited", s, ok}  the result of an "open in editor"
import { loadMicroPython } from "./micropython.mjs";
import * as store from "./moy_store.mjs";

let mp = null, step = null, applyEvents = null, assets = null, reload = null;
// The p8 drop's two Python entry points (#194), bound at boot like the rest.
let importP8Json = null, editCart = null, openCart = null;
let wantAssets = false;   // an assets request that arrived before the VM was up
let idleCollect = null;
let fbAddr = null, fbLen = null;
// The 3.4 sync push: about once a second, ask the console (moy_sync's
// watchers) for a batch of committed changes and POST it to the RELATIVE
// /sync -- a page served from a board writes back to that board; one served
// by a static host gets a 404/405/501 on the first try and push turns off
// for good. ONE batch in flight at a time, so ops arrive in order and a
// failed send simply requeues on the Python side (syncAck false).
//
// The batch is an OPAQUE string here, which is what lets the carts store and
// the #108 user files share this pump untouched: which root a batch speaks
// for is inside the body, chosen by web_boot, and the disable-on-404 above is
// about the ENDPOINT, not about either store. Whether the files half runs at
// all is settled once at boot by the files.json fetch (see writeFiles).
let syncPoll = null, syncAck = null, syncOff = null, rescan = null;
let syncBusy = false, lastSyncAt = 0;
const SYNC_MS = 1000;
// How long the board may stay silent before this page admits it is gone. A
// board that is merely busy serving can miss a few sweeps, and 15 seconds of
// silence is not busy.
//
// TIME, NOT A COUNT (2026-08-30). It was "15 consecutive failed pushes", which
// is 15 seconds ONLY while there is something to push every second -- and a
// page nobody is typing into pushes NOTHING, so the counter never advanced and
// an idle tab watching a board that had been switched off sat there for
// minutes. Reported from a real session: "I turned off wasm while I was
// connected... I only got this after like a minute or more". A clock cannot
// have that bug, and it also makes the sentence below TRUE, which the count
// only was by coincidence.
const SYNC_GIVE_UP_MS = 15000;
// What an idle page asks with. GET /sync is the capability marker: open on a
// pinned board by design, no store walk behind it, `{"sync":1}` either way --
// the cheapest question that has an answer. Slower than the push sweep because
// nothing is waiting on it; still well inside the give-up.
const HEARTBEAT_MS = 3000;
let lastHeartbeatAt = 0;
// The clock the give-up reads: when the board last answered ANYTHING.
let lastSyncOkAt = 0;
let syncFailing = false;      // at least one failure since the last answer
// Is there work this page holds that the board has not taken? Set when a poll
// hands us a batch, cleared when one is accepted. THIS is what decides whether
// a disconnect is frightening or merely inconvenient -- see syncLost.
let outstanding = false;
let syncLostSaid = false;

function syncFailed(now) {
    syncFailing = true;
    if (!lastSyncOkAt) lastSyncOkAt = now;      // first contact never made
    if (now - lastSyncOkAt >= SYNC_GIVE_UP_MS) syncLost();
}

function syncOk(now) {
    lastSyncOkAt = now;
    syncFailing = false;
    syncLostSaid = false;
}

// The board stopped answering. In BOARD MODE this page keeps no local store by
// design, so whatever has not been pushed lives only in this tab -- which is
// why this is the one disconnect that can carry a data-loss warning.
//
// CAN, not does. The warning is for work at risk, and this page KNOWS whether
// it is holding any: `outstanding` is true only when a batch was polled and
// never accepted. Telling an idle reader that "anything you changed in the last
// few seconds is only in this tab" when they changed nothing is the cry-wolf
// half of the same mistake the expected/lost split exists to avoid -- and it
// was what a real session got after switching the console off deliberately.
function syncLost(head, body) {
    if (syncLostSaid) return;
    syncLostSaid = true;
    persist("none", outstanding
        ? "the console is not answering -- recent changes are only here"
        : "the console is not answering");
    self.postMessage({ t: "lost", kind: "lost", risk: outstanding,
        head: head || "the console stopped answering",
        body: body || ("It has not answered for "
                       + Math.round(SYNC_GIVE_UP_MS / 1000) + " seconds.") });
}
// #9 physical I/O: the same pump shape as the sync push, at a physical-I/O
// rate. A cart's pin_write QUEUES (gpio_link.py: it must never stall a frame
// on the wire), so the pace here IS the latency a kid feels between the cart
// deciding and the LED changing. 33ms is one console frame; below that the
// round trip is the limit anyway, and above it a blink starts to look laggy.
// The busy flag is what keeps it honest: one POST in flight, and whatever
// happened while it was away coalesces into the next batch.
let gpioPoll = null, gpioAck = null, gpioOff = null;
let gpioBusy = false, lastGpioAt = 0;
const GPIO_MS = 33;
// The board's UPDATER, reached through the same shape (#41/#53). One second,
// not GPIO's 33ms: nothing here is felt by a running cart, and the numbers this
// carries change on the scale a flash write takes. The pump only speaks when a
// SCREEN is waiting -- `updateWants` is false on an idle console, so a page
// nobody is updating from never touches the wire.
let updatePoll = null, updateWants = null, updateAck = null, updateOff = null;
let updateBusy = false, lastUpdateAt = 0;
const UPDATE_MS = 1000;
// MODE 1 (#193): the same batches, applied into OPFS instead of POSTed. `mode`
// is decided ONCE at boot, before anything is written, because it decides where
// the VFS is seeded FROM -- a board's carts.json, or the browser's own store.
//   "board"  a console served this page; it owns the carts, nothing is kept here
//   "site"   a static host; OPFS is the truth and survives the reload
//   "none"   site mode with no usable OPFS: in memory, and the page SAYS so
// ONE OPFS store per registered root (site mode), keyed by root id -- so the
// carts store and the #108 files store persist side by side and a new root
// would be one more entry, not a second variable. null for a root whose OPFS
// could not be opened (that root then runs in memory; the page says so).
let mode = null, persistFails = 0, persistSaid = false;
const opfsStores = {};
let persistBatches = 0;
const PERSIST_GIVE_UP = 3;
// What the browser answered when asked to KEEP this origin (site mode only;
// moy_store's requestPersistence has the three outcomes). "granted" is the
// only one that lets the chip say the carts are safe here -- every other one
// means saved-but-evictable, and #193's rule is that eviction is never the
// silent kind. `sitePartial` is the seed tally's "some roots had no OPFS", kept
// so a later batch cannot quietly upgrade a partial store's sentence.
let keep = null, sitePartial = false;
let running = false, ahead = -1, inbox = [];
// Framebuffer ping-pong. A painted frame is copied out of the wasm heap into a
// plain ArrayBuffer and TRANSFERRED to the page (zero-copy handoff); the page
// posts the buffer back once it has blitted, and we reuse it. Allocating a
// fresh 1.2MB buffer 60 times a second instead would hand V8 a major GC to do
// during play, which is exactly the class of hitch this build spent months
// removing on the Python side.
//
// SharedArrayBuffer would avoid the copy entirely and is deliberately NOT used:
// it requires COOP/COEP response headers, and dist/ must stay a folder of
// static files anyone can host (GitHub Pages included).
let fbPool = [];
const FRAME_MS = 1000 / 60;
// GC scheduling (the surface-model gate-0 finding): web_boot raises the GC
// trigger from the port's 16KB (which made EVERY painted frame pay a full
// ~67ms collect at this boundary) to megabytes, so the WORKER now owns WHEN
// collects land: on the 3rd consecutive quiet frame (idle -- the hitch is
// invisible), or after COLLECT_MAX_FRAMES of sustained activity (a game that
// never idles must still collect eventually; one bounded hitch beats heap
// growth -- SPLIT_HEAP_AUTO grows the heap rather than collecting).
const IDLE_QUIET_FRAMES = 3, COLLECT_MAX_FRAMES = 600;
let quietStreak = 0, framesSinceCollect = 0, idleCollected = false;
// Worker-side perf accounting, reported every WP_MS and printed by the page's
// plog line: step mean/max, painted frames, >20ms stalls, and the cost of the
// out-of-band calls (assets/collect/input) that block this same loop.
const WP_MS = 2000;
let WP = { n: 0, sum: 0, max: 0, maxB: 0, painted: 0, bytes: 0, slow: 0,
           assets: 0, assetsN: 0, gc: 0, gcN: 0, inp: 0, inpN: 0, t: 0 };
function wpFlush() {
    const now = performance.now();
    if (!WP.t) { WP.t = now; return; }
    const dt = (now - WP.t) / 1000;
    if (dt <= 0) return;
    self.postMessage({ t: "wperf", s:
        "step " + (WP.n ? WP.sum / WP.n : 0).toFixed(2) + "/" + WP.max.toFixed(1) +
        " ms mean/max (worst frame " + (WP.maxB / 1024).toFixed(1) + "KB)" +
        " | steps " + (WP.n / dt).toFixed(1) + "/s painted " + (WP.painted / dt).toFixed(1) +
        "/s | >20ms " + WP.slow +
        " | assets " + WP.assetsN + "x" + (WP.assetsN ? WP.assets / WP.assetsN : 0).toFixed(0) +
        "ms gc " + WP.gcN + "x" + (WP.gcN ? WP.gc / WP.gcN : 0).toFixed(0) +
        "ms input " + WP.inpN + "x" + (WP.inpN ? WP.inp / WP.inpN : 0).toFixed(1) + "ms" });
    WP = { n: 0, sum: 0, max: 0, maxB: 0, painted: 0, bytes: 0, slow: 0,
           assets: 0, assetsN: 0, gc: 0, gcN: 0, inp: 0, inpN: 0, t: now };
}
setInterval(wpFlush, WP_MS);

function say(s) { self.postMessage({ t: "status", s: s }); }

function mkdirs(p) {
    let cur = "";
    for (const part of p.split("/")) {
        if (!part) continue;
        cur += "/" + part;
        try { mp.FS.mkdir(cur); } catch (e) { /* exists */ }
    }
}

// The carts VFS root, from the registry (store.ROOTS) so there is one source of
// the path. Only the carts-specific export/import code below names a root
// directly; everything else iterates store.ROOTS.
const CARTS_ROOT = store.rootById("carts").vfs;

function writeStore(root, files) {
    // mkdirs(root) even for an empty set: a root with no files YET (a board's
    // files layer with no drawings; a site-mode files store on first visit)
    // must still create its directory, because web_boot builds a watcher only
    // for a root whose store is present -- the empty dir IS the capability
    // signal, and skipping it would leave the kid's first drawing with nothing
    // watching for it.
    mkdirs(root);
    for (const rel in files) {
        const full = root + "/" + rel;
        mkdirs(full.slice(0, full.lastIndexOf("/")));
        mp.FS.writeFile(full, files[rel]);
    }
}

// `d` is the DETAIL -- how many files moved and how long it took. It rides the
// message rather than a console.log because that is the only form a test (or a
// curious owner) can read back: log lines are trimmed by whatever is capturing
// them, and this is the evidence that the store is actually being written.
function persist(m, s, d) {
    mode = m;
    self.postMessage({ t: "persist", mode: m, s: s, d: d || "", n: persistBatches });
    console.log("[moy] persist: " + m + " -- " + s + (d ? " (" + d + ")" : ""));
}

// The SITE-mode sentence, in one place: the boot says it, every later batch
// repeats it, and the page's answer to {t:"keep"} re-says it. It promises
// exactly what the browser granted -- an evictable store is still a store, and
// which one this is has to be readable BEFORE a cart goes missing, not after.
function siteSaid() {
    const base = sitePartial ? "carts saved here (some kinds could not)"
                             : "carts & drawings saved in this browser";
    if (keep && keep.state === "granted") return base;
    return base + " -- the browser may clear them if space runs short";
}

// ...and one site-mode REPORT, so the two halves of the detail line cannot come
// apart. `siteDetail` is the evidence of the last thing that happened to the
// store ("loaded in 79ms", "carts 3 ops in 5.6ms"); the storage note is appended
// at SEND time, because the page's answer lands whenever it lands. Re-emitting
// with the note ALONE would drop that evidence the moment a late answer arrives,
// and the evidence is what the E2E reads back.
let siteDetail = "";
function sitePersist(detail) {
    if (detail !== undefined) siteDetail = detail;
    const note = store.storageNote(keep);
    persist("site", siteSaid(), siteDetail + (siteDetail && note ? ", " : "") + note);
}

// The page asked on our behalf (see initStore) and this is what it got. Re-word
// the chip, but only while site mode is still the truth: a store that has since
// given up is saying something more important.
function keptAnswer(state) {
    if (!keep || (state !== "granted" && state !== "denied")) return;
    keep.state = state;
    if (mode === "site") sitePersist();
}

// Seed ONE root into the VFS, and in site mode into that root's OWN OPFS store.
// `data` is the served bundle for this root ({} / null when the host does not
// serve it). Returns "board" | "site" | "none" for the caller's tally, and sets
// opfsStores[root.id]. The carts and files roots go through the identical body,
// which is the whole point of the registry: files persistence is not a second
// path, it is this path run once more.
async function seedRoot(root, data, site) {
    data = data || {};
    if (!site) {                             // board mode: the console owns it
        writeStore(root.vfs, data);
        return "board";
    }
    const s = await store.openStore(navigator, root.id);
    opfsStores[root.id] = s;
    if (!s) {                                // no OPFS for this root: in memory
        writeStore(root.vfs, data);
        return "none";
    }
    try {
        if (await store.isEmpty(s)) {
            // First visit: the served bundle is the seed AND the baseline, so
            // the shelf is never empty and the first sweep has nothing to say.
            // On a static host the files bundle is empty, which correctly seeds
            // an empty files store the kid's first drawing then persists into.
            writeStore(root.vfs, data);
            await store.seed(s, data);
            return "seed";
        }
        // The local store WINS over the served bundle: it is the kid's work, and
        // the bundle is only ever the factory seed. "load" (vs "seed") is the
        // evidence that a reload READ FROM local rather than re-seeding fresh.
        writeStore(root.vfs, await store.readAll(s));
        return "load";
    } catch (e) {
        writeStore(root.vfs, data);
        opfsStores[root.id] = null;
        console.log("[moy] persist: OPFS failed for " + root.id + " -- " + e);
        return "none";
    }
}

// Decide the world and seed EVERY registered root. Runs BEFORE the console
// boots, because web_boot constructs a StoreWatcher over each root and rebases
// on what it finds: seed first and the baseline is correct with nothing pending,
// seed after and the whole store ships as "changed".
async function initStore(fetched) {
    const t0 = performance.now();
    const site = (await store.probeMode((u, o) => fetch(u, o))) !== "board";
    // Ask the browser to KEEP this origin BEFORE a byte is written -- OPFS is
    // best-effort and an origin under pressure is evicted whole. Here in the
    // Worker this can only READ the answer (persist() is window-only), so an
    // origin that is not already durable gets a {t:"keep"} sent to the page,
    // which can ask; its {t:"kept"} re-words the chip when it lands. That half
    // is deliberately NOT awaited: Firefox raises a permission PROMPT on
    // persist(), and a console that boots behind a dialog looks hung.
    keep = site ? await store.requestPersistence(navigator) : null;
    if (keep && keep.state !== "granted") self.postMessage({ t: "keep" });
    let anySite = false, anyNone = false, loaded = false;
    for (const root of store.ROOTS) {
        const data = fetched[root.id];
        // A sibling root the host does not serve (files.json 404 -> null): in
        // BOARD mode skip it (no watcher, no empty dir, exactly today's
        // capability rule); in SITE mode still give it an empty OPFS store, so a
        // drawing the kid makes locally has somewhere to persist.
        if (!site && data == null && root.id !== "carts") continue;
        const w = await seedRoot(root, data, site);
        if (w === "seed" || w === "load") anySite = true;
        if (w === "load") loaded = true;
        if (w === "none") anyNone = true;
    }
    if (!site) {
        persist("board", "carts are kept on the console");
    } else if (!anySite) {
        // No OPFS at all: a private window, blocked site data, file://. The
        // pre-#193 behaviour, but never silently -- the page says it out loud.
        persist("none", "this browser has no local storage: work will NOT survive a reload");
    } else {
        // "loaded" once any root read the kid's work back from OPFS (a revisit);
        // "seeded" on a first visit. The word is the persistence evidence the
        // E2E reads back, and the storage note behind it is the OTHER evidence:
        // whether the browser agreed to keep any of it, and how close the store
        // is to the quota it would be evicted at.
        sitePartial = anyNone;
        sitePersist((loaded ? "loaded" : "seeded") + " in "
                    + (performance.now() - t0).toFixed(0) + "ms");
    }
}

function vfsIsDir(p) {
    const m = mp.FS.stat(p).mode;
    return typeof mp.FS.isDir === "function" ? mp.FS.isDir(m) : (m & 0o170000) === 0o040000;
}

function vfsExists(p) {
    try { mp.FS.stat(p); return true; } catch (e) { return false; }
}

function cartNames() {
    const out = [];
    if (!mp) return out;
    for (const name of mp.FS.readdir(CARTS_ROOT)) {
        if (name === "." || name === ".." || store.skipName(name)) continue;
        try { if (vfsIsDir(CARTS_ROOT + "/" + name)) out.push(name); } catch (e) { }
    }
    return out.sort();
}

// The cart as it sits in the VFS RIGHT NOW -- the live shelf, not the local
// store, which lags it by up to one sweep. Skip-filtered at every level, so an
// exported zip carries exactly what a board would have been sent.
function walkVfs(path, prefix, out, depth) {
    if (depth > 6) return;
    for (const name of mp.FS.readdir(path)) {
        if (name === "." || name === ".." || store.skipName(name)) continue;
        const full = path + "/" + name;
        const rel = prefix + "/" + name;
        if (vfsIsDir(full)) walkVfs(full, rel, out, depth + 1);
        else out.push({ name: rel, data: mp.FS.readFile(full) });
    }
}

// Unzip into the store under a collision-safe name, then re-scan the shelf
// WITHOUT rebasing the watcher: the imported files must stay pending so the
// very next sweep writes them to the local store like any other commit.
async function importZip(name, buf) {
    const entries = await store.unzip(new Uint8Array(buf));
    if (!entries.length) throw new Error("no files in that zip");
    const top = store.zipTopDir(entries);
    const dir = store.uniqueCartDir((n) => vfsExists(CARTS_ROOT + "/" + n),
                                    store.cartBase(top || name));
    let n = 0;
    for (const e of entries) {
        const rel = top ? e.name.slice(top.length + 1) : e.name;
        if (!rel) continue;
        const parts = store.safeSegments(dir + "/" + rel);
        if (!parts) continue;                 // traversal, or a skip-listed name
        const full = CARTS_ROOT + "/" + parts.join("/");
        mkdirs(full.slice(0, full.lastIndexOf("/")));
        mp.FS.writeFile(full, e.data);
        n++;
    }
    if (!n) throw new Error("nothing in that zip looked like a cart");
    if (rescan) rescan();
    return { dir, n };
}

// -- the PICO-8 drop (#194) --------------------------------------------------
//
// A `.p8` / `.p8.png` is CONVERTED on the way in and is an ordinary editable
// `.moy` from then on -- there is deliberately no "run a p8 directly" tier
// (issue #194, 2026-08-28): a cart you can play but not open would be the one
// thing on this console that lies about what the console is.
//
// NOTHING IS CONVERTED IN JAVASCRIPT HERE, and that is the load-bearing part.
// The page could reach a `.p8.png`'s ROM without inflating anything --
// createImageBitmap + getImageData hands over the pixels and the low bits are
// two lines of JS -- and it would be a SECOND reader of a format the vendored
// converter already reads. That is the shape that cost this repo ten days of
// carts imported two octaves flat. So the bytes go into the VFS and the
// converter reads them there, in Python, exactly as it does on a desktop.
const P8_TMP = "/moy/tmp";

// Which importer a dropped file goes to. The NAME decides for a text `.p8`,
// but the BYTES have to decide for the PNG form: a BBS cart is downloaded as
// `foo.p8.png` and saved by a human as anything at all, and the file that
// arrives is a cart either way. There is no ambiguity to resolve -- the only
// other thing this page imports is a `.moy` export, which is a zip and starts
// "PK" -- so a dropped PNG is a PICO-8 cart attempt, and `png_problem` is what
// says so kindly when it turns out to be a holiday photo.
function isP8Drop(name, buf) {
    const n = String(name || "").toLowerCase();
    if (n.endsWith(".p8") || n.endsWith(".p8.png")) return true;
    if (!buf || buf.byteLength < 8) return false;
    const b = new Uint8Array(buf, 0, 8);
    return b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4E && b[3] === 0x47;
}

// The store's own folder rule, over a p8 file name. `cartBase` strips `.zip`
// and `.moy` (it was written for the zip path), so the p8 suffixes come off
// here -- otherwise `celeste.p8.png` becomes the folder `celeste_p8_png.moy`.
function p8Base(name) {
    return store.cartBase(String(name || "cart")
        .replace(/\.p8\.png$/i, "").replace(/\.p8$/i, ""));
}

async function importP8(name, buf) {
    if (!importP8Json) throw new Error("this console cannot import PICO-8 carts");
    const dir = store.uniqueCartDir((n) => vfsExists(CARTS_ROOT + "/" + n),
                                    p8Base(name));
    mkdirs(P8_TMP);
    // One fixed path rather than the dropped name: the name is attacker-shaped
    // text (it came off a file a browser handed us) and the converter only ever
    // needs SOME path to read. The real name still travels as `name`, which is
    // what upstream titles the cart from when its Lua has no title comment.
    const tmp = P8_TMP + "/drop";
    mp.FS.writeFile(tmp, new Uint8Array(buf));
    const r = JSON.parse(importP8Json(tmp, name, CARTS_ROOT + "/" + dir));
    if (!r.ok) return { ok: false, report: r.report || ["that cart could not be imported"] };
    // Same rule as importZip: do NOT rebase the sync watcher. An import is a
    // CHANGE, so the new files must stay pending and reach the local store on
    // the next sweep like any other commit.
    if (rescan) rescan();
    return { ok: true, dir, title: r.title, report: r.report };
}

// THE PIN, on every request that needs it. A GET has nowhere else to carry a
// credential, so it rides the query -- the same `?pin=` the page itself was
// opened with (a QR scan, or the prompt's remembered value). A page with no pin
// asks for the bare url and finds out from the answer.
let pin = null;
const withPin = (url) => (pin ? url + (url.indexOf("?") >= 0 ? "&" : "?")
                              + "pin=" + encodeURIComponent(pin) : url);

async function init(search) {
    const qs = new URLSearchParams(search || "");
    // Read BEFORE the first fetch, not after: since 2026-08-25 the board's read
    // half is gated too, so carts.json is the first request that needs it.
    pin = qs.get("pin") || null;
    say("loading vm...");
    // 16MB: the per-frame GC sweep scales with heap SIZE under this build's
    // GC_SPLIT_HEAP_AUTO (~0.13ms/MB/frame, #176), so a generous heap is a
    // permanent frame tax, not headroom.
    mp = await loadMicroPython({ heapsize: 16 * 1024 * 1024,
        stdout: (l) => console.log("[moy]", l) });
    say("loading console...");
    // FROZEN-first, like the page was: a ship build bakes the console into the wasm
    // and has no modules.json; a --stage-only dev dist adds one, and loading it into
    // /modules (first on sys.path) shadows the frozen copies.
    const [mods, cartsRes, files] = await Promise.all([
        fetch("modules.json").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch(withPin("carts.json")),
        fetch(withPin("files.json")).then((r) => r.ok ? r.json() : null)
            .catch(() => null)]);
    if (cartsRes.status === 403) {
        // A PINNED BOARD, and this page cannot read it. Stop the boot here --
        // there is nothing to boot, and seeding the VFS from an error body
        // would build a shelf out of the refusal. The page prompts; a submitted
        // pin comes back as a fresh load with ?pin= on it.
        say("this console needs a pin");
        self.postMessage({ t: "pin", tried: !!pin });
        console.log("[moy] carts.json refused the pin");
        return;
    }
    const carts = await cartsRes.json();
    let boot = "";
    if (mods) {
        mkdirs("/modules");
        for (const n in mods) mp.FS.writeFile("/modules/" + n, mods[n]);
        boot = "import sys\nsys.path.insert(0, '/modules')\n";
    }
    await initStore({ carts: carts, files: files });
    say("booting console...");

    let cart = qs.get("cart");
    const names = [...new Set(Object.keys(carts).map((k) => k.split("/")[0]))];
    if (!cart && names.length === 1) cart = names[0];
    // TIER SELECT (#73/#175). The DESKTOP desk is the default everywhere (owner
    // call 2026-08-14) -- on moybyte.com and on a board serving its own console
    // alike. It was the handheld 320x240 tier, which made the full-size product
    // the thing you had to know a url parameter to see.
    //   ?handheld=1  the 320x240 console (the phone-shaped one)
    //   ?size=WxH    override the desk's panel size
    // A single ?cart=<name> still implies handheld: that url is the one-cart
    // player (moy export / the gallery), where a desk around one game is chrome
    // nobody asked for.
    const desktop = qs.get("handheld") ? null : (qs.get("desktop") || !cart);
    let dw = 1024, dh = 600;
    const szm = /^(\d+)x(\d+)$/.exec(qs.get("size") || "");
    if (szm) { dw = +szm[1]; dh = +szm[2]; }
    const bootArgs = desktop
        ? ", None, " + dw + ", " + dh + ", True"
        : (cart ? ", cart=" + JSON.stringify(cart) : "");
    // #9: does whoever served this page have PINS? ONE probe, here, before the
    // console exists -- so `pin_write`/`pin_read` are decided once and a cart
    // either has the names or has never heard of them. An empty batch is the
    // probe (the board answers it with its allowlist), which also means the
    // probe passes through the PIN gate: a page opened without the board's
    // ?pin= is refused now rather than on every write it later makes.
    const gpioPins = await probeGpio(pin);
    const updateDoc = await probeUpdate(pin);
    mp.runPython(boot + "import web_boot\n"
        // BEFORE boot(): the mode is what decides whether the carts watcher
        // sweeps the journal, and boot() is where that watcher is built.
        // `mode || ""` and not `mode`: JSON.stringify(null) is the token `null`,
        // which is a NameError in the Python this string becomes.
        + "web_boot.store_mode(" + JSON.stringify(mode || "") + ")\n"
        + (gpioPins ? "web_boot.gpio_enable(" + JSON.stringify(JSON.stringify(gpioPins)) + ")\n" : "")
        + "web_boot.boot('/moy/carts'" + bootArgs + ")\n"
        // AFTER boot(), unlike gpio_enable: this one hangs the updater on the
        // live Workstation, which boot() is what creates.
        + (updateDoc ? "web_boot.update_enable("
            + JSON.stringify(JSON.stringify(updateDoc)) + ")\n" : "")
        + (desktop && cart ? "web_boot.open_cart(" + JSON.stringify(cart) + ")\n" : "")
        // Single-cart bundle: kiosk mode -- the exit gesture restarts the game
        // instead of dropping into the shell (the game IS the page).
        + (!desktop && names.length === 1 && cart
            ? "web_boot.kiosk(" + JSON.stringify(cart) + ")\n" : "")
        + "from web_boot import assets_json, step_frame_json, apply_events_json, "
        + "reload_cart, idle_collect, fb_addr, fb_len, "
        + "sync_poll_json, sync_ack, sync_off, sync_config, store_mode, "
        + "rescan_store, gpio_poll_json, gpio_ack_json, gpio_off, "
        + "update_poll_json, update_wants_poll, update_ack_json, update_off, "
        + "services_json, "
        + "import_p8_json, edit_cart, open_cart");
    step = mp.globals.get("step_frame_json");
    applyEvents = mp.globals.get("apply_events_json");
    assets = mp.globals.get("assets_json");
    reload = mp.globals.get("reload_cart");
    idleCollect = mp.globals.get("idle_collect");
    fbAddr = mp.globals.get("fb_addr");
    fbLen = mp.globals.get("fb_len");
    syncPoll = mp.globals.get("sync_poll_json");
    syncAck = mp.globals.get("sync_ack");
    syncOff = mp.globals.get("sync_off");
    rescan = mp.globals.get("rescan_store");
    importP8Json = mp.globals.get("import_p8_json");
    editCart = mp.globals.get("edit_cart");
    openCart = mp.globals.get("open_cart");
    if (gpioPins) {
        gpioPoll = mp.globals.get("gpio_poll_json");
        gpioAck = mp.globals.get("gpio_ack_json");
        gpioOff = mp.globals.get("gpio_off");
        console.log("[moy] gpio: " + gpioPins.length + " pins on the host");
    }
    if (updateDoc) {
        updatePoll = mp.globals.get("update_poll_json");
        updateWants = mp.globals.get("update_wants_poll");
        updateAck = mp.globals.get("update_ack_json");
        updateOff = mp.globals.get("update_off");
        console.log("[moy] update: the host serves " + updateDoc.running
                    + (updateDoc.screen ? " (has its own screen)"
                                        : " (headless -- this page IS it)"));
        // Tell the PAGE too. Nothing in the chrome draws an update any more --
        // the console's own Settings row does -- but "did the bridge bind?" is
        // otherwise observable only in a devtools log, and that is not
        // something a harness or a person can check. One message, no UI.
        //
        // `bound` is ASKED OF THE CONSOLE, not inferred from this branch. It
        // used to be inferred, and the difference is the whole bug: this block
        // runs because the PROBE answered, so the message reported an updater
        // whether or not web_boot.update_enable had actually hung one on the
        // Workstation -- and a browser test asserting on it proved the probe.
        let bound = null;
        try { bound = JSON.parse(mp.globals.get("services_json")()); }
        catch (e) { }
        if (bound && !bound.updater) {
            console.log("[moy] update: the PROBE answered but the console has "
                        + "no updater -- Settings will have no update row");
        }
        self.postMessage({ t: "update", running: updateDoc.running,
                           screen: !!updateDoc.screen,
                           bound: bound ? !!bound.updater : null,
                           services: bound });
    }
    if (pin) mp.globals.get("sync_config")(pin);
    self.postMessage({ t: "assets", json: assets() });
    wantAssets = false;      // the boot payload answers any pre-boot request
    say("live");
}

// Copy the finished framebuffer out of the wasm heap and transfer it to the
// page. HEAPU8 is read FRESH every time on purpose: the port builds with
// ALLOW_MEMORY_GROWTH, and a grown heap replaces the view, detaching any cached
// one. A frame that only carries audio (the redraw was skipped) ships without
// pixels and the page keeps what it has.
function shipFrame(s) {
    let fb = null;
    try {
        if (s.indexOf('"paint": 1') >= 0 || s.indexOf('"paint":1') >= 0) {
            const heap = mp._module.HEAPU8;
            const addr = fbAddr(), n = fbLen();
            let out = fbPool.pop();
            if (!out || out.byteLength !== n) out = new ArrayBuffer(n);
            new Uint8Array(out).set(heap.subarray(addr, addr + n));
            fb = out;
        }
    } catch (e) {
        self.postMessage({ t: "error", s: "framebuffer export failed: " + e });
        running = false;
        return;
    }
    if (fb) self.postMessage({ t: "frame", s: s, fb: fb }, [fb]);
    else self.postMessage({ t: "frame", s: s });
}

// The #9 probe. Returns the host's pin allowlist, or null for "this page is
// not served by anything with pins" -- a static host (moybyte.com, an export),
// a console board that spends its GPIOs on a panel, or a Zero whose pin gate
// this page cannot pass. All four are the same answer to a cart: no verbs.
async function probeGpio(pin) {
    const body = JSON.stringify(pin ? { v: 1, ops: [], pin: pin }
                                    : { v: 1, ops: [] });
    try {
        const r = await fetch("gpio", { method: "POST", body: body,
            headers: { "Content-Type": "application/json" } });
        if (!r.ok) return null;
        const d = await r.json();
        return (d && d.pins && d.pins.length) ? d.pins : null;
    } catch (e) {
        return null;                 // no such host, no such endpoint: fine
    }
}

// Does whoever served this page have an UPDATER? One GET, before the console
// exists, so `ws.updater` is decided exactly once and Settings either has an
// update row or has never heard of one -- the same rule as the pin verbs, and
// the reason a static host (moybyte.com, an export) shows nothing rather than a
// row that fails when tapped. The GET carries the pin the only place a GET can.
async function probeUpdate(pin) {
    try {
        const u = pin ? "update?pin=" + encodeURIComponent(pin) : "update";
        const r = await fetch(u);
        if (!r.ok) return null;
        const d = await r.json();
        return (d && d.running) ? d : null;
    } catch (e) {
        return null;                 // no such host, no such endpoint: fine
    }
}

function updatePump() {
    if (updateBusy || !updatePoll) return;
    const now = performance.now();
    if (now - lastUpdateAt < UPDATE_MS) return;
    lastUpdateAt = now;
    let body = "";
    try { body = updatePoll(); } catch (e) { return; }
    if (!body) {
        // Nothing queued. GET the status only while a screen is waiting on
        // something; otherwise say nothing at all.
        let wants = false;
        try { wants = updateWants(); } catch (e) { return; }
        if (!wants) return;
    }
    updateBusy = true;
    // A POST carries its pin in the BODY (take_json put it there, like gpio); a
    // GET has nowhere but the query, which is what withPin is for.
    const req = body
        ? fetch("update", { method: "POST", body: body,
                            headers: { "Content-Type": "application/json" } })
        : fetch(withPin("update"));
    req.then((r) => {
            // The boot probe already cleared these, so meeting one HERE means
            // the host changed under us -- rebooted into setup, pin rotated.
            // Go inert once rather than retry-logging: the SCREEN stays up and
            // reads an error, which is the most a person looking at it can be
            // told.
            if (r.status === 404 || r.status === 405 || r.status === 501
                || r.status === 403) {
                try { updateAck(0, ""); updateOff(); } catch (e) { }
                updatePoll = null;
                console.log("[moy] update off: host stopped answering ("
                            + r.status + ")");
                return null;
            }
            const ok = r.ok ? 1 : 0;
            return r.text().then((t) => {
                try { updateAck(ok, t); } catch (e) { }
                // The board said it is about to reboot. Said HERE rather than
                // from the board's closing window because during an install the
                // board must keep answering /update -- on a headless console
                // this page IS the progress screen -- so it cannot go quiet the
                // way a switch-off can. And "first reason wins" in the panel
                // means the silence that follows reads as this, not as a loss.
                try {
                    if ((JSON.parse(t) || {}).state === "reboot") {
                        expected("the console is restarting",
                                 "It is booting the version it just installed.");
                    }
                } catch (e) { }
            });
        })
        .catch(() => { try { updateAck(0, ""); } catch (e) { } })
        .finally(() => { updateBusy = false; });
}

function gpioPump() {
    if (gpioBusy || !gpioPoll) return;
    const now = performance.now();
    if (now - lastGpioAt < GPIO_MS) return;
    lastGpioAt = now;
    let body = "";
    try { body = gpioPoll(); } catch (e) { return; }
    if (!body) return;               // nothing queued and nothing watched
    gpioBusy = true;
    fetch("gpio", { method: "POST", body: body,
                    headers: { "Content-Type": "application/json" } })
        .then((r) => {
            // The probe already cleared 404/405/403, so reaching one HERE means
            // the host changed under us (rebooted into setup, a pin rotated).
            // Stop for good rather than retry-logging: the verbs go inert and
            // say so once, which is the most a running cart can be told.
            if (r.status === 404 || r.status === 405 || r.status === 501
                || r.status === 403) {
                try { gpioAck(0, ""); gpioOff(); } catch (e) { }
                gpioPoll = null;
                console.log("[moy] gpio off: host stopped answering ("
                            + r.status + ")");
                return null;
            }
            const ok = r.ok ? 1 : 0;
            return r.text().then((t) => {
                try { gpioAck(ok, t); } catch (e) { }
            });
        })
        .catch(() => { try { gpioAck(0, ""); } catch (e) { } })
        .finally(() => { gpioBusy = false; });
}

// MODE 1 delivery: the batch the sweep just built, applied into OPFS.
// Per-op failures are LOGGED and still ack true, exactly as the board path
// treats a 200 carrying `err` entries -- a malformed op is poison that would
// otherwise replay forever. Only a store-level failure requeues.
async function pumpLocal(body) {
    const t0 = performance.now();
    try {
        const doc = JSON.parse(body);
        const ops = doc.ops || [];
        // The batch names its root (v2 -> doc.root; a v1 batch is carts). Apply
        // it into THAT root's OPFS store -- so a files batch persists drawings
        // and a carts batch persists carts, from the one pump.
        const rootId = (doc.v === 2 && doc.root) ? doc.root : "carts";
        const s = opfsStores[rootId];
        if (!s) {                            // no store for this root: nothing to
            try { syncAck(1); } catch (e) { }  // keep; ack so it does not requeue
            return;
        }
        const r = await store.applyOps(s, ops);
        if (r.errors.length)
            console.log("[moy] persist: " + r.errors.length + " op(s) refused, first "
                + JSON.stringify(r.errors[0]));
        persistFails = 0;
        persistBatches++;
        try { syncAck(1); } catch (e) { }
        sitePersist(rootId + " " + ops.length + " ops in "
                    + (performance.now() - t0).toFixed(1) + "ms");
    } catch (e) {
        // Quota, or the browser evicted the store under us. Requeue; after a
        // few consecutive failures stop pretending, once, and tell the page --
        // #193's requirement is that this is never silent.
        try { syncAck(0); } catch (e2) { }
        persistFails++;
        console.log("[moy] persist: apply failed -- " + e);
        if (persistFails >= PERSIST_GIVE_UP && !persistSaid) {
            persistSaid = true;
            for (const id in opfsStores) opfsStores[id] = null;
            persist("none", "this browser stopped saving (storage full?): export your cart");
        }
    } finally {
        syncBusy = false;
    }
}

function syncPump() {
    if (syncBusy || !syncPoll) return;
    const now = performance.now();
    if (now - lastSyncAt < SYNC_MS) return;
    lastSyncAt = now;
    if (mode === "none") {
        // Nowhere to deliver: stop the sweep for good rather than take batches
        // and drop them. One decision, then silence.
        try { syncOff(); } catch (e) { }
        syncPoll = null;
        return;
    }
    let body = "";
    try { body = syncPoll(); } catch (e) { return; }
    if (!body) { heartbeat(now); return; }
    outstanding = true;
    syncBusy = true;
    if (mode === "site") { pumpLocal(body); return; }
    fetch("sync", { method: "POST", body: body,
                    headers: { "Content-Type": "application/json" } })
        .then((r) => {
            if (r.status === 404 || r.status === 405 || r.status === 501) {
                // A host with no push half (moybyte.com, an export, an older
                // read-only board): one probe, then off for good.
                try { syncAck(0); syncOff(); } catch (e) { }
                syncPoll = null;
                console.log("[moy] sync off: host has no /sync (" + r.status + ")");
                return;
            }
            if (r.status === 403) {
                // The board wants a pin this page was not opened with
                // (?pin=...). Retrying the same batch forever is noise.
                try { syncAck(0); syncOff(); } catch (e) { }
                syncPoll = null;
                console.log("[moy] sync off: board refused the pin");
                // SAY SO. This is the silent-loss case in its purest form: the
                // board is right there and answering, the page looks entirely
                // healthy, and nothing a kid types will ever reach the console
                // again. A devtools line is not a person being told.
                syncLostSaid = false;
                syncLost("the console refused this page",
                         "It is asking for a pin this page was not opened with. "
                         + "Re-scan the code on its WEB CONSOLE screen.");
                return;
            }
            if (r.ok) { syncOk(now); outstanding = false; }
            else if (r.status === 503) { closing(r); }
            else syncFailed(now);
            try { syncAck(r.ok ? 1 : 0); } catch (e) { }
        })
        .catch(() => {
            // The board is unreachable, not merely unhappy: no status at all.
            syncFailed(now);
            try { syncAck(0); } catch (e) { }
        })
        .finally(() => { syncBusy = false; });
}

// WHAT AN IDLE PAGE ASKS. With nothing to push there is no POST, and with no
// POST there was nothing to fail -- so a tab left sitting on a console that was
// then switched off noticed only when the reader happened to change something.
// This is the question that keeps the clock honest: GET /sync, the open
// capability marker, no pin and no store walk behind it.
//
// It shares syncOk/syncFailed with the push, so both paths feed ONE give-up
// rather than two thresholds that could disagree about when a board is gone.
function heartbeat(now) {
    if (mode !== "board") return;
    if (!lastSyncOkAt) lastSyncOkAt = now;      // start the clock at first idle
    if (now - lastHeartbeatAt < HEARTBEAT_MS) return;
    lastHeartbeatAt = now;
    syncBusy = true;
    fetch("sync", { method: "GET" })
        .then((r) => {
            if (r.ok) syncOk(now);
            else if (r.status === 503) closing(r);
            else syncFailed(now);
        })
        .catch(() => syncFailed(now))
        .finally(() => { syncBusy = false; });
}

// THE BOARD'S GOODBYE (moy_webhost's closing window). A console that is going
// away on purpose keeps answering for a few seconds to say so, because from out
// here a deliberate switch-off and an unplugged board look identical -- and
// guessing wrong means either an alarm nobody needed or a silence at the moment
// it mattered. This is the board removing the guess.
//
// It is EXPECTED, so it carries no data-loss warning: the board is right there
// and took everything it was given.
function closing(r) {
    r.text().then((t) => {
        let why = "";
        try { why = (JSON.parse(t) || {}).why || ""; } catch (e) { }
        if (why === "update") {
            expected("the console is updating itself",
                     "It will restart on the new version. This page will not "
                     + "work until it is back.");
        } else {
            expected("the console took its screen back",
                     "WEB CONSOLE was switched off on the device. Turn it on "
                     + "again there to carry on here.");
        }
    }).catch(() => expected("the console is shutting down", ""));
}

function expected(head, body) {
    if (syncLostSaid) return;
    syncLostSaid = true;
    self.postMessage({ t: "lost", kind: "expected", risk: false,
                       head: head, body: body });
}

// Self-driven frame loop. setTimeout (not rAF -- workers have none) against a
// deadline, so a slow frame catches up instead of quantizing to a slower rate.
let nextAt = 0, lastStep = 0, simTime = 0, wallFrom = 0;
function loop() {
    if (!running) return;
    const now = performance.now();
    if (nextAt && now < nextAt - 1) {
        setTimeout(loop, Math.max(0, nextAt - now));
        return;
    }
    // dt is REAL elapsed time since the previous step -- never derived from the
    // deadline. Deadlines advance by exactly FRAME_MS, but `now` is always late by
    // however long the last step took, so measuring deadline->now overstated dt by
    // that lateness EVERY frame and the game ran fast (~1.5x with an 8ms step).
    // Real elapsed is correct by construction: simulated time tracks the clock.
    const dt = lastStep ? Math.min(0.1, (now - lastStep) / 1000) : 1 / 60;
    lastStep = now;
    simTime += dt;
    if (!wallFrom) wallFrom = now;
    nextAt = nextAt ? Math.max(nextAt + FRAME_MS, now - 30) : now + FRAME_MS;
    try {
        if (inbox.length) {
            // ONE JS->Python crossing per frame, however fast the mouse fires:
            // merge every queued {"events":[...]} batch into a single batch.
            // A long drag on a high-poll mouse posts hundreds of move events a
            // second; crossing per batch multiplied the boundary overhead and
            // could land a pending GC collect mid-gesture on whichever crossing
            // came first. Event ORDER is preserved (paint strokes keep their
            // intermediate points).
            const batch = inbox;
            inbox = [];
            const _i0 = performance.now();
            if (batch.length === 1) {
                applyEvents(batch[0]);
            } else {
                const all = [];
                for (const j of batch) {
                    try { all.push(...JSON.parse(j).events); } catch (e) { }
                }
                applyEvents(JSON.stringify({ events: all }));
            }
            WP.inp += performance.now() - _i0; WP.inpN++;
        }
        const _t0 = performance.now();
        const s = step(dt, ahead);
        const _d = performance.now() - _t0;
        // WORKER-SIDE PERF: the page's `recv` fps says the worker is behind but
        // not WHY. Track step cost + the worst offender's payload so the HUD
        // line names it (headless probes have repeatedly failed to reproduce
        // what the browser does -- measure where it actually runs).
        WP.n++; WP.sum += _d; if (_d > WP.max) { WP.max = _d; WP.maxB = s ? s.length : 0; }
        if (_d > 20) WP.slow++;
        if (s) { WP.painted++; WP.bytes += s.length; }
        if (s) shipFrame(s);
        // GC scheduling (see the constants above). A painted frame re-arms the
        // idle collect; the periodic guard fires regardless of quiet.
        framesSinceCollect++;
        if (s) { quietStreak = 0; idleCollected = false; } else { quietStreak++; }
        if (idleCollect && ((quietStreak >= IDLE_QUIET_FRAMES && !idleCollected)
            || framesSinceCollect >= COLLECT_MAX_FRAMES)) {
            const _g0 = performance.now();
            idleCollect();               // the collect lands as this call returns
            WP.gc += performance.now() - _g0; WP.gcN++;
            idleCollected = true;
            framesSinceCollect = 0;
        }
        syncPump();
        gpioPump();
        updatePump();
    } catch (e) {
        self.postMessage({ t: "error", s: String((e && e.message) || e) });
        running = false;
        return;
    }
    setTimeout(loop, Math.max(0, nextAt - performance.now()));
}

self.onmessage = async (ev) => {
    const m = ev.data;
    try {
        if (m.t === "init") {
            await init(m.search);
        } else if (m.t === "input") {
            inbox.push(m.json);
        } else if (m.t === "ahead") {
            ahead = m.v;
        } else if (m.t === "run") {
            // lastStep too: a (re)start must begin with a clean 1/60 dt, not a
            // clamped jump measured from whenever the loop last ran.
            if (!running) { running = true; nextAt = 0; lastStep = 0; loop(); }
        } else if (m.t === "assets") {
            // BEFORE BOOT this is a no-op, not a crash. onmessage is async, so a
            // request arriving while init() is still awaiting does NOT queue
            // behind it -- it ran against a null `assets` and threw
            // "assets is not a function", which the page reports as a console
            // crash (owner, 2026-07-31). The page asks as soon as it wants an
            // image it lacks, which can easily precede a slow VM boot (and does
            // reliably if dist/ is rewritten by a rebuild mid-load). Remember
            // the ask; init() answers it the moment it is ready.
            if (!assets) { wantAssets = true; return; }
            const _a0 = performance.now();
            // Assets ON DEMAND. Covers/paint images are created LAZILY, so the
            // payload built at boot does not have them yet; the page's imgWant
            // latch re-requests when a draw references an image it lacks. Serving
            // that from a cached copy (which the first worker cut did) meant cover
            // thumbnails could never arrive.
            self.postMessage({ t: "assets", json: assets() });
            WP.assets += performance.now() - _a0; WP.assetsN++;
        } else if (m.t === "clock") {
            // Simulated vs wall time since the first step. Their ratio must be ~1:
            // anything else is a pacing bug, and it is invisible from frame counts
            // alone (a wrong dt changes game SPEED, not frame rate).
            self.postMessage({ t: "clock", sim: simTime,
                wall: wallFrom ? (performance.now() - wallFrom) / 1000 : 0 });
        } else if (m.t === "fbret") {
            // The page finished with a framebuffer: keep it for reuse. Two is
            // plenty (one in flight, one being filled); a deeper pool would just
            // hold megabytes hostage.
            if (m.b && fbPool.length < 2) fbPool.push(m.b);
        } else if (m.t === "reload") {
            const [carts, files] = await Promise.all([
                fetch(withPin("carts.json")).then((r) => r.json()),
                fetch(withPin("files.json")).then((r) => r.ok ? r.json() : null)
                    .catch(() => null)]);
            writeStore(CARTS_ROOT, carts);
            if (files) writeStore(store.rootById("files").vfs, files);
            reload();
            self.postMessage({ t: "assets", json: assets() });
        } else if (m.t === "kept") {
            keptAnswer(m.state);
        } else if (m.t === "carts") {
            self.postMessage({ t: "carts", names: cartNames() });
        } else if (m.t === "export") {
            // A failed export is a MESSAGE, never the fatal {t:"error"} the
            // outer catch would send -- the console is fine, the ask was not.
            try {
                const files = [];
                walkVfs(CARTS_ROOT + "/" + m.cart, m.cart, files, 0);
                if (!files.length) throw new Error("no such cart");
                const zip = store.zipStore(files);
                self.postMessage({ t: "exported", name: m.cart + ".zip",
                                   buf: zip.buffer }, [zip.buffer]);
            } catch (e) {
                self.postMessage({ t: "imported", ok: false,
                    s: "export failed: " + ((e && e.message) || e) });
            }
        } else if (m.t === "import") {
            try {
                if (isP8Drop(m.name, m.buf)) {
                    const r = await importP8(m.name, m.buf);
                    if (!r.ok) {
                        self.postMessage({ t: "imported", ok: false,
                            s: "could not import " + m.name, report: r.report });
                    } else {
                        // IT RUNS IMMEDIATELY (#194). The whole point of
                        // converting on import is that the result is an
                        // ordinary cart, so the honest proof is that it plays
                        // the moment it lands -- not a row on a shelf.
                        if (openCart) openCart(r.dir);
                        self.postMessage({ t: "imported", ok: true,
                            s: "imported " + r.dir + " into your carts",
                            report: r.report, dir: r.dir });
                    }
                } else {
                    const r = await importZip(m.name, m.buf);
                    self.postMessage({ t: "imported", ok: true,
                        s: "imported " + r.dir + " (" + r.n + " files)" });
                }
                if (assets) self.postMessage({ t: "assets", json: assets() });
            } catch (e) {
                self.postMessage({ t: "imported", ok: false,
                    s: "import failed: " + ((e && e.message) || e) });
            }
        } else if (m.t === "edit") {
            // The import report's one action. Like export, a failure is a
            // MESSAGE and never the fatal {t:"error"}: the console is fine.
            try {
                if (!editCart) throw new Error("no editor on this console");
                const st = JSON.parse(editCart(m.cart, m.tab || null));
                self.postMessage({ t: "edited", ok: !!st.ok,
                    s: st.ok ? ("editing " + (st.title || m.cart)
                                + " (" + (st.screen || "?") + "/"
                                + (st.tab || "?") + ")")
                             : ("no cart called " + m.cart) });
                if (assets) self.postMessage({ t: "assets", json: assets() });
            } catch (e) {
                self.postMessage({ t: "edited", ok: false,
                    s: "could not open the editor: " + ((e && e.message) || e) });
            }
        }
    } catch (e) {
        self.postMessage({ t: "error", s: String((e && e.stack) || e) });
    }
};
