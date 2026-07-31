// Shared boot for the node-side probes (pageshot, resync_test, assets_hint_test,
// harness): load the wasm console from dist/ and stage the VFS the same way the
// browser does. Three probes share it (harness.mjs predates it and keeps its own);
// copies of this are how probes start disagreeing about what "the console" is.
//
//   const { mp, py } = await loadConsole({ carts: true });
//   mp.runPython("import web_boot\nweb_boot.boot('/moy/carts', None, 1024, 600, True)");
//
// Frozen-first, like the page: a ship dist has no modules.json (the console is
// baked into the wasm); a --stage-only dev dist adds one, loaded into /modules,
// which shadows the frozen copies. `boot` is the prelude that sets that path up
// -- prepend it to the first runPython, exactly as the page's loader does.
import { readFileSync } from "node:fs";
import { pathToFileURL, fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));

export async function loadConsole(opts = {}) {
    const dist = resolve(opts.dist || process.env.MOY_DIST || join(HERE, "dist"));
    const { loadMicroPython } = await import(pathToFileURL(join(dist, "micropython.mjs")).href);
    const quiet = opts.quiet !== undefined ? opts.quiet : process.env.MOY_QUIET === "1";
    const mp = await loadMicroPython({
        heapsize: (opts.heapMB || 32) * 1024 * 1024,
        stdout: opts.stdout || ((l) => { if (!quiet) console.log("  [moy]", l); }),
    });
    const mkdirs = (p) => {
        let c = "";
        for (const s of p.split("/")) { if (!s) continue; c += "/" + s; try { mp.FS.mkdir(c); } catch (e) { } }
    };

    let mods = null, boot = "";
    try { mods = JSON.parse(readFileSync(join(dist, "modules.json"), "utf-8")); } catch (e) { }
    if (mods) {
        mkdirs("/modules");
        for (const n in mods) mp.FS.writeFile("/modules/" + n, mods[n]);
        boot = "import sys\nsys.path.insert(0,'/modules')\n";
    }
    const carts = JSON.parse(readFileSync(join(dist, "carts.json"), "utf-8"));
    for (const rel in carts) {
        const f = "/moy/carts/" + rel;
        mkdirs(f.slice(0, f.lastIndexOf("/")));
        mp.FS.writeFile(f, carts[rel]);
    }

    // py(expr): evaluate a Python expression that returns JSON TEXT and hand back
    // the parsed value -- the crossing every probe was hand-rolling.
    const py = (expr) => { mp.runPython(`_v = ${expr}`); return JSON.parse(mp.globals.get("_v")); };
    return { mp, py, dist, boot, mkdirs, mods: !!mods, carts };
}
