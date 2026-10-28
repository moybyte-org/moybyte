"""A scripted HTTP server for the kernel's client and updater
(native/moy_net/moy_ota.c) on the host: routes of (status, headers, body),
served over plain TCP on 127.0.0.1, every request recorded.

    with Server({"/m.json": (200, {}, b"{...}")}) as srv:
        srv.url("/m.json")
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Server:
    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.seen = []                  # (path, headers) per request

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def __enter__(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self):  # noqa: N802 -- http.server's name
                srv.seen.append((self.path, dict(self.headers)))
                route = srv.routes.get(self.path)
                if route is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status, headers, body = route
                if callable(body):
                    body = body()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if "Content-Length" not in headers and body is not None:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self._httpd.server_address[1]
        self._t = threading.Thread(target=self._httpd.serve_forever,
                                   kwargs={"poll_interval": 0.02}, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
