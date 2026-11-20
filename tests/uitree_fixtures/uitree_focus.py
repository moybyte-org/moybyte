"""A handler that shows a node and focuses it, and a page pushed from that
node: for the test that focus lands in the frame that first draws it."""
import uitree as ui

s = ui.state(open=False, seen=0)


def reveal():
    s.open = True
    ui.focus("extra")


def extra():
    s.seen += 1
    ui.push(details)


def opened():
    s.open = False
    s.seen = 0


ui.hook("opened", opened)
ui.action("reveal", "Show more", reveal)
ui.action("extra", "More", extra, when="open", off="hide")


def build():
    with ui.page("Focus"):
        with ui.group("Here"):
            ui.link("reveal")
            ui.label("{seen}")
            ui.link("extra")


def details():
    with ui.page("Details"):
        with ui.group("There"):
            ui.info("Seen", "{seen}")


def layer():
    import uitree_app
    return uitree_app.app_layer("uitree_focus", "Focus", build)
