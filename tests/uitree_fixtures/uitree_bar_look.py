"""The bar's look (`button kind="bar"`, the shell's bar item): no plate, an
icon in the chrome ink at the largest multiple of 16 its cell holds, a
label in the ui face, `marked=` the cell's ON mark, focus the bar's pill;
a bar item takes its row's whole height and its `w=` as its width. Menu
marks itself and turns the Wi-Fi icon to its off state; Tick widens the
clock."""
import uitree as ui

s = ui.state(open=False, wicon="wifi", clock="9:41", cw=40)


def menu():
    s.open = not s.open
    s.wicon = "wifi_off" if s.wicon == "wifi" else "wifi"


def tick():
    s.clock = "10:41" if s.clock == "9:41" else "9:41"
    s.cw = 52 if s.cw == 40 else 40


def opened():
    s.open = False
    s.wicon = "wifi"
    s.clock = "9:41"
    s.cw = 40


ui.hook("opened", opened)
ui.action("menu", "Menu", menu, hint="Menu")
ui.action("wifi", "Wi-Fi", menu, hint="Wi-Fi")
ui.action("battery", "Battery", tick, hint="Battery")
ui.action("tick", "Clock", tick, hint="Clock")


def _draw(cv, x, y, w, h):
    cv.rect(x + 1, y + 1, max(0, w - 2), max(0, h - 2) // 2, 9)


def build():
    with ui.page(""):
        with ui.row("bar", pad=2, gap=2):
            ui.button("menu", icon="moy", alone=True, kind="bar", w=16, marked="open")
            ui.canvas("zone", _draw, ground="surface")
            with ui.row("tray", gap=4):
                ui.button("wifi", icon="{wicon}", alone=True, kind="bar", w=16)
                ui.button("battery", icon="battery", alone=True, kind="bar", w=16, marked="open")
                ui.button("tick", label="{clock}", kind="bar", w="{cw}")
        ui.canvas("body", _draw, ground="surface")


def layer(engine):
    import uitree_app
    return uitree_app.app_layer("bar_look_" + ("c" if "C" in engine.__name__ else "ref"), "Bar look fixture",
                                build, engine=engine)
