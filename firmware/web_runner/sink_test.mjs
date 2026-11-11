// The page's AudioWorklet sink (page_core.html, THE SINK), run under node:
// its processor's source is read out of the page and driven block by block.
// A ring that runs dry plays silence after a short fade and never what it
// held before; the next chunk of a stream that went on counts the underrun
// and its dry samples, a fresh one does not; a chunk's `at` that skips or
// repeats is counted; a full ring refuses and counts; and a ring that wraps
// many times plays every sample once, in order.
//     node sink_test.mjs
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const page = readFileSync(join(HERE, "page_core.html"), "utf-8");
const m = page.match(/var AW_SRC=((?:"(?:[^"\\]|\\.)*"\+?\s*)+);/);
if (!m) throw new Error("no AW_SRC in page_core.html");
const src = new Function("return " + m[1])();

let Proc = null;
class AudioWorkletProcessor { constructor() { this.port = { onmessage: null, postMessage() {} }; } }
new Function("AudioWorkletProcessor", "registerProcessor", src)(
    AudioWorkletProcessor, (name, cls) => { Proc = cls; });

let failed = 0;
function check(ok, what) {
    if (!ok) { failed++; console.log("FAIL " + what); } else console.log("ok   " + what);
}

function sink() {
    const p = new Proc();
    const reports = [];
    const port = { onmessage: null, postMessage: (s) => reports.push(Object.assign({}, s)) };
    p.port.onmessage({ data: { port } });
    const send = (at, vals, fresh) => port.onmessage({ data: { p: Int16Array.from(vals), at, fresh: fresh ? 1 : 0 } });
    const block = (n = 128) => { const o = new Float32Array(n); p.process([], [[o]]); return o; };
    return { p, send, block, reports };
}

// contiguous: played one for one
{
    const s = sink();
    const a = Array.from({ length: 256 }, (_, i) => i + 1);
    s.send(0, a, true);
    const o = [...s.block(), ...s.block()];
    check(o.every((v, i) => v === (i + 1) / 32768), "samples play one for one");
    check(s.p.s.played === 256 && s.p.s.got === 256 && s.p.s.under === 0, "counted: got 256, played 256");
}

// starved: silence after a fade, then the underrun counted at the next chunk
{
    const s = sink();
    s.send(0, Array(100).fill(16384), true);
    const o = s.block(1024);
    const dry = Array.from(o.slice(100));
    check(dry.every((v, i) => i === 0 || Math.abs(v) <= Math.abs(dry[i - 1])), "the dry run only fades");
    check(dry.slice(128).every((v) => v === 0), "and is silence after the fade");
    s.send(100, Array(64).fill(1000), false);
    check(s.p.s.under === 1 && s.p.s.dry === 924, "an underrun and its dry samples: " + JSON.stringify(s.p.s));
    const o2 = s.block(128);
    check(o2.slice(0, 64).every((v) => v === 1000 / 32768) && o2.slice(64 + 128).every((v) => v === 0),
          "what plays next is the new chunk, then silence again");
}

// a fresh chunk after a silence is a stream starting
{
    const s = sink();
    s.send(0, Array(10).fill(1), true);
    s.block(512);
    s.send(10, Array(10).fill(1), true);
    check(s.p.s.under === 0 && s.p.s.dry === 0, "a fresh chunk counts no underrun");
}

// gaps and repeats in `at`
{
    const s = sink();
    s.send(0, Array(10).fill(1), true);
    s.send(15, Array(10).fill(1), false);
    s.send(20, Array(10).fill(1), false);
    check(s.p.s.gap === 5 && s.p.s.dup === 5, "a skipped and a repeated index counted: " + JSON.stringify(s.p.s));
}

// a full ring refuses what does not fit
{
    const s = sink();
    const cap = s.p.b.length;
    s.send(0, new Array(cap - 10).fill(1), true);
    s.send(cap - 10, new Array(30).fill(1), false);
    check(s.p.s.over === 20 && s.p.n === cap, "a full ring refuses and counts: " + s.p.s.over);
}

// wrapping: every sample once, in order, across many laps
{
    const s = sink();
    const cap = s.p.b.length;
    let at = 0, next = 0, bad = 0;
    for (let k = 0; k < 6 * cap / 512; k++) {
        s.send(at, Array.from({ length: 512 }, (_, i) => (at + i) % 30000), false);
        at += 512;
        for (let b = 0; b < 4; b++) {
            for (const v of s.block()) {
                if (Math.round(v * 32768) !== next % 30000) bad++;
                next++;
            }
        }
    }
    check(bad === 0 && s.p.s.under === 0 && s.p.s.played === at, "six laps of the ring play in order");
    check(s.reports.length > 0 && s.reports[s.reports.length - 1].played > 0, "the sink reports its counts");
}

process.exit(failed ? 1 : 0);
