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
//   {t:"init", search, coarse, channel, scheme, rate}  boot the console; `search` is
//                        location.search (tier + cart), `coarse` the page's
//                        (pointer: coarse), `channel` its MOY_CHANNEL, `rate`
//                        its AudioContext's sample rate (0: none), the output's
//   {t:"input", json}    an {"events":[...]} batch, applied before the next step
//   {t:"sink", port?}    where a frame's samples go: the worklet's MessagePort,
//                        which reports its counters back on it, or (no port)
//                        the page, which reports them as {t:"played", s}
//   {t:"played", s}      the page's sink's counters (page_core.html, THE SINK)
//   {t:"state"}          the console's `state` (web_boot.state_json), for a harness
//   {t:"run"}            start stepping (the page's play-button gesture)
//   {t:"fbret", b}       a framebuffer being handed BACK for reuse (see below)
//   {t:"reload"}         dev hot-reload: re-read carts/files/apps.json, restart
//   {t:"kept", state}    the page's answer to {t:"keep"}: "granted" | "denied"
//   {t:"carts"}          list the cart folders (the export picker)
//   {t:"export", cart}   zip one cart out of the VFS
//   {t:"import", name, buf}  a dropped file into the store: an MP3 (Music), a .moy zip, or a
//                        PICO-8 .p8 / .p8.png, which is CONVERTED first (#194)
//   {t:"edit", cart, tab}  open a cart in the Editor (the import report's
//                        action); `tab` optionally lands on paint/map/music
//   {t:"open", cart}     play a cart the console holds, as ?cart= does at boot
//   {t:"picked", id, name, buf}  the player's own copy of a file Get Carts
//                        asked for ({t:"pick"}), TRANSFERRED; or
//   {t:"picked", id, cancel}     ...the player closed the question
//   {t:"pod", cart}      play `cart` on the serving board (POST /run)
// worker -> main:
//   {t:"status", s}      boot progress text
//   {t:"assets", json}   the page's metadata payload (size/title/audio/input)
//   {t:"frame", s, fb}   frame metadata + the RGB565 framebuffer, TRANSFERRED
//   {t:"pcm", p, at, fresh}  a frame's samples (Int16Array, TRANSFERRED) for the
//                        page's sink; to a worklet sink the same over its port
//   {t:"error", s, msg}  fatal: the page shows it and stops. msg is the
//                        thrown value's String() (an abort's reason, a
//                        traceback), s that plus the stack's frames
//   {t:"persist", mode, s}   where carts are being kept, in the page's words
//   {t:"keep"}           site mode: ask the browser to make this origin
//                        durable. Sent to the PAGE because storage.persist()
//                        is window-only -- a Worker cannot ask, only look.
//   {t:"pair", pw, why, wait}  this board has not paired this browser, so the
//                        boot stopped before the VM: the page asks. `pw` a
//                        password can pair it (else only the QR can), `why`
//                        what failed last ("" the first time), `wait`
//                        signInWaitSays' sentence while the board holds wrong
//                        tries off, else ""
//   {t:"key", id, kid, key, reload}  a pairing's new device key for board `id`:
//                        the page keeps it (and with `reload`, loads again)
//   {t:"unkey", id}      board `id` refused this browser's key: the page drops it
//   {t:"wait", s}        the sync push meets a sign-in wait: the link panel says
//                        `s`, and "" (a push landed) takes it down
//   {t:"pod", s}         the play-on-device link's text after {t:"pod"}
//   {t:"carts", names}   the shelf's folder names
//   {t:"exported", name, buf}  a .moy zip, TRANSFERRED
//   {t:"imported", s, ok, report, dir}  the result of an import, in the page's
//                        words; `report` is the p8 compatibility summary (the
//                        shared writer's own lines) when there was one
//   {t:"edited", s, ok}  the result of an "open in editor"
//   {t:"pick", id, name, size, host}  Get Carts needs a file this page cannot
//                        fetch from `host`: the page asks the player for theirs
//   {t:"unpick", id}     ...and the question is over
//   {t:"installed", folder, d}  an install reached the browser's store
//   {t:"state", json}    the answer to {t:"state"}
import { loadMicroPython } from "./micropython.mjs";
import * as store from "./moy_store.mjs";

let mp = null, step = null, applyEvents = null, assets = null, reload = null;
// The drop's and the export's Python entry points (#194), bound at boot like
// the rest.
let importP8Json = null, importArchiveJson = null, exportCart = null, dropKind = null;
let editCart = null, openCart = null, stateJson = null;
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
let syncPoll = null, syncAck = null, syncOff = null, syncRoot = null, rescan = null;
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
let syncWaitSaid = false;     // the link panel is showing a sign-in wait

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
// GET CARTS (#124), site mode: the console's carts_link queues what it needs
// -- a fetch, a drop, an install to keep, a record, a file from the player --
// and this pump does it and answers through cartsSay. Polled every frame
// while anything is under way and every CARTS_IDLE_MS otherwise: the answers
// themselves never wait on the poll, only a new request does.
//
// A fetch's body is written into a SPOOL FILE in the VFS as fetch hands it
// over, and the console reads that file as far as it has grown: the bytes
// live in this worker's memory and never in the VM's heap. Python can only
// read between frames, which is exactly when these callbacks run.
let cartsPoll = null, cartsEvent = null, lastCartsAt = 0;
const CARTS_IDLE_MS = 100;
const cartsLive = new Map();          // id -> {ac, stream, path} or {pick, path}
function cartsSay(ev) {
    if (!cartsEvent) return;
    try { cartsEvent(JSON.stringify(ev)); }
    catch (e) { console.log("[moy] carts: answer refused -- " + e); }
}
// One OPFS writer at a time: the sweep's batches and an install's commit both
// write the carts store, in the order they were asked for.
let opfsChain = Promise.resolve();
function opfsSerial(fn) {
    const p = opfsChain.then(fn);
    opfsChain = p.catch(() => { });
    return p;
}

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
let running = false, inbox = [];
// The audio sink (page_core.html, THE SINK): null before the page names it,
// "page", or the worklet's port; `played` its count of samples played, -1
// with no sink, which the console's pull tops its depth up from.
let sink = null, sinkStats = null, played = -1;
function sinkSet(port) {
    sink = port || "page";
    played = 0;
    if (port) port.onmessage = (e) => { sinkStats = e.data; played = e.data.played; };
}
// A frame's samples, read out of the wasm heap where the pull rendered them,
// to the sink.
function shipPCM(f) {
    if (!sink) return;
    const n = f.pcm, b = new Int16Array(n);
    new Uint8Array(b.buffer).set(mp._module.HEAPU8.subarray(f.pa, f.pa + 2 * n));
    const c = { p: b, at: f.at, fresh: f.fresh };
    if (sink === "page") self.postMessage(Object.assign({ t: "pcm" }, c), [b.buffer]);
    else sink.postMessage(c, [b.buffer]);
}
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

// A COMPILED CART ("runtime": "wasm"), run beside the console as a SIBLING
// WebAssembly module: this worker's own engine compiles and instantiates the
// cart's main.wasm, never an engine inside the VM. The console drives it
// exactly as a board's does -- moycore through the session surface
// (native/moy_wasm/moy_wasm_session.h), which native/moy_wasm_web implements
// by calling `Module.moyEngine` below to open, bind, run a hook and close --
// so the Player, the fit check, the canvas and the verbs are the boards'.
//
// The cart's "moy" imports are adapters over libmoy's import table, generated
// from the table's own signature strings (moy_web_natives): every verb the
// cart calls is the C function every host calls (moycore's libmoy/moy_wasm.c),
// with the same marshalling, blit, read and traps. This is moy-spec's web
// player's adapter (runner/cart.js) on this worker's VM. A compiled app's
// "moybyte.app" imports are the same adapters over the app ABI's rows
// (moy_web_app_natives, native/moy_app/moy_app_wasm.h). The two modules have
// separate memories, so a pointer the cart hands over is an offset into ITS
// memory: where a row says '*~' -- a pointer and the length it covers -- the
// range is bounds-checked against the cart's memory, copied into the VM's,
// passed to C as that copy, and copied back when the call returns. A pointer
// a row carries as a plain i32 (blit's frame, camera's out) is the binding's
// to reach, through span() and store() (`Module.moyCart`, libmoy's embed.c).
// A trap the binding raises is thrown as a JavaScript exception, which
// unwinds the cart exactly as a wasm trap does.
//
// A cart's par items run here, on this one thread, one after another: the
// cart's memory is not shared, so no other worker could reach it. The binding
// calls item() for each, which moves the cart's own stack pointer as every
// host does.
const OUT_OF_BOUNDS = "out of bounds memory access";
class CartTrap extends Error {}

export function installCartEngine(M) {
    const dec = new TextDecoder();
    const u32 = () => new Uint32Array(M.HEAPU8.buffer);   // fresh: growth replaces it
    const cstr = (p) => {
        const h = M.HEAPU8;
        let e = p;
        while (h[e]) e++;
        return dec.decode(h.subarray(p, e));
    };
    // '*~' is one pointer-and-length pair; every other letter one scalar.
    const params = (sig) => {
        const inner = /^\((.*)\)(.?)$/.exec(sig)[1];
        const out = [];
        for (let k = 0; k < inner.length; k++) {
            if (inner[k] === "*" && inner[k + 1] === "~") { out.push("span"); k++; }
            else out.push(inner[k]);
        }
        return out;
    };
    let C = null;              // the VM's exports this engine calls (open's `vm`)
    let w = 0;                 // the binding the table is called with
    let instance = null, memory = null, imports = null, limit = -1;
    let owned = [];            // the VM-side copies of the current import call
    const cartBytes = () => new Uint8Array(memory.buffer);
    const scratch = (n) => {
        const p = C.malloc(n > 0 ? n : 1);
        owned.push(p);
        return p;
    };
    const trapText = (p) => (p ? cstr(p) : "");
    const reach = {
        // One of par's items: _par(i, arg) with the cart's stack pointer at sp,
        // then the stack pointer as it was. 0 when it returned; 1 when it threw,
        // with the trap recorded unless the binding recorded its own.
        item(i, arg, sp) {
            const ex = instance.exports;
            const g = ex.__stack_pointer;
            const saved = g.value;
            g.value = sp;
            try {
                ex._par(i, arg);
                return 0;
            } catch (e) {
                if (!(e instanceof Error)) throw e;
                if (!C.trapped(w)) {
                    const msg = String(e.message || e);
                    const b = new TextEncoder().encode(msg + "\0");
                    const p = C.malloc(b.length);
                    M.HEAPU8.set(b, p);
                    C.itemTrap(w, p);
                    C.free(p);
                }
                return 1;
            } finally {
                g.value = saved;
            }
        },
        span(off, n) {
            const mem = cartBytes();
            if (off + n > mem.length) return 0;
            const p = scratch(n);
            M.HEAPU8.set(mem.subarray(off, off + n), p);
            return p;
        },
        store(off, src, n) {
            const mem = cartBytes();
            if (off + n > mem.length) return 0;
            mem.set(M.HEAPU8.subarray(src, src + n), off);
            return 1;
        },
    };
    // One import per row of a table (`natives`: the "moy" table's, or the app
    // extension's "moybyte.app" rows), each an adapter over the row's C.
    function adapters(natives) {
        const cp = C.malloc(4);
        const base = natives(cp);
        const n = u32()[cp >> 2];
        C.free(cp);
        const out = {};
        for (let i = 0; i < n; i++) {
            const h = u32(), at = (base >> 2) + i * 4;   // NativeSymbol: four words
            const name = cstr(h[at]), fn = C.fn(h[at + 1]), kinds = params(cstr(h[at + 2]));
            out[name] = (...args) => {
                if (!w) throw new CartTrap("moy: an import ran before the cart was bound");
                const cargs = [w];
                const copies = [];
                const outer = owned;
                let a = 0;
                owned = [];
                try {
                    for (const k of kinds) {
                        if (k !== "span") { cargs.push(args[a++]); continue; }
                        const off = args[a++] >>> 0, len = args[a++] >>> 0;
                        const mem = cartBytes();
                        if (off + len > mem.length) throw new CartTrap(OUT_OF_BOUNDS);
                        const p = scratch(len);
                        M.HEAPU8.set(mem.subarray(off, off + len), p);
                        copies.push([off, p, len]);
                        cargs.push(p, len);
                    }
                    const r = fn(...cargs);
                    for (const [off, p, len] of copies)
                        cartBytes().set(M.HEAPU8.subarray(p, p + len), off);
                    const t = C.trapped(w);
                    if (t) throw new CartTrap(trapText(t));
                    return r;
                } finally {
                    for (const p of owned) C.free(p);
                    owned = outer;
                }
            };
        }
        return out;
    }
    M.moyEngine = {
        // Compile and instantiate the cart (the session's load, which the host
        // half has already checked against SPEC.md 16's shape). "" when it
        // stands, else why it does not, in the words the Player reads: a
        // memory the browser would not give is "out of memory".
        open(bytes, vm) {
            C = vm;
            try {
                // COMPILE FIRST. `bytes` is a view into the VM's own HEAPU8
                // (web_open's HEAPU8.subarray). On the FIRST open ever,
                // `adapters()` below calls into the VM (C.malloc/C.natives)
                // to build the import table, and that call can GROW the
                // VM's memory -- which DETACHES every view taken before the
                // growth, `bytes` included, reading back length 0
                // ("BufferSource argument is empty"). WebAssembly.Module()
                // only ever needs `bytes` for this one call, so nothing
                // after it may still depend on that view.
                // cart_engine_test.mjs pins it with a VM that grows on its
                // first malloc.
                const module = new WebAssembly.Module(bytes);
                if (!imports) {
                    imports = { moy: adapters(C.natives) };
                    if (C.appNatives) imports["moybyte.app"] = adapters(C.appNatives);
                }
                instance = new WebAssembly.Instance(module, imports);
            } catch (e) {
                instance = null;
                const msg = String((e && e.message) || e);
                if (e instanceof RangeError) return "out of memory: " + msg;
                return (e instanceof WebAssembly.CompileError ? "load: " : "instantiate: ") + msg;
            }
            memory = instance.exports.memory;
            M.moyCart = reach;
            return "";
        },
        bind(binding) { w = binding; },
        // One hook: null when it returned, else what it threw. Only an Error is
        // the cart's: anything else is the VM's own unwinding passing through.
        hook(h, dt) {
            const ex = instance.exports;
            try {
                if (h === 0) ex._init();
                else if (h === 1) ex._update(dt);
                else ex._draw();
                return null;
            } catch (e) {
                if (!(e instanceof Error)) throw e;
                return String(e.message || e);
            }
        },
        close() {
            instance = memory = null;
            w = 0;
            M.moyCart = null;
        },
        // The largest memory, in pages, this browser gives one module: the
        // fit check's figure, found once by allocating it.
        limitPages() {
            if (limit < 0) {
                limit = 0;
                for (let p = 65536; p >= 16; p >>= 1) {
                    try {
                        new WebAssembly.Memory({ initial: p, maximum: p });
                        limit = p;
                        break;
                    } catch (e) { }
                }
            }
            return limit;
        },
    };
}

// A WASI COMMAND the site's shell runs (native/moy_wasm_web/moy_wasi_web.h):
// the module compiled and instantiated here, a sibling of the VM as a
// compiled cart is, its imports the rows the VM's C registers
// (moy_web_cmd_row), each called with the module's own arguments -- a
// pointer is an offset in the module's memory -- or, where the C hands a
// row's signature, each pointer and length it marks `*~` copied into the VM
// first. What a row's C reaches of that memory is copied into the VM's
// (span) and its changed bytes copied back when the row returns; then, where
// the C says the module unwinds (proc_exit, Ctrl-C, the budget), it is thrown
// through the module. `_start` runs inside
// the shell door's crossing, to its end. Every decision is the C's: this only
// compiles, instantiates, calls and moves bytes.
class CommandEnd extends Error {}

export function installCommandEngine(M) {
    let C = null, run = 0, module = null, instance = null, spans = [];
    const bytes = () => new Uint8Array(instance.exports.memory.buffer);
    const cstr = (p) => {
        const h = M.HEAPU8;
        let e = p;
        while (h[e]) e++;
        return new TextDecoder().decode(h.subarray(p, e));
    };
    M.moyCommand = {
        load(code) {
            try {
                module = new WebAssembly.Module(code);
            } catch (e) {
                return { why: "load: " + String((e && e.message) || e) };
            }
            const out = [];
            for (const i of WebAssembly.Module.imports(module))
                out.push(["i", i.kind, i.module, i.name].join("\t"));
            for (const x of WebAssembly.Module.exports(module))
                out.push(["e", x.kind, x.name].join("\t"));
            return { listing: out.map((l) => l + "\n").join("") };
        },
        instantiate(r, vm) {
            C = vm;
            run = r;
            const imports = {};
            const at = C.malloc(12);
            try {
                for (let i = 0; ; i++) {
                    const fi = C.row(i, at, at + 4, at + 8);
                    if (fi < 0) break;
                    const h = new Uint32Array(M.HEAPU8.buffer, at, 3);
                    const mod = cstr(h[0]), name = cstr(h[1]), fn = C.fn(fi);
                    // the argument indices a `*~` pair starts at, where copied
                    const pairs = [];
                    if (h[2]) {
                        const sig = /^\((.*)\)/.exec(cstr(h[2]))[1];
                        for (let k = 0, n = 0; k < sig.length; k++, n++) {
                            if (sig[k] === "*" && sig[k + 1] === "~") { pairs.push(n); k++; n++; }
                        }
                    }
                    (imports[mod] = imports[mod] || {})[name] = (...a) => {
                        const outer = spans;
                        spans = [];
                        let r;
                        try {
                            for (const k of pairs) {
                                const p = M.moyCommand.span(a[k] >>> 0, a[k + 1] >>> 0);
                                if (!p) throw new Error("out of bounds memory access");
                                a[k] = p;
                            }
                            r = fn(run, ...a);
                            for (const [off, p, was] of spans) {
                                const mem = bytes(), now = M.HEAPU8;
                                for (let k = 0; k < was.length; k++)
                                    if (now[p + k] !== was[k]) mem[off + k] = now[p + k];
                            }
                        } finally {
                            for (const s of spans) C.free(s[1]);
                            spans = outer;
                        }
                        if (C.thrown(run)) throw new CommandEnd("exit");
                        return r;
                    };
                }
            } finally {
                C.free(at);
            }
            try {
                instance = new WebAssembly.Instance(module, imports);
            } catch (e) {
                instance = null;
                const msg = String((e && e.message) || e);
                return (e instanceof RangeError ? "out of memory: " : "instantiate: ") + msg;
            }
            if (!(instance.exports.memory instanceof WebAssembly.Memory)) {
                instance = null;
                return "instantiate: the module exports no memory";
            }
            return "";
        },
        // `_start` to its end: null, or what it threw. Only an Error is the
        // module's: anything else is the VM's own unwinding, and is rethrown.
        run() {
            try {
                instance.exports._start();
                return null;
            } catch (e) {
                if (!(e instanceof Error)) throw e;
                return String(e.message || e);
            }
        },
        span(off, n) {
            const mem = bytes();
            if (off + n > mem.length) return 0;
            const p = C.malloc(n > 0 ? n : 1);
            if (!p) return 0;
            const was = mem.slice(off, off + n);
            M.HEAPU8.set(was, p);
            spans.push([off, p, was]);
            return p;
        },
        close() {
            module = instance = null;
            run = 0;
        },
    };
}

function mkdirs(p) {
    let cur = "";
    for (const part of p.split("/")) {
        if (!part) continue;
        cur += "/" + part;
        try { mp.FS.mkdir(cur); } catch (e) { /* exists */ }
    }
}

// Where the console keeps things in the VFS (web_vfs), from the VM by
// bindStore: the carts folder, from which the registry places every other
// root, the seed's staging, Music's listing, a drop's and an export's file.
let VFS = null;
function dirOf(p) { return p.slice(0, p.lastIndexOf("/")); }
// The files compiled carts wrote (moy-spec SPEC.md 16.12), the apps root, and
// their store of record in OPFS: site mode only, where a page keeps its own
// carts.
let writtenStore = null;
// Get Carts' record, beside the carts folder as on every console
// (cart_index.RECORD_NAME, from the VM by bindStore).
let installRecord = null;
// The storage every store opens through: the origin's, or the page's channel's
// (moy_store.channelNav), set by init before anything is seeded.
let opfsNav = navigator;
// The roots initStore seeded, which web_boot builds its watchers over.
let seededRoots = null;

function writeStore(root, files) {
    // mkdirs(root) even for an empty set: a root with no files YET (a board's
    // files layer with no drawings; a site-mode files store on first visit)
    // must still create its directory, because web_boot builds a watcher only
    // for a root whose store is present -- the empty dir IS the capability
    // signal, and skipping it would leave the kid's first drawing with nothing
    // watching for it.
    mkdirs(root);
    for (const rel in files) {
        const data = store.fileData(files[rel]);   // text, or a cover's bytes
        if (data === null) continue;
        const full = root + "/" + rel;
        mkdirs(full.slice(0, full.lastIndexOf("/")));
        mp.FS.writeFile(full, data);
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
                             : "carts, drawings & settings saved in this browser";
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
    const s = await store.openStore(opfsNav, root.id);
    opfsStores[root.id] = s;
    if (!s) {                                // no OPFS for this root: in memory
        writeStore(root.vfs, data);
        return "none";
    }
    try {
        if (root.id === "carts") {
            // An install a reload interrupted finishes or goes BEFORE the
            // store is read, and the record comes with the carts it describes.
            const n = await store.recoverInstalls(s);
            if (n) console.log("[moy] carts: " + n + " interrupted install(s) finished");
            const rec = await store.readRecord(s);
            if (rec !== null) {
                const at = store.rootById("system").vfs;
                mkdirs(at);
                mp.FS.writeFile(at + "/" + installRecord, rec);
            }
        }
        return await seedLocal(root, s, data);
    } catch (e) {
        writeStore(root.vfs, data);
        opfsStores[root.id] = null;
        console.log("[moy] persist: OPFS failed for " + root.id + " -- " + e);
        return "none";
    }
}

// A root's store of record, and what of the served bundle it takes. The VM
// decides (moy_sync.seed_store: the carts by moy_seed.seed_folders --
// everything on a first visit, else the system carts a board would seed and
// a torn seed of any -- every other root the bundle whole on a first visit);
// this only moves bytes: OPFS into the VFS, the bundle into a staging folder
// the VM reads, and back into OPFS what the VM wrote and removed, in its
// order -- each cart's manifest last, so a reload part-way is seeded again.
// "seed" (a first visit) vs "load" is the evidence the E2E reads back.
async function seedLocal(root, s, data) {
    writeStore(root.vfs, await store.readAll(s));
    writeStore(VFS.seed, data);
    mp.runPython("import moy_sync, moy_seed, json\n_moy_seeded = json.dumps(moy_sync.seed_store("
                 + [root.id, VFS.seed, VFS.carts].map((v) => JSON.stringify(v)).join(", ")
                 + "))\nmoy_seed._rmtree(" + JSON.stringify(VFS.seed) + ")\n");
    const got = JSON.parse(mp.globals.get("_moy_seeded"));
    mp.runPython("del _moy_seeded");
    for (const rel of got.removed) await store.removeFile(s, rel);
    const wrote = {};
    for (const rel of got.wrote) wrote[rel] = mp.FS.readFile(root.vfs + "/" + rel);
    await store.seed(s, wrote);
    return got.first ? "seed" : "load";
}

// The store module's rules, from the VM before any store is opened
// (moy_store.mjs's `bind`).
function bindStore() {
    const v = mp.pyimport("web_vfs");
    VFS = { carts: v.CARTS, seed: v.SEED, listing: v.LISTING, drop: v.DROP, export: v.EXPORT };
    mp.runPython("import moy_sync\n_moy_roots = moy_sync.roots_json("
                 + JSON.stringify(VFS.carts) + ")\n");
    const roots = JSON.parse(mp.globals.get("_moy_roots"));
    mp.runPython("del _moy_roots");
    const native = mp.pyimport("moy_store"), sync = mp.pyimport("moy_sync");
    const ci = mp.pyimport("cart_index");
    installRecord = ci.RECORD_NAME;
    store.bind({
        roots,
        skip: (name, history) => native.skip(name, history),
        path: (id, rel, dc) => sync.local_segs(id, rel, dc),
        folder: (id, rel) => sync.local_folder(id, rel),
        song: (rel) => sync.local_song(rel),
        plain: (name) => ci.plain_name(name),
        record: installRecord,
        stage: ci.STAGE_DIR,
    });
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
    seededRoots = [];
    const sync = mp.pyimport("moy_sync");
    for (const root of store.roots()) {
        const data = fetched[root.id];
        // Which roots a page seeds is the VM's (moy_sync.seeds); a root it
        // skips gets no watcher and no empty directory.
        if (!sync.seeds(root.id, site, data != null)) continue;
        seededRoots.push(root.id);
        const w = await seedRoot(root, data, site);
        if (w === "seed" || w === "load") anySite = true;
        if (w === "load") loaded = true;
        if (w === "none") anyNone = true;
    }
    if (site) await seedWritten();
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

// The written files OPFS keeps, into the VFS before the console boots, with
// every write a reload interrupted finished or discarded first.
async function seedWritten() {
    writtenStore = await store.openStore(opfsNav, "apps");
    if (!writtenStore) return;
    try {
        const files = await store.readWritten(writtenStore);
        for (const rel in files) {
            const full = writtenStore.root.vfs + "/" + rel;
            mkdirs(full.slice(0, full.lastIndexOf("/")));
            mp.FS.writeFile(full, files[rel]);
        }
    } catch (e) {
        writtenStore = null;
        console.log("[moy] written: OPFS failed -- " + e);
    }
}

// A file a compiled cart wrote, from the VFS into OPFS, read as it is now:
// writes reach the store in the order the cart made them.
function writtenKeep(j) {
    if (!writtenStore) return;
    let data;
    try { data = mp.FS.readFile(writtenStore.root.vfs + "/" + j.cart + "/" + j.key); }
    catch (e) { return; }
    opfsSerial(() => store.commitWritten(writtenStore, j.cart, j.key, data))
        .then(() => cartsRoom())
        .catch((e) => console.log("[moy] written: not kept -- " + e));
}

function writtenDrop(j) {
    if (!writtenStore) return;
    opfsSerial(() => store.dropWritten(writtenStore, j.cart, j.key))
        .catch((e) => console.log("[moy] written: not dropped -- " + e));
}

function vfsIsDir(p) {
    const m = mp.FS.stat(p).mode;
    return typeof mp.FS.isDir === "function" ? mp.FS.isDir(m) : (m & 0o170000) === 0o040000;
}

function vfsExists(p) {
    try { mp.FS.stat(p); return true; } catch (e) { return false; }
}

// The shelf's cart folders (moy_sync.folders_json), from the moment the
// store's rules are bound.
function cartNames() {
    return VFS ? JSON.parse(mp.pyimport("moy_sync").folders_json(VFS.carts)) : [];
}

// A dropped `.moy` zip onto the shelf, unpacked, named and adopted by the
// VM (web_boot.import_archive_json over moy_carts.import_archive: a theme
// under its own name, a cart under the store's import rule), then a re-scan
// WITHOUT rebasing the watcher: the imported files stay pending, so the very
// next sweep writes them to the local store like any other commit.
async function importZip(name, buf) {
    if (!importArchiveJson) throw new Error("this console cannot import zips");
    const tmp = VFS.drop;
    mkdirs(dirOf(tmp));
    mp.FS.writeFile(tmp, new Uint8Array(buf));
    const r = JSON.parse(importArchiveJson(tmp, name, VFS.carts));
    if (r.error) throw new Error(r.error);
    if (rescan) rescan();
    return { dir: r.dir };
}

// The cart as it sits in the VFS RIGHT NOW (the live shelf, not the local
// store, which lags it by up to one sweep), packed by the VM with the wire's
// skip rule: an exported zip carries exactly what a board would be sent.
function exportZip(cart) {
    if (!exportCart) throw new Error("this console cannot export carts");
    const tmp = VFS.export;
    mkdirs(dirOf(tmp));
    try {
        if (!vfsIsDir(VFS.carts + "/" + cart) || !exportCart(VFS.carts + "/" + cart, tmp))
            throw new Error("no such cart");
        return mp.FS.readFile(tmp);
    } finally {
        try { mp.FS.unlink(tmp); } catch (e) { /* none written */ }
    }
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

// A dropped song into this browser's music folder, where Music finds it; a
// board-served page's songs are the board's own.
async function importSong(name, buf) {
    if (mode === "board") throw new Error("songs play from the console's own music folder");
    const s = opfsStores.files;
    if (!s) throw new Error("this browser keeps no songs");
    const kept = await opfsSerial(() => store.keepSong(s, name, new Uint8Array(buf)));
    if (!kept) throw new Error("not a song: " + name);
    await mediaList(siteSongs);
    return "added " + kept + " to your music";
}

// Which importer a dropped file goes to: the VM's call, from its name and
// its first bytes (web_boot.drop_kind).
function dropped(name, buf) {
    if (!dropKind) throw new Error("this console cannot import yet");
    const head = buf ? new Uint8Array(buf, 0, Math.min(8, buf.byteLength)) : [];
    return dropKind(String(name || ""), JSON.stringify(Array.from(head)));
}

async function importP8(name, buf) {
    if (!importP8Json) throw new Error("this console cannot import PICO-8 carts");
    // One fixed path rather than the dropped name: the name is attacker-shaped
    // text (it came off a file a browser handed us) and the converter only ever
    // needs SOME path to read. The real name still travels as `name`, which is
    // what upstream titles the cart from when its Lua has no title comment.
    const tmp = VFS.drop;
    mkdirs(dirOf(tmp));
    mp.FS.writeFile(tmp, new Uint8Array(buf));
    const r = JSON.parse(importP8Json(tmp, name, VFS.carts));
    if (!r.ok) return { ok: false, report: r.report || ["that cart could not be imported"] };
    // Same rule as importZip: do NOT rebase the sync watcher. An import is a
    // CHANGE, so the new files must stay pending and reach the local store on
    // the next sweep like any other commit.
    if (rescan) rescan();
    return { ok: true, dir: r.dir, title: r.title, report: r.report };
}

// THE SIGN-IN (native/moy_net/moy_seal.h; the threat model is
// docs/os_2026-11/system.md section 3). A board's store, shell, pins and
// updater answer only a sealed session: AES-GCM under keys a sign-in made.
// Plain HTTP has no secure context, so there is no WebCrypto here; every MAC,
// key and seal is the wasm's own C (moy_seal_web.c, the same mbedTLS the board
// runs), and this file only moves bytes. The sign-in, once per page:
//
//   a hello (POST /auth), then the first of: this browser's DEVICE KEY for
//   the board's id (the page keeps them, page_tail.js), the QR's single-use
//   token from the url's # (which pairs this browser: the board mints its key
//   and the page stores it), or nothing -- the page asks to pair: by the
//   console's password (SPAKE2+, `{t:"password"}`), or by scanning the code.
//
// A credential whose hello's nonce the board no longer holds is answered 409
// {"error":"nonce"} (other hellos took its place, or it ran out): the page
// asks again, a few times, and nothing it holds is spent. A key the board
// does not know (403 {"error":"kid"}) is forgotten; a wrong one is kept, and
// the page asks to pair. A QR's token is spent once the board has judged it.
//
// A sealed call that meets 401 signs in again once and retries. A host that
// answers the hello {"open": true} (the dev server, a test) is spoken to in
// the clear, and so is a static host (no /auth, no board marker), but only by
// a page that holds no key and has never signed in: a board always seals, so
// to any other page an open answer, a missing /auth (404, 405, 501) or a 2xx
// in the clear to a sealed call is an error.
//
// THE BACKOFF, said one way. A source that gave too many wrong credentials
// waits (native/moy_net/moy_webhost.c, the backoff), and while it waits the
// sign-in answers 429 {"error":"wait","ms":N} with its credential unread.
// Each call that meets one says signInWaitSays' sentence, wherever it says it:
// the pairing prompt, the link panel, Settings' update screen, the play-on-
// device link. "--", not a dash: the console's font draws Latin-1 and a few
// symbols, and the update screen is drawn in it. The Terminal says its own
// (native/moy_sh/moy_sh_link.c), from termWhy's ms.
function signInWaitSays(ms) {
    return "Too many wrong tries -- try again in "
        + Math.max(1, Math.ceil(ms / 1000)) + " s";
}

let auth = { keys: {}, token: null, board: null, name: "a browser" };
let authOpen = false;           // the host has no sign-in: plain requests
let signedIn = false;
let sealedOnce = false;         // this page has signed in: never plain again
const NONCE_TRIES = 3;          // hellos a credential is sent after
let signingIn = null;           // the sign-in in flight, which every caller shares
let authNeed = null;            // why the last sign-in failed: {pw, wait, why}
let SW = null;                  // moy_seal_web.c's entry points
const utf8 = new TextEncoder(), utf8d = new TextDecoder();

function sw() {
    if (SW) return SW;
    const M = mp._module;
    const f = (n) => M["_moy_sw_" + n];
    SW = { M, buf: f("buf"), hello: f("hello"), board: f("board"), key: f("key"),
           token: f("token"), pake: f("pake"), confirm: f("confirm"), done: f("done"),
           kid: f("kid"), devkey: f("device_key"), seal: f("seal"), target: f("target"),
           seq: f("seq"), open: f("open"), ws: f("ws"), up: f("ws_up"), down: f("ws_down"),
           reset: f("reset") };
    return SW;
}
// The exchange buffer: bytes in, bytes out. HEAPU8 read fresh: growth replaces it.
function swPut(u8) {
    const C = sw();
    const p = C.buf(u8.length);
    C.M.HEAPU8.set(u8, p);
    return u8.length;
}
function swGet(n) {
    const C = sw();
    const p = C.buf(0);
    return C.M.HEAPU8.slice(p, p + n);
}
// A C call with string arguments, each copied in and freed after.
function swCall(fn, ...strs) {
    const M = sw().M;
    const ps = strs.map((s) => {
        const n = M.lengthBytesUTF8(s) + 1;
        const p = M._malloc(n);
        M.stringToUTF8(s, p, n);
        return p;
    });
    try { return fn(...ps); }
    finally { for (const p of ps) { M.HEAPU8.fill(0, p, p + 1); M._free(p); } }
}
function b64urlHex(s) {
    try {
        const b = atob(s.replace(/-/g, "+").replace(/_/g, "/"));
        let h = "";
        for (let i = 0; i < b.length; i++) h += b.charCodeAt(i).toString(16).padStart(2, "0");
        return h;
    } catch (e) { return ""; }
}

async function authPost(text) {
    const r = await fetch("auth", { method: "POST", body: text,
        headers: { "Content-Type": "application/json" } });
    const t = await r.text();
    let doc = null;
    try { doc = JSON.parse(t); } catch (e) { }
    return { status: r.status, text: t, doc: doc || {} };
}

// A hello: the board's id, or the answer that stopped it (a wait, an error),
// or {open} from a host with no sign-in to a page that may believe it: one
// that holds no key and never signed in, told so by a host that answers
// {"open": true} (the dev server, a test) or by one with no /auth that is no
// board either (a static host: no GET /sync marker, moy_store.probeMode).
async function hello() {
    const r = await authPost('{"op":"hello"}');
    const open = r.status === 200 && r.doc.open === true;
    const none = r.status === 404 || r.status === 405 || r.status === 501;
    if (open || none) {
        if (!sealedOnce && Object.keys(auth.keys).length === 0
            && (open || (await store.probeMode((u, o) => fetch(u, o))) !== "board")) {
            return { open: true };
        }
        return { r: open ? { status: 0, doc: {} } : r };
    }
    if (r.status !== 200) return { r };
    if (sw().hello(swPut(utf8.encode(r.text))) !== 0) return { r: { status: 0, doc: {} } };
    return { id: sw().M.UTF8ToString(sw().board()), pw: !!r.doc.pw };
}

// The answer to a key login or a pairing: signed in, its key handed to the
// page when it is new.
function finish(r, fresh) {
    if (r.status !== 200 || sw().done(swPut(utf8.encode(r.text))) !== 0) return false;
    signedIn = true;
    sealedOnce = true;
    if (fresh) {
        const C = sw();
        const id = C.M.UTF8ToString(C.board());
        const kid = C.M.UTF8ToString(C.kid()), key = C.M.UTF8ToString(C.devkey());
        auth.keys[id] = { kid, key };
        self.postMessage({ t: "key", id, kid, key, reload: fresh === "reload" });
    }
    return true;
}

// A credential built by `build` (from the last hello's nonce) and its
// answer, asked again after a fresh hello while the board says the nonce is
// gone: {h, r}, `h` the hello it went with.
async function withNonce(h, build) {
    for (let i = 0; ; i++) {
        const r = await authPost(utf8d.decode(swGet(build())));
        if (r.status !== 409 || i + 1 >= NONCE_TRIES) return { h, r };
        h = await hello();
        if (!h.id) return { h, r: h.r || { status: 0, doc: {} } };
    }
}

async function signInOnce() {
    let h = await hello();
    if (h.open) { authOpen = true; return true; }
    if (!h.id) return need(h.r, false);
    const k = auth.keys[h.id];
    if (k) {
        let r;
        ({ h, r } = await withNonce(h, () => swCall(sw().key, k.kid, k.key)));
        if (finish(r, false)) return true;
        if (r.status !== 403) return need(r, h.pw);
        if (r.doc.error === "kid") {
            delete auth.keys[h.id];          // this board does not know it: pair again
            self.postMessage({ t: "unkey", id: h.id });
        }
        h = await hello();
        if (!h.id) return need(h.r, false);
    }
    const tok = auth.token ? b64urlHex(auth.token) : "";
    const mine = auth.board ? b64urlHex(auth.board) : "";
    if (tok && (!mine || h.id.startsWith(mine))) {
        let r;
        ({ h, r } = await withNonce(h, () => swCall(sw().token, tok, auth.name)));
        if (r.status === 200 || r.status === 403) auth.token = null;   // judged: spent
        if (finish(r, true)) return true;
        if (r.status === 403) {
            return need(null, h.pw, "that code was used or has run out -- scan it again");
        }
        return need(r, h.pw);
    }
    auth.token = null;
    return need(null, h.pw, tok ? "that code is another console's" : "");
}
// `gone`: no answer from the board at all (a fetch that failed, a 5xx from
// whatever stands between), which the Terminal says apart from a refusal.
function need(r, pw, why, gone) {
    let wait = "", ms = -1;
    if (r && r.status === 429) { ms = r.doc.ms | 0; wait = signInWaitSays(ms); }
    else if (r && !why) {
        why = "the console did not answer the sign-in (" + r.status + ")";
        gone = gone || r.status >= 500;
    }
    authNeed = { pw: !!pw, wait, ms, why: why || "", gone: !!gone };
    return false;
}
function signIn() {
    if (authOpen || signedIn) return Promise.resolve(true);
    if (!signingIn) {
        signingIn = signInOnce().catch(() => need(null, false, "the console did not answer", true))
            .finally(() => { signingIn = null; });
    }
    return signingIn;
}

// Pairing by the console's password (the page's prompt): SPAKE2+, then the
// key, then a fresh load signed in with it.
async function pairPassword(pw, name) {
    let why = "";
    try {
        for (let i = 0; i < NONCE_TRIES; i++) {
            const h = await hello();
            if (!h.id) return need(h.r, true);
            const n = swCall(sw().pake, pw, name || auth.name);
            if (n < 0) { why = "the console's answer did not check -- try again"; break; }
            let r = await authPost(utf8d.decode(swGet(n)));
            if (r.status === 409) continue;            // the nonce went: ask again
            if (r.status === 200) {
                // The board's confirmation fails to check under a wrong password
                // (and on another console, which the same sentence covers).
                const c = sw().confirm(swPut(utf8.encode(r.text)));
                if (c < 0) why = "that password did not work -- try again";
                else {
                    r = await authPost(utf8d.decode(swGet(c)));
                    if (finish(r, "reload")) return true;
                    if (r.status === 409) why = "the console waited too long -- try again";
                }
            }
            if (r.status === 429) return need(r, true);
            why = why || (r.doc.error === "nopw" ? "the console has no password set"
                                                 : "that password did not work -- try again");
            break;
        }
    } catch (e) { why = "the console did not answer"; }
    return need(null, true, why || "the console did not answer");
}
function askToPair() {
    self.postMessage(Object.assign({ t: "pair" }, authNeed || { pw: false, wait: "", why: "" }));
}

// A call to a sealed route: {status, text, wait}. `text` is the answer's
// plaintext, opened; `wait` the ms of a backoff, else -1. A refusal in the
// clear (no session, a goodbye) is {status, text} as it came, never opened,
// so nothing the network says without the session's key reads as the board's.
async function sealed(method, path, body, bin) {
    for (let tries = 0; tries < 2; tries++) {
        if (!(await signIn())) {
            const ms = authNeed ? authNeed.ms : -1;
            return { status: ms >= 0 ? 429 : 401, text: "", wait: ms };
        }
        if (authOpen) {
            const r = await fetch(path.slice(1), body === undefined ? { method }
                : { method, body, headers: { "Content-Type": "application/json" } });
            if (bin) return { status: r.status, bytes: new Uint8Array(await r.arrayBuffer()), wait: -1 };
            return { status: r.status, text: await r.text(), wait: -1 };
        }
        const C = sw();
        const raw = body === undefined ? null : utf8.encode(body);
        const n = swCall((m, p) => C.seal(m, p, raw ? swPut(raw) : 0), method, path);
        if (n < 0) return { status: 0, text: "", wait: -1 };
        const target = C.M.UTF8ToString(C.target()), seq = C.seq();
        const r = await fetch(target.slice(1), n ? { method, body: swGet(n) } : { method });
        if (r.status === 401) { signedIn = false; continue; }
        if ((r.headers.get("Content-Type") || "").indexOf("application/octet-stream") < 0) {
            // In the clear: a refusal at most, never the board's answer.
            if (r.status >= 200 && r.status < 300) return { status: 0, text: "", wait: -1 };
            return { status: r.status, text: await r.text(), wait: -1, clear: true };
        }
        const got = new Uint8Array(await r.arrayBuffer());
        const m = C.open(seq, r.status, got.length ? swPut(got) : 0);
        if (m < 0) return { status: 0, text: "", wait: -1 };
        if (bin) return { status: r.status, bytes: swGet(m), wait: -1 };
        return { status: r.status, text: utf8d.decode(swGet(m)), wait: -1 };
    }
    return { status: 401, text: "", wait: -1 };
}
function sealedJson(res) {
    try { return JSON.parse(res.text); } catch (e) { return null; }
}

// A fatal error as {t:"error"}. V8's e.stack leads with String(e), JSC's
// (Safari's) and SpiderMonkey's hold the frames alone -- so on an iPhone the
// stack without the message is an abort with no reason. The message goes first
// by hand when the stack does not already carry it.
function crashMessage(e) {
    const msg = String(e);
    const stack = (e && typeof e.stack === "string") ? e.stack : "";
    return { t: "error", msg,
             s: !stack ? msg : stack.startsWith(msg) ? stack : msg + "\n" + stack };
}

async function init(search, coarse, channel, scheme, signin, rate) {
    opfsNav = store.channelNav(navigator, channel);
    const qs = new URLSearchParams(search || "");
    if (signin) auth = Object.assign(auth, signin, { keys: Object.assign({}, signin.keys) });
    say("loading vm...");
    // 16MB: the per-frame GC sweep scales with heap SIZE under this build's
    // GC_SPLIT_HEAP_AUTO (~0.13ms/MB/frame, #176), so a generous heap is a
    // permanent frame tax, not headroom.
    mp = await loadMicroPython({ heapsize: 16 * 1024 * 1024,
        stdout: (l) => console.log("[moy]", l) });
    installCartEngine(mp._module);
    installCommandEngine(mp._module);
    say("loading console...");
    // FROZEN-first, like the page was: a ship build bakes the console into the wasm
    // and has no modules.json; a --stage-only dev dist adds one, and loading it into
    // /modules (first on sys.path) shadows the frozen copies.
    // indexes.json: the shelves Get Carts lists, chosen by whoever SERVES this
    // page -- written as the console's system/sources, a board's twin.
    // Absent (moybyte.com, a board) means the console's default shelves.
    //
    // Every body is READ as soon as its head arrives, carts.json's included. A
    // board serves one connection at a time and cuts off a client that stops
    // reading for its send budget (native/moy_net, MOY_HTTP_SEND_MS). A body
    // left unread until the other answers are in -- and they queue behind it
    // -- stops draining once the browser's buffer is full, and the board cuts
    // it off: a board with more carts than that serves a console that cannot
    // boot. tests/test_web_store_e2e.py boots one over a board-shaped link.
    // Each sealed read as {status, wait, json}, json for a 2xx alone (a host
    // with no sign-in answers in the clear: worker's THE SIGN-IN).
    const gated = (path) => sealed("GET", path)
        .then((r) => ({ status: r.status, wait: r.wait,
                        json: r.status >= 200 && r.status < 300 ? sealedJson(r) : null }));
    const none = () => ({ status: 0, wait: -1, json: null });
    const [mods, shelves] = await Promise.all([
        fetch("modules.json").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("indexes.json").then((r) => r.ok ? r.json() : null).catch(() => null)]);
    if (mods) {
        mkdirs("/modules");
        for (const n in mods) mp.FS.writeFile("/modules/" + n, mods[n]);
        // Before the store's rules and the seed, which are the VM's too.
        mp.runPython("import sys\nsys.path.insert(0, '/modules')\n");
    }
    bindStore();
    // Every root's pull, at the endpoint the VM's registry names: the carts,
    // and the roots firmware before them 404s (the files layer, the console's
    // own documents, the apps' private folders).
    const pulled = await Promise.all(store.roots().map((r) => r.id === "carts"
        ? gated("/" + r.endpoint) : gated("/" + r.endpoint).catch(none)));
    const fetched = {};
    store.roots().forEach((r, i) => { fetched[r.id] = pulled[i].json; });
    const cartsRes = pulled[store.roots().indexOf(store.rootById("carts"))];
    if (!signedIn && !authOpen && !(authNeed && authNeed.gone)) {
        // A BOARD THAT HAS NOT PAIRED THIS BROWSER: stop the boot here --
        // there is nothing to boot, and seeding the VFS from a refusal would
        // build a shelf out of it. The page asks; a pairing reloads it. A
        // host that answered nothing boots on as before: no board behind it.
        const a = authNeed || { pw: false, wait: "", why: "" };
        say(a.wait || "pair this browser with the console");
        askToPair();
        console.log("[moy] sign-in: " + (a.wait || a.why || "not paired"));
        return;
    }
    if (!signedIn && !authOpen && cartsRes.json === null) {
        // The board went away between the page and its pulls: no shelf to
        // seed, and nothing to sign in to.
        say("the console does not answer");
        console.log("[moy] sign-in: the console does not answer");
        return;
    }
    await initStore(fetched);
    // Music's songs: the serving board's music folder, or this browser's.
    await mediaList(mode === "board" ? () => gated("/media").then((r) => r.json) : siteSongs);
    say("booting console...");

    // #9: does whoever served this page have PINS? ONE probe, here, before the
    // console exists -- so `pin_write`/`pin_read` are decided once and a cart
    // either has the names or has never heard of them. An empty batch is the
    // probe (the board answers it with its allowlist), a sealed call like
    // every write it later makes.
    const gpioPins = await probeGpio();
    const updateDoc = await probeUpdate();
    mp.runPython("import web_boot\n"
        // BEFORE boot(): the mode is what decides whether the carts watcher
        // sweeps the journal, and boot() is where that watcher is built.
        // `mode || ""` and not `mode`: JSON.stringify(null) is the token `null`,
        // which is a NameError in the Python this string becomes.
        + "web_boot.store_mode(" + JSON.stringify(mode || "") + ", "
        + JSON.stringify(seededRoots || []) + ")\n"
        // indexes.json: the shelves Get Carts lists, chosen by whoever SERVES
        // this page (a site-mode page's system/sources).
        + (shelves ? "web_boot.site_sources(" + JSON.stringify(VFS.carts) + ", "
            + JSON.stringify(JSON.stringify(shelves)) + ")\n" : "")
        + (gpioPins ? "web_boot.gpio_enable(" + JSON.stringify(JSON.stringify(gpioPins)) + ")\n" : "")
        // The console this page's address asks for: its tier, size and cart
        // (web_boot.page_boot), on the page's pointer and colour scheme
        // (page_tail.js: ?scheme= or prefers-color-scheme).
        // The output's rate: the page's AudioContext's (web_boot.audio_rate).
        + "web_boot.audio_rate(" + Math.round(rate) + ")\n"
        + "web_boot.page_boot(" + JSON.stringify(VFS.carts) + ", "
        + JSON.stringify(JSON.stringify(Object.fromEntries(qs))) + ", "
        + (coarse ? "True" : "False") + ", " + JSON.stringify(scheme || "") + ")\n"
        // AFTER boot(), unlike gpio_enable: this one hangs the updater on the
        // live Workstation, which boot() is what creates.
        + (updateDoc ? "web_boot.update_enable("
            + JSON.stringify(JSON.stringify(updateDoc)) + ")\n" : "")
        // What the address starts it on (web_boot.page_start).
        + "web_boot.page_start()\n"
        + "from web_boot import assets_json, step_frame_json, apply_events_json, "
        + "reload_cart, idle_collect, fb_addr, fb_len, "
        + "sync_poll_json, sync_ack, sync_off, sync_root, store_mode, "
        + "rescan_store, gpio_poll_json, gpio_ack_json, gpio_off, "
        + "update_poll_json, update_wants_poll, update_ack_json, update_off, "
        + "services_json, carts_poll_json, carts_event_json, "
        + "import_p8_json, import_archive_json, export_cart, drop_kind, "
        + "edit_cart, open_cart, state_json, "
        + "term_enable, term_poll_json, term_feed, term_state");
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
    syncRoot = mp.globals.get("sync_root");
    rescan = mp.globals.get("rescan_store");
    cartsPoll = mp.globals.get("carts_poll_json");
    cartsEvent = mp.globals.get("carts_event_json");
    if (keep) cartsSay({ room: [keep.usage, keep.quota] });
    importP8Json = mp.globals.get("import_p8_json");
    importArchiveJson = mp.globals.get("import_archive_json");
    exportCart = mp.globals.get("export_cart");
    dropKind = mp.globals.get("drop_kind");
    editCart = mp.globals.get("edit_cart");
    openCart = mp.globals.get("open_cart");
    stateJson = mp.globals.get("state_json");
    mp.globals.get("term_enable")(mode === "board");
    if (mode === "board") {
        termPoll = mp.globals.get("term_poll_json");
        termFeed = mp.globals.get("term_feed");
        termState = mp.globals.get("term_state");
    }
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

// The #9 probe. Returns the host's GPIO allowlist, or null for "this page is
// not served by anything with pins" -- a static host (moybyte.com, an export),
// a console board that spends its GPIOs on a panel, or a Zero this page is not
// signed in to. All four are the same answer to a cart: no verbs.
async function probeGpio() {
    try {
        const r = await sealed("POST", "/gpio", JSON.stringify({ v: 1, ops: [] }));
        if (r.wait >= 0) console.log("[moy] gpio: " + signInWaitSays(r.wait));
        if (r.status !== 200) return null;
        const d = sealedJson(r);
        return (d && d.pins && d.pins.length) ? d.pins : null;
    } catch (e) {
        return null;                 // no such host, no such endpoint: fine
    }
}

// Does whoever served this page have an UPDATER? One GET, before the console
// exists, so `ws.updater` is decided exactly once and Settings either has an
// update row or has never heard of one -- the same rule as the GPIO verbs, and
// the reason a static host (moybyte.com, an export) shows nothing rather than a
// row that fails when tapped.
async function probeUpdate() {
    try {
        const r = await sealed("GET", "/update");
        if (r.wait >= 0) console.log("[moy] update: " + signInWaitSays(r.wait));
        if (r.status !== 200) return null;
        const d = sealedJson(r);
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
    const req = body ? sealed("POST", "/update", body) : sealed("GET", "/update");
    req.then((r) => {
            // A wait is the board answering: the screen says it and the link
            // stays up (update_link.RemoteUpdater.ack).
            if (r.wait >= 0) {
                try { updateAck(0, JSON.stringify({ wait: signInWaitSays(r.wait) })); }
                catch (e) { }
                return null;
            }
            // The boot probe already cleared these, so meeting one HERE means
            // the host changed under us -- rebooted into setup, this browser's
            // key dropped.
            // Go inert once rather than retry-logging: the SCREEN stays up and
            // reads an error, which is the most a person looking at it can be
            // told.
            if (r.status === 404 || r.status === 405 || r.status === 501
                || r.status === 401 || r.status === 0) {
                try { updateAck(0, ""); updateOff(); } catch (e) { }
                updatePoll = null;
                console.log("[moy] update off: host stopped answering ("
                            + r.status + ")");
                return null;
            }
            const ok = r.status >= 200 && r.status < 300 ? 1 : 0;
            return Promise.resolve(r.text).then((t) => {
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
    sealed("POST", "/gpio", body)
        .then((r) => {
            // The probe already cleared 404/405/401, so reaching one HERE means
            // the host changed under us (rebooted into setup, this browser's
            // key dropped).
            // Stop for good rather than retry-logging: the verbs go inert and
            // say so once, which is the most a running cart can be told.
            if (r.status === 404 || r.status === 405 || r.status === 501
                || r.status === 401 || r.status === 0) {
                try { gpioAck(0, ""); gpioOff(); } catch (e) { }
                gpioPoll = null;
                console.log("[moy] gpio off: host stopped answering ("
                            + r.status + ")");
                return null;
            }
            try { gpioAck(r.status === 200 ? 1 : 0, r.text); } catch (e) { }
        })
        .catch(() => { try { gpioAck(0, ""); } catch (e) { } })
        .finally(() => { gpioBusy = false; });
}

// THE TERMINAL on a board-served page is a face of the board's shell
// (web_boot's term_*, native/moy_sh/moy_sh_link.h): one WebSocket to the
// board's GET /term (native/moy_tty), opened when the Terminal asks and
// closed when it lets go. The upgrade is a sealed request of the page's
// session and every frame after it is sealed (moy_seal.h: 'k' keys and 's'
// the size up, the face's bytes down). Its bytes and its fate queue here in
// arrival order and are handed to the VM between steps, never inside one. A
// socket that fails before it opens says nothing of why, so the same route
// without the upgrade is asked: 401 this browser refused, a wait, 404 no
// shell here.
const TERM = { OPEN: 2, ENDED: 3, LOST: 4, UNREACHABLE: 5, REFUSED: 6, WAIT: 7, BUSY: 8, NONE: 9 };
let termPoll = null, termFeed = null, termState = null;
let termWs = null, termSealed = false;
const termQ = [];
function termB64(u8) {
    let s = "";
    for (let i = 0; i < u8.length; i += 0x8000) {
        s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    }
    return btoa(s);
}
function termClose() {
    const ws = termWs;
    termWs = null;
    if (ws) { try { ws.close(1000); } catch (e) { } }
}
async function termWhy() {
    try {
        const r = await sealed("GET", "/term?probe=1");
        if (r.wait >= 0) return [TERM.WAIT, r.wait];
        if (r.status === 401 || r.status === 0) return [TERM.REFUSED, 0];
        if (r.status === 404) return [TERM.NONE, 0];
    } catch (e) { }
    return [TERM.UNREACHABLE, 0];
}
// One frame up: `kind` 'k' (keys) or 's' (the size), sealed on a signed-in
// page; the open host's plain messages otherwise.
function termSend(ws, kind, u8) {
    if (!termSealed) {
        ws.send(kind === "s" ? utf8d.decode(u8) : u8);
        return;
    }
    const raw = new Uint8Array(u8.length + 1);
    raw[0] = kind.charCodeAt(0);
    raw.set(u8, 1);
    const n = sw().up(swPut(raw));
    if (n > 0) ws.send(swGet(n));
}
async function termOpen(cols, rows) {
    termClose();
    const path = "/term?cols=" + cols + "&rows=" + rows;
    let target = path;
    termSealed = false;
    if (!(await signIn())) {
        const a = authNeed || { ms: -1 };
        termQ.push({ s: a.ms >= 0 ? TERM.WAIT : a.gone ? TERM.UNREACHABLE : TERM.REFUSED,
                     n: a.ms >= 0 ? a.ms : 0 });
        return;
    }
    if (!authOpen) {
        const C = sw();
        if (swCall((m, p) => C.seal(m, p, 0), "GET", path) < 0) {
            termQ.push({ s: TERM.REFUSED, n: 0 });
            return;
        }
        target = C.M.UTF8ToString(C.target());
        C.ws(C.seq());
        termSealed = true;
    }
    const u = new URL(target.slice(1), self.location.href);
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    let ws;
    try { ws = new WebSocket(u.href, "moy.sh"); } catch (e) {
        termQ.push({ s: TERM.UNREACHABLE, n: 0 });
        return;
    }
    ws.binaryType = "arraybuffer";
    let opened = false;
    termWs = ws;
    ws.onopen = () => { if (termWs === ws) { opened = true; termQ.push({ s: TERM.OPEN, n: 0 }); } };
    ws.onmessage = (m) => {
        if (termWs !== ws || typeof m.data === "string") return;
        let u8 = new Uint8Array(m.data);
        if (termSealed) {
            const n = sw().down(swPut(u8));
            if (n < 0) {                 // a frame that does not open: the session is over
                termWs = null;
                try { ws.close(1000); } catch (e) { }
                termQ.push({ s: TERM.LOST, n: 0 });
                return;
            }
            u8 = swGet(n);
        }
        termQ.push({ d: termB64(u8) });
    };
    ws.onclose = (c) => {
        if (termWs !== ws) return;
        termWs = null;
        const exit = /^exit (-?\d+)$/.exec(c.reason || "");
        if (c.code === 1000 && exit) termQ.push({ s: TERM.ENDED, n: +exit[1] });
        else if (c.code === 1013) termQ.push({ s: TERM.BUSY, n: 0 });
        else if (c.code === 1008 && c.reason === "revoked") termQ.push({ s: TERM.REFUSED, n: 0 });
        else if (opened) termQ.push({ s: TERM.LOST, n: 0 });
        else termWhy().then(([st, n]) => termQ.push({ s: st, n: n }));
    };
}
function termPump() {
    if (!termPoll) return;
    let j = "";
    try { j = termPoll(); } catch (e) { return; }
    if (j) {
        const d = JSON.parse(j);
        if (d.want === 2) termClose();
        if (d.want === 1) termOpen(d.cols, d.rows);
        if (termWs && termWs.readyState === 1) {
            if (d.size) termSend(termWs, "s", utf8.encode(JSON.stringify({ size: d.size })));
            if (d.out) termSend(termWs, "k", new Uint8Array(d.out));
        }
    }
    while (termQ.length) {
        const q = termQ.shift();
        try { if (q.d !== undefined) termFeed(q.d); else termState(q.s, q.n); } catch (e) { }
    }
}

// MODE 1 delivery: the batch the sweep just built, applied into OPFS.
// Per-op failures are LOGGED and still ack true, exactly as the board path
// treats a 200 carrying `err` entries -- a malformed op is poison that would
// otherwise replay forever. Only a store-level failure requeues.
async function pumpLocal(body) {
    const t0 = performance.now();
    try {
        const ops = JSON.parse(body).ops || [];
        // Into the OPFS store of the root the VM took the batch from -- so a
        // files batch persists drawings and a carts batch persists carts,
        // from the one pump.
        const rootId = syncRoot();
        const s = opfsStores[rootId];
        if (!s) {                            // no store for this root: nothing to
            try { syncAck(1); } catch (e) { }  // keep; ack so it does not requeue
            return;
        }
        const r = await opfsSerial(() => store.applyOps(s, ops));
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

function cartsPump() {
    if (!cartsPoll) return;
    const now = performance.now();
    if (!cartsLive.size && now - lastCartsAt < CARTS_IDLE_MS) return;
    lastCartsAt = now;
    let jobs = "";
    try { jobs = cartsPoll(); } catch (e) { return; }
    if (!jobs) return;
    for (const j of JSON.parse(jobs)) {
        if (j.op === "get") cartsGet(j);
        else if (j.op === "mget") mediaGet(j);
        else if (j.op === "drop") cartsDrop(j.id);
        else if (j.op === "keep") cartsKeep(j);
        else if (j.op === "record") cartsRecord(j);
        else if (j.op === "wput") writtenKeep(j);
        else if (j.op === "wdel") writtenDrop(j);
        else if (j.op === "pick") {
            cartsLive.set(j.id, { pick: true, path: j.path });
            self.postMessage({ t: "pick", id: j.id, name: j.name, size: j.size, host: j.host });
        } else if (j.op === "unpick") {
            cartsDrop(j.id);
            self.postMessage({ t: "unpick", id: j.id });
        }
    }
}

async function cartsGet(j) {
    const path = j.path;
    mkdirs(dirOf(path));
    mp.FS.writeFile(path, new Uint8Array(0));
    const live = { ac: new AbortController(), stream: null, path };
    cartsLive.set(j.id, live);
    const mine = () => cartsLive.get(j.id) === live;
    let r;
    try {
        // no-cache: revalidate, so CHECK reads the shelf as it is now. A
        // range (part of a release asset, cart_index's ranged read) is one
        // `bytes=a-b`, which a page may send any host without a preflight.
        const opt = { signal: live.ac.signal, cache: "no-cache" };
        if (j.range) opt.headers = { Range: "bytes=" + j.range[0] + "-" + j.range[1] };
        r = await fetch(j.url, opt);
    } catch (e) {
        // CORS and no network look the same from here, and both mean this
        // page cannot read that host: status 0.
        if (mine()) cartsSay({ id: j.id, status: 0, error: String((e && e.message) || e) });
        return;
    }
    if (!mine()) return;
    cartsSay({ id: j.id, status: r.status });
    if ((r.status !== 200 && r.status !== 206) || !r.body) {
        try { live.ac.abort(); } catch (e) { }
        return;
    }
    try {
        const reader = r.body.getReader();
        live.stream = mp.FS.open(path, "w");
        for (;;) {
            const { done, value } = await reader.read();
            if (!mine()) return;
            if (done) break;
            mp.FS.write(live.stream, value, 0, value.length);
        }
        mp.FS.close(live.stream);
        live.stream = null;
        cartsSay({ id: j.id, end: 1 });
    } catch (e) {
        if (mine()) cartsSay({ id: j.id, error: String((e && e.message) || e) });
    }
}

// The console let an answer (or a question to the player) go: stop it, and
// take its spool or its picked file with it.
function cartsDrop(id) {
    const live = cartsLive.get(id);
    cartsLive.delete(id);
    if (!live) return;
    if (live.ac) { try { live.ac.abort(); } catch (e) { } }
    if (live.stream) { try { mp.FS.close(live.stream); } catch (e) { } }
    if (live.segs !== undefined) {
        for (let k = 0; k <= live.segs; k++) {
            try { mp.FS.unlink(live.spool + "." + k); } catch (e) { }
        }
        return;
    }
    try { mp.FS.unlink(live.path); } catch (e) { }
}

// -- Music's bytes (media_link.py): a song or a URL, into spool segments ----
// A song is the browser's OPFS's (site) or the serving board's, read by its
// sealed GET /media a range at a time (board); a URL is the page's fetch.
// The bytes land in SEG-byte segments the console reads and removes, at
// "<spool>.<k>" (the job's spool), and the read waits while AHEAD of them
// are unread, so a live stream holds a few segments of the page's memory
// however long it plays.
const SEG = 32768, AHEAD = 4;
const EACCES = 13, EIO = 5, EHOSTUNREACH = 113, ENOENT = 2;

// The songs this page plays and does not keep, listed for media_link.
async function mediaList(fetchSongs) {
    let doc = null;
    try { doc = await fetchSongs(); } catch (e) { doc = null; }
    mkdirs(dirOf(VFS.listing));
    mp.FS.writeFile(VFS.listing, JSON.stringify(doc && typeof doc === "object" ? doc : {}));
}
function siteSongs() {
    const s = opfsStores.files;
    return s ? store.songs(s) : Promise.resolve({});
}

async function mediaGet(j) {
    mkdirs(dirOf(j.spool));
    const live = { ac: new AbortController(), segs: 0, stream: null, low: 0, n: 0,
                   spool: j.spool };
    cartsLive.set(j.id, live);
    const mine = () => cartsLive.get(j.id) === live;
    const fail = (errno, why) => {
        live.failed = true;
        if (mine()) cartsSay({ id: j.id, error: why, errno: errno });
    };
    // Appends across segment boundaries, saying each whole one.
    const put = (u8) => {
        let at = 0;
        while (at < u8.length) {
            if (!live.stream) live.stream = mp.FS.open(j.spool + "." + live.segs, "w");
            const k = Math.min(SEG - live.n, u8.length - at);
            mp.FS.write(live.stream, u8, at, k);
            at += k;
            live.n += k;
            if (live.n === SEG) {
                mp.FS.close(live.stream);
                live.stream = null;
                live.n = 0;
                live.segs++;
                cartsSay({ id: j.id, seg: live.segs });
            }
        }
    };
    const room = async () => {
        for (;;) {
            while (live.low < live.segs && !vfsExists(j.spool + "." + live.low)) live.low++;
            if (live.segs - live.low < AHEAD || !mine()) return;
            await new Promise((ok) => setTimeout(ok, 50));
        }
    };
    const end = () => {
        if (live.stream) { mp.FS.close(live.stream); live.stream = null; }
        if (live.n) { live.segs++; live.n = 0; }
        cartsSay({ id: j.id, end: 1, segs: live.segs });
    };
    try {
        if (j.url) await mediaUrl(j, live, mine, put, room, fail);
        else if (mode === "board") await mediaBoard(j, mine, put, room, fail);
        else await mediaSite(j, mine, put, room, fail);
        if (mine() && !live.failed) end();
    } catch (e) {
        if (!live.failed) fail(EIO, String((e && e.message) || e));
    }
}

async function mediaUrl(j, live, mine, put, room, fail) {
    // A page served over https may not fetch http (mixed content): it asks
    // for the same station over https, which a station without it refuses.
    // A station's request names no page: SomaFM's servers answer 403 to a
    // Referer naming a github.io or localhost page, and a board's request
    // names none either.
    // `send` are the request's headers and `head` the response's to read
    // back, null where the station does not expose one; without `probe` a
    // failed fetch is reported as it failed.
    let url = j.url;
    if (self.location.protocol === "https:" && /^http:/i.test(url)) url = "https:" + url.slice(5);
    const how = { cache: "no-store", referrerPolicy: "no-referrer" };
    let r;
    try {
        r = await fetch(url, Object.assign({ signal: live.ac.signal, headers: j.send || {} }, how));
    } catch (e) {
        if (!mine()) return;
        if (!j.probe) { fail(EIO, String((e && e.message) || e)); return; }
        // CORS and no network look the same from here; a no-cors fetch that
        // answers says the station is there and will not let a page read it.
        let there = false;
        const ac = new AbortController();
        const t = setTimeout(() => ac.abort(), 8000);
        try { await fetch(url, Object.assign({ mode: "no-cors", signal: ac.signal }, how)); there = true; }
        catch (e2) { /* not there */ }
        clearTimeout(t);
        ac.abort();
        fail(there ? EACCES : EHOSTUNREACH, there ? "the station does not let a page read it"
                                                  : String((e && e.message) || e));
        return;
    }
    if (!mine()) return;
    const len = r.headers.get("Content-Length");
    const head = {};
    for (const h of j.head || []) head[h] = r.headers.get(h);
    cartsSay({ id: j.id, status: r.status, size: len !== null && /^\d+$/.test(len) ? +len : null, head });
    if (r.status !== 200 || !r.body) {
        try { live.ac.abort(); } catch (e) { }
        fail(EIO, "HTTP " + r.status);
        return;
    }
    const reader = r.body.getReader();
    for (;;) {
        await room();
        if (!mine()) { try { live.ac.abort(); } catch (e) { } return; }
        const { done, value } = await reader.read();
        if (!mine()) return;
        if (done) break;
        put(value);
    }
}

async function mediaSite(j, mine, put, room, fail) {
    const f = opfsStores.files ? await store.songFile(opfsStores.files, j.song) : null;
    if (!f) { fail(ENOENT, "no such song"); return; }
    cartsSay({ id: j.id, status: 200, size: f.size });
    for (let at = j.at || 0; at < f.size; at += SEG) {
        await room();
        if (!mine()) return;
        put(new Uint8Array(await f.slice(at, at + SEG).arrayBuffer()));
    }
}

function hexUtf8(s) {
    return Array.from(utf8.encode(s), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function mediaBoard(j, mine, put, room, fail) {
    const f = hexUtf8(j.song);
    cartsSay({ id: j.id, status: 200, size: typeof j.size === "number" ? j.size : null });
    for (let at = j.at || 0; ; ) {
        await room();
        if (!mine()) return;
        const r = await sealed("GET", "/media?f=" + f + "&at=" + at + "&len=" + SEG, undefined, true);
        if (!mine()) return;
        if (r.status !== 200 || !r.bytes) {
            fail(r.status === 404 ? ENOENT : EIO, "the console answered " + r.status);
            return;
        }
        if (!r.bytes.length) return;
        put(r.bytes);
        at += r.bytes.length;
        if (r.bytes.length < SEG) return;
    }
}

// The staged folder the console checked, made durable with its record
// (moy_store.commitInstall). Twice the cart's bytes must fit what the browser
// will keep, because the commit writes a staging copy before the shelf's:
// short of that it says FULL now, with nothing changed.
async function cartsKeep(j) {
    const t0 = performance.now();
    const s = opfsStores.carts;
    const live = { keep: true };
    cartsLive.set(j.id, live);
    const answer = (ev) => { cartsLive.delete(j.id); cartsSay(Object.assign({ id: j.id }, ev)); };
    if (!s) { answer({ error: "this browser is not keeping carts" }); return; }
    let files = [], bytes = 0;
    try {
        for (const name of mp.FS.readdir(j.stage)) {
            if (name === "." || name === "..") continue;
            const data = mp.FS.readFile(j.stage + "/" + name);
            files.push({ name, data });
            bytes += data.length;
        }
    } catch (e) {
        answer({ error: "the build is not there: " + e });
        return;
    }
    try {
        const est = await navigator.storage.estimate();
        if (est && typeof est.quota === "number" && typeof est.usage === "number"
                && est.quota - est.usage < 2 * bytes) {
            answer({ error: "needs " + 2 * bytes + " bytes, the browser keeps "
                     + (est.quota - est.usage) + " more", full: 1 });
            return;
        }
    } catch (e) { /* no estimate: try, and the write says if it cannot */ }
    try {
        await opfsSerial(() => store.commitInstall(s, j.folder, files, j.record));
    } catch (e) {
        const full = !!(e && (e.name === "QuotaExceededError"));
        answer({ error: String((e && e.message) || e), full: full ? 1 : 0 });
        return;
    }
    files = null;
    const d = "installed " + j.folder + " in " + (performance.now() - t0).toFixed(0) + "ms";
    answer({ kept: 1 });
    self.postMessage({ t: "installed", folder: j.folder, d: d });
    if (mode === "site") sitePersist(d);
    cartsRoom();
}

function cartsRecord(j) {
    const s = opfsStores.carts;
    if (!s) return;
    opfsSerial(() => store.writeRecord(s, j.record))
        .then(() => cartsRoom())
        .catch((e) => console.log("[moy] carts: the record was not kept -- " + e));
}

function cartsRoom() {
    if (!navigator.storage || typeof navigator.storage.estimate !== "function") return;
    navigator.storage.estimate()
        .then((e) => cartsSay({ room: [e.usage, e.quota] }))
        .catch(() => { });
}

// The player's answer to {t:"pick"}: the file into the VFS where the console
// checks it against the index, or the question closed.
function cartsPicked(m) {
    const live = cartsLive.get(m.id);
    if (!live) return;
    if (m.cancel || !m.buf) { cartsSay({ id: m.id, cancel: 1 }); return; }
    const path = live.path;
    mkdirs(dirOf(path));
    mp.FS.writeFile(path, new Uint8Array(m.buf));
    cartsSay({ id: m.id, picked: path });
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
    sealed("POST", "/sync", body)
        .then((r) => {
            const ok = r.status >= 200 && r.status < 300;
            if (r.wait >= 0) {
                // The board answered, so this is no loss: the batch requeues
                // and the sweep holds off until the window ends, saying so on
                // the link panel until a push lands.
                syncOk(now);
                try { syncAck(0); } catch (e) { }
                lastSyncAt = performance.now() + r.wait - SYNC_MS;
                syncWaitSaid = true;
                self.postMessage({ t: "wait", s: signInWaitSays(r.wait) });
                return;
            }
            if (ok && syncWaitSaid) {
                syncWaitSaid = false;
                self.postMessage({ t: "wait", s: "" });
            }
            if (r.status === 404 || r.status === 405 || r.status === 501) {
                // A host with no push half (moybyte.com, an export, an older
                // read-only board): one probe, then off for good.
                try { syncAck(0); syncOff(); } catch (e) { }
                syncPoll = null;
                console.log("[moy] sync off: host has no /sync (" + r.status + ")");
                return;
            }
            if (r.status === 401 || r.status === 0) {
                // The board no longer takes this browser's key (it was
                // removed from its paired browsers), or its answer did not
                // open. Retrying the same batch forever is noise.
                try { syncAck(0); syncOff(); } catch (e) { }
                syncPoll = null;
                console.log("[moy] sync off: the board refused this browser");
                // SAY SO. This is the silent-loss case in its purest form: the
                // board is right there and answering, the page looks entirely
                // healthy, and nothing a kid types will ever reach the console
                // again. A devtools line is not a person being told.
                syncLostSaid = false;
                syncLost("the console refused this page",
                         "This browser is no longer paired with it. "
                         + "Scan the code on its WEB CONSOLE screen to pair again.");
                return;
            }
            if (ok) { syncOk(now); outstanding = false; }
            else if (r.status === 503) { closingSaid(r.text); }
            else syncFailed(now);
            try { syncAck(ok ? 1 : 0); } catch (e) { }
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
// capability marker, no sign-in and no store walk behind it.
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

// PLAY ON DEVICE (page_tail.js's link): POST /run, sealed, and the link's new
// text back as {t:"pod", s} -- "" puts its old text back.
async function podRun(cart) {
    let s = "";
    try {
        const r = await sealed("POST", "/run", JSON.stringify({ cart: cart }));
        if (r.wait >= 0) s = signInWaitSays(r.wait);
        else if (r.status === 200) s = "playing on device: " + ((sealedJson(r) || {}).run || cart);
        else if (r.status === 401) s = "device refused this browser";
        if (!s) console.log("[moy] play on device failed: " + r.status);
    } catch (e) {
        console.log("[moy] play on device failed: " + e);
    }
    self.postMessage({ t: "pod", s: s });
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
    r.text().then(closingSaid).catch(() => expected("the console is shutting down", ""));
}
function closingSaid(t) {
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
        const s = step(dt, played);
        const _d = performance.now() - _t0;
        // WORKER-SIDE PERF: the page's `recv` fps says the worker is behind but
        // not WHY. Track step cost + the worst offender's payload so the HUD
        // line names it (headless probes have repeatedly failed to reproduce
        // what the browser does -- measure where it actually runs).
        WP.n++; WP.sum += _d; if (_d > WP.max) { WP.max = _d; WP.maxB = s ? s.length : 0; }
        if (_d > 20) WP.slow++;
        if (s) { WP.painted++; WP.bytes += s.length; }
        if (s) {
            if (s.indexOf('"pcm"') >= 0) shipPCM(JSON.parse(s));
            shipFrame(s);
        }
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
        termPump();
        updatePump();
        cartsPump();
    } catch (e) {
        self.postMessage(crashMessage(e));
        running = false;
        return;
    }
    setTimeout(loop, Math.max(0, nextAt - performance.now()));
}

self.onmessage = async (ev) => {
    const m = ev.data;
    try {
        if (m.t === "init") {
            await init(m.search, !!m.coarse, m.channel || "", m.scheme, m.auth, +m.rate || 0);
        } else if (m.t === "password") {
            // The pairing prompt's password: a pairing reloads the page (its
            // {t:"key"}), anything else asks again with why.
            if (!(await pairPassword(m.pw, m.name))) askToPair();
        } else if (m.t === "input") {
            inbox.push(m.json);
        } else if (m.t === "sink") {
            sinkSet(m.port);
        } else if (m.t === "played") {
            sinkStats = m.s;
            played = m.s.played;
        } else if (m.t === "state") {
            self.postMessage({ t: "state", json: stateJson ? stateJson() : "null", sink: sinkStats });
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
            // The roots the VM pulls again (moy_sync.reload_json), the
            // carts first: without them there is nothing to reload.
            const roots = JSON.parse(mp.pyimport("moy_sync").reload_json())
                .map((id) => store.rootById(id));
            const got = await Promise.all(roots.map((r, i) => {
                const pull = sealed("GET", "/" + r.endpoint);
                return i ? pull.catch(() => ({ status: 0 })) : pull;
            }));
            if (got[0].wait >= 0) { say(signInWaitSays(got[0].wait)); return; }
            const docs = got.map((r) => r.status === 200 ? sealedJson(r) : null);
            if (!docs[0]) { say("the console did not send its carts"); return; }
            roots.forEach((r, i) => { if (docs[i]) writeStore(r.vfs, docs[i]); });
            reload();
            self.postMessage({ t: "assets", json: assets() });
        } else if (m.t === "pod") {
            podRun(m.cart);
        } else if (m.t === "kept") {
            keptAnswer(m.state);
        } else if (m.t === "carts") {
            self.postMessage({ t: "carts", names: cartNames() });
        } else if (m.t === "export") {
            // A failed export is a MESSAGE, never the fatal {t:"error"} the
            // outer catch would send -- the console is fine, the ask was not.
            try {
                const zip = exportZip(m.cart);
                self.postMessage({ t: "exported", name: m.cart + ".zip",
                                   buf: zip.buffer }, [zip.buffer]);
            } catch (e) {
                self.postMessage({ t: "imported", ok: false,
                    s: "export failed: " + ((e && e.message) || e) });
            }
        } else if (m.t === "import") {
            try {
                const kind = dropped(m.name, m.buf);
                if (kind === "song") {
                    const s = await importSong(m.name, m.buf);
                    self.postMessage({ t: "imported", ok: true, s: s });
                } else if (kind === "p8") {
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
                        s: "imported " + r.dir + " into your carts" });
                }
                if (assets) self.postMessage({ t: "assets", json: assets() });
            } catch (e) {
                self.postMessage({ t: "imported", ok: false,
                    s: "import failed: " + ((e && e.message) || e) });
            }
        } else if (m.t === "picked") {
            cartsPicked(m);
        } else if (m.t === "open") {
            if (openCart) openCart(m.cart);
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
        self.postMessage(crashMessage(e));
    }
};
