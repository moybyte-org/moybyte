"""Settings' System log page: the OS log (native/moy_kernel/moy_log.h, through
runtime/system/os_log.py) as one `uitree` lines view that follows, its
newest record at the foot, each record a line `seconds level process: text`
(a traceback's further lines under it, indented).

The page follows the ring: a record written while it is open shows on the next
frame (`moved`, which the console's animation gate asks): the page reads
them as the frame's first hook (`log_poll`), and a view at its foot moves to
the new foot. Opened on one process (`open(pid)`) it
shows that process's records alone: what a task manager's "this app crashed:
details" and a crash notification open.
"""

import uitree as ui
import uitree_app
import os_log as _log

KEEP = 240                  # the lines the page holds, the oldest dropped first
TITLE = "System log"

s = ui.state(lines=ui.model(key="n", n=0, line=""))


def log_poll(page):
    """The records that came since the page last read the ring, onto its
    lines (a view at its foot follows them)."""
    if page._read():
        s.lines = [{"n": n, "line": line} for n, line in enumerate(page.lines, page._first)]


# Lines are cut at the view's edge, where they fade, not wrapped: a wrapped
# view that follows its foot draws stale rows when its top line is cut
# part-way (moy_ui's text view, measured 2026-11-03).
def log_page():
    with ui.page(""):
        ui.text_view("lines", follow=True)


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
    host): the ring's reader, and the page it fills."""

    NEEDS = ()

    def __init__(self, host=None, damage=None, env=None):
        self.pid = None               # the one process shown, else every one
        self.lines = []
        self._first = 0               # the running number of lines[0], each line's key
        self._seen = 0                # the seq the next read starts at
        self.engine = None if host is None else uitree_app.hosted_page(log_page, host, damage, env=env)

    def open(self, pid=None):
        self.pid = pid
        self.lines = []
        self._first = 0
        self._seen = 0
        if self.engine is not None:
            self.engine.open()

    def close(self):
        if self.engine is not None:
            self.engine.close()

    def title(self):
        if self.pid is not None:
            return "Log: %s" % (_log.name(self.pid) or self.pid)
        return TITLE

    def back(self):
        return False

    def moved(self):
        """A record was written since the page last read the ring."""
        return _log.next_seq() != self._seen

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
            self._first += len(lines) - KEEP
            del lines[:len(lines) - KEEP]
        return True

    def draw(self, cv, th, rect, desk, dt):
        self.engine.run(log_poll, self, action="log_poll")
        ui.commit(self.engine)
        self.engine.paint(dt, cv, th, rect, desk)
