"""cron and at (docs/os_2026-11/between_apps.md section 3): command lines run
at set times over the alarms (moybyte.WAKES.alarm), each a process whose
output goes to the OS log. The shell's `cron` and `at` (runtime/shell/
sh_roles.py) are their command faces.

THE CRONTAB is system/crontab (/etc/crontab in the shell), read at the
console's first frame (`boot`) and by `cron reload`: a line is five fields
and a command line, `minute hour day-of-month month day-of-week command`,
each field `*`, a number, a range `a-b`, a step `*/n` or `a-b/n`, or a list
of those (`0,30`, `1-5`); day-of-week runs 0-7, 0 and 7 Sunday, and when
both day fields are restricted a day matching either runs the line, as
cron's. @hourly, @daily (@midnight), @weekly, @monthly and @yearly
(@annually) stand for their five fields. The reader is strict: blank lines
and `#` comments aside, a line that is not one is passed over and logged.
The times are local, in the console's time zone (runtime/system/
wallclock.py), so no line runs while the clock is not synced: the table
then looks at the clock once a minute and arms once it is. One alarm of the
shell's is armed at the next minute a line runs; when it goes off, the
lines that match that minute (in the zone in force then) run, and the next
is armed. A minute the console was not running (a VM stop, a power-off) is
not run afterwards. A crontab edited by hand is read at the next start or
by `cron reload`, as `settings reload` reads the settings.

AT JOBS (`at TIME COMMAND`): an alarm each, kept until it runs or `at -r`
removes it, in RAM: a restart forgets them.

A JOB runs its line in a shell session of its own (native/moy_sh/
moy_sh_job.h, `moy_sh.Job`) as a process: a pid from the OS log, named
"cron" or "at", a row of kind "job" in the scheduler's table while it runs.
Its first log line is the command line, then a line of the log for each
line it writes (stdout at info, stderr at err), then `exit N`. One job runs
at a time, the others queued in order; a run that waits parks (the frame
goes on) and a wake of the shell's looks at it each frame until it ends.
`kill PID` interrupts it as Ctrl-C would. Where the console has no C shell
of its own (the browser console a board serves) a job is logged and exits
NOT_RUN. Every wake here is the shell's and plain (the scheduler's
`plain_alarm`), and none lights the screen.
"""

try:
    import moy_log as _L
except ImportError:  # pragma: no cover - an image without the kernel
    _L = None

try:
    import scheduler as _sched
except ImportError:  # host: the runtime package
    from runtime import scheduler as _sched

try:
    import wallclock as _wc
except ImportError:  # host: the runtime package
    from runtime import wallclock as _wc

SHELL = 1
JOB = "job"                     # a running job's kind in the scheduler's table
NAME = "crontab"                # in the system folder
NOT_RUN = 126
POLL_MS = 20                    # how often a parked job is looked at
WATCH_MS = 60000                # how often an unsynced clock is looked at
YEARS = 5                       # how far ahead a line's next minute is looked for

# Each field's range: minute, hour, day of the month, month, day of the week.
FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
SHORT = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
         "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *",
         "@annually": "0 0 1 1 *"}
UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def _log(level, pid, text):
    if _L is not None:
        _L.write(getattr(_L, level), pid, text)


# -- the crontab ---------------------------------------------------------------------

def _field(text, lo, hi):
    """A field's values, a sorted tuple, or None for one that does not parse."""
    out = set()
    for part in text.split(","):
        base, _, step = part.partition("/")
        if step:
            if not step.isdigit() or int(step) < 1:
                return None
            step = int(step)
        else:
            step = 1
        if base == "*":
            a, b = lo, hi
        else:
            a, sep, b = base.partition("-")
            if not a.isdigit() or (sep and not b.isdigit()):
                return None
            a = int(a)
            b = int(b) if sep else (hi if part.find("/") > 0 else a)
        if a < lo or b > hi or a > b:
            return None
        out.update(range(a, b + 1, step))
    return tuple(sorted(out))


def parse_line(line):
    """A crontab line as (spec, command), spec the five fields' values and
    whether each day field is `*`; None for a line that is not one."""
    words = line.split(None, 1)
    if not words:
        return None
    if words[0] in SHORT:
        if len(words) < 2 or not words[1].strip():
            return None
        fields, cmd = SHORT[words[0]].split(), words[1].strip()
    else:
        words = line.split(None, 5)
        if len(words) < 6 or not words[5].strip():
            return None
        fields, cmd = words[:5], words[5].strip()
    got = []
    for text, (lo, hi) in zip(fields, FIELDS):
        v = _field(text, lo, hi)
        if v is None:
            return None
        got.append(v)
    dows = tuple(sorted(set(0 if d == 7 else d for d in got[4])))
    spec = (got[0], got[1], got[2], got[3], dows,
            fields[2].startswith("*"), fields[4].startswith("*"))
    return spec, cmd


def parse(text):
    """A crontab's text as ([(spec, command, line)], [(number, line)] passed
    over)."""
    lines, passed = [], []
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        got = parse_line(line)
        if got is None:
            passed.append((n, line))
        else:
            lines.append((got[0], got[1], line))
    return lines, passed


def _day_runs(spec, days):
    """Whether day `days` (from 1970-01-01, local) is one of the line's."""
    _mins, _hours, doms, mons, dows, dom_any, dow_any = spec
    y, m, d = _wc._civil(days)
    if m not in mons:
        return False
    in_dom = d in doms
    in_dow = (days + 4) % 7 in dows
    if dom_any and dow_any:
        return True
    if dom_any:
        return in_dow
    if dow_any:
        return in_dom
    return in_dom or in_dow


def _utc(local):
    """Unix seconds of local time `local` (seconds from 1970, the zone's)."""
    u = local - _wc.offset(local)
    return local - _wc.offset(u)


def matches(spec, t):
    """Whether Unix time `t`'s local minute is one the line runs at."""
    local = t + _wc.offset(t)
    days = local // 86400
    secs = local - days * 86400
    return (secs // 3600 in spec[1] and secs % 3600 // 60 in spec[0]
            and _day_runs(spec, days))


def next_time(spec, after):
    """The Unix seconds of the first minute after `after` the line runs at,
    local in the zone in force; None within YEARS."""
    local = after + _wc.offset(after)
    start = (local // 60 + 1) * 60
    day = start // 86400
    for days in range(day, day + 366 * YEARS):
        if not _day_runs(spec, days):
            continue
        base = days * 86400
        for h in spec[1]:
            for m in spec[0]:
                at = base + h * 3600 + m * 60
                if at < start:
                    continue
                u = _utc(at)
                if u > after:
                    return u
    return None


def when(t):
    """Unix time `t` as the local `YYYY-MM-DD HH:MM`."""
    y, mo, d, h, mi = _wc.localtime(t)[:5]
    return "%04d-%02d-%02d %02d:%02d" % (y, mo, d, h, mi)


# -- at's times ------------------------------------------------------------------------

def at_time(words, now):
    """`at`'s TIME at the front of `words`: (Unix seconds, words it took),
    or (None, why). `now`, `now + N UNIT` (seconds, minutes, hours, days,
    weeks, singular or plural, the `+` alone or joined to N) and `HH:MM`
    (the next one, today's or tomorrow's) optionally followed by `tomorrow`
    or a date `YYYY-MM-DD`. An HH:MM needs the clock synced."""
    if not words:
        return None, "no time"
    w0 = words[0]
    if w0 == "now" or w0.startswith("now+"):
        rest = [w0[3:]] if len(w0) > 3 else []
        rest += words[1:]
        took = 1
        if not rest or not rest[0].startswith("+"):
            return now, 1
        n = rest[0][1:]
        took += 1
        if not n:
            if len(rest) < 2:
                return None, "now + N UNIT"
            n = rest[1]
            rest = rest[1:]
            took += 1
        if len(w0) > 3:
            took -= 1
        if len(rest) < 2 or not n.isdigit():
            return None, "now + N UNIT"
        unit = rest[1].rstrip("s")
        if unit not in UNITS:
            return None, "a unit is seconds, minutes, hours, days or weeks"
        return now + int(n) * UNITS[unit], took + 1
    h, sep, m = w0.partition(":")
    if not sep or not h.isdigit() or not m.isdigit() or len(m) != 2 \
            or int(h) > 23 or int(m) > 59:
        return None, "a time is now, now + N UNIT or HH:MM"
    if not _wc.synced():
        return None, "the clock is not set"
    local = now + _wc.offset(now)
    day = local // 86400
    took = 1
    if len(words) > 1 and words[1] == "tomorrow":
        day += 1
        took = 2
    elif len(words) > 1 and len(words[1]) == 10 and words[1][4] == "-" and words[1][7] == "-":
        y, mo, d = words[1][:4], words[1][5:7], words[1][8:]
        if not (y.isdigit() and mo.isdigit() and d.isdigit()) \
                or not 1 <= int(mo) <= 12 or not 1 <= int(d) <= 31:
            return None, "a date is YYYY-MM-DD"
        day = _wc._days(int(y), int(mo), int(d))
        took = 2
    t = _utc(day * 86400 + int(h) * 3600 + int(m) * 60)
    if t <= now:
        if took == 2:
            return None, "that time has passed"
        t = _utc((day + 1) * 86400 + int(h) * 3600 + int(m) * 60)
    return t, took


_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./=:,+@%"


def command_line(words):
    """The command words as one line: a single word is the line itself (a
    quoted pipeline, redirections and all), several are each quoted where
    the shell would read them otherwise."""
    if len(words) == 1:
        return words[0]
    out = []
    for w in words:
        if w and all(c in _SAFE for c in w):
            out.append(w)
        else:
            out.append("'" + w.replace("'", "'\\''") + "'")
    return " ".join(out)


# -- the table ---------------------------------------------------------------------------

def _io():
    try:
        import moy_sh
        moy_sh.DOOR
        return moy_sh
    except (ImportError, AttributeError):   # a folder of that name on CPython's path
        pass
    try:                # CPython: the ctypes binding (runtime/host/moy_sh.py)
        from runtime import moy_sh
        return moy_sh
    except ImportError:  # the browser serving a board's shell: none of its own
        return None


def _browser_shell(io):
    """The site's browser console, whose shell runs in the page over the
    page's store (terminal_app's _BROWSER_SHELL)."""
    import sys
    return sys.platform == "webassembly" and not hasattr(io, "link_want")


class Cron:
    """The crontab's lines, the at jobs and the job running."""

    def __init__(self, sched=None):
        self.sched = sched
        self.ws = None
        self.lines = []             # [(spec, command, line)]
        self.passed = []            # [(number, line)] the reader passed over
        self.wid = None             # the crontab's alarm
        self.due_at = None          # its Unix seconds
        self.watch = None           # the unsynced clock's wake
        self.ats = {}               # id -> (Unix seconds, command, wid)
        self.next_id = 1
        self.queue = []             # [(name, command)] waiting their turn
        self.job = None             # [pid, face] running
        self.poll = None            # its wake while it is parked
        self.exits = []             # [(pid, name, code)], newest first

    def _s(self):
        return self.sched or _sched.SCHED

    def bind(self, ws):
        self.ws = ws

    # -- the crontab -------------------------------------------------------------

    def path(self):
        ws = self.ws
        try:
            from moy_docs import system_store_path, CARTS_DIR
        except ImportError:  # host: the runtime package
            from runtime.moy_docs import system_store_path, CARTS_DIR
        root = getattr(ws, "carts_root", None) or CARTS_DIR
        return system_store_path(root).rsplit("/", 1)[0] + "/" + NAME

    def _read(self):
        path = self.path()

        def read():
            try:
                with open(path) as f:
                    return f.read()
            except OSError:
                return None
        sd = getattr(self.ws, "_with_sd", None)
        return sd(read) if sd is not None else read()

    def reload(self):
        """Read the crontab again and arm its next minute: (lines, passed
        over)."""
        text = self._read() if self.ws is not None else None
        self.lines, self.passed = parse(text or "")
        for n, line in self.passed:
            _log("WARNING", SHELL, "crontab:%d: passed over: %s" % (n, line))
        self.arm()
        return len(self.lines), len(self.passed)

    def load(self, text):
        """The crontab's lines from `text` (a test's, or a reload's), armed."""
        self.lines, self.passed = parse(text)
        self.arm()

    def arm(self, after=None):
        """The crontab's alarm at the next minute a line runs, or the
        unsynced clock's watch."""
        s = self._s()
        if self.wid is not None:
            s.plain_cancel(self.wid)
            self.wid = self.due_at = None
        if self.watch is not None:
            s.plain_cancel(self.watch)
            self.watch = None
        if not self.lines:
            return None
        if not _wc.synced():
            self.watch = s.plain_every(WATCH_MS, self._watch)
            return None
        now = _wc.now()
        if after is None or after < now:
            after = now
        best = None
        for spec, _cmd, _line in self.lines:
            t = next_time(spec, after)
            if t is not None and (best is None or t < best):
                best = t
        if best is not None:
            self.due_at = best
            self.wid = s.plain_alarm(best, self._fire)
        return best

    def _watch(self):
        if _wc.synced():
            self.arm()

    def _fire(self):
        due = self.due_at
        self.wid = self.due_at = None
        if due is not None:
            for spec, cmd, _line in self.lines:
                if matches(spec, due):
                    self.queue.append(("cron", cmd))
        self.arm(due)
        self.pump()

    def next_of(self, spec):
        """A line's next run, Unix seconds, or None (the clock not set)."""
        if not _wc.synced():
            return None
        return next_time(spec, _wc.now())

    # -- at ----------------------------------------------------------------------

    def add_at(self, t, cmd):
        """An at job at Unix time `t`: its id."""
        jid = self.next_id
        self.next_id += 1
        wid = self._s().plain_alarm(t, lambda: self._at_fire(jid))
        self.ats[jid] = (t, cmd, wid)
        return jid

    def remove_at(self, jid):
        got = self.ats.pop(jid, None)
        if got is None:
            return False
        self._s().plain_cancel(got[2])
        return True

    def _at_fire(self, jid):
        got = self.ats.pop(jid, None)
        if got is not None:
            self.queue.append(("at", got[1]))
            self.pump()

    # -- the jobs ----------------------------------------------------------------

    def pump(self):
        """Start the queued jobs, one at a time, while each ends at once."""
        while self.job is None and self.queue:
            name, cmd = self.queue.pop(0)
            self._start(name, cmd)

    def _start(self, name, cmd):
        s = self._s()
        pid = _L.spawn(name) if _L is not None else s._next_pid()
        s.procs[pid] = _sched.Proc(pid, JOB, name)
        _log("INFO", pid, cmd)
        io = _io()
        make = getattr(io, "Job", None) if io is not None else None
        if make is None:
            _log("ERR", pid, "no shell on this console")
            self._end(pid, None, NOT_RUN)
            return
        try:
            if getattr(io, "HOST", False) or _browser_shell(io):
                face = make(pid, self._host_root())
            else:
                face = make(pid)
        except OSError:
            _log("ERR", pid, "no memory for a shell")
            self._end(pid, None, NOT_RUN)
            return
        self.job = [pid, face]
        code = face.run(cmd + "\n")
        if code is None:
            self.poll = s.plain_every(POLL_MS, self._poll)
        else:
            self._end(pid, face, code)

    def _host_root(self):
        try:
            from terminal_app import _host_root
        except ImportError:  # host: the runtime package
            from runtime.terminal_app import _host_root
        return _host_root(getattr(self.ws, "carts_root", None) or "")

    def _poll(self):
        job = self.job
        if job is None:
            return
        code = job[1].ended()
        if code is not None:
            self._end(job[0], job[1], code)
            self.pump()

    def _end(self, pid, face, code):
        s = self._s()
        if self.poll is not None:
            s.plain_cancel(self.poll)
            self.poll = None
        if face is not None:
            face.close()
        if self.job is not None and self.job[0] == pid:
            self.job = None
        p = s.procs.pop(pid, None)
        _log("INFO", pid, "exit %d" % code)
        self.exits = ([(pid, p.name if p is not None else JOB, code)] + self.exits)[:8]

    def kill(self, pid):
        """`kill PID` of the running job: interrupted, as Ctrl-C. False for
        a pid that is not it."""
        job = self.job
        if job is None or job[0] != pid:
            return False
        job[1].interrupt()
        return True

    def running(self, pid):
        return self.job is not None and self.job[0] == pid


CRON = Cron()


def boot(ws):
    """The crontab read at the console's first frame."""
    try:
        CRON.bind(ws)
        CRON.reload()
    except Exception as exc:  # noqa: BLE001 -- the desk comes up regardless
        print("Moybyte cron: %s" % (exc,))
