"""A live MP3 station for the browser's e2e: an endless body, no
Content-Length, at the stream's own byte rate after a connect burst, as an
Icecast station sends one. CORS lets a page read it; it answers the
preflight an ICY request makes and sends no `icy-metaint`, so the page reads
it again plainly. `stall(s)`, or a GET of `/stall?s=<seconds>` (which a
page can make), stops its bytes for that long."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TONE = ROOT / "tests" / "fixtures" / "media" / "tone_1k.mp3"
TONE_SECONDS = 2.037551         # the fixture's length (ffprobe), 44.1 kHz stereo 128 kbps


def tone_frames():
    """The fixture's MP3 frames, its ID3 tag dropped, so they loop seamlessly."""
    b = TONE.read_bytes()
    assert b[:3] == b"ID3"
    tag = 10 + ((b[6] & 0x7f) << 21 | (b[7] & 0x7f) << 14 | (b[8] & 0x7f) << 7 | (b[9] & 0x7f))
    return b[tag:]


class LiveStation:
    def __init__(self, burst=64 * 1024, chunk=1024):
        frames = tone_frames()
        self.rate = len(frames) / TONE_SECONDS        # bytes a second
        self.sent = 0
        self.gets = 0
        self._stall_until = 0.0
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Icy-MetaData")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                if self.path.startswith("/stall?s="):
                    host.stall(float(self.path.split("=", 1)[1]))
                    self.send_response(204)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                host.gets += 1
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                t0 = time.monotonic()
                at = 0
                try:
                    while True:
                        due = burst + (time.monotonic() - t0) * host.rate
                        if time.monotonic() < host._stall_until or at >= due:
                            time.sleep(0.02)
                            continue
                        k = min(chunk, int(due - at) or 1)
                        off = at % len(frames)
                        part = frames[off:off + k]
                        self.wfile.write(part)
                        self.wfile.flush()
                        at += len(part)
                        host.sent += len(part)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stall(self, seconds):
        self._stall_until = time.monotonic() + seconds

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
