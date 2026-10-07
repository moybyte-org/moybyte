"""The dev channel's input words: `tap`, `swipe` and `drag`
(runtime/dev_channel.py reads the line; this file says what the words do).

Each word is `word(chan, ws, parts, line)` in WORDS, the table DevChannel
dispatches through; a word that returns False was not this one's after all,
and the line goes on to the channel's own table. A gesture plays back a
sample a frame in DevChannel._scripts. Every sample goes into the channel's own source
in the input table (`DevChannel.point`), so the frame's merge hands it to the
pointer exactly as it hands over a finger's.
"""


def tap(chan, ws, parts, line):
    r = None
    if len(parts) == 3:
        try:
            r = (int(parts[1]), int(parts[2]))
        except ValueError:
            r = None
    elif len(parts) == 2:
        rect = getattr(ws.layout, parts[1] + "_btn", None)
        if rect:
            r = (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
    if r is None:
        print("REMOTE ? %s" % line)
        return
    chan.point(r[0], r[1], True, True)
    chan._tap = [r[0], r[1], True]   # released by the frame after the press is merged
    print("REMOTE tap %d %d" % r)


def swipe(chan, ws, parts, line):
    if len(parts) < 5:
        return False        # not a gesture: the board's extras may know it
    # A synthetic touch gesture fed through the SAME pointer path as
    # the glass, so the harness can exercise scroll/drag/fling on any
    # surface. Playback is per-frame in _scripts().
    try:
        chan._swipe = {"i": 0,
                       "x0": int(parts[1]), "y0": int(parts[2]),
                       "x1": int(parts[3]), "y1": int(parts[4]),
                       "n": max(2, int(parts[5]))
                       if len(parts) > 5 else 20}
        print("REMOTE swipe %d,%d -> %d,%d frames=%d"
              % (chan._swipe["x0"], chan._swipe["y0"],
                 chan._swipe["x1"], chan._swipe["y1"],
                 chan._swipe["n"]))
    except ValueError:
        chan._swipe = None
        print("REMOTE swipe ? %s" % line)


def drag(chan, ws, parts, line):
    # Grab the TOP window's title strip and oscillate it for n frames,
    # so the PERF sampler reports DRAG-time fps. Windowed tier only --
    # a WM without windows declines, it does not traceback.
    order = getattr(ws.wm, "_order", None) or []
    if not order:
        print("REMOTE drag: no window open")
        return
    win = ws.wm._wins[order[-1]]
    n = 120
    step = 6
    if len(parts) >= 2:
        try:
            n = max(8, int(parts[1]))
        except ValueError:
            pass
    if len(parts) >= 3:
        try:
            step = max(1, int(parts[2]))  # px/frame amplitude scale
        except ValueError:
            pass
    chan._drag = {"i": 0, "n": n, "step": step,
                  "cx": win.x + 30,
                  "cy": win.y + max(6, win.title_h // 2)}
    print("REMOTE drag win=%s cx=%d cy=%d frames=%d step=%d"
          % (order[-1], chan._drag["cx"], chan._drag["cy"], n, step))


WORDS = {"tap": tap, "swipe": swipe, "drag": drag}
