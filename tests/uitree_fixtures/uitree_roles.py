"""A page with no stop, whose A and B are its two role actions: their hints
are templates over the state, "" where a key names nothing."""
import uitree as ui

s = ui.state(said="", go="Send", leave="Cancel", sent=0, left=0)


def go():
    s.sent += 1


def leave():
    s.left += 1


def quiet():
    s.go = s.leave = ""


ui.action("go", "Go", go, keys=("role:confirm",), hint="{go}")
ui.action("leave", "Back", leave, keys=("role:back",), hint="{leave}")
ui.hook("quiet", quiet)


def build():
    with ui.page(""):
        with ui.group("Roles"):
            ui.label("{said}")


def layer():
    import uitree_app
    return uitree_app.app_layer("uitree_roles", "Roles", build)
