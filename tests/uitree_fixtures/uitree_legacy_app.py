"""A page on the Editor's kinds hosting a surface still drawn with today's
verbs (`ui.legacy`, the bridge) for the bridge's tests: on a handheld under
a head row, on the desk in the first pane of a workspace of panes whose
held sizes, pane focus and grown pane are declared state, with a popover
that lies over the hosted body."""
import ui as verbs
import ui_draw
import uitree as ui
import uitree_app

s = ui.state(
    side_w=ui.opt(0),
    run_h=ui.opt(0),
    on=ui.opt(""),
    big=ui.opt(""),
    menu=False,
    said=0,
)


class Hosted:
    """The hosted surface: its own host and its own verbs pass, as an
    Editor tab still on today's verbs is."""

    def __init__(self):
        self.host = ui_draw.AppHost()
        self.draws = 0
        self.rects = []
        self.focused = []
        self.typed = []
        self.presses = []
        self.gen = 0
        self.hits = 0

    def draw(self, cv, th, x, y, w, h, focused, dt):
        self.draws += 1
        self.rects.append((x, y, w, h))
        self.focused.append(focused)
        self.host.begin(cv, th, (x, y, w, h), True, True, elsewhere=not focused)
        verbs.page("Hosted")
        if verbs.button("Alpha"):
            self.hits += 1
        verbs.button("Beta")
        verbs.end()
        cv.rect(x, y, w, 4, th["value"])          # the mark the tests look for

    def keys(self, i):
        ch = getattr(i, "typed", 0) & 0xFF
        if 32 <= ch < 127:
            self.typed.append(chr(ch))
            return True
        return False

    def pointer(self, x, y, click):
        self.presses.append((x, y, bool(click)))
        return True


_SURF = {}      # one hosted surface a console (its app context's id)


def surface():
    e = uitree_app.env()
    got = _SURF.get(id(e))
    if got is None:
        got = _SURF[id(e)] = Hosted()
    return got


def draw(cv, th, x, y, w, h, focused, dt):
    surface().draw(cv, th, x, y, w, h, focused, dt)


def keys(i):
    return surface().keys(i)


def pointer(x, y, click):
    return surface().pointer(x, y, click)


def hints():
    return ("confirm", "Pick")


def gen():
    return surface().gen


def say():
    s.said += 1


def menu_open():
    s.menu = True


def menu_close():
    s.menu = False


ui.action("say", "Say", say, keys=("ctrl+y",))
ui.action("menu_open", "Menu", menu_open, keys=("ctrl+u",))
ui.action("menu_close", "Close", menu_close)
ui.action("say_again", "Say again", say)


def hosted():
    ui.legacy("list", draw, pointer=pointer, keys=keys, hints=hints, gen=gen)


def build():
    with ui.page("Legacy"):
        with ui.panel("hand", shown="@handheld"):
            with ui.row("head"):
                ui.button("say", label="Say")
            hosted()
        with ui.panel("desk", shown="@desk"):
            with ui.panes("row", "space", focused="on", grown="big"):
                with ui.pane("list_pane", title="List", icon="code", grow=True, min=(120, 80)):
                    hosted()
                with ui.panes("col", "right", size="side_w", start=200):
                    with ui.pane("tools", title="Tools", size="run_h", start=160, min=(160, 100)):
                        with ui.row("tools_row"):
                            ui.button("say", label="Say")
                    with ui.pane("notes", title="Notes", grow=True, min=(160, 80)):
                        with ui.row("notes_row"):
                            ui.status("Said {said}")
        with ui.popover("menu", anchor="{@desk?list_pane.head:head}", w="{@desk?240:150}",
                        close="menu_close", shown="menu"):
            ui.link("say_again", label="Say again")


def layer():
    import uitree_editor
    return uitree_app.app_layer("uitree_legacy", "Legacy fixture", build, engine=uitree_editor.Engine)
