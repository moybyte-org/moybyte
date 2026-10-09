"""The kernel's chrome as the shell draws it (native/moy_play/moy_chrome.h).

The chrome over a cart and the bar's shared pieces are ONE body each, in C:
a piece is a display list, replayed here through the canvas it lands on (the
canvas keeps its damage, its batch order and a web view's recording), and
rasterised by the kernel for a frame it draws with no VM call."""

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui

_CHROME_NAMED = ("black", "white", "red", "orange", "yellow", "peach", "dark_blue",
                 "dark_purple", "light_grey", "dark_grey")
_CHROME_ROLES = ("bar", "bar_edge", "chrome_ink", "chrome_ink_dim", "surface",
                 "border", "ink", "ink_dim", "play", "panel", "edge")
_mp_chrome = []


def _moy_play():
    if not _mp_chrome:
        try:
            import moy_play
        except ImportError:                 # CPython: the host's ctypes module
            from runtime import moy_play
        _mp_chrome.append(moy_play)
    return _mp_chrome[0]


def chrome_inks(th, names):
    """Hand the kernel's chrome the inks this theme draws with: the named
    palette it uses, the theme's roles, the system menu's row colours and the
    dialog's ring count. Before every chrome draw, so a theme edited in place
    is never drawn stale."""
    on = _ui.state_colors(th, "row_menu", _ui.ON)
    off = _ui.state_colors(th, "row_menu", _ui.REST)
    inks = [names[n] for n in _CHROME_NAMED]
    for r in _CHROME_ROLES:
        inks.append(th.get(r))
    inks += [on[0], on[1], off[0], off[1], None]
    mp = _moy_play()
    mp.chrome_inks(inks, bool(th.get("bar_light", False)), _ui.dialog_rings())
    return mp


def replay(ops, cv, ws=None):
    """Draw a chrome display list through `cv`: rect, rectb, print, a 12x12
    glyph (`ws._glyph`) and a bar icon (`ws._icon`), the scale 0 meaning the
    canvas's own."""
    for op, c, sc, x, y, w, h, t in ops:
        if op == 1:
            cv.rect(x, y, w, h, c)
        elif op == 2:
            cv.rectb(x, y, w, h, c)
        elif op == 3:
            cv.print(t, x, y, c, sc)
        elif op == 4:
            ws._glyph(t, (x, y, w, h), c, cv, sc or None)
        elif op == 5:
            ws._icon(t, x, y, cv, sc or None)
