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
no grant to narrow and is not listed. The page is a `uitree` build that
Settings hosts (`PermsPage`); it reaches the shell through its AppContext
(`PermsPage.NEEDS`): the carts role for the shelf, the install role for the
consent records, the nav role for which carts are the shell's.
"""

import uitree as ui
import uitree_app
import app_perms as _perms

TITLE = "App permissions"
EMPTY = "No app on this console asks for more than a game does."
NATIVE = "native"             # the native-module switch's permission in a row

s = ui.state(grants=ui.model(key="id", id="", app="", aid="", perm="", line="", on=False, asked=""))



def opened():
    """A row per permission the install prompt names, grouped by cart in
    the shelf's order."""
    ctx = uitree_app.env()
    consent = ctx.install.consent()
    rows = []
    for cart in ctx.carts.all() or ():
        if ctx.nav.is_system_app(cart):
            continue                  # the shell's own: no grant to narrow
        aid = _perms.app_id(cart)
        lines = [(p, _perms.line(p)) for p in _perms.asked(_perms.perms_of(cart), _perms.tiers_of(cart))]
        asked = ""
        if _perms.native_choice(cart):
            lines.append((NATIVE, _perms.NATIVE_LINE))
            asked = " ".join(cart.get("permissions") or ())
        for perm, line in lines:
            on = (_perms.native_opted(consent.get(aid)) if perm == NATIVE else consent.is_on(aid, perm))
            rows.append({"id": aid + " " + perm, "app": cart.get("title") or "?", "aid": aid,
                         "perm": perm, "line": line, "on": on, "asked": asked})
    s.grants = rows


def turn(row):
    consent = uitree_app.env().install.consent()
    if row.perm == NATIVE:
        consent.opt(row.aid, "full_access", row.on, row.asked.split())
    else:
        consent.turn(row.aid, row.perm, row.on)


ui.hook("opened", opened)
ui.hook("turn", turn, takes="row")


def build():
    with ui.page(""):
        ui.empty(TITLE, icon="check", lines=[EMPTY], shown="!grants")
        with ui.rows("grants", group="{.app}"):
            ui.toggle("{.line}", ".on", on="turn", hint="{.on?Turn off:Turn on}")


class PermsPage:
    """The page as Settings hosts it: opened on each visit, drawn into its
    sub page's frame."""

    NEEDS = ("carts", "install", "nav")

    def __init__(self, ctx, host, damage):
        self.page = uitree_app.hosted_page(build, host, damage, env=ctx)

    def open(self):
        self.page.open()

    def title(self):
        return TITLE

    def back(self):
        return False

    def draw(self, cv, th, rect, desk, dt):
        self.page.paint(dt, cv, th, rect, desk)
