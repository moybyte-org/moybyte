"""Settings' System log page: the OS log (native/moy_kernel/moy_log.h, through
runtime/system/os_log.py) as one text view on the public UI API's verbs, its
newest record at the foot, each record a line `seconds level process: text`
(a traceback's further lines under it, indented).

The page follows the ring: a record written while it is open shows on the next
frame (`moved`, which the console's animation gate asks): the page reads
them before its host begins the frame (`aim`), and a view at its foot moves
to the new foot as that frame's shift. Opened on one process (`open(pid)`) it
shows that process's records alone: what a task manager's "this app crashed:
details" and a crash notification open.
"""

from array import array

import ui as _ui
import ui_draw as _ui_draw
import os_log as _log

KEEP = 240                  # the lines the page holds, the oldest dropped first
TITLE = "System log"
# Lines are cut at the view's edge, where they fade, not wrapped: a wrapped
# view that follows its foot draws stale rows when its top line is cut
# part-way (moy_ui's text view, measured 2026-11-03).
_VIEW = _ui.text_options({"follow": True, "syntax": "plain"})
_INTO = array("i", [0, 0])


def record_lines(rec, out):
    """`rec` (seq, t_us, level, pid, text) as the page's lines, onto `out`."""
    _seq, t_us, level, pid, text = rec
    ms = t_us // 1000
    head = "%d.%03d %s %s:" % (ms // 1000, ms % 1000, _log.level_name(level),
                               _log.name(pid) or str(pid))
    parts = text.split("\n")
    out.append("%s %s" % (head, parts[0]))
    for more in parts[1:]:
        out.append("  " + more)


class LogPage:
    """Settings' System log page (`SettingsLayer` draws it on its sub page's
    host)."""

    NEEDS = ()

    def __init__(self, ctx=None):
        self.pid = None               # the one process shown, else every one
        self.lines = []
        self._seen = 0                # the seq the next read starts at
        self._gen = 0

    def open(self, pid=None):
        self.pid = pid
        self.lines = []
        self._seen = 0
        self._gen += 1

    def title(self):
        if self.pid is not None:
            return "Log: %s" % (_log.name(self.pid) or self.pid)
        return TITLE

    def back(self):
        return False

    def moved(self):
        """A record was written since the page last read the ring."""
        return _log.next_seq() != self._seen

    def aim(self, host):
        """Before `host` begins the frame: the records that came, and the
        view at its foot moved to the new foot."""
        if self._read():
            _ui_draw.text_aim(host, TITLE, -1, len(self.lines))

    def _read(self):
        if not self.moved():
            return False
        lines = self.lines
        while True:
            recs = _log.records(self._seen, 32)
            if not recs:
                break
            for rec in recs:
                if self.pid is None or rec[3] == self.pid:
                    record_lines(rec, lines)
            self._seen = recs[-1][0] + 1
        self._seen = _log.next_seq()
        if len(lines) > KEEP:
            del lines[:len(lines) - KEEP]
        self._gen += 1
        return True

    def draw(self, rect):
        x, y, w, h = _ui.page("")
        _ui.at(x, y, w, h)
        _VIEW[_ui.TXO_GEN] = self._gen
        _ui.text_view(TITLE, self.lines, _VIEW, None, None, _INTO)
