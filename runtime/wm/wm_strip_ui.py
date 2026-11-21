"""A window's title strip as a placed page: the back chevron while its app
has a page open, the window's icon and title (one bar item that takes what
the buttons leave, its title cut with "…"), then minimise, maximise and
close, every one a bar item (`button kind="bar"`) in the strip's ink
(`tone`); on a full-screen window's row maximise shows restore
(`max_icon`). The window manager places it on the strip of the frame it drew
bare (`ui_draw.FRAME_BARE`), reads the buttons' rects back for its tap
resolution, and runs a tapped button's action (`wm_strip.fire`); the
strip's drag and the frame's resize stay the window manager's own held
press."""

import uitree as ui

# The strip's look, in units: its least pad (`wm_strip` writes `pad` in
# pixels, at least the columns a depth's rounded corners take) and the gap
# between its items, which keeps a button's share of the fat-finger hit
# (`WindowChrome._strip_button_hit`) at least 32 pixels on a desk.
PAD = 8
GAP = 10

s = ui.state(title="", icon="app", tone="", back_on=False, pad=PAD, max_icon="maximize")

TAP = [None]        # the window manager's run of a strip button: f(name)


def _tap(name):
    if TAP[0] is not None:
        TAP[0](name)


def _back():
    _tap("back")


def _min():
    _tap("min")


def _max():
    _tap("max")


def _close():
    _tap("close")


def _head():
    """The title is the strip's drag handle, which the window manager holds."""


ui.action("back", "Back", _back, hint="Back")
ui.action("head", "Window", _head)
ui.action("min", "Minimise", _min, hint="Minimise")
ui.action("max", "Maximise", _max, hint="Maximise")
ui.action("close", "Close", _close, hint="Close")


def strip():
    with ui.page(""):
        with ui.row("strip", pad="{pad}", gap=GAP, grow=True):
            ui.button("back", icon="back", alone=True, kind="bar", tone="{tone}", shown="back_on")
            ui.button("head", label="{title}", icon="{icon}", kind="bar", tone="{tone}", grow=True)
            ui.button("min", icon="minimize", alone=True, kind="bar", tone="{tone}")
            ui.button("max", icon="{max_icon}", alone=True, kind="bar", tone="{tone}")
            ui.button("close", icon="close", alone=True, kind="bar", tone="{tone}")
