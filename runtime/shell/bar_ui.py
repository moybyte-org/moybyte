"""The shell's bar as a placed page: the themes' bar (`menu chips zone`
from its start, the status cluster and the clock from its end) laid out by
the engine's rule along x in the bar's chrome scale, its unit, and drawn by
it onto the bar's strip (`bar_engine.draw`). What it shows is its host's to
write (`bar_engine`): which widgets the layout places, the system button's,
the status cell's and the clock's widths in pixels, the status gap, the
Wi-Fi icon, the time, whether the battery is drawn by hand.

Wi-Fi, the battery and the clock are bar items (`button kind="bar"`). The
canvases are drawn by the bar layer (`PAINT`), each for its reason: the
system button is the theme's own part (its tile, `ui_draw.bar_part`, with
Moy on it); the status items are a list of the apps' icons, each in its
state's ink; the battery's low and charging states are drawn by hand (no
icon set has them); the chips are the window manager's and the zone its
owner's, drawn after the page."""

import uitree as ui

s = ui.state(sys_w=16, status_w=0, sg=4, cw=40, wicon="wifi", time="00:00",
             menu=True, chips=True, zone=True, tray=True, status=False, wifi_on=True,
             batt_icon=True, batt_glyph=False, clock_on=True)

PAINT = [None]      # the bar layer's draw of a canvas: f(name, cv, x, y, w, h)
TAP = [None]        # the bar layer's open of an item: f(name)


def cell(cv, x, y, w, h):
    """The chips and the zone: their owners draw them after the page."""


def menu_cell(cv, x, y, w, h):
    PAINT[0]("menu", cv, x, y, w, h)


def status_cell(cv, x, y, w, h):
    PAINT[0]("status", cv, x, y, w, h)


def battery_cell(cv, x, y, w, h):
    PAINT[0]("battery", cv, x, y, w, h)


def _wifi():
    if TAP[0] is not None:
        TAP[0]("wifi")


def _battery():
    if TAP[0] is not None:
        TAP[0]("battery")


def _clock():
    if TAP[0] is not None:
        TAP[0]("clock")


ui.action("wifi", "Wi-Fi", _wifi, hint="Wi-Fi")
ui.action("battery", "Battery", _battery, hint="Battery")
ui.action("clock", "Clock", _clock, hint="Clock")


def bar():
    with ui.page(""):
        with ui.row("bar", pad=2, gap=2, grow=True):
            ui.canvas("menu", menu_cell, w="{sys_w}", shown="menu")
            ui.canvas("chips", cell, w=24, grow=True, shown="chips")
            ui.canvas("zone", cell, w=64, grow=True, shown="zone")
            with ui.row("tray", gap="{sg}", shown="tray"):
                ui.canvas("status", status_cell, w="{status_w}", shown="status")
                ui.button("wifi", icon="{wicon}", alone=True, kind="bar", w=16, shown="wifi_on")
                ui.button("battery", icon="battery", alone=True, kind="bar", w=16, shown="batt_icon")
                ui.canvas("battery_st", battery_cell, w=16, shown="batt_glyph")
                ui.button("clock", label="{time}", kind="bar", w="{cw}", shown="clock_on")
