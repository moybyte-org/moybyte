"""A page with no stop, whose A and B are its two role actions: their hints
are templates over the state, "" where a key names nothing."""
import uitree as ui

s = ui.state(said="", go_word="Send", leave_word="Cancel", sent=0, left=0)


def go():
    s.sent += 1


def leave():
    s.left += 1


def quiet():
    s.go_word = s.leave_word = ""


ui.action("go", "Go", go, keys=("role:confirm",), hint="{go_word}")
ui.action("leave", "Back", leave, keys=("role:back",), hint="{leave_word}")
ui.hook("quiet", quiet)


def build():
    with ui.page(""):
        with ui.group("Roles"):
            ui.label("{said}")


def layer():
    import uitree_app
    return uitree_app.app_layer("uitree_roles", "Roles", build)
