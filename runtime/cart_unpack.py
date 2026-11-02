# Map (grep -n a name to jump there):
#   _Sink                 the staging folder's writer: network bytes read straight in
#   _ZipReader            a zip archive's members, streamed
#   _Ranged               a ranged download: the members a cart needs out of a bigger archive
#   _ZlibStream           a deflate stream, inflated as it is read
#   _TarGzReader          a .tar.gz archive's members, streamed
#   _Bare                 a cart's files fetched one by one, no archive
#   _Local                a cart archive already on the card
"""The stream readers an install unpacks a cart through (cart_index.Install):
the staging writer, the zip and tar.gz readers, a ranged read out of a
bigger archive, and the bare and local sources. Each reads a slice per call
into the job's scratch buffer, so an install stays a slice per frame.

They raise cart_index's InstallError with its kid-facing words and read its
fetch helpers, which this module imports at its FOOT: cart_index imports
this module at its own foot and re-exports the readers, so either may load
first. MicroPython-safe.
"""

import hashlib

try:
    import os
except ImportError:  # pragma: no cover
    os = None

try:
    import io as _io
except ImportError:  # pragma: no cover
    _io = None

try:
    from ticks import _ticks_ms, _ticks_diff
    from moy_fs import _exists
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.ticks import _ticks_ms, _ticks_diff
    from runtime.moy_fs import _exists


def _exact(src, n, job):
    """Exactly `n` bytes from `src`, or an InstallError (a short archive)."""
    out = bytearray(n)
    mv = memoryview(out)
    got = 0
    while got < n:
        k = src.readinto(mv[got:])
        if not k:
            raise InstallError(PACKING, "an archive ended inside a header")
        got += k
    return out


def _skip(src, n, job):
    mv = job.scratch_mv
    while n > 0:
        k = src.readinto(mv[:min(n, len(mv))])
        if not k:
            raise InstallError(PACKING, "an archive ended inside a member")
        n -= k


def _u16(b, i):
    return b[i] | (b[i + 1] << 8)


def _u32(b, i):
    return b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)


class _Sink:
    """The staging folder's writer. Network bytes are read straight into `buf`;
    a full buffer, or the end of a file, goes to the store in ONE session, and
    the file stays open between sessions the way the OTA download's does."""

    def __init__(self, job, size):
        self.job = job
        self.buf = bytearray(size)
        self.mv = memoryview(self.buf)
        self.fill = 0
        self.name = None
        self.cart_file = True
        self.path = None
        self.f = None
        self.size = 0

    def begin(self, name, path=None):
        """Start the cart's file `name`, or with `path` a file outside the
        cart (an external file's archive), which `end` does not count."""
        self.name = name
        self.cart_file = path is None
        self.path = path or self.job.stage + "/" + name
        self.fill = 0
        self.size = 0
        self.f = None

    def space(self, want):
        """A view to read up to `want` bytes into."""
        if self.fill == len(self.buf):
            self._flush(False)
        n = len(self.buf) - self.fill
        if n > want:
            n = want
        if n > READ_CHUNK:
            n = READ_CHUNK
        return self.mv[self.fill:self.fill + n]

    def wrote(self, n):
        self.fill += n
        self.size += n

    def end(self):
        self._flush(True)
        if self.cart_file:
            self.job.written[self.name] = self.size
        self.name = None

    def _flush(self, close):
        path = self.path
        data = self.mv[:self.fill]

        def _io():
            if self.f is None:
                self.f = open(path, "wb")
            if len(data):
                self.f.write(data)
            if close:
                f, self.f = self.f, None
                f.close()
        self.job.in_store(_io)
        self.fill = 0

    def abort(self):
        f, self.f = self.f, None
        if f is not None:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass


class _ZipReader:
    """A STORED zip, member by member, as it streams past. The members this
    console keeps go to the sink, each hashed against the index; the rest
    (another chip's module) are read and dropped. The central directory at
    the end is read for the asset's hash and otherwise ignored: the local
    headers already said everything, and the asset's sha256 vouches for all of
    it."""

    def __init__(self, job, src, cart, asset, seen=None, run=False):
        self.job = job
        self.src = src
        self.prefix = cart["folder"] + "/"
        self.files = asset["files"]
        self.seen = {} if seen is None else seen
        self.run = run            # a ranged read's run: members, then its end
        self.member = None        # [name, bytes left, sha256 or None, data descriptor]
        self.tail = False

    def unit(self):
        """One bounded piece of work. False once the asset (or the run) is
        done."""
        if self.member is not None:
            return self._data()
        if self.run and not self.src.left:
            return False
        if self.tail:
            if self.src.left:
                self.src.readinto(self.job.scratch)
                return True
            return False
        sig = _exact(self.src, 4, self.job)
        if sig == b"PK\x03\x04":
            self._local()
            return True
        if sig in (b"PK\x01\x02", b"PK\x05\x06") and not self.run:
            missing = [fn for fn in self.files if fn not in self.seen]
            if missing:
                raise InstallError(MISMATCH, "the zip has no %s" % ", ".join(missing))
            self.tail = True
            return True
        raise InstallError(PACKING, "unexpected zip record %r" % bytes(sig))

    def _local(self):
        h = _exact(self.src, 26, self.job)
        flags, method = _u16(h, 2), _u16(h, 4)
        csize, usize = _u32(h, 14), _u32(h, 18)
        name = bytes(_exact(self.src, _u16(h, 22), self.job)).decode()
        if _u16(h, 24):
            _skip(self.src, _u16(h, 24), self.job)
        if method != 0:
            raise InstallError(PACKING, "%s is compressed (method %d)" % (name, method))
        fn = name[len(self.prefix):] if name.startswith(self.prefix) else None
        meta = self.files.get(fn) if fn is not None else None
        if meta is None or fn in self.seen:
            raise InstallError(MISMATCH, "the zip holds %s, which the index does "
                                         "not list (or lists once)" % name)
        size = meta["size"]
        if not (flags & 8) and (csize != size or usize != size):
            raise InstallError(MISMATCH, "%s is %d bytes, the index says %d"
                               % (name, usize, size))
        self.seen[fn] = True
        keep = fn in self.job.plan["files"]
        if keep:
            self.job.sink.begin(fn)
        self.member = [fn, size, hashlib.sha256() if keep else None, bool(flags & 8)]

    def _data(self):
        m = self.member
        left = m[1]
        if left:
            if m[2] is not None:
                mv = self.job.sink.space(left)
                n = self.src.readinto(mv)
                m[2].update(mv[:n])
                self.job.sink.wrote(n)
            else:
                mv = self.job.scratch_mv
                n = self.src.readinto(mv[:min(left, len(mv))])
            m[1] = left - n
            if m[1]:
                return True
        if m[2] is not None:
            self.job.sink.end()
            got = _hex(m[2].digest())
            if got != self.files[m[0]]["sha256"]:
                raise InstallError(MISMATCH, "%s hashes to %s, the index says %s"
                                   % (m[0], got, self.files[m[0]]["sha256"]))
        if m[3]:                       # a data descriptor, signature optional
            d = _exact(self.src, 4, self.job)
            _skip(self.src, 12 if bytes(d) == b"PK\x07\x08" else 8, self.job)
        self.member = None
        return True


# The most of a zip's end its end record can be from: the record and the
# longest comment.
ZIP_END_MAX = 22 + 65535


def _zip_end(tail):
    """Where the zip's end record starts in `tail` (the end of the file), or
    -1: the last signature whose comment runs exactly to the end."""
    i = len(tail) - 22
    while i >= 0:
        if tail[i] == 0x50 and tail[i + 1] == 0x4B and tail[i + 2] == 5 \
                and tail[i + 3] == 6 and i + 22 + _u16(tail, i + 20) == len(tail):
            return i
        i -= 1
    return -1


class _Ranged:
    """A release asset read in pieces (the module docstring's `ranges`): the
    end of its zip, whose central directory says where every member starts,
    then each run of members this console keeps, read as a stored zip's
    members are (`_ZipReader`), each checked against the index. It is the
    job's source (`ready`, `finish`, `close`) and its reader (`unit`) at
    once. It asks the asset's first URL (the mirror, on a console with
    `cors`); an answer to its first range that is not 206 -- the whole file,
    a refusal, no answer -- sends the asset back to the queue, to be read
    whole from every URL the way a console without `ranges` reads it."""

    def __init__(self, job, url, cart, asset, keep):
        self.job = job
        self.url = resolve(cart["index"], url)
        self.cart = cart
        self.asset = asset
        self.size = asset["size"]
        self.keep = keep
        self.what = asset["name"]
        self.counted = _ranged_bytes(cart, asset, keep)   # its share of job.total
        self.tail_n = _tail_bytes(cart, asset)
        self.spent = 0            # the end's bytes read so far
        self.resp = None          # the answer awaited, or being read
        self.need = 0             # what that answer must have in hand
        self.runs = None          # [(start, end)] once the directory is read
        self.src = None           # a run's bytes
        self.zip = None           # and its members
        self.seen = {}
        self.whole = False        # the server sent the file: read it whole
        self._open(self.size - self.tail_n, self.tail_n)

    def _open(self, start, n):
        """Ask for `n` bytes from `start`."""
        job = self.job
        t = _ticks_ms()
        try:
            self.resp = job.net.open(self.url, span=(start, n))
        except Exception as exc:  # noqa: BLE001 -- a dropped request is a stopped one
            raise InstallError(net_text(job.net, STOPPED), "%s: %s" % (self.url, exc))
        finally:
            job.t_net += _ticks_diff(_ticks_ms(), t)
        self.need = n

    def _count(self, n):
        """The read fetches `n` bytes in all, as far as it now knows."""
        self.job.total += n - self.counted
        self.counted = n

    def ready(self):
        if self.src is not None:
            return self.src.ready()
        resp = self.resp
        if resp is None or resp.status is None:
            return resp is None
        if resp.status != 206:
            return True
        r = getattr(resp, "ready", None)
        return r is None or r(self.need)

    def unit(self):
        if self.zip is not None:
            if self.zip.unit():
                return True
            self.src.finish()
            self.src.close()
            self.src = self.zip = None
            return True
        resp = self.resp
        if resp is not None:
            return self._answered(resp)
        if not self.runs:
            return False
        start, end = self.runs.pop(0)
        self._open(start, end - start)
        return True

    def _answered(self, resp):
        """The head of the answer in hand is in: read on, or decide."""
        if resp.status != 206 and self.runs is None:
            _log("%s: no range from %s (HTTP %s), reading it whole"
                 % (self.what, self.url, resp.status))
            self.close()
            self.whole = True
            self.job.whole.append(self.asset)
            self.job._queue.insert(0, ("asset", self.asset))
            self._count(self.spent + self.size)
            return False
        if resp.status != 206:
            self.close()
            raise InstallError(STOPPED, "%s: HTTP %d for a range of %s"
                               % (self.url, resp.status, self.what))
        if self.runs is not None:
            self.resp = None
            self.src = _Fetched(self.job, resp, self.need, None, self.what)
            self.zip = _ZipReader(self.job, self.src, self.cart, self.asset,
                                  self.seen, run=True)
            return True
        tail = bytes(_exact(_Fetched(self.job, resp, self.need, None, self.what),
                            self.need, self.job))
        self.spent += len(tail)
        self.close()
        self._directory(tail)
        return True

    def _directory(self, tail):
        """The runs to read, from the zip's end in `tail`."""
        i = _zip_end(tail)
        if i < 0:
            whole = min(self.size, ZIP_END_MAX)
            if len(tail) >= whole:
                raise InstallError(PACKING, "%s has no zip end record" % self.what)
            # A comment longer than the slack: ask for all the end could be.
            self._count(self.counted + whole)
            self._open(self.size - whole, whole)
            return
        count, cd_off = _u16(tail, i + 10), _u32(tail, i + 16)
        base = self.size - len(tail)
        if cd_off < base:
            # A directory longer than the index's names make: fetch it whole.
            self._count(self.counted + self.size - cd_off)
            self._open(cd_off, self.size - cd_off)
            return
        prefix = self.cart["folder"] + "/"
        names = {}
        j = cd_off - base
        for _ in range(count):
            if j + 46 > len(tail) or bytes(tail[j:j + 4]) != b"PK\x01\x02":
                raise InstallError(PACKING, "%s's zip directory is malformed" % self.what)
            n, x, c = _u16(tail, j + 28), _u16(tail, j + 30), _u16(tail, j + 32)
            name = bytes(tail[j + 46:j + 46 + n]).decode()
            fn = name[len(prefix):] if name.startswith(prefix) else None
            if fn not in self.asset["files"] or fn in names:
                raise InstallError(MISMATCH, "the zip holds %s, which the index does "
                                             "not list (or lists once)" % name)
            names[fn] = _u32(tail, j + 42)
            j += 46 + n + x + c
        missing = [fn for fn in self.asset["files"] if fn not in names]
        if missing:
            raise InstallError(MISMATCH, "the zip has no %s" % ", ".join(missing))
        order = sorted((off, fn) for fn, off in names.items())
        runs = []
        for k, (off, fn) in enumerate(order):
            end = order[k + 1][0] if k + 1 < len(order) else cd_off
            if fn in self.keep:
                if runs and runs[-1][1] == off:
                    runs[-1] = (runs[-1][0], end)
                else:
                    runs.append((off, end))
        fetched = self.spent
        for start, end in runs:
            fetched += end - start
        self._count(fetched)
        self.runs = runs

    def finish(self):
        if self.whole:
            return
        missing = [fn for fn in self.keep if fn not in self.seen]
        if missing:
            raise InstallError(MISMATCH, "the zip has no %s" % ", ".join(missing))

    def close(self):
        resp, self.resp = self.resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        src, self.src = self.src, None
        if src is not None:
            src.close()


class _ZlibStream:
    """CPython's stand-in for MicroPython's `deflate.DeflateIO(src, GZIP)`:
    the same pull-shaped `readinto`, over zlib."""

    def __init__(self, src):
        try:
            import zlib
        except ImportError:
            zlib = None
        if zlib is None:
            raise InstallError(PACKING, "no inflater: neither deflate nor zlib")
        self.src = src
        self.z = zlib.decompressobj(31)
        self.raw = bytearray(READ_CHUNK)
        self.pending = b""

    def readinto(self, buf):
        mv = memoryview(buf)
        while True:
            if self.z.eof:
                return 0
            if not self.pending:
                n = self.src.readinto(self.raw)
                if not n:
                    raise InstallError(PACKING, "the archive's gzip stream is cut short")
                self.pending = bytes(self.raw[:n])
            out = self.z.decompress(self.pending, len(mv))
            self.pending = self.z.unconsumed_tail
            if out:
                mv[:len(out)] = out
                return len(out)


def _gunzip(src):
    try:
        import deflate
    except ImportError:
        deflate = None
    if deflate is None:
        return _ZlibStream(src)
    return deflate.DeflateIO(src, deflate.GZIP)


def _cstr(b):
    b = bytes(b)
    cut = b.find(b"\0")
    return (b if cut < 0 else b[:cut]).decode()


class _TarGzReader:
    """An external file's archive, in two halves. FETCH: the archive's bytes
    are kept whole as they arrive -- in RAM, or (`job.archive == "store"`) in
    a file beside the build -- hashed against the index, and the hash is
    settled before anything is inflated, so the inflater only ever sees bytes
    the index vouched for. INFLATE: the tar inside is walked from those bytes
    and the one member the index names is written as the external's path,
    hashed against its own sha256.

    Why whole and not off the socket: MicroPython's `deflate.DeflateIO` pulls
    its source ONE BYTE per call, and through a Python-level stream that is a
    method call per compressed byte (1.76 million for Doom's WAD). Over a
    native stream -- `io.BytesIO`, or an open file -- the same inflate is a C
    loop: 84ms against 1.5s for the whole WAD on the desktop build.

    RAM or a file is the console's call (`Install`'s `archive`). In RAM,
    MicroPython's `BytesIO(n)` preallocates without a copy, so the archive
    costs its own size once -- but on a board whose Python heap grows and
    never shrinks, that size stays out of what a compiled cart can load into
    until a reboot. In a file it costs a write and a read of its size."""

    def __init__(self, job, src, ext):
        self.job = job
        self.src = src
        self.ext = ext
        self.want = ext["archive"]["member"]
        self.path = None
        self.buf = None
        if job.archive == "store":
            self.path = stage_root(job.root) + "/" + job.folder + ".archive"
            job.sink.begin(None, self.path)
        else:
            self.buf = _io.BytesIO(ext["archive"]["size"]) if _MP else _io.BytesIO()
        self.f = None
        self.gz = None
        self.member = None        # [bytes left, sha256 or None, padding]
        self.long = None
        self.found = False

    def unit(self):
        if self.gz is None:
            return self._fetch()
        if self.f is None:
            return self._step()
        return self.job.in_store(self._step)

    def _step(self):
        if self.member is not None:
            return self._data()
        hdr = bytearray(512)
        got = 0
        while got < 512:
            k = self.gz.readinto(memoryview(hdr)[got:])
            if not k:
                break
            got += k
        if got == 0 or not any(hdr):
            return False              # end of the archive
        if got < 512:
            raise InstallError(PACKING, "an archive ended inside a header")
        name = _cstr(hdr[0:100])
        if bytes(hdr[257:262]) == b"ustar" and hdr[345]:
            name = _cstr(hdr[345:500]) + "/" + name
        if self.long is not None:
            name, self.long = self.long, None
        try:
            size = int(_cstr(hdr[124:136]).strip() or "0", 8)
        except ValueError:
            raise InstallError(PACKING, "a tar header with no size")
        pad = (512 - size % 512) % 512
        kind = hdr[156]
        if kind in (ord("L"), ord("x")):
            body = bytes(_exact(self.gz, size, self.job)) if size else b""
            _skip(self.gz, pad, self.job)
            if kind == ord("L"):
                self.long = _cstr(body)
            else:
                for line in body.split(b"\n"):
                    k = line.find(b" path=")
                    if k > 0:
                        self.long = line[k + 6:].decode()
            return True
        keep = (kind in (0, ord("0"))) and name == self.want and not self.found
        if keep:
            if size != self.ext["size"]:
                raise InstallError(MISMATCH, "%s is %d bytes, the index says %d"
                                   % (name, size, self.ext["size"]))
            self.found = True
            self.job.sink.begin(self.ext["path"])
        self.member = [size, hashlib.sha256() if keep else None, pad]
        return True

    def _fetch(self):
        if self.buf is None:
            sink = self.job.sink
            mv = sink.space(self.src.left)
            sink.wrote(self.src.readinto(mv))
        else:
            mv = self.job.scratch_mv
            n = self.src.readinto(mv)
            self.buf.write(mv[:n])
        if self.src.left:
            return True
        self.src.finish()
        self.src.close()
        if self.buf is None:
            self.job.sink.end()
            self.f = self.job.in_store(lambda: open(self.path, "rb"))
            self.gz = _gunzip(self.f)
        else:
            self.buf.seek(0)
            self.gz = _gunzip(self.buf)
        return True

    def _data(self):
        m = self.member
        if m[0]:
            if m[1] is not None:
                mv = self.job.sink.space(m[0])
                n = self.gz.readinto(mv)
                if n:
                    m[1].update(mv[:n])
                    self.job.sink.wrote(n)
                    self.job.done += n
            else:
                mv = self.job.scratch_mv
                n = self.gz.readinto(mv[:min(m[0], len(mv))])
            if not n:
                raise InstallError(PACKING, "the archive ended inside a member")
            m[0] -= n
            if m[0]:
                return True
        if m[1] is not None:
            self.job.sink.end()
            got = _hex(m[1].digest())
            if got != self.ext["sha256"]:
                raise InstallError(MISMATCH, "%s hashes to %s, the index says %s"
                                   % (self.ext["path"], got, self.ext["sha256"]))
        if m[2]:
            _skip(self.gz, m[2], self.job)
        self.member = None
        return True

    def check(self):
        if not self.found:
            raise InstallError(MISMATCH, "the archive has no %s" % self.want)
        self.close()

    def close(self):
        """Let the archive go: the buffer, or the file and its bytes."""
        self.buf = None
        self.gz = None
        f, self.f = self.f, None
        path, self.path = self.path, None
        if f is None and path is None:
            return

        def _drop():
            if f is not None:
                f.close()
            if path is not None and _exists(path):
                os.remove(path)
        self.job.in_store(_drop)


class _Bare:
    """An external file from its mirror: the file itself, written as it
    streams. Its hash is the download's own, which `_Fetched.finish` checks
    before the build can commit."""

    def __init__(self, job, src, ext):
        self.job = job
        self.src = src
        self.left = ext["size"]
        job.sink.begin(ext["path"])

    def unit(self):
        sink = self.job.sink
        if self.left:
            mv = sink.space(self.left)
            n = self.src.readinto(mv)
            sink.wrote(n)
            self.left -= n
            if self.left:
                return True
        sink.end()
        return False


class _Local:
    """An external file the player supplied, where this console could not
    fetch it from its archive's hosts (the browser): copied from where it was
    put into the build and held to the index's size and sha256 on the way,
    exactly as the file out of a downloaded archive is."""

    def __init__(self, job, path, ext):
        self.job = job
        self.ext = ext
        self.left = ext["size"]
        self.h = hashlib.sha256()
        self.f = job.in_store(lambda: open(path, "rb"))
        job.sink.begin(ext["path"])

    def unit(self):
        return self.job.in_store(self._unit)

    def _unit(self):
        job = self.job
        name = self.ext["path"]
        if self.left:
            mv = job.sink.space(self.left)
            n = self.f.readinto(mv)
            if not n:
                raise InstallError(MISMATCH, "%s is %d bytes short" % (name, self.left))
            self.h.update(mv[:n])
            job.sink.wrote(n)
            job.done += n
            self.left -= n
            if self.left:
                return True
        if self.f.read(1):
            raise InstallError(MISMATCH, "%s is larger than the index says (%d bytes)"
                               % (name, self.ext["size"]))
        job.sink.end()
        got = _hex(self.h.digest())
        if got != self.ext["sha256"]:
            raise InstallError(MISMATCH, "%s hashes to %s, the index says %s"
                               % (name, got, self.ext["sha256"]))
        return False

    def close(self):
        f, self.f = self.f, None
        if f is not None:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass


# cart_index's words and fetch helpers, imported last (see above).
try:
    from cart_index import (InstallError, MISMATCH, PACKING, READ_CHUNK, STOPPED,
                            _Fetched, _MP, _hex, _log, _ranged_bytes,
                            _tail_bytes, net_text, resolve, stage_root)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.cart_index import (InstallError, MISMATCH, PACKING, READ_CHUNK, STOPPED,
                                    _Fetched, _MP, _hex, _log, _ranged_bytes,
                                    _tail_bytes, net_text, resolve, stage_root)
