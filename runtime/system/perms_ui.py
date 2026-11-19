"""Settings' App permissions page (docs/os_2026-11/processes.md §2): every
cart on the console that lists a permission the install prompt names, a
group per cart and a switch per permission. Turning one off is the consent
record's (`app_perms.Consent.turn`); from the cart's next launch it is absent,
its names `None`, and a cart that requires it is refused with a message
naming it. An app installed with every permission keeps the ones left on.

A compiled cart carrying a native module for a console that does not confine
it (`app_perms.native_choice`) gets one more switch, off until the user turns
it on: `app_perms.NATIVE_LINE`, consent to `full_access` (`Consent.opt`),
from which the cart runs its native module rather than its wasm one.

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
_NATIVE = "native"            # the native-module switch's key in a row


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
        self._asked = {}              # app id -> its manifest's permissions (a native switch's)

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
                perms = _perms.asked(_perms.perms_of(cart), _perms.tiers_of(cart))
                lines = [(p, _perms.line(p)) for p in perms]
                if _perms.native_choice(cart):
                    lines.append((_NATIVE, _perms.NATIVE_LINE))
                    self._asked[_perms.app_id(cart)] = list(cart.get("permissions") or ())
                if lines:
                    out.append((cart.get("title") or "?", _perms.app_id(cart), tuple(lines)))
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
                native = perm == _NATIVE
                on = (_perms.native_opted(consent.get(aid)) if native
                      else consent.is_on(aid, perm))
                self._on[sid] = on
                now = _ui.toggle(line, on, {"id": sid})
                if now != on:
                    if native:
                        consent.opt(aid, "full_access", now, self._asked.get(aid, ()))
                    else:
                        consent.turn(aid, perm, now)
                    self._on[sid] = now
            _ui.group()
