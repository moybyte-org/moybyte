"""What a board's boot ACTUALLY runs, as text, for the source-pinning guards.

A board's `modules/moy_runtime.py` used to BE its whole boot, so a guard that
grepped that one file saw every service the board wires, every step of its
boot order, every hook of its frame loop. Two dozen guards across this suite
are written that way, and they are the reason those mechanisms are one body
instead of four copies.

Since 2026-09-09 the two P4 boards delegate their desktop body to the shared
`device/p4_desktop.py` -- one spine for both, because their two copies had
drifted into fifty differing lines of which all but five were the board's own
name in a print string. Grepping the board file alone would then silently stop
seeing the wiring it pins: 26 of those guards went red the moment the spine
landed, which is exactly them working.

So a board resolves to its own module PLUS every shared body it delegates to,
and a guard keeps asking the question it was written to ask. A board that
delegates nothing reads exactly as before.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The shared bodies a board's runtime may delegate its boot to, keyed by the
# import that proves it does. Add a row when a new spine is taken, not a
# special case in a test.
_SPINES = {
    "from p4_desktop import": ROOT / "device" / "p4_desktop.py",
}


def runtime_text(path):
    """The text of `path` (a board's moy_runtime.py) plus every shared spine
    it delegates to. Takes a str or Path, absolute or repo-relative."""
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    src = p.read_text(encoding="utf-8")
    for marker, spine in _SPINES.items():
        if marker in src:
            src += "\n\n# ---- " + spine.name + " (taken by the board above) ----\n"
            src += spine.read_text(encoding="utf-8")
    return src


def wiring_source(path):
    """The PATH whose `run_desktop` actually wires this board's console: the
    shared spine where the board's own module delegates to one, else the board
    module itself. For the guards that parse a boot function rather than grep
    it -- the board's `run_desktop` is three lines of arguments now, and the
    body they were written to inspect lives in the spine."""
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    src = p.read_text(encoding="utf-8")
    for marker, spine in _SPINES.items():
        if marker in src:
            return spine
    return p
