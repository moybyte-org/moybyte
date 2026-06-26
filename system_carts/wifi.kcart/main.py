# WiFi -- the v0.4 NETWORK manager cartridge (#38).
#
# This is a SYSTEM cart: its manifest grants "network", so the console injects a
# `wifi` object into this cart's namespace (a normal kid cart never gets it -- the
# sandbox is preserved). The UI is 100% ordinary cart verbs (cls/rect/print/touch/
# key) driving that injected service: scan, pick a network, type the password on
# the keyboard, CONNECT/FORGET, and a status line with the connected SSID + IP.
#
# The connection is system state -- it persists after this cart closes, so the web
# editor (#22) and AI helper (#8) reuse it. Same code runs on host (FakeWifi) and
# device (network.WLAN); only the injected backend differs.

# Screens: "list" picks a network; "pass" types the password for the picked one.
mode = "list"
nets = []          # [(ssid, signal, locked), ...] from the last scan
sel = 0            # selected row in the list
pick = None        # ssid currently being connected to (in "pass" mode)
pw = ""            # password being typed
msg = ""           # one-line feedback ("CONNECTED", "TYPE PASSWORD", ...)
known_set = []     # remembered SSIDs (for the SAVED marker)
_kprev = 0         # last keyboard byte (edge detect for typing)

ROW_Y0 = 56
ROW_H = 22
ROW_N = 5          # visible network rows
CONNECT_BTN = (8, 208, 96, 26)
FORGET_BTN = (112, 208, 96, 26)
RESCAN_BTN = (216, 208, 96, 26)


def _has_wifi():
    # Defensive: if this cart is ever run without the network permission it gets
    # no `wifi` name -- show a clear message instead of crashing.
    return "wifi" in globals() and wifi is not None


def _rescan():
    global nets, sel, known_set, msg
    if not _has_wifi():
        msg = "NO NETWORK PERMISSION"
        return
    nets = wifi.scan()
    known_set = wifi.known()
    if sel >= len(nets):
        sel = max(0, len(nets) - 1)
    msg = "TAP A NETWORK"


def _init():
    global mode, pw, pick, msg
    mode = "list"
    pw = ""
    pick = None
    _rescan()


def _row_rect(i):
    return (8, ROW_Y0 + i * ROW_H, 304, ROW_H - 2)


def _in(px, py, r):
    x, y, w, h = r
    return x <= px < x + w and y <= py < y + h


def _begin_pass(ssid):
    global mode, pick, pw, msg
    mode = "pass"
    pick = ssid
    pw = ""
    msg = "TYPE PASSWORD, ENTER"


def _do_connect(ssid, password):
    global mode, msg, known_set
    ok = wifi.connect(ssid, password)
    known_set = wifi.known()
    if ok:
        msg = "CONNECTED"
    else:
        msg = "CONNECT FAILED"
    mode = "list"


def _do_forget():
    global msg, known_set
    if not nets:
        return
    ssid = nets[sel][0]
    wifi.forget(ssid)
    known_set = wifi.known()
    msg = "FORGOT " + ssid


def _type_password(dt):
    # Capture keyboard bytes into the password. The console reports one byte per
    # frame via key(); act on the press edge so a held key inserts once.
    global pw, _kprev, mode
    k = key()
    if k and k != _kprev:
        if k in (10, 13):                 # enter -> connect
            _do_connect(pick, pw)
        elif k in (8, 127):               # backspace
            pw = pw[:-1]
        elif k == 27:                     # esc -> back to list
            mode = "list"
        elif 32 <= k <= 126 and len(pw) < 32:
            pw = pw + chr(k)
    _kprev = k


def _update(dt):
    global sel, mode
    if not _has_wifi():
        return
    if mode == "pass":
        _type_password(dt)
        return
    # list mode: trackball/keys move the selection; touch picks/hits buttons.
    if btnp("up"):
        sel = max(0, sel - 1)
    if btnp("down"):
        sel = min(max(0, len(nets) - 1), sel + 1)
    tp = touch()
    if tp is not None and tp[2]:
        x, y = tp[0], tp[1]
        if _in(x, y, CONNECT_BTN) and nets:
            ssid, _sig, locked = nets[sel]
            if locked:
                _begin_pass(ssid)         # locked AP -> ask for the password
            else:
                _do_connect(ssid, "")     # open AP -> join straight away
        elif _in(x, y, FORGET_BTN):
            _do_forget()
        elif _in(x, y, RESCAN_BTN):
            _rescan()
        else:
            for i in range(min(ROW_N, len(nets))):
                if _in(x, y, _row_rect(i)):
                    sel = i


def _draw():
    cls(col("dark_blue"))
    print("WIFI", 8, 8, col("white"), 2)

    # status line: connected SSID + IP (the live link other features use).
    if _has_wifi():
        connected, ssid, ip = wifi.status()
    else:
        connected, ssid, ip = (False, None, None)
    if connected:
        print("ONLINE " + str(ssid) + "  " + str(ip), 8, 30, col("green"), 1)
    else:
        print("OFFLINE", 8, 30, col("light_grey"), 1)

    if not _has_wifi():
        print("NO NETWORK PERMISSION", 8, 120, col("red"), 2)
        return

    if mode == "pass":
        _draw_password()
        return

    # network list
    if not nets:
        print("NO NETWORKS FOUND", 8, ROW_Y0, col("light_grey"), 2)
    for i in range(min(ROW_N, len(nets))):
        x, y, w, h = _row_rect(i)
        name, sig, locked = nets[i]
        on = (i == sel)
        rect(x, y, w, h, col("dark_purple") if on else col("black"))
        rectb(x, y, w, h, col("yellow") if on else col("dark_grey"))
        print(name, x + 6, y + 6, col("white"), 1)
        tag = ""
        if locked:
            tag += "LOCK "
        if name in known_set:
            tag += "SAVED "
        tag += str(sig) + "%"
        print(tag, x + w - len(tag) * 8 - 6, y + 6, col("peach"), 1)

    _btn("CONNECT", CONNECT_BTN, "green")
    _btn("FORGET", FORGET_BTN, "red")
    _btn("RESCAN", RESCAN_BTN, "blue")
    if msg:
        print(msg, 8, 192, col("yellow"), 1)


def _draw_password():
    print("NETWORK: " + str(pick), 8, ROW_Y0, col("white"), 1)
    print("PASSWORD", 8, ROW_Y0 + 22, col("light_grey"), 1)
    bx, by, bw, bh = (8, ROW_Y0 + 36, 304, 24)
    rect(bx, by, bw, bh, col("black"))
    rectb(bx, by, bw, bh, col("white"))
    # mask the password with dots; a kid can't read it back but sees the length.
    print("*" * len(pw), bx + 6, by + 8, col("green"), 1)
    print("ENTER = CONNECT   ESC = BACK", 8, by + 40, col("light_grey"), 1)
    if msg:
        print(msg, 8, 192, col("yellow"), 1)


def _btn(label, r, fill):
    x, y, w, h = r
    rect(x, y, w, h, col(fill))
    rectb(x, y, w, h, col("white"))
    print(label, x + 8, y + (h - 8) // 2, col("black"), 1)
