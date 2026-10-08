"""The dev channel's input words: `tap`, `swipe` and `drag`
(runtime/dev_channel.py holds the console's words; this file says what these
do).

Each word is `word(chan, ws, parts, line)` in WORDS, the table DevChannel
dispatches through; a word that returns False was not this one's after all,
and the line goes on to the channel's own table. What a word finds is the
console's -- a named button, the top window's title strip -- and the gesture
it starts is the kernel's (native/moy_kernel/moy_devch.c): a sample a frame
into the channel's own source in the input table, so the frame's merge hands
it to the pointer exactly as it hands over a finger's.
"""

try:                        # a VM: the kernel's module
    import moy_loop as _loop
except ImportError:         # host CPython: the ctypes binding
    try:
        from runtime import moy_loop as _loop
    except ImportError:     # a tier whose frames are not the kernel's yet
        _loop = None


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
    if _loop is None:
        print("REMOTE tap: no kernel loop on this tier")
        return
    _loop.tap(r[0], r[1])            # released by the frame after the press is merged
    print("REMOTE tap %d %d" % r)


def swipe(chan, ws, parts, line):
    if len(parts) < 5:
        return False        # not a gesture: the board's extras may know it
    # A synthetic touch gesture fed through the SAME pointer path as
    # the glass, so the harness can exercise scroll/drag/fling on any
    # surface. Playback is per-frame in _scripts().
    try:
        x0, y0, x1, y1 = (int(parts[1]), int(parts[2]), int(parts[3]),
                          int(parts[4]))
        n = max(2, int(parts[5])) if len(parts) > 5 else 20
    except ValueError:
        print("REMOTE swipe ? %s" % line)
        return
    if _loop is None:
        print("REMOTE swipe: no kernel loop on this tier")
        return
    _loop.swipe(x0, y0, x1, y1, n)
    print("REMOTE swipe %d,%d -> %d,%d frames=%d" % (x0, y0, x1, y1, n))


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
    cx = win.x + 30
    cy = win.y + max(6, win.title_h // 2)
    if _loop is None:
        print("REMOTE drag: no kernel loop on this tier")
        return
    _loop.drag(cx, cy, n, step)
    print("REMOTE drag win=%s cx=%d cy=%d frames=%d step=%d"
          % (order[-1], cx, cy, n, step))


WORDS = {"tap": tap, "swipe": swipe, "drag": drag}
