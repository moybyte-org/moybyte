#!/usr/bin/env python3
"""Pull a console's backup archive to this machine, or push one back and
restore it, over the serial console (#127).

    python tools/console_backup.py pull --board tdeck [--out backup.zip]
    python tools/console_backup.py push backup.zip --board p4

`pull` asks the console for a fresh archive (the dev channel's `backup`,
runtime/carts/moy_backup.py's `archive`: it lands in the user files'
Backups/) and reads it back a piece at a time over `py`. `push` sends an
archive with the raw receive tools/push_cart.py uses, into the console's
Backups/, and restores it there (`restore`, `moy_backup.restore`), which
reads the shelf again. The board's line state and identity check are
push_cart's (`connect`); --board is required for the same reason.
"""

import argparse
import binascii
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_cart                                                 # noqa: E402

PIECE = 768          # archive bytes a `py` answer carries (base64 under a line)


def remote_line(b, word, timeout=600.0):
    """The console's answer to `word`: the rest of its REMOTE line, or exits
    naming what it said."""
    line = b.cmd(word, wait_for="REMOTE " + word.split()[0], timeout=timeout, retry=False)
    if line is None:
        sys.exit("no answer to %r" % word)
    rest = line.split("REMOTE " + word.split()[0], 1)[1]
    if rest.startswith(":"):
        sys.exit("the console said: %s" % rest[1:].strip())
    return rest.strip()


def read_back(b, path, out, log=print):
    """The console's file `path` written to `out`, a PIECE at a time."""
    if b.pyval("ws.__dict__.__setitem__('_bk', open(%r, 'rb')) or 1" % path) != 1:
        sys.exit("the console cannot open %s" % path)
    got = 0
    try:
        with open(out, "wb") as f:
            while True:
                text = b.pyval("__import__('binascii').b2a_base64(ws._bk.read(%d))"
                               ".decode().strip()" % PIECE, strict=True)
                data = binascii.a2b_base64(text) if text else b""
                if not data:
                    break
                f.write(data)
                got += len(data)
                if got % (PIECE * 64) < PIECE:
                    log("  %d KB" % (got // 1024))
    finally:
        b.pyval("ws._bk.close() or 1")
    return got


def pull(a):
    b = push_cart.connect(a.board, a.port, a.verbose)
    try:
        path, n = remote_line(b, "backup").rsplit(" ", 1)
        print("the console wrote %s (%s files)" % (path, n))
        out = a.out or os.path.basename(path)
        got = read_back(b, path, out)
        print("pulled %d bytes to %s" % (got, out))
    finally:
        b.close()


def push(a):
    if not os.path.isfile(a.archive):
        sys.exit("no archive: " + a.archive)
    b = push_cart.connect(a.board, a.port, a.verbose)
    try:
        where = b.pyval("__import__('moy_backup').backups_dir(ws.carts_root)",
                        strict=True)
        b.pyval("[__import__('moy_fs')._mkdir(p) for p in (%r, %r)] and 1"
                % (where.rsplit("/", 1)[0], where), strict=True)
        dest = where + "/" + os.path.basename(a.archive)
        link = push_cart.raw_link(b, push_cart.serial_cfg(a.board))
        if not push_cart.push_file_raw(b, a.archive, dest, link, a.verbose):
            sys.exit("the push did not land")
        print("restored %s files" % remote_line(b, "restore " + dest))
    finally:
        b.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("verb", choices=("pull", "push"))
    ap.add_argument("archive", nargs="?",
                    help="push: the backup archive to send (moybyte-backup.zip)")
    ap.add_argument("--board", required=True, choices=sorted(push_cart.BOARDS),
                    help="which board's [serial] declaration to use (required)")
    ap.add_argument("--port", default="auto")
    ap.add_argument("--out", help="pull: where the archive lands (default: its name)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.verb == "push" and not a.archive:
        ap.error("push needs the archive")
    (pull if a.verb == "pull" else push)(a)


if __name__ == "__main__":
    main()
