"""A page lending the shell's bar its head row (`tools lend="bar"`): tabs
with one value offered on a condition, undo, and Play in place of Stop
while it plays, Stop marked while something is owed; the bar's zone
replays it (uitree_editor.BarZone)."""
import uitree as ui

s = ui.state(tab=ui.enum("one", "two", "three", init="one"), has_three=False,
             can_undo=False, playing=False, owed=False, opened=0, undone=0, played=0)
ui.labels("TABS", {"one": "One", "two": "Two", "three": "Three"})
ui.labels("TAB_ICONS", {"one": "edit", "two": "code", "three": "paint"})


def opened():
    s.opened += 1


def undo():
    s.undone += 1


def play():
    s.played += 1
    s.playing = True


def stop():
    s.playing = False


ui.action("open_tab", "Open", opened)
ui.action("undo", "Undo", undo, when="can_undo", off="grey")
ui.action("play", "Play", play)
ui.action("stop", "Stop", stop)


def page():
    with ui.page("Bar row"):
        with ui.tools("head", lend="bar"):
            ui.tabs("tab", "TABS", icons="TAB_ICONS", on="open_tab", offered={"three": "has_three"})
            ui.button("undo", icon="undo", alone=True)
            ui.button("play", icon="play", kind="primary", shown="!playing")
            ui.button("stop", icon="stop", kind="primary", shown="playing", marked="owed")
