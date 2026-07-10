# Open Machine -- the quiet construction-grid wallpaper from the visual identity.
#
# STATIC by design: there is no _update, so an idle desktop stays free under the
# shell's redraw-on-change gate. The pattern uses only MOY64 indices and remains
# deliberately low-contrast behind warm-light Studio windows.

grid = 8
field = 0
dot = 1
accent = 13
show_marks = 1


def _init():
    global grid, field, dot, accent, show_marks
    try:
        grid = int(cfg("grid", 8))
    except (TypeError, ValueError):
        grid = 8
    if grid < 6:
        grid = 6
    elif grid > 24:
        grid = 24

    field_name = cfg("field", "black")
    field = col(field_name)
    dot = col("dark_blue") if field_name == "black" else 60
    accent = col(cfg("accent", "indigo"))
    try:
        show_marks = int(cfg("marks", 1))
    except (TypeError, ValueError):
        show_marks = 1
    background(field)


def _draw():
    # A regular dot field with rare accent nodes. Fixed arithmetic keeps the same
    # designed pattern every boot without an RNG or an animating state.
    row = 0
    for y in range(grid // 2, H, grid):
        column = 0
        for x in range(grid // 2, W, grid):
            c = accent if (column * 5 + row * 3) % 47 == 0 else dot
            pix(x, y, c)
            column += 1
        row += 1

    # Oversized, partly clipped construction geometry: enough structure to echo
    # the identity deck, never a central illustration competing with windows.
    cx = W - 10
    cy = H // 2
    radius = 82
    line(cx, cy - radius, cx + radius, cy, dot)
    line(cx + radius, cy, cx, cy + radius, dot)
    line(cx, cy + radius, cx - radius, cy, dot)
    line(cx - radius, cy, cx, cy - radius, dot)

    line(-24, H - 52, 76, H - 52, dot)
    line(76, H - 52, 116, H - 12, dot)
    line(116, H - 12, 214, H - 12, dot)

    if show_marks:
        _mark(18, 18)
        _mark(W - 19, 18)
        _mark(18, H - 19)
        _mark(W - 19, H - 19)


def _mark(x, y):
    line(x - 4, y, x + 4, y, accent)
    line(x, y - 4, x, y + 4, accent)
    pix(x, y, col("yellow"))
