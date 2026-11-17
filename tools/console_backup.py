#!/usr/bin/env python3
"""Mirror a console's moybyte folder to this machine, or put a tree back,
over the serial console (#127).

    python tools/console_backup.py pull --board tdeck [--out DIR]
    python tools/console_backup.py push DIR --board p4 [--no-reboot]

A backup is a copy of the whole folder the console's carts folder sits in,
`system/` and its settings included (the WiFi passwords, `system/password`
and `authorized_keys` among them). A restore is that folder put back and a
boot: the store's door (runtime/carts/moy_seed.py, `sweep_store`) steps an
older format, and a folder another board wrote drops the rows bound to that
board (`stamp_board`).

`pull` lists the console's folder -- each file's path, size and sha256
prefix, less what never travels (`moy_store.skip` with the journal let
through: thumbnails, __pycache__, .tmp and .bak files) and the install
stage -- and reads back, over `py` a PIECE at a time, only the files whose
size or hash differs from the mirror's copy. A file the console no longer
has leaves the mirror. The store's publish marker (`.publish`) and the
`.bak` it names come too, so a file caught mid-save heals where the folder
is put back (runtime/carts/moy_fs.py). USB drive mode, or the card in a
reader with the console off, copies a folder nothing is writing.

`push` sends each file of the tree that differs from the console's with the
raw receive tools/push_cart.py uses, makes the folders it needs, leaves the
console's files the tree does not have, and reboots the console
(tools/board.py's reboot), so the boot reads what landed before the running
console writes its settings over it. The board's line state and identity
check are push_cart's (`connect`); --board is required for the same reason.
"""

import argparse
import binascii
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_cart                                                 # noqa: E402

PIECE = 768          # file bytes a `py` answer carries (base64 under a line)
LIST_BUDGET = 640    # listing characters a `py` answer carries
DIRS_LINE = 160      # folder paths a `py` line carries
PY_RATE = 36 * 1024  # bytes a second a pull reads over `py`, for its estimate
MARKER = ".publish"
STAMP = "store.ver"
STAGE = "install"
_SKIP_DIRS = ("thumbs", "__pycache__")

# The console's half, run in `ws._g` beside push_cart.HELPERS (`_gated`,
# `_size`, `_sha`): the folder's walk a batch at a time, the publish marker's
# files, and a file read a piece at a time.
WALK = """
import os
def _bk_base():
    r = getattr(ws, 'carts_root', None)
    return r.rstrip('/').rsplit('/', 1)[0] if r else None
def _bk_walk(base):
    sk = __import__('moy_store').skip
    todo = ['']
    while todo:
        rel = todo.pop()
        try: ents = sorted(os.ilistdir(base + '/' + rel if rel else base))
        except OSError: continue
        for e in ents:
            n = e[0]
            if sk(n, True) or (not rel and n in (%r, %r)): continue
            r = rel + '/' + n if rel else n
            if e[1] & 0x4000: todo.append(r)
            else: yield r
def _bk_list(base):
    ws._bkb = base
    ws._bkw = _bk_walk(base)
    return 1
def _bk_next(budget):
    def go():
        out, used = [], 0
        for r in ws._bkw:
            p = ws._bkb + '/' + r
            out.append((r, _size(p), _sha(p)))
            used += len(r) + 32
            if used >= budget: break
        return out
    return _gated(go)
def _bk_marker():
    def go():
        b = ws._bkb
        p = b + '/' + %r
        try:
            f = open(p)
            line = f.read(512)
            f.close()
        except OSError: return []
        out = [(%r, _size(p), _sha(p))]
        bits = line.rstrip('\\n').split(' ', 3)
        if len(bits) == 4 and bits[3].startswith(b + '/'):
            r = bits[3][len(b) + 1:] + '.bak'
            if _size(b + '/' + r): out.append((r, _size(b + '/' + r), _sha(b + '/' + r)))
        return out
    return _gated(go)
def _bk_open(p):
    ws._bk = _gated(lambda: open(p, 'rb'))
    return 1
def _bk_read(n):
    return __import__('binascii').b2a_base64(_gated(lambda: ws._bk.read(n))).decode().strip()
def _bk_close():
    ws._bk.close()
    return 1
""" % (STAGE, MARKER, MARKER, MARKER)


def sha12(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def install(b):
    """The console's half in `ws._g`; exits where it will not go in."""
    if not b.pyexec(push_cart.HELPERS) or not b.pyexec(WALK):
        sys.exit("could not install the backup helpers")


def console_base(b):
    base = b.pyval("ws._g['_bk_base']()", strict=True)
    if not base:
        sys.exit("the console has no store")
    return base


def listing(b, base, log=print):
    """{path under the folder: (size, sha12)} of every file the folder
    carries, the publish marker's included."""
    b.pyval("ws._g['_bk_list'](%r)" % base, strict=True)
    out = {}
    while True:
        batch = b.pyval("ws._g['_bk_next'](%d)" % LIST_BUDGET, timeout=120.0, strict=True)
        if not batch:
            break
        for rel, size, sha in batch:
            out[rel] = (size, sha)
        if len(out) % 200 < len(batch):
            log("  listed %d files" % len(out))
    for rel, size, sha in b.pyval("ws._g['_bk_marker']()", strict=True):
        out[rel] = (size, sha)
    return out


def read_back(b, path, out, log=print):
    """The console's file `path` written to `out`, a PIECE at a time; `out`
    is replaced only once the whole file is read."""
    b.pyval("ws._g['_bk_open'](%r)" % path, strict=True)
    got = 0
    part = out + ".part"
    try:
        with open(part, "wb") as f:
            while True:
                text = b.pyval("ws._g['_bk_read'](%d)" % PIECE, strict=True)
                data = binascii.a2b_base64(text) if text else b""
                if not data:
                    break
                f.write(data)
                got += len(data)
                if got % (PIECE * 64) < PIECE:
                    log("  %d KB" % (got // 1024))
    finally:
        b.pyval("ws._g['_bk_close']()")
    os.replace(part, out)
    return got


def _skipped(name, top):
    """What `moy_store.skip(name, True)` leaves home, and the install stage."""
    return (name in _SKIP_DIRS or name.endswith(".bak") or name.endswith(".tmp")
            or name.endswith(".part") or (top and name == STAGE))


def local_files(tree):
    """{path under `tree`: its full path} of every file a pull keeps there,
    the publish marker and the `.bak` it names included."""
    out = {}
    for d, dirs, files in os.walk(tree):
        top = d == tree
        dirs[:] = sorted(n for n in dirs if not _skipped(n, top))
        for n in sorted(files):
            if not _skipped(n, top) and not (top and n == MARKER):
                out[os.path.relpath(os.path.join(d, n), tree).replace(os.sep, "/")] = \
                    os.path.join(d, n)
    marker = os.path.join(tree, MARKER)
    if os.path.isfile(marker):
        out[MARKER] = marker
        with open(marker, encoding="utf-8", errors="replace") as f:
            bits = f.read(512).rstrip("\n").split(" ", 3)
        if len(bits) == 4:
            # The marker names a console path; its folder is the tree's root.
            tail = [p for p in bits[3].split("/") if p and p != ".."]
            for k in range(len(tail)):
                rel = "/".join(tail[k:]) + ".bak"
                if os.path.isfile(os.path.join(tree, rel)):
                    out[rel] = os.path.join(tree, rel)
                    break
    return out


def is_folder(tree):
    """Whether `tree` holds a moybyte folder: its store stamp or its carts."""
    return (os.path.isfile(os.path.join(tree, STAMP))
            or os.path.isdir(os.path.join(tree, "carts")))


def changed(theirs, ours):
    """The paths of `theirs` ({path: (size, sha12)}) whose file in `ours`
    ({path: full path}) is absent or differs in size or hash."""
    out = []
    for rel in sorted(theirs):
        size, sha = theirs[rel]
        mine = ours.get(rel)
        if mine is None or os.path.getsize(mine) != size or sha12(mine) != sha:
            out.append(rel)
    return out


def _prune(tree):
    for d, _dirs, _files in sorted(os.walk(tree), reverse=True):
        if d != tree and not os.listdir(d):
            os.rmdir(d)


def mirror(b, out, log=print):
    """The console's folder mirrored into `out`: (files copied, files
    removed)."""
    if os.path.isdir(out) and os.listdir(out) and not is_folder(out):
        sys.exit("%s holds something that is not a pulled folder; pull into an empty "
                 "or a pulled one" % out)
    install(b)
    base = console_base(b)
    log("listing %s on the console (every file read for its hash)" % base)
    theirs = listing(b, base, log)
    os.makedirs(out, exist_ok=True)
    ours = local_files(out)
    todo = changed(theirs, ours)
    total = sum(theirs[r][0] for r in todo)
    log("%d files on the console, %d KB in all; %d to copy, %d KB, over `py` at "
        "about %d KB/s: about %d s"
        % (len(theirs), sum(s for s, _h in theirs.values()) // 1024, len(todo),
           total // 1024, PY_RATE // 1024, total // PY_RATE))
    for rel in todo:
        dest = os.path.join(out, *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        log("  < %s (%d B)" % (rel, theirs[rel][0]))
        read_back(b, base + "/" + rel, dest, log)
    gone = []
    for d, _dirs, files in os.walk(out):
        for n in files:
            rel = os.path.relpath(os.path.join(d, n), out).replace(os.sep, "/")
            if rel not in theirs:
                gone.append(rel)
                os.remove(os.path.join(d, n))
    for rel in sorted(gone):
        log("  - %s" % rel)
    _prune(out)
    return len(todo), len(gone)


def pull(a):
    b = push_cart.connect(a.board, a.port, a.verbose)
    out = a.out or "moybyte-%s" % a.board
    try:
        n, gone = mirror(b, out)
    finally:
        b.close()
    print("pulled %d files into %s, %d removed from it" % (n, out, gone))


def put_tree(b, tree, link, log=print):
    """The files of `tree` that differ from the console's folder sent there:
    the folders first, the publish marker's files last. Returns the files
    sent."""
    install(b)
    base = console_base(b)
    ours = local_files(tree)
    theirs = listing(b, base, log)
    want = {r: (os.path.getsize(p), sha12(p)) for r, p in ours.items()}
    todo = [r for r in sorted(want) if theirs.get(r) != want[r]
            and not (r not in theirs and r.endswith(".bak") and b.pyval(
                "ws._g['_sha'](%r, %d)" % (base + "/" + r, want[r][0])) == want[r][1])]
    last = {MARKER} | {r for r in todo if r.endswith(".bak")}
    todo = [r for r in todo if r not in last] + [r for r in todo if r in last]
    need = sum(want[r][0] for r in todo)
    room = b.pyval("ws._g['_room'](%r)" % base, strict=True)
    if room is not None and need > room:
        sys.exit("the console's store has %d KB free and the tree's changed files take "
                 "%d KB" % (room // 1024, need // 1024))
    dirs = sorted({base + "/" + "/".join(r.split("/")[:k])
                   for r in todo for k in range(1, len(r.split("/")))},
                  key=lambda p: p.count("/"))
    while dirs:
        k = 1
        while k < len(dirs) and len(repr(dirs[:k + 1])) <= DIRS_LINE:
            k += 1
        b.pyval("ws._g['_mkdirs'](%r)" % dirs[:k], strict=True)
        dirs = dirs[k:]
    log("%d of the tree's %d files differ from the console's: %d KB to send"
        % (len(todo), len(want), need // 1024))
    sent = 0
    for rel in todo:
        if push_cart.push_file_raw(b, ours[rel], base + "/" + rel, link, False):
            sent += 1
    return sent


def push(a):
    if not os.path.isdir(a.tree) or not is_folder(a.tree):
        sys.exit("not a moybyte folder (no %s, no carts/): %s" % (STAMP, a.tree))
    b = push_cart.connect(a.board, a.port, a.verbose)
    try:
        link = push_cart.raw_link(b, push_cart.serial_cfg(a.board))
        n = put_tree(b, a.tree, link)
    finally:
        b.close()
    print("pushed %d files" % n)
    if a.no_reboot:
        print("the console reads them at its next boot")
        return
    import board as bd
    bd.cmd_reboot(a.board, bd.boards(), argparse.Namespace(
        port=None if a.port == "auto" else a.port, force=False, soft=False, timeout=180))
    print("the console booted on the pushed folder")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("verb", choices=("pull", "push"))
    ap.add_argument("tree", nargs="?", help="push: the folder to put back")
    ap.add_argument("--board", required=True, choices=sorted(push_cart.BOARDS),
                    help="which board's [serial] declaration to use (required)")
    ap.add_argument("--port", default="auto")
    ap.add_argument("--out", help="pull: the mirror (default: moybyte-<board>)")
    ap.add_argument("--no-reboot", action="store_true",
                    help="push: leave the console running; it reads the folder at its "
                         "next boot")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.verb == "push" and not a.tree:
        ap.error("push needs the folder")
    (pull if a.verb == "pull" else push)(a)


if __name__ == "__main__":
    main()
