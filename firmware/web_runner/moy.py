#!/usr/bin/env python3
"""moy -- the spec-cart developer CLI (#151): scaffold, live-run, export.

The authoring story for moy-spec devs is "your editor + this loop", not a
bundled IDE: a cart is a folder of text files, so VS Code/git/Aseprite work
natively; this CLI supplies the missing glue.

    moy.py new <name>            scaffold a Lua cart (manifest + main.lua +
                                 moy-api.lua editor stubs)
    moy.py run <cart.moy>        serve the wasm runner + THAT cart with hot
                                 reload: save a file -> the game restarts in
                                 the browser (<1s), state-of-the-art loop
    moy.py export <cart.moy>     build the self-contained static web bundle
                                 (the --spec export; host it anywhere/itch.io)

Lives beside the web runner it wraps (the frozen dist/ is the simulator);
destined for the moy-spec repo once the runner bundle ships as a versioned
release artifact. Pure stdlib.
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
DEFAULT_PORT = 8323

MANIFEST = {
    "format": "moybyte-cart-v1",
    "version": 1,
    "title": None,                    # filled from the name
    "type": "game",
    "runtime": "lua",
    "main": "main.lua",
    "canvas": {"width": 320, "height": 240, "palette": "moy64"},
    "permissions": ["graphics", "input"],
    "input": ["buttons"],
    "config": {},
}

MAIN_LUA = """\
-- {title}: a moy cart. Three verbs, called by the console:
--   _init()      once at start
--   _update(dt)  every tick (dt in seconds)
--   _draw()      every frame
-- The full API is documented in moy-api.lua (your editor's Lua language
-- server reads it for autocomplete + hover docs).

local x, y = 160, 120
local speed = 120

function _init()
end

function _update(dt)
  if btn("left") then x = x - speed * dt end
  if btn("right") then x = x + speed * dt end
  if btn("up") then y = y - speed * dt end
  if btn("down") then y = y + speed * dt end
  if x < 8 then x = 8 elseif x > 312 then x = 312 end
  if y < 8 then y = 8 elseif y > 232 then y = 232 end
end

function _draw()
  cls(1)
  circ(x, y, 8, 8)
  circb(x, y, 8, 7)
  print("{title}", 8, 8, 7)
  print("arrows move - hold backspace exits", 8, 228, 6)
end
"""


def die(msg):
    print("moy: " + msg, file=sys.stderr)
    sys.exit(1)


def cart_dir(arg):
    d = arg if arg.endswith(".moy") else arg + ".moy"
    return os.path.abspath(d)


# --- new ---------------------------------------------------------------------

def cmd_new(args):
    if not args:
        die("usage: moy.py new <name>")
    dst = cart_dir(args[0])
    if os.path.exists(dst):
        die("already exists: " + dst)
    name = os.path.basename(dst)[:-4]
    title = name.replace("_", " ").replace("-", " ").title()
    os.makedirs(dst)
    man = dict(MANIFEST)
    man["title"] = title
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(man, f, indent=2)
        f.write("\n")
    with open(os.path.join(dst, "main.lua"), "w") as f:
        f.write(MAIN_LUA.replace("{title}", title))
    with open(os.path.join(dst, "config.json"), "w") as f:
        f.write("{}\n")
    stubs = os.path.join(HERE, "moy-api.lua")
    if os.path.isfile(stubs):
        shutil.copy(stubs, os.path.join(dst, "moy-api.lua"))
    print("created %s\n  next: %s run %s" % (dst, sys.argv[0], dst))


# --- run (the hot-reload dev loop) -------------------------------------------

def pack_cart(src):
    """The cart folder as the carts.json bundle shape {<name>/<rel>: text}."""
    name = os.path.basename(src.rstrip("/"))
    bundle = {}
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in ("thumbs", "__pycache__", ".git")]
        for fn in sorted(filenames):
            if fn == "moy-api.lua":     # editor stubs -- never part of the game
                continue
            p = os.path.join(dirpath, fn)
            rel = name + "/" + os.path.relpath(p, src).replace(os.sep, "/")
            try:
                with open(p, encoding="utf-8") as f:
                    bundle[rel] = f.read()
            except (UnicodeDecodeError, OSError):
                pass
    return bundle


def cart_stamp(src):
    """Latest mtime under the cart folder -- the page's reload poll target."""
    latest = 0.0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in ("thumbs", "__pycache__", ".git")]
        for fn in filenames:
            try:
                m = os.stat(os.path.join(dirpath, fn)).st_mtime
            except OSError:
                continue
            if m > latest:
                latest = m
    return "%f" % latest


def cmd_run(args):
    if not args:
        die("usage: moy.py run <cart.moy> [port]")
    src = cart_dir(args[0])
    if not os.path.isdir(src):
        die("no such cart: " + src)
    if not os.path.isfile(os.path.join(DIST, "micropython.wasm")):
        die("the runner isn't built -- run ./build.sh in %s first" % HERE)
    port = int(args[1]) if len(args) > 1 else DEFAULT_PORT

    class Handler(http.server.SimpleHTTPRequestHandler):
        extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map,
                              **{".mjs": "text/javascript", ".js": "text/javascript",
                                 ".wasm": "application/wasm"})

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=DIST, **kw)

        def log_message(self, *a):
            pass

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/carts.json":       # packed LIVE from the cart folder
                self._send(json.dumps(pack_cart(src)).encode(), "application/json")
            elif path == "/stamp":          # the reload poll
                self._send(cart_stamp(src).encode(), "text/plain")
            else:
                super().do_GET()

    url = "http://127.0.0.1:%d/?dev=1" % port
    print("moy run: %s" % src)
    print("  %s   (save a file -> the game restarts)" % url)
    with http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler) as srv:
        webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


# --- export ------------------------------------------------------------------

def cmd_export(args):
    if not args:
        die("usage: moy.py export <cart.moy> [outdir]")
    src = cart_dir(args[0])
    if not os.path.isdir(src):
        die("no such cart: " + src)
    out = os.path.abspath(args[1]) if len(args) > 1 else src[:-4] + "-web"
    build = os.path.join(HERE, "build.sh")
    print("moy export: building the spec bundle (first run compiles the toolchain)...")
    subprocess.run([build, "--spec", src], check=True,
                   stdout=subprocess.DEVNULL)
    spec = os.path.join(HERE, "dist-spec")
    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(spec, out)
    print("exported -> %s  (static: host anywhere, or zip for itch.io HTML5)" % out)


def main():
    cmds = {"new": cmd_new, "run": cmd_run, "export": cmd_export}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__.strip())
        sys.exit(0 if len(sys.argv) < 2 else 1)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
