// The page's end of the sign-in (worker.js, THE SIGN-IN) against a scripted
// board: when it asks for another nonce, which refusal makes it forget its
// key or spend its token, and that a page with a key or a session never
// speaks in the clear. The section runs as worker.js has it, cut out of the
// source; the wasm's C under it is a stand-in that builds requests a script
// can read (the C itself is tests/test_web_signin.py's). Run from
// firmware/web_runner: node signin_test.mjs
import { readFileSync } from "node:fs";

const SRC = readFileSync(new URL("./worker.js", import.meta.url), "utf-8");
const FROM = SRC.indexOf("// THE SIGN-IN (native/moy_net/moy_seal.h");
const TO = SRC.indexOf("// A fatal error as {t:\"error\"}");
if (FROM < 0 || TO < FROM) { console.log("FAIL: worker.js has no sign-in section"); process.exit(1); }
const SECTION = SRC.slice(FROM, TO);

// The wasm's C, as the section reaches it through mp._module.
function fakeModule() {
    const M = { HEAPU8: new Uint8Array(1 << 16), top: 1 << 15 };
    const BUF = 16;
    const enc = new TextEncoder(), dec = new TextDecoder();
    const st = { board: "", nb: "", kid: "", key: "", strs: {} };
    const put = (s) => { const b = enc.encode(s); M.HEAPU8.set(b, BUF); return b.length; };
    const read = (n) => dec.decode(M.HEAPU8.slice(BUF, BUF + n));
    const cstr = (p) => { let e = p; while (M.HEAPU8[e]) e++; return dec.decode(M.HEAPU8.slice(p, e)); };
    const keep = (name, s) => {
        const p = (1 << 14) + Object.keys(st.strs).indexOf(name) * 256;
        const b = enc.encode(s);
        M.HEAPU8.set(b, p);
        M.HEAPU8[p + b.length] = 0;
        return p;
    };
    for (const n of ["board", "kid", "key", "target"]) st.strs[n] = 1;
    Object.assign(M, {
        _malloc: (n) => { const p = M.top; M.top += n + 8; return p; },
        _free: () => {},
        lengthBytesUTF8: (s) => enc.encode(s).length,
        stringToUTF8: (s, p) => { const b = enc.encode(s); M.HEAPU8.set(b, p); M.HEAPU8[p + b.length] = 0; },
        UTF8ToString: (p) => cstr(p),
        _moy_sw_buf: () => BUF,
        _moy_sw_hello: (n) => { const d = JSON.parse(read(n)); st.board = d.id; st.nb = d.nb; return 0; },
        _moy_sw_board: () => keep("board", st.board),
        _moy_sw_key: (kid, key) => put(JSON.stringify({ op: "key", nb: st.nb, kid: cstr(kid), mac: cstr(key) })),
        _moy_sw_token: (tok, name) => put(JSON.stringify({ op: "token", nb: st.nb, tok: cstr(tok) })),
        _moy_sw_pake: (pw) => put(JSON.stringify({ op: "pake", nb: st.nb, pw: cstr(pw) })),
        _moy_sw_confirm: () => put(JSON.stringify({ op: "confirm", nb: st.nb })),
        _moy_sw_done: (n) => {
            const d = JSON.parse(read(n));
            if (!d.sid) return -1;
            if (d.kid) { st.kid = d.kid; st.key = d.key; }
            return 0;
        },
        _moy_sw_kid: () => keep("kid", st.kid),
        _moy_sw_device_key: () => keep("key", st.key),
        _moy_sw_seal: (m, p) => { st.target = cstr(p) + "?s=1&n=1"; return 0; },
        _moy_sw_target: () => keep("target", st.target),
        _moy_sw_seq: () => 1,
        _moy_sw_open: (seq, status, n) => n,
    });
    return M;
}

// A board: `auth(op, doc, i)` answers POST /auth (i counts that op's
// requests), `route(path)` the rest; every request is logged.
function board(auth, route) {
    const log = [], count = {};
    const fetch = async (url, opts = {}) => {
        const path = String(url).split("?")[0];
        log.push({ path, method: opts.method || "GET" });
        let ans;
        if (path === "auth") {
            const d = JSON.parse(opts.body);
            count[d.op] = (count[d.op] || 0) + 1;
            log[log.length - 1].op = d.op;
            ans = auth(d.op, d, count[d.op]);
        } else {
            ans = route ? route(path) : { status: 404, body: "" };
        }
        const body = typeof ans.body === "string" ? ans.body : JSON.stringify(ans.body || {});
        return {
            status: ans.status,
            headers: { get: () => ans.type || "application/json" },
            text: async () => body,
            arrayBuffer: async () => new TextEncoder().encode(body).buffer,
        };
    };
    return { fetch, log, count };
}

const HELLO = { status: 200, body: { v: 1, id: "b0a4d", nb: "aa", pw: false } };
const SIGNED = { status: 200, body: { sid: "51d", proof: "00" } };

function page(b, signin) {
    const posted = [];
    const self = { postMessage: (m) => posted.push(m) };
    const mp = { _module: fakeModule() };
    const store = { probeMode: async (f) => ((await f("sync")).status === 200 ? "board" : "site") };
    const make = new Function("mp", "fetch", "self", "store", SECTION + `
        return { signIn, sealed, pairPassword,
                 state: () => ({ auth, authOpen, signedIn, authNeed }),
                 set: (a) => { auth = Object.assign(auth, JSON.parse(JSON.stringify(a))); } };`);
    const w = make(mp, b.fetch, self, store);
    if (signin) w.set(signin);
    w.posted = posted;
    return w;
}

let fail = 0;
function ok(name, cond, extra = "") {
    if (cond) console.log("  ok   " + name);
    else { fail++; console.log("  FAIL " + name + (extra ? "  " + extra : "")); }
}
const KEY = { keys: { b0a4d: { kid: "k1d", key: "5ec" } } };

{
    const b = board((op, d, i) => op === "hello" ? HELLO
        : i === 1 ? { status: 409, body: { error: "nonce" } } : SIGNED);
    const w = page(b, KEY);
    ok("a nonce a flood took is asked for again, and the key signs in", await w.signIn(),
       JSON.stringify(b.log));
    ok("...and the key is kept", w.state().auth.keys.b0a4d && !w.posted.some((m) => m.t === "unkey"));
}
{
    const b = board((op) => op === "hello" ? HELLO : { status: 403, body: { error: "key" } });
    const w = page(b, KEY);
    ok("a wrong key does not sign in", !(await w.signIn()));
    ok("...and is kept: only a key the board does not know is forgotten",
       w.state().auth.keys.b0a4d && !w.posted.some((m) => m.t === "unkey"));
}
{
    const b = board((op) => op === "hello" ? HELLO : { status: 403, body: { error: "kid" } });
    const w = page(b, KEY);
    ok("a key the board does not know does not sign in", !(await w.signIn()));
    ok("...and is forgotten", !w.state().auth.keys.b0a4d && w.posted.some((m) => m.t === "unkey"));
}
{
    const b = board((op) => op === "hello" ? HELLO : { status: 409, body: { error: "nonce" } });
    const w = page(b, KEY);
    ok("a board that keeps losing the nonce is not signed in to", !(await w.signIn()));
    ok("...its hellos are few", b.count.hello <= 3, "hellos " + b.count.hello);
    ok("...and the key is kept", w.state().auth.keys.b0a4d && !w.posted.some((m) => m.t === "unkey"));
}
{
    const b = board((op, d, i) => op === "hello" ? HELLO
        : i === 1 ? { status: 409, body: { error: "nonce" } }
        : Object.assign({}, SIGNED, { body: { sid: "51d", proof: "00", kid: "n3w", key: "f00" } }));
    const w = page(b, { token: "q1ZQ" });
    ok("a QR token whose nonce a flood took pairs on the next hello", await w.signIn());
    ok("...and the key it minted is stored", w.posted.some((m) => m.t === "key" && m.kid === "n3w"));
}
{
    const b = board((op) => op === "hello" ? HELLO : { status: 409, body: { error: "nonce" } });
    const w = page(b, { token: "q1ZQ" });
    ok("a token the board never judged does not pair", !(await w.signIn()));
    ok("...and is kept for the next try", w.state().auth.token === "q1ZQ");
}
{
    const b = board((op) => op === "hello" ? HELLO : { status: 403, body: { error: "token" } });
    const w = page(b, { token: "q1ZQ" });
    ok("a refused token does not pair", !(await w.signIn()));
    ok("...and is spent", w.state().auth.token === null);
}
{
    const b = board(() => ({ status: 200, body: { v: 1, open: true } }),
                    () => ({ status: 200, body: { "hop.moy/main.py": "evil" } }));
    const w = page(b, KEY);
    const r = await w.sealed("GET", "/carts.json");
    ok("a page with a key takes no host's word that it is open", !w.state().authOpen && r.status === 401,
       JSON.stringify(r));
    ok("...and asks nothing in the clear", !b.log.some((e) => e.path === "carts.json"));
}
for (const st of [404, 405, 501]) {
    const evil = { status: 200, body: { "hop.moy/main.py": "evil" } };
    const onBoard = board(() => ({ status: st, body: "" }),
                          (p) => p === "sync" ? { status: 200, body: { sync: 1 } } : evil);
    let w = page(onBoard, null);
    let r = await w.sealed("GET", "/carts.json");
    ok("a board's missing /auth (" + st + ") is an error, never a board without a sign-in",
       !w.state().authOpen && r.status === 401 && !onBoard.log.some((e) => e.path === "carts.json"));
    const site = board(() => ({ status: st, body: "" }),
                       (p) => p === "sync" ? { status: 404, body: "" } : evil);
    w = page(site, KEY);
    r = await w.sealed("GET", "/carts.json");
    ok("...and to a page with a key, a static host's (" + st + ") too",
       !w.state().authOpen && r.status === 401 && !site.log.some((e) => e.path === "carts.json"));
    const seed = board(() => ({ status: st, body: "" }),
                       (p) => p === "sync" ? { status: 404, body: "" }
                                           : { status: 200, body: { "hop.moy/main.py": "x = 1\n" } });
    w = page(seed, null);
    r = await w.sealed("GET", "/carts.json");
    ok("a keyless page reads a static host (no /auth, no board marker) in the clear",
       w.state().authOpen && r.status === 200);
}
{
    const b = board(() => ({ status: 200, body: { v: 1, open: true } }),
                    () => ({ status: 200, body: { "hop.moy/main.py": "x = 1\n" } }));
    const w = page(b, null);
    const r = await w.sealed("GET", "/carts.json");
    ok("a page with no key speaks in the clear to a host that says it is open",
       w.state().authOpen && r.status === 200 && JSON.parse(r.text)["hop.moy/main.py"] === "x = 1\n");
}
{
    let opened = false, calls = 0;
    const b = board((op) => opened ? { status: 200, body: { v: 1, open: true } }
                          : op === "hello" ? HELLO : SIGNED,
                    () => { calls++; return calls === 1 ? { status: 200, type: "application/octet-stream", body: "{}" }
                                      : calls === 2 ? { status: 200, body: { "hop.moy/main.py": "evil" } }
                                      : { status: 401, body: { error: "session" } }; });
    const w = page(b, KEY);
    ok("a sealed answer opens", (await w.sealed("GET", "/carts.json")).status === 200);
    const r = await w.sealed("GET", "/carts.json");
    ok("a 2xx in the clear to a sealed call is never the board's answer",
       r.status === 0 && r.text === "", JSON.stringify(r));
    opened = true;
    const again = await w.sealed("GET", "/carts.json");
    ok("a page that signed in never goes plain when its next hello says open",
       !w.state().authOpen && again.status === 401, JSON.stringify(again));
}
{
    const b = board((op) => op === "hello" ? Object.assign({}, HELLO, { body: Object.assign({}, HELLO.body, { pw: true }) })
        : op === "pake" ? { status: 409, body: { error: "nonce" } } : SIGNED);
    const w = page(b, null);
    ok("a password pairing whose nonce keeps going says so", !(await w.pairPassword("correct horse", "x")));
    ok("...after a few hellos", b.count.hello === 3, "hellos " + b.count.hello);
}

if (fail) { console.log(fail + " failed"); process.exit(1); }
console.log("all ok");
