#!/usr/bin/env python3
"""Serve the web runner's dist/ statically for local testing.

Plain `python -m http.server` minus two gotchas: .mjs must ship as
text/javascript (older CPython mimetypes map it to text/plain, which browsers
refuse for ES modules) and .wasm as application/wasm (streaming compile).

    python serve.py [port]      # default 8321
"""

import http.server
import os
import sys

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist"))
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8321

handler = http.server.SimpleHTTPRequestHandler
handler.extensions_map[".mjs"] = "text/javascript"
handler.extensions_map[".js"] = "text/javascript"
handler.extensions_map[".wasm"] = "application/wasm"

with http.server.ThreadingHTTPServer(("0.0.0.0", port), handler) as srv:
    print("web runner at http://127.0.0.1:%d/" % port)
    srv.serve_forever()
