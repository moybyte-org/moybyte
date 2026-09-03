// Moybyte web runner BROWSER harness: the page in a REAL browser, driven over
// the Chrome DevTools Protocol (no puppeteer -- node 22 has a WebSocket client).
//
// pageshot.mjs runs the console + the page replayer in node, which covers the
// wasm side and the drawing side but NOT the browser plumbing: the worker
// postMessage pump, the async /assets fetch, rAF, canvas sizing. Owner-visible
// bugs have hidden in exactly that gap ("it doesn't appear until I drag"), so
// this drives the shipped page itself and screenshots the canvas.
//
//   node browsershot.mjs scenario.json [outdir]
//
// Same scenario vocabulary as pageshot.mjs where it makes sense --
//   {"wait":ms} {"shot":"name"} {"click":[x,y]} {"move":[x,y]}
//   {"drag":[x0,y0,x1,y1,steps]} {"key":"a"} {"js":"..."} {"note":"..."}
//   {"file":"path|$ENVVAR","as":"__name"}   local bytes -> window.__name
//                                           (an ArrayBuffer, for the drop paths)
// -- with coordinates in CANVAS pixels (the page's own 1024x600-style space);
// they are mapped through the canvas's on-screen rect for you.
//
// It serves dist/ itself and launches headless Chrome, so it is one command.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { createServer } from "node:http";

const HERE = dirname(fileURLToPath(import.meta.url));
const DIST = resolve(process.env.MOY_DIST || join(HERE, "dist"));
const CHROME = process.env.MOY_CHROME || "google-chrome";

// -- static server (mjs/wasm mime types, like serve.py) ----------------------
const MIME = { ".html": "text/html", ".mjs": "text/javascript", ".js": "text/javascript",
               ".wasm": "application/wasm", ".json": "application/json" };
const server = createServer((req, res) => {
    let p = decodeURIComponent(req.url.split("?")[0]);
    if (p === "/") p = "/index.html";
    try {
        const body = readFileSync(join(DIST, p));
        const ext = p.slice(p.lastIndexOf("."));
        res.writeHead(200, { "content-type": MIME[ext] || "application/octet-stream",
                             "cross-origin-opener-policy": "same-origin",
                             "cross-origin-embedder-policy": "require-corp" });
        res.end(body);
    } catch (e) { res.writeHead(404); res.end("nope"); }
});
// MOY_BASE points this at a page served by SOMETHING ELSE -- in practice a
// BOARD running the WEB CONSOLE Settings row (moycore plan 3.4). Serving dist/
// locally proves the build; only fetching it from the console proves the
// feature, because the board is where the carts, the mime types and the
// generated carts.json actually come from.
//   MOY_BASE=http://192.168.1.151:8080 node browsershot.mjs scenario.json
const BASE = process.env.MOY_BASE || "";
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const PORT = server.address().port;
const ORIGIN = BASE || `http://127.0.0.1:${PORT}`;

// -- headless chrome + CDP ---------------------------------------------------
// MOY_PROFILE pins the Chrome profile across invocations. The default name is
// per-port and therefore per-RUN, which is right for a screenshot but wrong for
// anything that must survive a reload: the browser-local cart store (#193) lives
// in the profile, so proving persistence needs two runs to share one.
const profile = process.env.MOY_PROFILE
    || join(process.env.TMPDIR || "/tmp", "moy-browsershot-" + PORT);
// MOY_CHROME_FLAGS appends launch flags, for environments the defaults do not
// fit. CI sets `--no-sandbox`: Ubuntu 24.04 restricts unprivileged user
// namespaces under AppArmor, and where that bites, Chrome's zygote dies before
// it ever prints a debug port -- which reads here as "chrome did not report a
// debug port" and would be a red run about the runner, not about the console.
// The page under test is served from 127.0.0.1 out of our own dist/, so the
// sandbox is not what is keeping anything out.
const EXTRA = (process.env.MOY_CHROME_FLAGS || "").split(/\s+/).filter(Boolean);
const chrome = spawn(CHROME, [
    "--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + profile,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu",
    "--window-size=1280,800", "--hide-scrollbars", ...EXTRA, "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

const wsUrl = await new Promise((ok, err) => {
    let buf = "";
    const t = setTimeout(() => err(new Error("chrome did not report a debug port")), 20000);
    chrome.stderr.on("data", (d) => {
        buf += d;
        const m = buf.match(/ws:\/\/[^\s]+/);
        if (m) { clearTimeout(t); ok(m[0]); }
    });
});

let msgId = 0;
const pending = new Map();
const ws = new WebSocket(wsUrl);
await new Promise((ok) => ws.addEventListener("open", ok));
ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
function send(method, params = {}, sessionId = undefined) {
    const id = ++msgId;
    return new Promise((ok, err) => {
        pending.set(id, (m) => m.error ? err(new Error(method + ": " + JSON.stringify(m.error))) : ok(m.result));
        ws.send(JSON.stringify({ id, method, params, sessionId }));
    });
}

const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
const cdp = (method, params) => send(method, params, sessionId);
await cdp("Page.enable");
await cdp("Runtime.enable");
const logs = [];
ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === "Runtime.consoleAPICalled")
        logs.push(m.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
    if (m.method === "Runtime.exceptionThrown")
        logs.push("EXCEPTION " + (m.params.exceptionDetails?.exception?.description
            || m.params.exceptionDetails?.text));
});

const argv = process.argv.slice(2);
const scenario = JSON.parse(readFileSync(argv[0], "utf-8"));
const outdir = resolve(argv[1] || join(HERE, "shots-browser"));
mkdirSync(outdir, { recursive: true });

const evaluate = async (expr) => {
    const r = await cdp("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " " + (r.exceptionDetails.exception?.description || ""));
    return r.result.value;
};
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

// PHONE/TABLET shape: {"device":{"width":390,"height":844,"dpr":3,"mobile":true}}.
// The small tiers are where the on-screen controls live, and their layout (plus
// the browser's own dynamic toolbar) is load-bearing -- a desktop-sized window
// never exercises it.
if (scenario.device) {
    const d = scenario.device;
    await cdp("Emulation.setDeviceMetricsOverride", {
        width: d.width, height: d.height, deviceScaleFactor: d.dpr || 2,
        mobile: d.mobile !== false,
    });
    await cdp("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    await cdp("Emulation.setEmitTouchEventsForMouse", { enabled: true, configuration: "mobile" });
}
await cdp("Page.navigate", { url: `${ORIGIN}/${scenario.query || ""}` });
await sleep(scenario.boot_ms || 6000);

// Canvas rect, so scenario coordinates are CANVAS pixels like every other probe.
async function canvasRect() {
    return await evaluate(`(function(){var c=document.getElementById("cv");if(!c)return null;
        var r=c.getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height,cw:c.width,ch:c.height};})()`);
}
async function toPage(x, y) {
    const r = await canvasRect();
    if (!r) throw new Error("no canvas element");
    return { x: r.x + (x / r.cw) * r.w, y: r.y + (y / r.ch) * r.h };
}
async function mouse(type, x, y, button = "left", clickCount = 1) {
    const p = await toPage(x, y);
    await cdp("Input.dispatchMouseEvent", { type, x: p.x, y: p.y, button, clickCount, buttons: type === "mouseMoved" ? 0 : 1 });
}
async function shot(name) {
    // The CANVAS pixels, not the page: element.toDataURL is exactly what the
    // console painted, with no browser chrome or CSS scaling in the way.
    const url = await evaluate(`document.getElementById("cv").toDataURL("image/png")`);
    const b64 = url.split(",")[1];
    const path = join(outdir, name + ".png");
    writeFileSync(path, Buffer.from(b64, "base64"));
    const st = await evaluate(`JSON.stringify({fps:(typeof HUD!=="undefined"&&HUD.fps)||0,
        unknown:(typeof HUD!=="undefined"&&HUD.unknown)||0,
        surf:(typeof SURF!=="undefined")?Object.keys(SURF).length:-1,
        w:document.getElementById("cv").width,h:document.getElementById("cv").height})`);
    console.log(`shot ${name}: -> ${path}  ${st}`);
}

let failed = 0;
for (const step of scenario.steps || []) {
    try {
        if (step.note != null) console.log("--", step.note);
        if (step.wait != null) await sleep(step.wait);
        if (step.move) { await mouse("mouseMoved", step.move[0], step.move[1]); await sleep(60); }
        if (step.click) {
            await mouse("mousePressed", step.click[0], step.click[1]);
            await sleep(80);
            await mouse("mouseReleased", step.click[0], step.click[1]);
            await sleep(step.settle ?? 400);
        }
        if (step.drag) {
            const [x0, y0, x1, y1, n = 8] = step.drag;
            await mouse("mousePressed", x0, y0);
            for (let i = 1; i <= n; i++) {
                await cdp("Input.dispatchMouseEvent", {
                    type: "mouseMoved", buttons: 1,
                    ...(await toPage(Math.round(x0 + (x1 - x0) * i / n), Math.round(y0 + (y1 - y0) * i / n))),
                });
                await sleep(30);
            }
            await mouse("mouseReleased", x1, y1);
            await sleep(step.settle ?? 300);
        }
        if (step.key != null) {
            // CDP takes `text` only for printable chars; a NAMED key (Enter,
            // ArrowDown, ...) must go as rawKeyDown with its virtual keycode
            // or the call is refused with "Invalid 'text' parameter".
            const k = String(step.key);
            const VK = { Enter: 13, Backspace: 8, Escape: 27, Tab: 9,
                         ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40 };
            if (k.length === 1) {
                await cdp("Input.dispatchKeyEvent", { type: "keyDown", text: k, key: k });
                await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k });
            } else {
                const vk = VK[k] || 0;
                await cdp("Input.dispatchKeyEvent", { type: "rawKeyDown", key: k, code: k, windowsVirtualKeyCode: vk });
                await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k, code: k, windowsVirtualKeyCode: vk });
            }
            await sleep(150);
        }
        // {"file": path, "as": "__name"} -- a LOCAL file's bytes as an
        // ArrayBuffer on window. The drop paths (#193's .moy zips, #194's
        // PICO-8 carts) take real bytes and nothing else, and a base64 blob
        // pasted into a scenario would be a fixture nobody could regenerate.
        // `$NAME` resolves from the environment, so a test can point one
        // scenario at a file it just built in a tmpdir.
        if (step.file) {
            const p = step.file.startsWith("$")
                ? (process.env[step.file.slice(1)] || "")
                : resolve(HERE, step.file);
            if (!p) throw new Error("no path for " + step.file);
            const b64 = readFileSync(p).toString("base64");
            const as = step.as || "__file";
            // ...and its NAME beside it, because a drop carries one and half of
            // what the p8 import does with a file is decided by its suffix.
            await evaluate(`window[${JSON.stringify(as)}] =`
                + ` Uint8Array.from(atob(${JSON.stringify(b64)}),`
                + ` c => c.charCodeAt(0)).buffer,`
                + ` window[${JSON.stringify(as + "Name")}] =`
                + ` ${JSON.stringify(p.split("/").pop())},`
                + ` "loaded ${b64.length}b64"`);
            console.log("   file ->", p);
        }
        if (step.js) console.log("   js ->", JSON.stringify(await evaluate(step.js)));
        if (step.shot) await shot(step.shot);
    } catch (e) {
        failed++;
        console.log("STEP FAILED", JSON.stringify(step), "\n  ", e && e.message ? e.message : e);
    }
}
if (logs.length) { console.log("-- page console --"); for (const l of logs.slice(-25)) console.log("  " + l); }
chrome.kill();
server.close();
process.exit(failed ? 1 : 0);
