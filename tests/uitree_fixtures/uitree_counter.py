"""A small app on `uitree` for the hosting tests: a count, a switch, a
name field, a pushed page and a dialog."""
import uitree as ui

s = ui.state(count=0, loud=False, name="", asking=False)


def add():
    s.count += 1


def reset_ask():
    s.asking = True


def reset():
    s.count = 0
    s.asking = False


def keep():
    s.asking = False


def more():
    ui.push(details)


def opened():
    s.count = 0
    s.asking = False


ui.action("add", "Add one", add, key="a")
ui.action("reset_ask", "Reset", reset_ask, when="count", off="grey")
ui.action("keep", "Keep", keep)
ui.action("reset", "Reset", reset)
ui.action("more", "Details", more)
ui.hook("opened", opened)


def build():
    with ui.page("Counter"):
        with ui.group("Count"):
            ui.info("Count", "{count}")
            ui.link("add")
            ui.link("reset_ask")
            ui.toggle("Loud", "loud")
            ui.field("Name", "name", max=16)
            ui.link("more", "{count}")
        with ui.dialog("Reset the count?", ["keep", "reset"], shown="asking"):
            ui.label("It goes back to 0.")


def details():
    with ui.page("Details"):
        with ui.group("Now"):
            ui.info("Count", "{count}")
            ui.info("Loud", "{loud?yes:no}")


CounterApp = None


def layer():
    import uitree_app
    return uitree_app.app_layer("uitree_counter", "Counter", build)
