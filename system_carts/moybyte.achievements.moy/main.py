"""Achievements: the console's badges as a background process
(docs/os_2026-11/processes.md section 8).

It wakes only on the notify role's events, which the console routes to it as
messages: `achieve(kind, key)`, a milestone, and `award(id, quiet, day)`, a
badge by its id. Each goes to the tracker (runtime/toolkit/widgets.py's
`Achievements`), which hands the document back through `badges_save`; a fresh
find posts a notification, which the console shows as the unlock banner. The
role's reads are the functions below.

The console hands it, in its namespace: `badges` (the document its store
holds, or None), `badges_save(doc)` (the console writes it at its next frame)
and `clock_day()` (today as YYYYMMDD off the bar's clock, 0 without one).
"""

try:
    from widgets import Achievements, ACH_TITLE, ACH_GLYPH, ACH_HINT
except ImportError:  # the host: the runtime package
    from runtime.widgets import Achievements, ACH_TITLE, ACH_GLYPH, ACH_HINT

ACH = None


def _found(ach_id):
    moybyte.notify(ACH_TITLE[ach_id], ACH_HINT[ach_id], ACH_GLYPH.get(ach_id, "trophy"))


def moy_start():
    global ACH
    ACH = Achievements(badges, on_save=badges_save, on_unlock=_found, today=clock_day)
    moybyte.on("achieve", lambda who, *a: ACH.note(*a))
    moybyte.on("award", lambda who, *a: ACH.award(*a))


def count():
    return ACH.count()


def has(ach_id):
    return ACH.has(ach_id)


def found_on(ach_id):
    return ACH.found_on(ach_id)


def progress(ach_id):
    return ACH.progress(ach_id)


def today():
    return clock_day()
