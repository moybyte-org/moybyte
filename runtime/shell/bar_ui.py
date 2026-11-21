"""The shell's bar as a placed page: the themes' bar (`menu chips zone`
from its start, the status cluster and the clock from its end) laid out by
the engine's rule along x in the bar's chrome scale, its unit, and drawn by
it onto the bar's strip (`bar_engine.draw`). What it shows is its host's to
write (`bar_engine`): which widgets the layout places, the system button's
and the clock's widths in pixels, the tray's gap, the Wi-Fi icon, the time,
whether the battery is drawn by hand, the windows and the status items.

Wi-Fi, the battery, the clock, the chips (a row from a model: a window
each, `wins`, which the window manager writes, its focused one marked) and
the apps' status items (a row from a model: an item each, `items`, its icon
in its state's ink, the one whose menu is open marked) are bar items
(`button kind="bar"`). The canvases are drawn by the bar layer (`PAINT`),
each for its reason: the system button is the theme's own part (its tile,
`ui_draw.bar_part`, with Moy on it); the battery's low and charging states
are drawn by hand (no icon set has them); the zone is its owner's, drawn
after the page."""

import uitree as ui

s = ui.state(sys_w=16, sg=4, cw=40, wicon="wifi", time="00:00",
             menu=True, chips=True, zone=True, tray=True, status=False, wifi_on=True,
             batt_icon=True, batt_glyph=False, clock_on=True,
             wins=ui.model(key="k", k="", title="", icon="", on=False, tone=""),
             items=ui.model(key="k", k="", icon="", on=False, tone=""))

PAINT = [None]      # the bar layer's draw of a canvas: f(name, cv, x, y, w, h)
TAP = [None]        # the bar layer's open of an item: f(name)
CHIP = [None]       # the window manager's tap of a window's chip: f(kind)
ITEM = [None]       # the bar layer's open of status item k's menu: f(k)


def cell(cv, x, y, w, h):
    """The zone: its owner draws it after the page."""


def menu_cell(cv, x, y, w, h):
    PAINT[0]("menu", cv, x, y, w, h)


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


def _chip(row):
    if CHIP[0] is not None:
        CHIP[0](row.k)


def _item(row):
    if ITEM[0] is not None:
        ITEM[0](int(row.k))


ui.action("wifi", "Wi-Fi", _wifi, hint="Wi-Fi")
ui.action("chip", "Window", _chip, takes="row")
ui.action("item", "Status", _item, takes="row")
ui.action("battery", "Battery", _battery, hint="Battery")
ui.action("clock", "Clock", _clock, hint="Clock")


def bar():
    with ui.page(""):
        with ui.row("bar", pad=2, gap=2, grow=True):
            ui.canvas("menu", menu_cell, w="{sys_w}", shown="menu")
            with ui.row("chips", pad=6, gap=12, grow=True, shown="chips"):
                with ui.rows("wins", dir="row"):
                    ui.button("chip", label="{.title}", icon="{.icon}", kind="bar", marked=".on",
                              tone="{.tone}")
            ui.canvas("zone", cell, w=40, grow=True, shown="zone")
            with ui.row("tray", gap="{sg}", shown="tray"):
                with ui.rows("items", dir="row", shown="status"):
                    ui.button("item", icon="{.icon}", alone=True, kind="bar", marked=".on",
                              tone="{.tone}")
                ui.button("wifi", icon="{wicon}", alone=True, kind="bar", shown="wifi_on")
                ui.button("battery", icon="battery", alone=True, kind="bar", shown="batt_icon")
                ui.canvas("battery_st", battery_cell, w=16, shown="batt_glyph")
                ui.button("clock", label="{time}", kind="bar", w="{cw}", shown="clock_on")
