# Map (grep -n a name to jump there):
#   AchievementsUI              the three Easter eggs: their trigger state and steps
#   fill                        the badges as the page shows them, from notify.groups()
#   achievements_page           the page: the count, the badges by game, a badge's popover
#   AchievementsPage            the page as Settings hosts it
"""Achievements on the console (#21, docs/history/look_2026-10.md step 32): the
hidden Easter eggs, Settings' Achievements page, a `uitree` build, and the
unlock banner's burst.

The badges are the achievements process's (runtime/system/achievements.py,
docs/app_api_v1.md section 9): the console's own and every game's.
This module draws, and keeps the eggs; both reach the badges through their
AppContext's `notify` role.

* The eggs: the Konami code on the home, seven taps on the bar's clock and
  five on Settings' title, each awarding the console's hidden badge through
  `notify.award`. A first find shows the unlock banner ("You found a
  secret!", with confetti); a second shows nothing.
* The page is a page of Settings (`SettingsLayer`, Settings' way back): the
  count, then the badges as cards under a caption a game (`notify.groups`:
  the console's own first, then each game that declares a badge, then each
  game gone from the shelf whose badges were found), each card its badge's
  icon on a tile, its name and how it is found, a locked one veiled, a
  secret one "Secret" until found and a goal under way its count ("3 of
  5"). Enter or a tap opens a badge's popover, hung from its card: the day
  it was found, or its goal's bar. The desk's foot names the focused badge.
* The banner is native/moy_ui's `moy_ui_unlock`, drawn by the console
  (`console_notices.Notices._draw_toast`) with the head the process posted
  and the confetti's burst named here.
"""

import uitree as ui
import uitree_app
import layout_base as _lb


TITLE = "Achievements"
NONE_LINE = "Play, draw and make things to find them."
COUNT_LINE = ("{none?None found yet:}{every?All {total} found!:}"
              "{!none && !every?{found} of {total} found:}")
SECRET = "Secret"
SECRET_HOW = "Keep exploring"
NOT_FOUND = "Not found yet"
BURST_MS = 420                 # the confetti's burst, from the banner's edges out
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def day_line(day, today=0):
    """When a badge was found, as its popover says it: "Found 12 October"
    (its year where it is not this one), "Found" where the console had no
    clock."""
    if not day:
        return "Found"
    out = "Found %d %s" % (day % 100, _MONTHS[(day // 100 % 100 - 1) % 12])
    if today and today // 10000 != day // 10000:
        out += " %d" % (day // 10000)
    return out


def goal_line(value, goal):
    """A goal under way, as its card and its popover say it."""
    return "%d of %d" % (min(value, goal), goal)


class AchievementsUI:
    """The three hidden Easter eggs: their trigger state and their steps.
    Each awards its hidden badge (`notify.award`), whose first find is the
    unlock banner; the Konami code's badge, and every secret's, bursts
    confetti from it."""

    _KONAMI = ("up", "up", "down", "down", "left", "right", "left", "right", "b", "a")
    _CLOCK_TAP_GOAL = 7
    _SECRET_TAP_GOAL = 5

    NEEDS = ("notify",)

    def __init__(self, ctx):
        self._notify = ctx.notify
        self._konami_pos = 0          # how far into the Konami sequence we are (home)
        self._clock_taps = 0          # clock taps on the bar (Time Traveler)
        self._secret_taps = 0         # Settings-title taps (Secret Finder's door)

    def konami_watch(self, i):
        """The Konami Easter egg (#21) watches every button press on the
        home, the bar's over it included, for the secret sequence: a passive
        observer, so the nav still runs."""
        for b in self._KONAMI:
            if i.pressed(b):
                self._konami_step(b)
                return

    def _konami_step(self, name):
        """Advance the home's Konami sequence on a button press; the full code in
        order awards "Secret Coder". A wrong key restarts (but still counts if it's
        the sequence's first key, so a fresh start works)."""
        seq = self._KONAMI
        if name == seq[self._konami_pos]:
            self._konami_pos += 1
        else:
            self._konami_pos = 1 if name == seq[0] else 0
        if self._konami_pos >= len(seq):
            self._konami_pos = 0
            self._notify.award("konami")

    def _tap_clock(self):
        """Count a clock tap; the _CLOCK_TAP_GOAL'th in a row awards "Time
        Traveler". Any other bar tap resets the run."""
        self._clock_taps += 1
        if self._clock_taps >= self._CLOCK_TAP_GOAL:
            self._clock_taps = 0
            self._notify.award("clock_tinker")

    def _tap_secret_door(self):
        """Count a Settings-title tap (the hidden door); the _SECRET_TAP_GOAL'th
        knocks it open -> "Secret Finder"."""
        self._secret_taps += 1
        if self._secret_taps >= self._SECRET_TAP_GOAL:
            self._secret_taps = 0
            self._notify.award("secret_door")


s = ui.state(badges=ui.model(key="id", id="", title="", sub="", how="", state="", icon="",
                             tone="", group="", got=False, going=False, value=0, goal=0),
             cur=ui.row_of("badges"), found=0, total=0, none=False, every=False, pop=False)


def fill():
    """The badges as `notify.groups()` holds them: a card each, by game;
    a badge's popover line made here (the day it was found, its goal's
    count, or "Not found yet")."""
    notify = uitree_app.env().notify
    today = notify.today()
    rows, n = [], 0
    for app, cap, badges in notify.groups():
        for b in badges:
            got = b[6] is not None
            secret = b[5] and not got
            how = SECRET_HOW if secret else b[3]
            goal = b[4] if not got and b[4] else 0
            if got:
                n += 1
                state = day_line(b[6], today)
            else:
                state = goal_line(b[7], goal) if goal else NOT_FOUND
            rows.append({"id": "%s %s" % (app, b[0]), "title": SECRET if secret else b[1],
                         "sub": goal_line(b[7], goal) if goal and b[7] > 0 else how,
                         "how": how, "state": state, "icon": "secret" if secret else b[2],
                         "tone": "" if got else "locked", "group": cap, "got": got,
                         "going": bool(goal), "value": max(0, min(b[7], goal)), "goal": goal})
    s.badges = rows
    s.found, s.total, s.none, s.every = n, len(rows), n == 0, 0 < len(rows) <= n
    if rows and not any(r.id == s.cur for r in s.badges):
        s.cur = rows[0]["id"]


def open_badge(row):
    s.cur = row.id
    s.pop = True


def close():
    s.pop = False
    ui.focus(s.cur + "/open")


ui.hook("opened", fill)
ui.hook("fill", fill)
ui.action("open", "Open", open_badge, takes="row")
ui.action("close", "Close", close)


def achievements_page():
    with ui.page(""):
        ui.progress("found", max="{total}", look="count",
                    label=COUNT_LINE)
        ui.label(NONE_LINE, tone="dim", wrap="word", shown="none")
        with ui.cards("badges", cur="cur", w="{@desk?92:76}", h="{@desk?68:56}", group="{.group}"):
            ui.card("open", label="{.title}", sub="{.sub}", icon="{.icon}", tone="{.tone}")
        with ui.foot(shown="@desk"):
            ui.label("{cur.title} · {cur.how} · {cur.got?found:not found yet}", tone="dim")
        with ui.popover("badge", anchor="badges.cur", shown="pop", close="close",
                        w="{@desk?420:240}"):
            with ui.split("row", sizes=(64, "1*")):
                ui.picture(icon="{cur.icon}", tone="{cur.tone}", side=56)
                with ui.group(""):
                    ui.label("{cur.title}", face="title")
                    ui.label("{cur.how}", tone="dim", shown="!cur.going")
                    ui.label("{cur.state}", tone="value", shown="cur.got")
                    ui.label("{cur.state}", tone="dim", shown="!cur.got && !cur.going")
                    ui.progress("cur.value", max="{cur.goal}", label="{cur.how}",
                                shown="cur.going")


class AchievementsPage:
    """Settings' Achievements page (`SettingsLayer` draws it on its sub
    page's host, its way back Settings'): `back` closes an open popover
    first."""

    NEEDS = ("surface", "theme", "damage", "notify")

    def __init__(self, ctx, host=None):
        self._ctx = ctx
        self._host = host
        self.page = None
        self._groups = None           # the badges the model was made from
        self._today = None            # the day its popover lines were made on
        self._page()                  # at boot: its first open makes no engine, which holds no C store until then

    def _page(self):
        if self.page is None:
            self.page = uitree_app.hosted_page(achievements_page, self._host, self._ctx.damage, gen=True,
                                               env=self._ctx)
        return self.page

    def open(self):
        """A visit: the model made again only when the badges or the day
        moved since it was made (its `opened` hook, `fill`)."""
        notify = self._ctx.notify
        groups, today = notify.groups(), notify.today()
        made = groups is self._groups and today == self._today and self.page is not None
        self._groups, self._today = groups, today
        self._page().open(hook=None if made else "opened")

    def close(self):
        if self.page is not None:
            self.page.close()

    def back(self):
        """Esc, B or the bar's way back: an open popover closes onto its
        badge. False where none was open (Settings then leaves the page)."""
        pg = self.page
        if pg is None or not pg.s._v["pop"]:
            return False
        pg.run(close, action="close")
        ui.commit(pg)
        self._ctx.damage.all()
        return True

    def keys(self, i):
        return self._page().keys(i, damage=False)

    def _row(self, bid):
        for r in self._page().s._v["badges"]:
            if r._f["id"] == bid:
                return r
        return None

    def focused(self):
        """The focused badge, "APP ID", or None."""
        pg = self.page
        return None if pg is None or not pg.s._v["badges"] else pg.s._v["cur"]

    def popover(self):
        """The open popover: (badge "APP ID", its title, how, its state), or
        None."""
        pg = self.page
        if pg is None or not pg.s._v["pop"]:
            return None
        f = self._row(pg.s._v["cur"])._f
        return (f["id"], f["title"], f["how"], f["state"])

    def open_badge(self, bid):
        """Open badge `bid`'s popover, as its card's Enter does."""
        pg = self._page()
        pg.run(open_badge, self._row(bid), action="open")
        ui.commit(pg)
        self._ctx.damage.all()

    def focus(self, bid):
        """Move the focus onto badge `bid`'s card."""
        pg = self._page()
        pg.run(ui.focus, bid + "/open", action="focus")
        ui.commit(pg)
        self._ctx.damage.all()

    def draw(self, rect, dt=0.0):
        pg = self._page()
        groups = self._ctx.notify.groups()
        if groups is not self._groups:
            self._groups, self._today = groups, self._ctx.notify.today()
            pg.run(fill, action="fill")
            ui.commit(pg)
        cv, th = self._ctx.surface.canvas(), self._ctx.theme.colors()
        pg.paint(dt, cv, th, rect, _lb.metric_class() == _lb.DESK)
