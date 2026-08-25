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
//   {t:"reload"}         dev hot-reload: re-read carts.json and restart the cart
// worker -> main:
//   {t:"status", s}      boot progress text
//   {t:"assets", json}   the page's metadata payload (size/title/audio/input)
//   {t:"frame", s, fb}   frame metadata + the RGB565 framebuffer, TRANSFERRED
//   {t:"error", s}       fatal: the page shows it and stops
import { loadMicroPython } from "./micropython.mjs";

let mp = null, step = null, applyEvents = null, assets = null, reload = null;
let wantAssets = false;   // an assets request that arrived before the VM was up
let idleCollect = null;
let fbAddr = null, fbLen = null;
// The 3.4 sync push: about once a second, ask the console (moy_sync's
// watcher) for a batch of committed changes and POST it to the RELATIVE
// /sync -- a page served from a board writes back to that board; one served
// by a static host gets a 404/405/501 on the first try and push turns off
// for good. ONE batch in flight at a time, so ops arrive in order and a
// failed send simply requeues on the Python side (syncAck false).
let syncPoll = null, syncAck = null, syncOff = null;
let syncBusy = false, lastSyncAt = 0;
const SYNC_MS = 1000;
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

function writeCarts(carts) {
    mkdirs("/moy/carts");
    for (const rel in carts) {
        const full = "/moy/carts/" + rel;
        mkdirs(full.slice(0, full.lastIndexOf("/")));
        mp.FS.writeFile(full, carts[rel]);
    }
}

async function init(search) {
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
    const [mods, carts] = await Promise.all([
        fetch("modules.json").then((r) => r.ok ? r.json() : null).catch(() => null),
        fetch("carts.json").then((r) => r.json())]);
    let boot = "";
    if (mods) {
        mkdirs("/modules");
        for (const n in mods) mp.FS.writeFile("/modules/" + n, mods[n]);
        boot = "import sys\nsys.path.insert(0, '/modules')\n";
    }
    writeCarts(carts);
    say("booting console...");

    const qs = new URLSearchParams(search || "");
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
    const pin = qs.get("pin");
    // #9: does whoever served this page have PINS? ONE probe, here, before the
    // console exists -- so `pin_write`/`pin_read` are decided once and a cart
    // either has the names or has never heard of them. An empty batch is the
    // probe (the board answers it with its allowlist), which also means the
    // probe passes through the PIN gate: a page opened without the board's
    // ?pin= is refused now rather than on every write it later makes.
    const gpioPins = await probeGpio(pin);
    mp.runPython(boot + "import web_boot\n"
        + (gpioPins ? "web_boot.gpio_enable(" + JSON.stringify(JSON.stringify(gpioPins)) + ")\n" : "")
        + "web_boot.boot('/moy/carts'" + bootArgs + ")\n"
        + (desktop && cart ? "web_boot.open_cart(" + JSON.stringify(cart) + ")\n" : "")
        // Single-cart bundle: kiosk mode -- the exit gesture restarts the game
        // instead of dropping into the shell (the game IS the page).
        + (!desktop && names.length === 1 && cart
            ? "web_boot.kiosk(" + JSON.stringify(cart) + ")\n" : "")
        + "from web_boot import assets_json, step_frame_json, apply_events_json, "
        + "reload_cart, idle_collect, fb_addr, fb_len, "
        + "sync_poll_json, sync_ack, sync_off, sync_config, "
        + "gpio_poll_json, gpio_ack_json, gpio_off");
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
    if (gpioPins) {
        gpioPoll = mp.globals.get("gpio_poll_json");
        gpioAck = mp.globals.get("gpio_ack_json");
        gpioOff = mp.globals.get("gpio_off");
        console.log("[moy] gpio: " + gpioPins.length + " pins on the host");
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

function syncPump() {
    if (syncBusy || !syncPoll) return;
    const now = performance.now();
    if (now - lastSyncAt < SYNC_MS) return;
    lastSyncAt = now;
    let body = "";
    try { body = syncPoll(); } catch (e) { return; }
    if (!body) return;
    syncBusy = true;
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
                return;
            }
            try { syncAck(r.ok ? 1 : 0); } catch (e) { }
        })
        .catch(() => { try { syncAck(0); } catch (e) { } })
        .finally(() => { syncBusy = false; });
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
            const carts = await fetch("carts.json").then((r) => r.json());
            writeCarts(carts);
            reload();
            self.postMessage({ t: "assets", json: assets() });
        }
    } catch (e) {
        self.postMessage({ t: "error", s: String((e && e.stack) || e) });
    }
};
