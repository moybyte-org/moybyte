"""Shared fixtures for the cart store (#124): carts repositories built here in
the shapes moybyte-org's publish (an index.json, a STORED zip per cart, an
external file inside a tar.gz with its licence), an in-memory transport, and a
local HTTP server for the host's real urllib transport.

`fixtures/carts/index.json` is a snapshot of moybyte-org/carts' index (its
first: Doom, ESP 88 and Jet Teapot v4, 2026-10-03), so the console's reader is
pinned to the format the repository actually publishes -- moy-spec's `moy
index` writes it. `fixtures/carts/installed-v3.json` is the record a console
of each chip kept after installing the three at v3 from the two indexes that
came before it (gpl-carts and mit-carts)."""

import gzip
import hashlib
import io
import json
import tarfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "carts"
INDEX_URL = "https://moybyte-org.github.io/carts/index.json"
_TIME = (1980, 1, 1, 0, 0, 0)


def snapshot(name):
    return (FIXTURES / name).read_bytes()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stored_zip(folder, files):
    """`files` as `folder/<name>` in a STORED zip, sorted, the way the carts
    repositories' scripts/build.py packs a release asset."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for fn in sorted(files):
            info = zipfile.ZipInfo("%s/%s" % (folder, fn), _TIME)
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, files[fn])
    return buf.getvalue()


def tar_gz(members):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for name in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(members[name])
            info.mtime = 0
            t.addfile(info, io.BytesIO(members[name]))
    return gzip.compress(raw.getvalue(), mtime=0)


def module_bytes(chip, fmt, tag=b""):
    return (b"\0aot" + chip.encode() + b"f" + str(fmt).encode() + tag) * 40


class Repo:
    """One carts repository served under `base` (a URL). `files` maps a path
    under `base` to its bytes; `add` builds a cart's release asset and its
    index entry the way moy-spec's `moy index` lays one out."""

    def __init__(self, base, name="Test carts"):
        self.base = base.rstrip("/")
        self.name = name
        self.files = {}
        self.releases = {}        # full URL -> bytes, served by another host
        self.carts = []

    def url(self, rel):
        return self.base + "/" + rel

    def add(self, cid, version=1, runtime="wasm", name=None, spdx="MIT",
            modules=(("esp32s3", 2), ("esp32p4", 2)), extra=None, external=None,
            config=b'{"speed": 2}\n', main=b"\0asm\x01\0\0\0 main", cover=None,
            release=None, mirror=False):
        """`release` puts the release asset on another host (a GitHub release
        download's base URL) instead of under `base`; `mirror` serves a copy
        under `base` too and names it in the index, the way moy-spec's
        `moy index --mirror releases` does. Each of `external` is (path, data,
        archive member), or with a fourth item True a file the repository
        also serves bare at files/<id>/<path>, named as its mirror."""
        folder = cid + ".moy"
        name = name or cid.replace("_", " ").title()
        files = {"manifest.json": json.dumps({"format": "moy-1", "title": name,
                                              "runtime": runtime, "main": "main.wasm",
                                              "memory": 4}).encode(),
                 "config.json": config,
                 "main.wasm": main + str(version).encode(),
                 "LICENSES.txt": b"MIT License\n\nfor " + cid.encode()}
        for chip, fmt in modules:
            files["main.%s.f%s.aot" % (chip, fmt)] = module_bytes(chip, fmt,
                                                                 str(version).encode())
        files.update(extra or {})
        if cover is not None:
            files["cover.png"] = cover
        z = stored_zip(folder, files)
        rel = "releases/%s-v%d/%s.zip" % (cid, version, folder)
        if release is None:
            self.files[rel] = z
            asset_url = self.url(rel)
        else:
            asset_url = "%s/%s" % (release.rstrip("/"), rel)
            self.releases[asset_url] = z
            if mirror:
                self.files[rel] = z
        lic = ("The MIT licence of %s.\n" % name).encode()
        self.files["carts/%s/LICENSES.txt" % cid] = lic
        entry = {
            "id": cid, "name": name, "version": version, "folder": folder,
            "runtime": runtime, "memory": 4,
            "chips": sorted(set(c for c, _f in modules)),
            "licence": {"spdx": spdx, "name": spdx + " License",
                        "url": "carts/%s/LICENSES.txt" % cid,
                        "size": len(lic), "sha256": sha(lic)},
            "source": self.url("tree/x/carts/" + cid),
            "release": self.url("releases/tag/%s-v%d" % (cid, version)),
            "assets": [{"name": folder + ".zip", "url": asset_url, "size": len(z),
                        "sha256": sha(z),
                        "files": dict((fn, {"size": len(b), "sha256": sha(b)})
                                      for fn, b in files.items())}],
            "external": [],
            "build": {"commit": "0" * 40, "keys": {}, "modules": {}, "pins": {}},
        }
        if mirror:
            entry["assets"][0]["mirror"] = rel
        if cover is not None:
            # moy-spec's `moy index` (cartindex.with_cover): beside the licence,
            # served from the repository like it.
            self.files["carts/%s/cover.png" % cid] = cover
            ref = {"url": "carts/%s/cover.png" % cid, "size": len(cover),
                   "sha256": sha(cover), "w": 128, "h": 128}
            placed = {}
            for k, v in entry.items():
                placed[k] = v
                if k == "licence":
                    placed["cover"] = ref
            entry = placed
        for path, data, member, *bare in external or ():
            arc = tar_gz({member: data, "pkg/README": b"read me\n"})
            arel = "mirror/%s.tar.gz" % path
            self.files[arel] = arc
            ltext = ("The licence for %s.\n\nYou may use it for fun.\n" % path).encode()
            lrel = "carts/%s/licenses/%s.txt" % (cid, path)
            self.files[lrel] = ltext
            ext = {"path": path, "size": len(data), "sha256": sha(data)}
            if bare and bare[0]:
                ext["mirror"] = "files/%s/%s" % (cid, path)
                self.files[ext["mirror"]] = data
            ext["licence"] = {"name": "The %s licence" % path, "url": lrel,
                              "size": len(ltext), "sha256": sha(ltext)}
            ext["archive"] = {"urls": [self.url(arel)], "format": "tar.gz",
                              "size": len(arc), "sha256": sha(arc), "member": member}
            entry["external"].append(ext)
        self.carts = [c for c in self.carts if c["id"] != cid] + [entry]
        self.files["index.json"] = self.index()
        return entry

    def index(self):
        return json.dumps({"version": 1, "name": self.name,
                           "home": self.base, "carts": self.carts}).encode()

    def routes(self):
        """{full URL: bytes} for an in-memory transport."""
        out = dict((self.url(rel), data) for rel, data in self.files.items())
        out.update(self.releases)
        return out


class _Resp:
    def __init__(self, data, status=200, length=None):
        self._b = io.BytesIO(data)
        self.status = status
        self.length = len(data) if length is None else length
        self.closed = False

    def readinto(self, buf):
        return self._b.readinto(buf)

    def close(self):
        self.closed = True


class MemNet:
    """A transport over `{url: bytes}`. A value may also be a callable that
    returns a response (a fault: a short body, a dropped socket)."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.opened = []
        self.up = True

    def online(self):
        return self.up

    def open(self, url):
        self.opened.append(url)
        got = self.routes.get(url)
        if got is None:
            return _Resp(b"not found", 404)
        if callable(got):
            return got()
        return _Resp(got)


class Truncated(_Resp):
    """A body the server stops sending part way: `cut` bytes, then a reset."""

    def __init__(self, data, cut):
        _Resp.__init__(self, data[:cut], 200, len(data))

    def readinto(self, buf):
        n = self._b.readinto(buf)
        if not n:
            raise OSError(104, "connection reset")
        return n


class Server:
    """A local HTTP server for the host transport: `routes` maps a path to
    bytes, ("redirect", path) or ("cut", bytes, n) -- a body cut after n
    bytes with the full length advertised."""

    def __init__(self):
        self.routes = {}
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                got = routes.get(self.path)
                if got is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if isinstance(got, tuple) and got[0] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", got[1])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body, cut = got, None
                if isinstance(got, tuple) and got[0] == "cut":
                    body, cut = got[1], got[2]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body if cut is None else body[:cut])

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def serve(self, repo, prefix):
        for rel, data in repo.files.items():
            self.routes["%s/%s" % (prefix, rel)] = data

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
