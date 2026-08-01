#!/usr/bin/env python3
"""Serve the web runner's dist/ statically for local testing.

Plain `python -m http.server` minus two gotchas: .mjs must ship as
text/javascript (older CPython mimetypes map it to text/plain, which browsers
refuse for ES modules) and .wasm as application/wasm (streaming compile).

    python serve.py [port] [dir]    # defaults: 8321, dist/ (dir e.g. dist-spec)
                                    # dir may be absolute (e.g. the _site build)

Bind the directory by PATH, never by chdir. Every build here REPLACES its output
folder (build.sh and site/build.py both remove and recreate it), and a server
sitting inside the old one keeps a deleted inode as its cwd: the socket stays
open, and every request then dies in os.getcwd() with FileNotFoundError. Binding
by path re-resolves per request, so a rebuild mid-session is invisible.
"""

import functools
import http.server
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
root = os.path.join(_here, sys.argv[2] if len(sys.argv) > 2 else "dist")
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8321

http.server.SimpleHTTPRequestHandler.extensions_map[".mjs"] = "text/javascript"
http.server.SimpleHTTPRequestHandler.extensions_map[".js"] = "text/javascript"
http.server.SimpleHTTPRequestHandler.extensions_map[".wasm"] = "application/wasm"
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)

with http.server.ThreadingHTTPServer(("0.0.0.0", port), handler) as srv:
    print("serving %s at http://127.0.0.1:%d/" % (root, port))
    srv.serve_forever()
