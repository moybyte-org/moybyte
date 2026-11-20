"""The display kinds and the keys of §7.2 and §13 on one page: a label's face,
word wrap, lines and align, a divider, a note, a read-only lines view, a
progress as a count, a grid with pads (`keypad`), an action's letter and icon, and the
arrows as roles."""
import uitree as ui

s = ui.state(said="", steps=0, done=2, log=ui.model(key="n", n=0, line=""), touch_word="")


def mark():
    s.said = "marked"


def left():
    s.steps -= 1


def right():
    s.steps += 1


def grow():
    s.log = [{"n": i, "line": "line %d" % i} for i in range(len(s.log) + 1)]


def opened():
    s.said, s.steps, s.done = "", 0, 2
    s.log = [{"n": 0, "line": "line 0"}]


ui.app("Kinds", icon="check", min=(160, 120))
ui.hook("opened", opened)
ui.action("mark", "Mark", mark, keys=("m",), icon="check")
ui.action("left", "Less", left, keys=("role:left",))
ui.action("right", "More", right, keys=("role:right",))
ui.action("grow", "Add a line", grow, keys=("g",))


def build():
    with ui.page("Kinds"):
        with ui.group("Text"):
            ui.label("{said}", face="title", shown="said")
            ui.label("Steps {steps}", align="center")
            ui.label("A long paragraph that breaks at its word breaks across lines in the page's width",
                     wrap="word", lines=2)
            ui.label("Big", face="result", size="fit", icon="check")
            ui.label("Touch", shown="@touch")
        ui.rule()
        ui.note(["A note's first paragraph.", "And {steps} more."], icon="info")
        ui.progress("done", max=5, label="Steps", look="count")
        with ui.group("Do"):
            ui.button("mark")
            ui.button("grow")
        ui.text_view("log", follow=True)


def keypad():
    """A page placed by a grid, its cells stood in from its edges by pad=."""
    with ui.page("Pad"):
        with ui.grid(1, 2, pad="m"):
            ui.button("mark")
            ui.button("grow")


def layer():
    import uitree_app
    return uitree_app.app_layer("uitree_kinds", None, build)


def log_page():
    """A page that is one lines view, kept at its foot as lines arrive."""
    with ui.page("Log"):
        ui.text_view("log", follow=True)


DRAWN = []                      # the rects split_page's canvas was handed, frame by frame


def draw_pad(cv, x, y, w, h):
    DRAWN.append((x, y, w, h))


def split_page():
    """A canvas that is a split's cell, beside a column."""
    with ui.page("Split"):
        with ui.split("row", sizes=(2, 3)):
            ui.canvas("pad", draw_pad)
            with ui.group(""):
                ui.label("beside")
                ui.button("mark")
