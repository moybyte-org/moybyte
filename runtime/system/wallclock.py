"""The wall clock (docs/os_2026-11/hardware.md section 1): whether the time is
known, and the time of day in the console's time zone.

  synced()          whether the clock holds real time: on a board, NTP has
                    answered since power-on (native/moy_net's clock_state);
                    the host, the unix port and the browser read their
                    machine's clock, which counts as synced once it is past
                    2020. Allocates nothing
  now()             the clock in Unix seconds, synced or not (a board's
                    counts from power-on until NTP answers)
  localtime(t=None) the local (year, month, day, hour, minute, second,
                    weekday from Monday 0, yearday from 1) of `t`, Unix
                    seconds, now when None -- None while the clock is not
                    synced and no `t` is given
  offset(t)         the zone's offset from UTC at `t`, seconds east
  set_zone(tz)      the zone in force, a POSIX TZ string ("CET-1CEST,M3.5.0,
                    M10.5.0/3"); False, and UTC kept, for one that does not
                    parse
  zone()            the TZ string in force
  set_server(name)  the NTP server the board's next sync asks (None: the
                    kernel's default, pool.ntp.org)
  ZONES             the zones the Settings picker names: (place, TZ string)

The times here are Unix seconds. `time.time()` counts from its platform's
epoch: 1970 on the host, the unix port and the browser, 2000 on a board's
MicroPython, whose system clock NTP sets on that epoch
(native/moy_net/moy_wifi.c). The calendar is this module's own, so it is
the same on every tier.

A TZ string is POSIX's: a standard name and offset (west of UTC, so "CET-1"
is an hour east), then optionally a summer name, its offset (an hour ahead of
standard when absent) and the two rules `,start[/time],end[/time]`, a rule
being `Mm.w.d` (day d of week w of month m, week 5 the last), `Jn` (day n of
the year, February 29 never counted) or `n` (day n from 0, leap days
counted), its time local and 02:00 when absent. A summer zone given without
rules takes the US ones, `M3.2.0,M11.1.0`, as POSIX implementations do.
"""

import time

UTC = "UTC0"
SYNCED = 1                  # native/moy_net/moy_net.h's MOY_CLOCK_SYNCED

# The Settings picker's zones, west to east: the place a reader knows each by,
# and its rules as of 2026.
ZONES = (
    ("Honolulu", "HST10"),
    ("Anchorage", "AKST9AKDT,M3.2.0,M11.1.0"),
    ("Los Angeles", "PST8PDT,M3.2.0,M11.1.0"),
    ("Phoenix", "MST7"),
    ("Denver", "MST7MDT,M3.2.0,M11.1.0"),
    ("Mexico City", "CST6"),
    ("Chicago", "CST6CDT,M3.2.0,M11.1.0"),
    ("New York", "EST5EDT,M3.2.0,M11.1.0"),
    ("Bogota, Lima", "<-05>5"),
    ("Halifax", "AST4ADT,M3.2.0,M11.1.0"),
    ("Santiago", "<-04>4<-03>,M9.1.6/24,M4.1.6/24"),
    ("Sao Paulo, Buenos Aires", "<-03>3"),
    ("UTC", UTC),
    ("London, Dublin", "GMT0BST,M3.5.0/1,M10.5.0"),
    ("Lisbon", "WET0WEST,M3.5.0/1,M10.5.0"),
    ("Lagos", "WAT-1"),
    ("Paris, Berlin, Belgrade", "CET-1CEST,M3.5.0,M10.5.0/3"),
    ("Johannesburg", "SAST-2"),
    ("Cairo", "EET-2EEST,M4.5.5/0,M10.5.4/24"),
    ("Athens, Helsinki, Kyiv", "EET-2EEST,M3.5.0/3,M10.5.0/4"),
    ("Istanbul", "<+03>-3"),
    ("Moscow", "MSK-3"),
    ("Nairobi", "EAT-3"),
    ("Tehran", "<+0330>-3:30"),
    ("Dubai", "<+04>-4"),
    ("Karachi", "PKT-5"),
    ("Delhi, Mumbai", "IST-5:30"),
    ("Kathmandu", "<+0545>-5:45"),
    ("Dhaka", "<+06>-6"),
    ("Bangkok, Jakarta", "<+07>-7"),
    ("Singapore", "<+08>-8"),
    ("Beijing, Shanghai", "CST-8"),
    ("Perth", "AWST-8"),
    ("Tokyo", "JST-9"),
    ("Seoul", "KST-9"),
    ("Adelaide", "ACST-9:30ACDT,M10.1.0,M4.1.0/3"),
    ("Brisbane", "AEST-10"),
    ("Sydney, Melbourne", "AEST-10AEDT,M10.1.0,M4.1.0/3"),
    ("Auckland", "NZST-12NZDT,M9.5.0,M4.1.0/3"),
)

_FLOOR_YEAR = 2020          # a machine clock before this was never set


def _days(y, m, d):
    """Days from 1970-01-01 to y-m-d."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


def _civil(days):
    """(year, month, day) of `days` from 1970-01-01."""
    z = days + 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    m = mp + 3 if mp < 10 else mp - 9
    return yoe + era * 400 + (m <= 2), m, doy - (153 * mp + 2) // 5 + 1


def _leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


_MDAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _rule_day(y, rule):
    """Rule `rule`'s day in year `y`, as days from 1970-01-01."""
    kind = rule[0]
    if kind == "M":
        _k, m, w, d = rule
        first = _days(y, m, 1)
        day = first + (d - (first + 4) % 7) % 7 + 7 * (w - 1)
        n = _MDAYS[m - 1] + (1 if m == 2 and _leap(y) else 0)
        while day >= first + n:
            day -= 7
        return day
    n = rule[1]
    if kind == "J":
        n = n - 1 + (1 if _leap(y) and n >= 60 else 0)
    return _days(y, 1, 1) + n


# -- the TZ string ---------------------------------------------------------------

def _name(s, i):
    """The zone name at s[i]: (name, next index), or (None, i)."""
    if i < len(s) and s[i] == "<":
        j = s.find(">", i)
        return (s[i + 1:j], j + 1) if j > i + 1 else (None, i)
    j = i
    while j < len(s) and s[j].isalpha():
        j += 1
    return (s[i:j], j) if j - i >= 3 else (None, i)


def _hms(s, i, signed):
    """[+|-]hh[:mm[:ss]] at s[i]: (seconds, next index), or (None, i)."""
    sign = 1
    if signed and i < len(s) and s[i] in "+-":
        sign = -1 if s[i] == "-" else 1
        i += 1
    total = 0
    for part in range(3):
        j = i
        while j < len(s) and s[j].isdigit():
            j += 1
        if j == i or j - i > 3:
            return (None, i) if part == 0 else (None, i - 1)
        total += int(s[i:j]) * (3600, 60, 1)[part]
        i = j
        if i >= len(s) or s[i] != ":" or part == 2:
            break
        i += 1
    return sign * total, i


def _rule(s, i):
    """A rule and its time at s[i]: ((rule, seconds), next index), or None."""
    if i < len(s) and s[i] == "M":
        parts = []
        i += 1
        for k in range(3):
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            if j == i:
                return None
            parts.append(int(s[i:j]))
            i = j
            if k < 2:
                if i >= len(s) or s[i] != ".":
                    return None
                i += 1
        m, w, d = parts
        if not (1 <= m <= 12 and 1 <= w <= 5 and 0 <= d <= 6):
            return None
        rule = ("M", m, w, d)
    else:
        kind = "D"
        if i < len(s) and s[i] == "J":
            kind = "J"
            i += 1
        j = i
        while j < len(s) and s[j].isdigit():
            j += 1
        if j == i:
            return None
        n = int(s[i:j])
        if (kind == "J" and not 1 <= n <= 365) or n > 365:
            return None
        rule = (kind, n)
        i = j
    at = 7200
    if i < len(s) and s[i] == "/":
        at, i = _hms(s, i + 1, True)
        if at is None:
            return None
    return (rule, at), i


def parse(tz):
    """A TZ string as (standard name, its offset east, summer name or None,
    its offset east, (start rule, time), (end rule, time)), or None."""
    if not isinstance(tz, str) or not tz:
        return None
    s = tz[1:] if tz[0] == ":" else tz
    std, i = _name(s, 0)
    if std is None:
        return None
    off, i = _hms(s, i, True)
    if off is None:
        return None
    if i == len(s):
        return (std, -off, None, -off, None, None)
    dst, i = _name(s, i)
    if dst is None:
        return None
    doff = off - 3600
    if i < len(s) and s[i] != ",":
        doff, i = _hms(s, i, True)
        if doff is None:
            return None
    rules = ",M3.2.0,M11.1.0" if i == len(s) else s[i:]
    got = _rule(rules, 1) if rules[0] == "," else None
    if got is None:
        return None
    start, j = got
    got = _rule(rules, j + 1) if j < len(rules) and rules[j] == "," else None
    if got is None or got[1] != len(rules):
        return None
    return (std, -off, dst, -doff, start, got[0])


# -- the zone in force -------------------------------------------------------------

_zone = [UTC, parse(UTC)]
# The transitions of the year last asked about, Unix seconds:
# [first second, past the last, summer starts, summer ends].
_year = [0, 0, 0, 0]


def set_zone(tz):
    """Make `tz` the zone in force: False, and UTC, where it does not parse."""
    z = parse(tz)
    ok = z is not None
    if not ok:
        tz, z = UTC, parse(UTC)
    _zone[0], _zone[1] = tz, z
    _year[0] = _year[1] = 0
    return ok


def zone():
    return _zone[0]


def place(tz):
    """The picker's name for `tz`, else its standard name (a TZ string set
    some other way)."""
    for name, t in ZONES:
        if t == tz:
            return name
    z = parse(tz)
    return z[0] if z is not None else "UTC"


def utc_label(east):
    """An offset as the picker shows it: UTC, UTC+1, UTC-3:30."""
    if east == 0:
        return "UTC"
    a = -east if east < 0 else east
    h, m = a // 3600, a % 3600 // 60
    return "UTC%s%d%s" % ("-" if east < 0 else "+", h, (":%02d" % m) if m else "")


def offset(t):
    """Seconds east of UTC in the zone in force at Unix time `t`."""
    z = _zone[1]
    std = z[1]
    if z[2] is None:
        return std
    yr = _year
    if not yr[0] <= t < yr[1]:
        y = _civil((t + std) // 86400)[0]
        yr[0] = _days(y, 1, 1) * 86400 - std
        yr[1] = _days(y + 1, 1, 1) * 86400 - std
        (srule, sat), (erule, eat) = z[4], z[5]
        yr[2] = _rule_day(y, srule) * 86400 + sat - std
        yr[3] = _rule_day(y, erule) * 86400 + eat - z[3]
    start, end = yr[2], yr[3]
    summer = start <= t < end if start < end else not end <= t < start
    return z[3] if summer else std


# -- the clock's state ----------------------------------------------------------------

_kernel = [None, False]     # moy_net.clock_state, and whether it was looked for


def _state_fn():
    k = _kernel
    if not k[1]:
        k[1] = True
        try:
            import moy_net
            k[0] = getattr(moy_net, "clock_state", None)
        except ImportError:
            k[0] = None
    return k[0]


# Seconds from 1970 to `time.time()`'s epoch.
_EPOCH = 946684800 if time.gmtime(0)[0] == 2000 else 0


def _now():
    return int(time.time()) + _EPOCH


def now():
    return _now()


_floor = _days(_FLOOR_YEAR, 1, 1) * 86400


def synced():
    fn = _state_fn()
    if fn is not None:
        return fn() == SYNCED
    return _now() >= _floor


def localtime(t=None):
    if t is None:
        if not synced():
            return None
        t = _now()
    t += offset(t)
    days = t // 86400
    secs = t - days * 86400
    y, m, d = _civil(days)
    return (y, m, d, secs // 3600, secs % 3600 // 60, secs % 60, (days + 3) % 7,
            days - _days(y, 1, 1) + 1)


def set_server(name):
    """The NTP server the board's next sync asks; False where the name is too
    long for the kernel."""
    fn = _state_fn()
    if fn is None:
        return True
    import moy_net
    try:
        moy_net.clock_server(name if isinstance(name, str) and name else None)
    except (ValueError, AttributeError):
        return False
    return True
