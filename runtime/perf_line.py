"""The PERF line: ONE format, ONE field order, every board (#206 item 2).

WHAT THIS IS. Every ~2s while Settings -> PERF DIAG is on, each board puts one
line on serial naming what its frame cost; with it off (kid mode, the default)
there is no line at all, and whatever reads one turns the diag on for its
measurement and puts it back. The writer is the kernel's
(native/moy_kernel/moy_perf.c); this file is the reader.
`tools/p4_perf.py --diag` reads the line for #66's per-phase numbers (its
default, the shipping fps, reads the same `_frames_drawn` counter with the diag
off), so the line is a CONTRACT -- and until 2026-08-28 it was three contracts
wearing one name:

    T-Deck   Moybyte <ms> PERF cart=<n> fps=<n> net=<t|-> flush=<ms> draw=<ms>
    P4       PERF fps=<d>/<n> net=<t|-> busy=<n>ms draw= ... cart=<n>
    Guition  PERF fps=<d>/<n> busy=<n>ms draw= ... cart=<n>

Three shapes, three producers, one name. The Guition's said so in its own
comment ("the P4's PERF line, verbatim shape"), which is what a copy looks
like; the T-Deck's went through the offline diag ring, and BOTH readers filter
on `line.startswith("PERF ")`, so that board's `Moybyte <uptime> ` prefix meant
every T-Deck sample was silently dropped by the tool that produces the fps
ledger. The board most in need of measuring was the one invisible to it.

THE FIELD SET IS THE UNION AND IT IS THE SAME EVERYWHERE (owner call,
2026-08-28: "we need the same format for all three, that comes from the same
data"). Same names, same order, same conversions, on every board -- because
per-board subsets are exactly what drifted.

A FIELD A BOARD CANNOT MEASURE PRINTS `-`, NEVER 0. This is the 2026-08-22
doctrine and the whole reason `fold=0` hid for weeks: a frozen 0 is also what a
broken lever looks like, so a board with no lever must say so. `net=-` and
`cart=-` already spelled it; every optional field now follows. The T-Deck and
the Guition have no PPA and no DSI fences, and neither runs the windowed WM, so
those columns read `-` there -- and a P4 whose windowed WM did not run in a
sample reads `-` in the wm columns too, which is the honest answer to "what did
you measure".

TOKENISABLE ON WHITESPACE, because that is what both readers do. Hence two
rules the emitter enforces rather than trusting callers with: a cart title is
SLUGGED (the T-Deck's diag has always done this, for this reason), and a
compound value is joined with `/` and never a space -- which is why the
launcher's frame split is `home=wp/grid/bar` and not the P4's old trailing
` home(wp=.. grid=.. bar=..)`, whose inner `=` signs a tokeniser reads as
fields of their own.

Pure: no imports, no state, host and MicroPython alike. It is the READER --
`parse_perf` is what `tools/p4_perf.py` uses -- and FIELDS below is the
writer's table too: moy_perf.c's field table names the same fields in the same
order with the same units, and tests/test_device_boot.py holds every line the
C writes to this parser, so the two halves of the contract cannot drift
apart.
"""

ABSENT = "-"

# (name, printf spec, unit suffix). Order IS the line's order. A spec with more
# than one conversion is a compound, `/`-joined. moy_perf.c's FIELDS is this
# table in C.
FIELDS = (
    ("cart", "%s", ""),                 # slugged title, `-` at the launcher
    ("fps", "%d/%d", ""),               # frames DRAWN / frames LOOPED, per second
    ("net", "%d", ""),                  # #65 lockstep ticks/s; `-` = no session
    ("tick", "%d/%d", ""),              # #217 logic rate / draw divisor; `-` = no paced game
    ("miss", "%d", ""),                 # #217 frames whose tick debt was written off, this sample
    ("busy", "%dms", "ms"),             # mean loop ms excluding the pacing sleep
    ("draw", "%.0f", ""),               # ws._draw_ms   } the shared console's
    ("flush", "%.0f", ""),              # ws._flush_ms  } EMA phase split
    ("logic", "%.0f", ""),              # ws._upd_ms    } (cart update)
    ("render", "%.0f", ""),             # ws._cart_ms   } (cart draw)
    ("chrome", "%.0f", ""),             # ws._chrome_ms } (bar/cursor/overlays)
    ("wmr", "%d", ""),                  # windowed WM: drag backdrop restore ms
    ("wmw", "%d", ""),                  # windowed WM: window-stack pass ms
    ("wms", "%d", ""),                  # windowed WM: window content stamp ms
    ("ppa", "%d/%d/%d/%d/%d", ""),      # P4 overlap deltas: deferred/obsolete/
                                        # reuse-fences/game-fences/timeouts
    ("fence_ms", "%.1f", ""),           # P4: composite fence ms this sample
    ("gfence_ms", "%.1f", ""),          # P4: game fence ms (hides in busy= else)
    ("home", "%d/%d/%d", ""),           # launcher split: wallpaper/grid/bar ms
    ("gc", "%d/%d/%d", ""),             # collections / their pause us / longest
                                        # pause us, this sample (gc.pauses())
)

_NAMES = tuple(n for n, _s, _u in FIELDS)

def slug(name):
    """A cart title as ONE token, as the writer slugs it (moy_perf.c): the
    line is tokenised on whitespace by both readers, so `cart=Brick Siege`
    would arrive as a field `cart=Brick` and a stray word. A reader matching
    a title against the line slugs it the same way."""
    out = name
    if not isinstance(out, str):          # str() of a str is a copy on device
        try:
            out = str(name)
        except Exception:  # noqa: BLE001 -- a diag never raises on its own input
            return "?"
    for bad in (" ", "\t", "\n", "\r"):
        out = out.replace(bad, "_")
    return out or "?"


def parse_perf(line):
    """A PERF line -> {field: value}, or None if the line is not one.

    Tolerates the diag ring's `Moybyte <uptime> ` stamp: the T-Deck rings every
    sample for the offline SD log and replays the ring to serial at the next
    boot, so a prefixed PERF line is a legitimate thing to read. Both readers
    used to filter on `startswith("PERF ")` and threw that board away.

    An absent field comes back None -- never 0, which is the whole point of the
    `-`. A compound comes back as a tuple of floats; `cart` stays a string."""
    s = line.strip()
    if s.startswith("Moybyte "):
        head = s[8:].split(" ", 1)
        if len(head) == 2 and head[0].isdigit():
            s = head[1]
    if not s.startswith("PERF "):
        return None
    out = {}
    for tok in s[5:].split():
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        if v == ABSENT:
            out[k] = None
        elif k == "cart":
            out[k] = v
        else:
            for _n, _spec, unit in FIELDS:
                if _n == k and unit and v.endswith(unit):
                    v = v[:-len(unit)]
                    break
            try:
                parts = tuple(None if p == ABSENT else float(p)
                              for p in v.split("/"))
            except ValueError:
                out[k] = v
                continue
            out[k] = parts[0] if len(parts) == 1 else parts
    return out or None
