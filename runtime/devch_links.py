"""The dev channel's link words: `link`, `web`, `recv` and the tier-1
sideload lines `moy?`, `moy-put`, `moy-del`, `moy-rescan` and `moy-run` (see
runtime/devch_input.py for the shape of a word). `recv` and `moy-put` leave
the line discipline, so the byte readers they hand the stream to stay the
channel's (DevChannel._recv, DevChannel._moy_put).
"""


def link(chan, ws, parts, line):
    # `link` alone reports; `link on|off` arms the radio by hand, which
    # is what a two-board bench needs -- the Player only arms it for a
    # cart that declares the multiplayer permission.
    lk = getattr(ws, "link", None)
    if lk is None:
        print("REMOTE link: no radio on this board")
        return
    action = parts[1] if len(parts) > 1 else ""
    if action == "on":
        lk.start()
    elif action == "off":
        lk.stop()
    elif action == "cart" and len(parts) > 2:
        lk.announce(" ".join(parts[2:]), 1)
    import json
    print("LINK %s" % json.dumps(lk.stats()))


def web(chan, ws, parts, line):
    # Serve the wasm console FROM this board (moy_webhost), which since
    # #197 also parks the glass on the connection screen. The PAIRED url
    # is what gets printed -- the pin is what the page must carry to
    # write anything back, so a bare address would be an address that
    # syncs nothing and says nothing about why.
    try:
        wh = getattr(ws, "webhost", None)
        if wh is None:
            print("WEB no service")
        else:
            if not wh.serving:
                ws.toggle_webhost()
            url = ws.web_console_url() or wh.url()
            print("WEB %s %s" % (url, wh.error or ""))
    except Exception as exc:  # noqa: BLE001
        print("WEB ERR %s: %s" % (type(exc).__name__, exc))


def recv(chan, ws, parts, line):
    # The one command that leaves the line discipline: everything after
    # its newline is payload, not commands. See _recv.
    chan._recv(line, parts, ws)


def moy(chan, ws, parts, line):
    return chan._moy(ws, parts[0], parts, line) or False


WORDS = {"link": link, "web": web, "recv": recv, "moy?": moy, "moy-put": moy,
         "moy-del": moy, "moy-rescan": moy, "moy-run": moy}
