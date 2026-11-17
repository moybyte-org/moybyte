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
runtime/system/achievements.py's `read_badges`), so a deleted game's badges
stay under its name. The process holds that row in memory: it reads it at
its first message and again only where the console's `row_stamp()` moved
past its own last write (the console's `seed`), so a message for a badge
found already or a value it holds already does no I/O. A fresh find writes
the row at once and posts a notification, which the console shows as the
unlock banner; a new value is written SAVE_MS later with whatever else
moved meanwhile, and at its stop.

The console hands it, in its namespace: `game(app)` (the app's name and
declared badges, None for an app that declares none, kept by the console
while its shelf stands), `clock_day()` (today as YYYYMMDD off the bar's
clock, 0 without one) and `row_stamp()`.
"""

import achievements as _A
import os_log as _log


def _refuse(who, ach_id, why):
    _log.printed("achievements: %s's %s refused: %s" % (who, ach_id, why))


SAVE_MS = 2000               # a new value's write, with what else moved by then

_ROW = [None, None, False, 0]   # the row, the stamp it was read or written at,
_DOC, _STAMP, _DIRTY, _WAKE = range(4)  # a value waiting to be written, its wake


def _doc():
    """The row as this process holds it, read again where the console's
    stamp moved past this process's last write."""
    st = row_stamp()
    if _ROW[_DOC] is None or st != _ROW[_STAMP]:
        _ROW[_DOC] = _A.read_badges(prefs.get(_A.PREFS_KEY)) or {}
        _ROW[_STAMP] = st
        _ROW[_DIRTY] = False
    return _ROW[_DOC]


def _save():
    """The row written, each record's empty halves left out."""
    if _ROW[_WAKE]:
        moybyte.cancel(_ROW[_WAKE])
        _ROW[_WAKE] = 0
    out = {}
    for who, rec in _ROW[_DOC].items():
        r = {"name": rec["name"]}
        if rec["found"]:
            r["found"] = rec["found"]
        if rec["value"]:
            r["value"] = rec["value"]
        out[who] = r
    prefs.set(_A.PREFS_KEY, out)
    _ROW[_STAMP] = row_stamp()
    _ROW[_DIRTY] = False


def _later():
    _ROW[_WAKE] = 0
    if _ROW[_DIRTY]:
        _doc()                  # a seed meanwhile: its row stands
        if _ROW[_DIRTY]:
            _save()


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
    doc = _doc()
    rec = doc.get(who)
    found = rec["found"] if rec is not None else {}
    values = rec["value"] if rec is not None else {}
    if ach_id in found:
        return
    fresh = value is None or value >= goal
    if not fresh and values.get(ach_id) == value:
        return
    if fresh:
        found[ach_id] = [clock_day() or 0, a["title"], a["icon"], a["hint"]]
        values.pop(ach_id, None)
    else:
        values[ach_id] = value
    doc[who] = {"name": g["name"], "found": found, "value": values}
    if fresh:
        _save()
        moybyte.notify(_A.head(who, g["name"], a["secret"]), a["title"], a["icon"])
    else:
        _ROW[_DIRTY] = True
        if not _ROW[_WAKE]:
            _ROW[_WAKE] = moybyte.after(SAVE_MS, _later)


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


def moy_stop():
    _later()
