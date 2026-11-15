"""The dev channel's link words: `link`, `web`, `recv`, the store's
`backup` and `restore` (runtime/carts/moy_backup.py), and the tier-1
sideload lines `moy?`, `moy-put`, `moy-del`, `moy-rescan` and `moy-run` (see
runtime/boot/devch_input.py for the shape of a word). `recv` and `moy-put` leave
the line discipline, so the byte readers they hand the stream to stay the
channel's (DevChannel._recv, DevChannel._moy_put).
"""


def link(chan, ws, parts, line):
    # `link` alone reports; `link on|off` arms the radio by hand, which
    # is what a two-board bench needs -- the Player only arms it for a
    # cart that declares the multiplayer permission. Like the Player's, the
    # arm holds the radio's "link" row, which the off lets go.
    lk = getattr(ws, "link", None)
    if lk is None:
        print("REMOTE link: no radio on this board")
        return
    action = parts[1] if len(parts) > 1 else ""
    if action == "on":
        ws.spine.wifi_hold("link")
        lk.start()
    elif action == "off":
        lk.stop()
        ws.spine.wifi_release("link")
    elif action == "rate" and len(parts) > 2:
        lk.rate_force = int(parts[2]) if parts[2] in ("30", "60") else None
    elif action == "check" and len(parts) > 2:
        lk.check_worlds(parts[2] == "on")
    elif action == "cart" and len(parts) > 2:
        lk.announce(" ".join(parts[2:]), 1)
    import json
    print("LINK %s" % json.dumps(lk.stats()))


def _settle(wh, limit_ms=25000):
    """The dev word answers with the outcome, so it waits out the join the
    Settings row never waits for: the switch's poll, the watchdog fed."""
    import time
    sleep_ms = getattr(time, "sleep_ms", None)
    waited = 0
    while getattr(wh, "joining", False) and waited < limit_ms:
        wh.poll()
        try:
            import moy_ota
            moy_ota.keep_alive()
        except ImportError:
            pass
        if sleep_ms is not None:
            sleep_ms(100)
        else:
            time.sleep(0.1)
        waited += 100
    wh.poll()


def web(chan, ws, parts, line):
    # Serve the wasm console FROM this board (moy_webhost), which since
    # #197 also parks the glass on the connection screen. The PAIRED url
    # is what gets printed -- its token pairs the browser that opens it, so
    # a bare address would be an address that signs nothing in.
    try:
        wh = getattr(ws, "webhost", None)
        if wh is None:
            print("WEB no service")
        else:
            if not wh.serving and not getattr(wh, "joining", False):
                ws.web.toggle()
            _settle(wh)
            url = ws.web.url() or wh.url()
            print("WEB %s %s" % (url, wh.error or ""))
    except Exception as exc:  # noqa: BLE001
        print("WEB ERR %s: %s" % (type(exc).__name__, exc))


def recv(chan, ws, parts, line):
    # The one command that leaves the line discipline: everything after
    # its newline is payload, not commands. See _recv.
    chan._recv(line, parts, ws)


def moy(chan, ws, parts, line):
    return chan._moy(ws, parts[0], parts, line) or False


def backup(chan, ws, parts, line):
    # `backup` writes the store's archive into the user files' Backups/ and
    # names it: `REMOTE backup <path> <files>` (tools/console_backup.py reads it).
    root = getattr(ws, "carts_root", None)
    if root is None:
        print("REMOTE backup: no store")
        return
    import moy_backup
    try:
        path, n = moy_backup.archive(root)
    except (OSError, MemoryError) as exc:
        print("REMOTE backup: failed %r" % (exc,))
        return
    print("REMOTE backup %s %d" % (path, n))


def restore(chan, ws, parts, line):
    # `restore <path>`: the archive at `path` on the console restored into its
    # store, then the shelf read again.
    root = getattr(ws, "carts_root", None)
    if root is None or len(parts) < 2:
        print("REMOTE restore: needs a store and an archive's path")
        return
    import moy_backup
    try:
        n = moy_backup.restore(line.split(None, 1)[1].strip(), root)
    except (OSError, ValueError, MemoryError) as exc:
        print("REMOTE restore: failed %r" % (exc,))
        return
    print("REMOTE restore %d" % n)
    rescan = getattr(getattr(ws, "carts", None), "synced", None)
    if rescan is not None:
        rescan()


WORDS = {"link": link, "web": web, "recv": recv, "backup": backup, "restore": restore,
         "moy?": moy, "moy-put": moy, "moy-del": moy, "moy-rescan": moy, "moy-run": moy}
