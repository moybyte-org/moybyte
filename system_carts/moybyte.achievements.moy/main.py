"""Achievements: every game's badges as a background process
(docs/os_2026-11/processes.md section 8, docs/app_api_v1.md section 9).

It wakes only on two messages, `award(id)` and `progress(id, value)`, which
a game sends with `moybyte.award` and `moybyte.progress` and the console
sends for its own badges. The sender is the app id the OS stamped, so a game
reaches only the badges its own manifest declares: an id its sender does not
declare is refused, and the refusal logged. `progress` stores the game's
last value and finds the badge once it reaches the goal; the counting is the
game's.

What is found, and each goal's last value, live in its prefs (`badges`,
runtime/system/achievements.py's `read_badges`), read and written whole on
each message, so a deleted game's badges stay under its name. A fresh find
posts a notification, which the console shows as the unlock banner.

The console hands it, in its namespace: `game(app)` (the app's name and
declared badges, None for an app that declares none) and `clock_day()`
(today as YYYYMMDD off the bar's clock, 0 without one).
"""

try:
    import achievements as _A
    import os_log as _log
except ImportError:  # the host: the runtime package
    from runtime import achievements as _A
    from runtime import os_log as _log


def _refuse(who, ach_id, why):
    _log.printed("achievements: %s's %s refused: %s" % (who, ach_id, why))


def _take(who, ach_id, value):
    g = game(who)
    a = None
    for b in (g["achievements"] if g is not None else ()):
        if b["id"] == ach_id:
            a = b
    if a is None:
        _refuse(who, ach_id, "not declared")
        return
    goal = a["goal"]
    if value is not None:
        if goal is None:
            _refuse(who, ach_id, "no goal")
            return
        if type(value) is not int:
            _refuse(who, ach_id, "not a whole number")
            return
    doc = _A.read_badges(prefs.get(_A.PREFS_KEY)) or {}
    rec = doc.get(who)
    found = rec["found"] if rec is not None else {}
    values = rec["value"] if rec is not None else {}
    if ach_id in found:
        return
    fresh = value is None or value >= goal
    if fresh:
        found[ach_id] = [clock_day() or 0, a["title"], a["icon"], a["hint"]]
        values.pop(ach_id, None)
    elif values.get(ach_id) == value:
        return
    else:
        values[ach_id] = value
    rec = {"name": g["name"]}
    if found:
        rec["found"] = found
    if values:
        rec["value"] = values
    doc[who] = rec
    prefs.set(_A.PREFS_KEY, doc)
    if fresh:
        moybyte.notify(_A.head(who, g["name"], a["secret"]), a["title"], a["icon"])


def _award(who, ach_id=None, *_):
    _take(who, ach_id, None)


def _progress(who, ach_id=None, value=None, *_):
    if value is None:
        _refuse(who, ach_id, "no value")
        return
    _take(who, ach_id, value)


def moy_start():
    moybyte.on("award", _award)
    moybyte.on("progress", _progress)
