"""The shell's bar as a placed page: the themes' bar (`menu chips zone`
from its start, the status cluster and the clock from its end) laid out by
the engine's rule along x in the bar's chrome scale, its unit. What it
shows is its host's to write (`bar_engine`): which widgets the layout
places, the system button's, the status cell's and the clock's widths in
pixels, the status gap. Each cell draws through the bar layer's painters
today; `cell` is the draw a frame of the page's own would call."""

import uitree as ui

s = ui.state(sys_w=16, status_w=0, sg=4, cw=40,
             menu=True, chips=True, zone=True, tray=True, status=False, wifi=True, battery=True, clock=True)


def cell(cv, x, y, w, h):
    """A bar cell's draw: the bar layer paints its cells today."""


def bar():
    with ui.page(""):
        with ui.row("bar", pad=2, gap=2):
            ui.canvas("menu", cell, w="{sys_w}", shown="menu")
            ui.canvas("chips", cell, w=24, grow=True, shown="chips")
            ui.canvas("zone", cell, w=64, grow=True, shown="zone")
            with ui.row("tray", gap="{sg}", shown="tray"):
                ui.canvas("status", cell, w="{status_w}", shown="status")
                ui.canvas("wifi", cell, w=16, shown="wifi")
                ui.canvas("battery", cell, w=16, shown="battery")
                ui.canvas("clock", cell, w="{cw}", shown="clock")
