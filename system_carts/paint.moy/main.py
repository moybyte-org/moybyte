# Paint -- a full-canvas drawing studio for Moybyte.
#
# The document is always a game-ready 320x240 MOY64 indexed image. FIT shows the
# whole picture at 1:2 and paints chunky 2x2 pixels; DETAIL shows a panned 1:1
# window for precise work. The shell injects `artwork` only into this shipped app:
# it persists the document, publishes My Art as a wallpaper, and copies the image
# into a selected project's images/bg.moyimg.

ART_W = 320
ART_H = 240
ART_N = ART_W * ART_H
PAPER = 7

# Screen geometry. The shell owns y=0..17 (the app bar).
TOP_Y = 20
TOP_H = 23
VIEW = (38, 47, 218, 171)
INNER = (40, 49, 214, 167)
FIT_X = 67
FIT_Y = 72
FIT_W = 160
FIT_H = 120
STATUS_Y = 225

TOOLS = ("PENCIL", "BRUSH", "ERASER", "FILL", "PICK",
         "LINE", "BOX", "CIRCLE", "SPRAY", "PAN")
TOOL_GLYPHS = ("P", "B", "E", "F", "I", "/", "#", "O", "*", "+")
TOOL_RECTS = []
for _i in range(10):
    TOOL_RECTS.append((3 + (_i & 1) * 16, 50 + (_i // 2) * 20, 15, 18))

NEW_BTN = (4, TOP_Y, 20, TOP_H)
UNDO_BTN = (26, TOP_Y, 20, TOP_H)
REDO_BTN = (48, TOP_Y, 20, TOP_H)
SAVE_BTN = (70, TOP_Y, 36, TOP_H)
WALL_BTN = (108, TOP_Y, 36, TOP_H)
GAME_BTN = (146, TOP_Y, 36, TOP_H)
SHOW_BTN = (184, TOP_Y, 36, TOP_H)
VIEW_BTN = (222, TOP_Y, 34, TOP_H)

PAL_X = 262
PAL_Y = 50
PAL_CELL = 13
PAL_PAGE_BTN = (262, 104, 52, 18)
SIZE_BTNS = ((262, 128, 16, 18), (280, 128, 16, 18), (298, 128, 16, 18))
FILL_BTN = (262, 150, 52, 18)
PAN_UP = (280, 174, 16, 16)
PAN_LEFT = (262, 192, 16, 16)
PAN_RIGHT = (298, 192, 16, 16)
PAN_DOWN = (280, 192, 16, 16)

pixels = bytearray(ART_N)
pixels[:] = bytes((PAPER,)) * ART_N
thumb_pix = bytearray(FIT_W * FIT_H)
art_img = None
thumb_img = None

tool = 0
color = 12
pal_page = 0
brush_size = 1
shape_fill = False
fit_view = True
pan_x = 53
pan_y = 36

history = []
future = []
action_live = False
stroke_last = None
shape_start = None
shape_now = None
pan_last = None
new_armed = False
mode = "paint"
status = "READY"
project_top = 0
project_names = ()


def _in(x, y, r):
    return r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]


def _clamp(v, lo, hi):
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _button(label, r, on=False, ink=0):
    x, y, w, h = r
    fill = 10 if on else 6
    rect(x, y, w, h, fill)
    rectb(x, y, w, h, 7 if on else 5)
    tw = len(label) * 8
    print(label, x + max(2, (w - tw) // 2), y + (h - 8) // 2, ink)


def _invalidate(img):
    if img is None:
        return
    try:
        img._rgb_i = None
    except Exception:
        pass
    try:
        img._rgb = None
    except Exception:
        pass
    try:
        img._variants = {}
    except Exception:
        pass


def _rebuild_thumb():
    for ty in range(FIT_H):
        src = (ty * 2) * ART_W
        dst = ty * FIT_W
        for tx in range(FIT_W):
            thumb_pix[dst + tx] = pixels[src + tx * 2]
    _invalidate(thumb_img)


def _restore(data):
    pixels[:] = data
    _invalidate(art_img)
    _rebuild_thumb()


def _snapshot():
    global history, future, action_live
    if action_live:
        return
    history.append(bytes(pixels))
    if len(history) > 3:
        history.pop(0)
    future = []
    action_live = True


def _finish_action():
    global action_live, stroke_last, shape_start, shape_now, pan_last
    action_live = False
    stroke_last = None
    shape_start = None
    shape_now = None
    pan_last = None


def _undo():
    global history, future, status
    if not history:
        status = "NOTHING TO UNDO"
        return
    future.append(bytes(pixels))
    if len(future) > 3:
        future.pop(0)
    _restore(history.pop())
    status = "UNDO"


def _redo():
    global history, future, status
    if not future:
        status = "NOTHING TO REDO"
        return
    history.append(bytes(pixels))
    if len(history) > 3:
        history.pop(0)
    _restore(future.pop())
    status = "REDO"


def _set_pixel(x, y, c):
    if x < 0 or y < 0 or x >= ART_W or y >= ART_H:
        return
    i = y * ART_W + x
    c = int(c) & 63
    if pixels[i] == c:
        return
    pixels[i] = c
    thumb_pix[(y // 2) * FIT_W + (x // 2)] = c


def _stamp(x, y, c, radius):
    radius = max(0, int(radius))
    if radius == 0:
        _set_pixel(x, y, c)
        return
    rr = radius * radius
    for yy in range(y - radius, y + radius + 1):
        dy = yy - y
        for xx in range(x - radius, x + radius + 1):
            dx = xx - x
            if dx * dx + dy * dy <= rr:
                _set_pixel(xx, yy, c)


def _line_pixels(x0, y0, x1, y1, c, radius=0):
    dx = x1 - x0 if x1 >= x0 else x0 - x1
    dy = y1 - y0 if y1 >= y0 else y0 - y1
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        _stamp(x0, y0, c, radius)
        if x0 == x1 and y0 == y1:
            break
        e2 = err + err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


def _rect_pixels(a, b, c):
    x0 = min(a[0], b[0])
    y0 = min(a[1], b[1])
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    if shape_fill:
        for y in range(y0, y1 + 1):
            _line_pixels(x0, y, x1, y, c)
    else:
        r = max(0, brush_size - 1)
        _line_pixels(x0, y0, x1, y0, c, r)
        _line_pixels(x1, y0, x1, y1, c, r)
        _line_pixels(x1, y1, x0, y1, c, r)
        _line_pixels(x0, y1, x0, y0, c, r)


def _circle_pixels(a, b, c):
    cx, cy = a
    dx = b[0] - cx
    dy = b[1] - cy
    r = int((dx * dx + dy * dy) ** 0.5)
    if r < 1:
        _set_pixel(cx, cy, c)
        return
    if shape_fill:
        for yy in range(-r, r + 1):
            span = int((r * r - yy * yy) ** 0.5)
            _line_pixels(cx - span, cy + yy, cx + span, cy + yy, c)
        return
    x = r
    y = 0
    err = 0
    thick = max(0, brush_size - 1)
    while x >= y:
        for px, py in ((x, y), (y, x), (-y, x), (-x, y),
                       (-x, -y), (-y, -x), (y, -x), (x, -y)):
            _stamp(cx + px, cy + py, c, thick)
        y += 1
        if err <= 0:
            err += 2 * y + 1
        else:
            x -= 1
            err -= 2 * x + 1


def _flood(x, y, c):
    old = pixels[y * ART_W + x]
    c = int(c) & 63
    if old == c:
        return
    stack = [(x, y)]
    while stack:
        sx, sy = stack.pop()
        left = sx
        while left > 0 and pixels[sy * ART_W + left - 1] == old:
            left -= 1
        right = sx
        while right + 1 < ART_W and pixels[sy * ART_W + right + 1] == old:
            right += 1
        for xx in range(left, right + 1):
            _set_pixel(xx, sy, c)
        if sy > 0:
            xx = left
            while xx <= right:
                if pixels[(sy - 1) * ART_W + xx] == old:
                    stack.append((xx, sy - 1))
                    while xx <= right and pixels[(sy - 1) * ART_W + xx] == old:
                        xx += 1
                xx += 1
        if sy + 1 < ART_H:
            xx = left
            while xx <= right:
                if pixels[(sy + 1) * ART_W + xx] == old:
                    stack.append((xx, sy + 1))
                    while xx <= right and pixels[(sy + 1) * ART_W + xx] == old:
                        xx += 1
                xx += 1


def _screen_to_art(x, y):
    if fit_view:
        if not (FIT_X <= x < FIT_X + FIT_W and FIT_Y <= y < FIT_Y + FIT_H):
            return None
        return ((x - FIT_X) * 2, (y - FIT_Y) * 2)
    if not _in(x, y, INNER):
        return None
    ax = pan_x + x - INNER[0]
    ay = pan_y + y - INNER[1]
    if ax < 0 or ay < 0 or ax >= ART_W or ay >= ART_H:
        return None
    return (ax, ay)


def _art_to_screen(p):
    if fit_view:
        return (FIT_X + p[0] // 2, FIT_Y + p[1] // 2)
    return (INNER[0] + p[0] - pan_x, INNER[1] + p[1] - pan_y)


def _pan(dx, dy):
    global pan_x, pan_y
    pan_x = _clamp(pan_x + int(dx), 0, ART_W - INNER[2])
    pan_y = _clamp(pan_y + int(dy), 0, ART_H - INNER[3])


def _save():
    global status
    if "artwork" not in globals() or not artwork.available():
        status = "STORAGE OFF"
        return False
    if artwork.save(pixels, ART_W, ART_H):
        status = "SAVED"
        return True
    status = artwork.last_error or "SAVE FAILED"
    return False


def _publish_wall():
    global status
    if not _save():
        return
    if artwork.set_wallpaper():
        status = "WALLPAPER SET"
    else:
        status = artwork.last_error or "WALL FAILED"


def _open_projects():
    global mode, project_names, project_top, status
    if not _save():
        return
    project_names = artwork.targets()
    project_top = 0
    mode = "projects"
    status = "CHOOSE A PROJECT"


def _clear_canvas():
    global status, new_armed
    _snapshot()
    pixels[:] = bytes((PAPER,)) * ART_N
    _invalidate(art_img)
    _rebuild_thumb()
    _finish_action()
    new_armed = False
    status = "NEW DRAWING"


def _toolbar_tap(x, y):
    global fit_view, pal_page, brush_size, shape_fill, mode, new_armed, status
    global tool, color
    if _in(x, y, NEW_BTN):
        if new_armed:
            _clear_canvas()
        else:
            new_armed = True
            status = "TAP N AGAIN"
        return True
    new_armed = False
    if _in(x, y, UNDO_BTN):
        _undo()
    elif _in(x, y, REDO_BTN):
        _redo()
    elif _in(x, y, SAVE_BTN):
        _save()
    elif _in(x, y, WALL_BTN):
        _publish_wall()
    elif _in(x, y, GAME_BTN):
        _open_projects()
    elif _in(x, y, SHOW_BTN):
        mode = "show"
    elif _in(x, y, VIEW_BTN):
        fit_view = not fit_view
        status = "FIT VIEW" if fit_view else "DETAIL VIEW"
    else:
        for i in range(len(TOOL_RECTS)):
            if _in(x, y, TOOL_RECTS[i]):
                tool = i
                status = TOOLS[i]
                return True
        for i in range(16):
            r = (PAL_X + (i & 3) * PAL_CELL,
                 PAL_Y + (i // 4) * PAL_CELL, PAL_CELL, PAL_CELL)
            if _in(x, y, r):
                color = pal_page * 16 + i
                status = "COLOR " + str(color)
                return True
        if _in(x, y, PAL_PAGE_BTN):
            pal_page = (pal_page + 1) & 3
            status = "PALETTE " + str(pal_page + 1) + "/4"
        elif _in(x, y, SIZE_BTNS[0]):
            brush_size = 1
        elif _in(x, y, SIZE_BTNS[1]):
            brush_size = 2
        elif _in(x, y, SIZE_BTNS[2]):
            brush_size = 4
        elif _in(x, y, FILL_BTN):
            shape_fill = not shape_fill
        elif _in(x, y, PAN_UP):
            _pan(0, -16)
        elif _in(x, y, PAN_DOWN):
            _pan(0, 16)
        elif _in(x, y, PAN_LEFT):
            _pan(-16, 0)
        elif _in(x, y, PAN_RIGHT):
            _pan(16, 0)
        else:
            return False
    return True


def _paint_touch(x, y, tapped, held):
    global stroke_last, shape_start, shape_now, pan_last, status, color
    if tapped and _toolbar_tap(x, y):
        return
    p = _screen_to_art(x, y)
    if tool == 9:  # PAN reads screen deltas, including over the canvas border.
        if tapped:
            pan_last = (x, y)
        elif held and pan_last is not None:
            _pan(pan_last[0] - x, pan_last[1] - y)
            pan_last = (x, y)
        return
    if p is None:
        return
    factor = 2 if fit_view else 1
    if tapped:
        if tool == 4:  # picker never enters history
            color = pixels[p[1] * ART_W + p[0]]
            status = "PICKED " + str(color)
            return
        _snapshot()
        if tool == 3:
            _flood(p[0], p[1], color)
            _invalidate(art_img)
            _invalidate(thumb_img)
            _finish_action()
            status = "FILLED"
        elif tool in (5, 6, 7):
            shape_start = p
            shape_now = p
        else:
            stroke_last = p
            _stroke_to(p, factor)
        return
    if held:
        if tool in (5, 6, 7) and shape_start is not None:
            shape_now = p
        elif stroke_last is not None:
            _stroke_to(p, factor)


def _stroke_to(p, factor):
    global stroke_last
    c = PAPER if tool == 2 else color
    radius = 0
    if tool == 1:
        radius = brush_size * factor
    elif tool == 2:
        radius = (brush_size + 1) * factor
    elif tool == 0:
        radius = max(0, brush_size * factor - 1)
    if tool == 8:
        count = 5 + brush_size * 3
        spread = 4 + brush_size * 3
        for _i in range(count):
            ox = int(rnd(spread * 2 + 1)) - spread
            oy = int(rnd(spread * 2 + 1)) - spread
            if ox * ox + oy * oy <= spread * spread:
                _set_pixel(p[0] + ox, p[1] + oy, color)
    else:
        _line_pixels(stroke_last[0], stroke_last[1], p[0], p[1], c, radius)
    stroke_last = p
    _invalidate(art_img)
    _invalidate(thumb_img)


def _release_touch():
    global status
    if not action_live:
        _finish_action()
        return
    if shape_start is not None and shape_now is not None:
        if tool == 5:
            _line_pixels(shape_start[0], shape_start[1],
                         shape_now[0], shape_now[1], color, max(0, brush_size - 1))
        elif tool == 6:
            _rect_pixels(shape_start, shape_now, color)
        elif tool == 7:
            _circle_pixels(shape_start, shape_now, color)
        _invalidate(art_img)
        _invalidate(thumb_img)
    _finish_action()
    status = "DRAWING CHANGED"


def _project_touch(x, y):
    global mode, project_top, status
    if y < 44:
        mode = "paint"
        status = "BACK TO PAINT"
        return
    for row in range(7):
        idx = project_top + row
        r = (10, 48 + row * 23, 300, 20)
        if idx < len(project_names) and _in(x, y, r):
            name = artwork.attach(idx)
            if name:
                status = "BG ADDED TO " + name
                mode = "paint"
            else:
                status = artwork.last_error or "COPY FAILED"
            return
    if _in(x, y, (10, 214, 72, 20)):
        project_top = max(0, project_top - 7)
    elif _in(x, y, (238, 214, 72, 20)):
        project_top = min(max(0, len(project_names) - 7), project_top + 7)


def _init():
    global pixels, art_img, thumb_img, status
    loaded = artwork.load() if "artwork" in globals() else None
    if loaded is not None and loaded[0] == ART_W and loaded[1] == ART_H:
        pixels[:] = loaded[2]
        status = "DRAWING LOADED"
    else:
        status = "NEW DRAWING"
    art_img = Image(ART_W, ART_H, pixels, -1)
    art_img._paint = True
    thumb_img = Image(FIT_W, FIT_H, thumb_pix, -1)
    thumb_img._paint = True
    _rebuild_thumb()


def _update(dt):
    global mode, fit_view
    tp = touch()
    if mode == "show":
        if (tp is not None and tp[2]) or btnp("a") or btnp("b"):
            mode = "paint"
        return
    if mode == "projects":
        if tp is not None and tp[2]:
            _project_touch(int(tp[0]), int(tp[1]))
        if btnp("b"):
            mode = "paint"
        return

    if btnp("left"):
        _pan(-12, 0)
    if btnp("right"):
        _pan(12, 0)
    if btnp("up"):
        _pan(0, -12)
    if btnp("down"):
        _pan(0, 12)
    if btnp("b"):
        fit_view = not fit_view

    if tp is None:
        _release_touch()
        return
    x, y, tapped, held = int(tp[0]), int(tp[1]), bool(tp[2]), bool(tp[3])
    if tapped or held:
        _paint_touch(x, y, tapped, held)
    else:
        _release_touch()


def _draw_art_view():
    rect(VIEW[0], VIEW[1], VIEW[2], VIEW[3], 0)
    rectb(VIEW[0], VIEW[1], VIEW[2], VIEW[3], 48)
    clip(INNER[0], INNER[1], INNER[2], INNER[3])
    if fit_view:
        # Crisp whole-document view; a screen pixel represents a 2x2 art block.
        spr(thumb_img, FIT_X, FIT_Y)
        rectb(FIT_X - 1, FIT_Y - 1, FIT_W + 2, FIT_H + 2, 6)
    else:
        spr(art_img, INNER[0] - pan_x, INNER[1] - pan_y)
    clip()
    _draw_shape_preview()


def _draw_shape_preview():
    if shape_start is None or shape_now is None:
        return
    a = _art_to_screen(shape_start)
    b = _art_to_screen(shape_now)
    clip(INNER[0], INNER[1], INNER[2], INNER[3])
    if tool == 5:
        line(a[0], a[1], b[0], b[1], color)
    elif tool == 6:
        x = min(a[0], b[0])
        y = min(a[1], b[1])
        w = max(1, abs(a[0] - b[0]) + 1)
        h = max(1, abs(a[1] - b[1]) + 1)
        if shape_fill:
            rect(x, y, w, h, color)
        else:
            rectb(x, y, w, h, color)
    elif tool == 7:
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        r = int((dx * dx + dy * dy) ** 0.5)
        if shape_fill:
            circ(a[0], a[1], r, color)
        else:
            circb(a[0], a[1], r, color)
    clip()


def _draw_chrome():
    # Command strip: restrained desktop-grey with punchy selected states.
    rect(0, 18, W, 28, 48)
    _button("N", NEW_BTN, new_armed)
    _button("U", UNDO_BTN, bool(history))
    _button("R", REDO_BTN, bool(future))
    _button("SAVE", SAVE_BTN)
    _button("WALL", WALL_BTN)
    _button("GAME", GAME_BTN)
    _button("SHOW", SHOW_BTN)
    _button("FIT" if fit_view else "1:1", VIEW_BTN, fit_view)

    rect(0, 46, 36, 176, 5)
    for i in range(len(TOOL_RECTS)):
        _button(TOOL_GLYPHS[i], TOOL_RECTS[i], i == tool,
                0 if i == tool else 7)

    rect(258, 46, 62, 176, 5)
    for i in range(16):
        c = pal_page * 16 + i
        x = PAL_X + (i & 3) * PAL_CELL
        y = PAL_Y + (i // 4) * PAL_CELL
        rect(x, y, PAL_CELL - 1, PAL_CELL - 1, c)
        if c == color:
            rectb(x - 1, y - 1, PAL_CELL + 1, PAL_CELL + 1, 10)
            rectb(x, y, PAL_CELL - 1, PAL_CELL - 1, 0 if c > 7 else 7)
    _button("PAL" + str(pal_page + 1), PAL_PAGE_BTN)
    for i, s in enumerate((1, 2, 4)):
        _button(str(s), SIZE_BTNS[i], brush_size == s)
    _button("SOLID" if shape_fill else "EDGE", FILL_BTN, shape_fill)
    _button("^", PAN_UP)
    _button("<", PAN_LEFT)
    _button(">", PAN_RIGHT)
    _button("v", PAN_DOWN)

    # Current-colour chip and concise state line.
    rect(5, 154, 27, 27, color)
    rectb(4, 153, 29, 29, 7)
    label = TOOLS[tool] + " C" + str(color) + " S" + str(brush_size)
    rect(0, 222, W, 18, 0)
    print(label, 4, STATUS_Y, 7)
    msg = status[:18]
    print(msg, W - len(msg) * 8 - 4, STATUS_Y, 10)


def _draw_projects():
    cls(55)
    rect(0, 18, W, 28, 48)
    _button("< PAINT", (4, 20, 68, 23), True)
    print("ADD AS BG", 84, 27, 0)
    for row in range(7):
        idx = project_top + row
        if idx >= len(project_names):
            break
        r = (10, 48 + row * 23, 300, 20)
        rect(r[0], r[1], r[2], r[3], 5 if row & 1 else 1)
        rectb(r[0], r[1], r[2], r[3], 13)
        print(project_names[idx][:31], r[0] + 7, r[1] + 6, 7)
        print(">", r[0] + r[2] - 14, r[1] + 6, 10)
    _button("PREV", (10, 214, 72, 20), project_top > 0)
    _button("NEXT", (238, 214, 72, 20), project_top + 7 < len(project_names))


def _draw():
    if mode == "show":
        spr(art_img, 0, 0)
        return
    if mode == "projects":
        _draw_projects()
        return
    cls(55)
    _draw_art_view()
    _draw_chrome()
