"""Taking the screen (docs/os_2026-11/processes.md §2; owner, 2026-11-08):
the one gate every way a process brings an app to the front goes through.

A process asks for the front by opening an item in the app that handles it
(`moybyte.open_with`, `moybyte.share`), by putting the console's picker up
(`moybyte.pick`; both runtime/system/intents.py), and by the nav role's
verbs (`open_app` and the rest, runtime/shell/shell_servers.py's
`NavServer`), from every tier that holds them. Each passes `take(pid,
summary, then)`, which answers `then()`'s answer where the request goes
through:

  the shell (pid 1)                     never gated: its own apps and the
                                        user's input, a notification's act
  the run on screen (`on_screen()`)     the app the user is in
  a process holding `front`             the permission (app_perms.TABLE)

and otherwise posts `summary` as a notification under the asking process's
title (notifications.py's `request`) whose act performs `then()`, and
answers `wait`. A caller no run claims (pid None) is gated as one in the
background. An alarm lights the screen and takes nothing: what its
callback asks for passes here as any call of its process does.

What the console supplies: `on_screen()`, the pid of the run the user is
in (None where they are in the shell), and `bring(pid)`, which brings
process `pid`'s run to the front and answers whether it is there (what a
deferred pick does before its picker goes up). Unbound, `on_screen` names
no run and `bring` brings nothing; the permission reads through
runtime/system/devices.py's `may`, which an unbound console lets pass."""

PERM = "front"
SHELL = 1


def _devices():
    import devices
    return devices


def _notes():
    from notifications import NOTES
    return NOTES


class Front:
    def __init__(self):
        self.on_screen = None       # fn() -> the pid of the run on screen, or None
        self.bring = None           # fn(pid) -> True where its run is now the front

    def bind(self, on_screen, bring):
        self.on_screen, self.bring = on_screen, bring

    def may(self, pid):
        """True where process `pid` may take the front now."""
        if pid == SHELL:
            return True
        if pid is None:
            return False
        if self.on_screen is not None and self.on_screen() == pid:
            return True
        return _devices().may(pid, PERM)

    def take(self, pid, summary, then, wait=True):
        """`then()` for process `pid` where it may take the front, else a
        notification whose act runs it: `then()`'s answer, or `wait`."""
        if self.may(pid):
            return then()
        _notes().request(pid, summary, then)
        return wait

    def come(self, pid):
        """Process `pid`'s run comes to the front: True where it is there."""
        return bool(self.bring(pid)) if self.bring is not None else False


FRONT = Front()
