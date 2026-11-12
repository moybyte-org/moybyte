"""Settings' App permissions page (docs/os_2026-11/processes.md §2): every
cart on the console that lists a permission the install prompt names, a
group per cart and a switch per permission. Turning one off is the consent
record's (`app_perms.Consent.turn`); from the cart's next launch it is absent,
its names `None`, and a cart that requires it is refused with a message
naming it. An app installed with every permission keeps the ones left on.

A shipped app the shell runs as its own layer (Files, Paint, Appearance) holds
no grant to narrow and is not listed. The page reaches the shell through its
AppContext (`PermsPage.NEEDS`): the carts role for the shelf, the install
role for the consent records, the nav role for which carts are the shell's.
"""

import ui as _ui
import app_perms as _perms

TITLE = "App permissions"

# The keycap hints for a switch, by whether it is on (Settings' own).
_HINTS = (("confirm", "Turn on", "back", "Back"), ("confirm", "Turn off", "back", "Back"))
_EMPTY = ("No app on this console asks for more than a game does.",)


class PermsPage:
    """Settings' App permissions page (`SettingsLayer` draws it on its sub
    page's host)."""

    NEEDS = ("carts", "install", "nav")

    def __init__(self, ctx):
        self._carts = ctx.carts
        self._inst = ctx.install
        self._nav = ctx.nav
        self._rows = None             # ((title, app id, ((perm, line), ...)), ...)
        self._on = {}                 # a switch's id -> whether it showed on

    def open(self):
        self._rows = None

    def title(self):
        return TITLE

    def back(self):
        return False

    def rows(self):
        """The carts the page shows, made once an opening: (title, app id,
        ((permission, its line), ...)) in the shelf's order."""
        if self._rows is None:
            out = []
            for cart in self._carts.all() or ():
                if self._nav.is_system_app(cart):
                    continue              # the shell's own: no grant to narrow
                perms = _perms.asked(cart.get("permissions"), _perms.tiers_of(cart))
                if perms:
                    out.append((cart.get("title") or "?", _perms.app_id(cart),
                                tuple((p, _perms.line(p)) for p in perms)))
            self._rows = tuple(out)
        return self._rows

    def draw(self, rect):
        _ui.page("")
        rows = self.rows()
        consent = self._inst.consent()
        f = _ui.focus()
        if f in self._on:
            _ui.hints(_HINTS[bool(self._on[f])])
        if not rows:
            _ui.empty("check", TITLE, _EMPTY)
            return
        for title, aid, perms in rows:
            _ui.group(title)
            for perm, line in perms:
                sid = aid + " " + perm
                on = consent.is_on(aid, perm)
                self._on[sid] = on
                now = _ui.toggle(line, on, {"id": sid})
                if now != on:
                    consent.turn(aid, perm, now)
                    self._on[sid] = now
            _ui.group()
