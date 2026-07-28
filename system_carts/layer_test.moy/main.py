# Layer Test -- does the #54 scroll layer still pay for itself?
#
# A layer trades 75KB+ of RAM for a cheaper per-frame background: instead of
# re-running map() over the visible level every frame, pre-render the level once
# into a wide off-screen buffer and window-copy the visible part.
#
# The original measurement (#54) was ~12-14ms of map() against ~7ms of copy. That
# was taken BEFORE the moy_gfx -O3 pragma (#77) cut render ~40% on the S3, which
# sped up BOTH sides -- so the absolute saving shrank and nobody re-measured. If
# it is now ~3ms, 75KB is a bad trade and the layers extension is carrying its
# justification on a stale number.
#
# Note what this is NOT about: neither path runs in the interpreter. A scroller
# issues ONE verb call per frame either way, and the cost is pixels inside a C
# kernel. So the Lua tier's ~1.9x on arithmetic cannot move this number, and a
# faster cart language is not an argument for dropping layers.
#
# The cart alternates the two every 40 frames from a FIXED camera -- identical
# pixels, identical content, only the route differs -- and prints both averages
# and the ratio. RATIO > 1 means the layer is still winning.

CAM = 96                     # fixed scroll position: deterministic, mid-level
LW = 512                     # layer width in px (the map is 64 tiles = 512px)

lay = None
ab_mode = 0
ab_acc = [0, 0]
ab_cnt = [0, 0]
ab_txt = "MEASURING"
frames = 0
t_mark = 0
fps = 0


def _init():
    global lay, ab_mode, ab_acc, ab_cnt, ab_txt, frames, t_mark, fps

    # Build the layer ONCE -- this is the cost a layer front-loads, and the whole
    # point is that it never recurs.
    lay = make_layer(LW, H)
    lay.cls(0)
    lay.map(0, 0, LW // 8, H // 8, 0, 0)

    ab_mode = 0
    ab_acc = [0, 0]
    ab_cnt = [0, 0]
    ab_txt = "MEASURING"
    frames = 0
    t_mark = time()
    fps = 0


def _update(dt):
    pass                     # camera parked: both routes must draw the same pixels


def _tenths(total, n):
    v = total * 10 // n if n else 0
    return str(v // 10) + "." + str(v % 10)


def _draw():
    global ab_mode, ab_txt, frames, t_mark, fps

    t0 = time()
    if ab_mode:
        draw_layer(lay, CAM, 0)          # window-copy the pre-rendered level
    else:
        # Re-render the visible level: 41 tile columns covers 320px plus the
        # sub-tile offset, which is what a layerless scroller pays every frame.
        map(CAM // 8, 0, (W // 8) + 1, H // 8, -(CAM % 8), 0)
    ab_acc[ab_mode] = ab_acc[ab_mode] + (time() - t0)
    ab_cnt[ab_mode] = ab_cnt[ab_mode] + 1

    if ab_cnt[ab_mode] >= 40:
        if ab_cnt[0] >= 40 and ab_cnt[1] >= 40:
            ab_txt = ("MAP " + _tenths(ab_acc[0], ab_cnt[0])
                      + "  LAYER " + _tenths(ab_acc[1], ab_cnt[1]))
            if ab_acc[1]:
                ab_txt = ab_txt + "  " + _tenths(ab_acc[0], ab_acc[1]) + "X"
        ab_mode = 1 - ab_mode

    frames = frames + 1
    now = time()
    if now - t_mark >= 500:
        fps = frames * 1000 // (now - t_mark)
        frames = 0
        t_mark = now

    rect(0, 0, W, 30, 0)                 # HUD backdrop: the tiles are too busy
    print("FPS " + str(fps), 3, 3, 7)
    print(ab_txt, 3, 12, 10)
    print("N " + str(ab_cnt[0]) + "/" + str(ab_cnt[1]) + "  >1 = LAYER WINS",
          3, 21, 6)
