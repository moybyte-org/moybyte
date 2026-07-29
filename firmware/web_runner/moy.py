#!/usr/bin/env python3
"""moy -- the cart developer CLI: scaffold, live-run, publish.

A moy cart is a folder of text files, so your own editor, git and your own
art tools already work; this CLI supplies the loop around them.

    moy.py new <name>            scaffold a Lua cart (manifest + main.lua +
                                 moy-api.lua editor stubs -- the Lua language
                                 server reads those for autocomplete + docs)
    moy.py run <cart.moy>        play the cart in your browser with HOT
                                 RELOAD: save a file, the game restarts in
                                 under a second
    moy.py export <cart.moy>     the publishable web bundle: ~1.1MB of static
                                 files that boot straight into the game --
                                 host anywhere (itch.io HTML5 uploads work)
    moy.py port <cart.p8|url>    convert a PICO-8 cart: assets via p8_import,
                                 code mechanically ported to Lua 5.4 under the
                                 p8 compat shim (p8_lua_port)
    moy.py demo                  fetch Celeste Classic (PICO-8), port it, run
                                 it -- the one-command show-off

Pure Python stdlib, no dependencies. The player it wraps is runner/ (see
runner/BUILD.md); the spec it implements is SPEC.md.
"""

import http.server
import json
import os
import shutil
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "runner")
RUNNER_FILES = ("index.html", "micropython.mjs", "micropython.wasm")
DEFAULT_PORT = 8323

# The spec manifest (SPEC.md 3.1) -- brand-neutral, fields the spec defines.
# fps 60 is an explicit opt-in (SPEC.md 5): a fresh scaffold trivially sustains
# it, and hosts that can't fall back to the guaranteed 30.
MANIFEST = {
    "format": "moy-1",
    "title": None,                    # filled from the name
    "version": 1,
    "main": "main.lua",
    "fps": 60,
    "input": ["buttons"],
}

MAIN_LUA = """\
-- {title}: a moy cart. Three verbs, called by the console:
--   _init()      once at start
--   _update(dt)  every tick (dt in seconds)
--   _draw()      every frame
-- The full API is documented in moy-api.lua (your editor's Lua language
-- server reads it for autocomplete + hover docs) and in the spec.

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
  print("arrows move", 8, 228, 6)
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
    with open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, indent=2)
        f.write("\n")
    with open(os.path.join(dst, "main.lua"), "w", encoding="utf-8", newline="\n") as f:
        f.write(MAIN_LUA.replace("{title}", title))
    with open(os.path.join(dst, "config.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write("{}\n")
    stubs = os.path.join(HERE, "moy-api.lua")
    if os.path.isfile(stubs):
        shutil.copy(stubs, os.path.join(dst, "moy-api.lua"))
    print("created %s" % dst)
    print("  next: %s run %s" % (sys.argv[0], os.path.relpath(dst)))


# --- run (the hot-reload dev loop) -------------------------------------------

def pack_cart(src):
    """The cart folder as the player's carts.json shape {<name>/<rel>: text}."""
    name = os.path.basename(src.rstrip("/"))
    bundle = {}
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames
                       if d not in ("thumbs", "__pycache__", ".git")]
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
    """Latest mtime under the cart folder -- the page's reload-poll target."""
    latest = 0.0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames
                       if d not in ("thumbs", "__pycache__", ".git")]
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
    port = int(args[1]) if len(args) > 1 else DEFAULT_PORT

    class Handler(http.server.SimpleHTTPRequestHandler):
        extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map,
                              **{".mjs": "text/javascript", ".js": "text/javascript",
                                 ".wasm": "application/wasm"})

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=RUNNER, **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            # A DEV server must never let the browser cache the player -- a
            # half-cached page (old wasm, new index) is undebuggable.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

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
    os.makedirs(out, exist_ok=True)
    for fn in RUNNER_FILES:
        shutil.copy(os.path.join(RUNNER, fn), os.path.join(out, fn))
    with open(os.path.join(out, "carts.json"), "w", encoding="utf-8") as f:
        json.dump(pack_cart(src), f)
    print("exported -> %s" % out)
    print("  static files: host anywhere, or zip the folder for itch.io (HTML5)")


# --- port / demo (PICO-8) ----------------------------------------------------

CELESTE_URL = "https://www.lexaloffle.com/bbs/cposts/1/15133.p8.png"
CELESTE_NOTE = """\
  Celeste Classic (PICO-8, 2016) by Maddy Thorson & Noel Berry
  https://www.lexaloffle.com/bbs/?tid=2145 / https://celesteclassic.github.io/
  PICO-8 BBS carts default to CC BY-NC-SA 4.0: the port is for personal /
  development use with attribution -- do not ship it in anything commercial."""


def cmd_port(args):
    if not args:
        die("usage: moy.py port <cart.p8 | url> [out.moy]")
    src = args[0]
    if src.startswith(("http://", "https://")):
        import urllib.request
        local = os.path.abspath(os.path.basename(src.split("?")[0]) or "cart.p8")
        print("fetching %s" % src)
        req = urllib.request.Request(src, headers={"User-Agent": "moy-cli"})
        with urllib.request.urlopen(req) as r, open(local, "wb") as f:
            f.write(r.read())
        src = local
    src = os.path.abspath(src)
    if not os.path.isfile(src):
        die("no such .p8: " + src)
    out = cart_dir(args[1] if len(args) > 1
                   else os.path.splitext(os.path.basename(src))[0])
    import p8_lua_port
    p8_lua_port.port(src, out)
    print("ported -> %s" % out)
    print("  PICO-8 carts carry their own licenses (BBS default CC BY-NC-SA")
    print("  4.0) -- ported carts are dev/personal material unless stated.")
    print("  next: %s run %s" % (sys.argv[0], os.path.relpath(out)))


def cmd_demo(args):
    """Fetch + port + run Celeste Classic -- the one-command demo."""
    print(CELESTE_NOTE)
    out = cart_dir("celeste")
    if not os.path.isdir(out):
        cmd_port([CELESTE_URL, "celeste"])
    else:
        print("using existing %s" % out)
    cmd_run(["celeste.moy"] + list(args))


def main():
    cmds = {"new": cmd_new, "run": cmd_run, "export": cmd_export,
            "port": cmd_port, "demo": cmd_demo}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__.strip())
        sys.exit(0 if len(sys.argv) < 2 else 1)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
