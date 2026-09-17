"""What a board's boot ACTUALLY runs, as text, for the source-pinning guards.

A board's `modules/moy_runtime.py` used to BE its whole boot, so a guard that
grepped that one file saw every service the board wires, every step of its
boot order, every hook of its frame loop. Two dozen guards across this suite
are written that way, and they are the reason those mechanisms are one body
instead of four copies.

A board delegates its boot body to a shared spine now -- the two P4 boards to
`device/p4_desktop.py` (the P4 tier: PPA canvas, windowed WM, C6), and every
console board, that one included, to `device/desktop_spine.py` (the boot
order, the service set, the frame loop). Grepping the board file alone would
silently stop seeing the wiring a guard pins: when the P4 body first moved,
26 guards went red the moment it landed, which is exactly them working.

So a board resolves to its own module PLUS every shared body it delegates to,
in the order it delegates, and a guard keeps asking the question it was
written to ask. A board that delegates nothing reads exactly as before.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The shared bodies a board's runtime may delegate its boot to, keyed by the
# import that proves it does: (the spine, the function in it that wires the
# console). Add a row when a new spine is taken, not a special case in a test.
_SPINES = {
    "from p4_desktop import": (ROOT / "device" / "p4_desktop.py", "run_desktop"),
    "from desktop_spine import": (ROOT / "device" / "desktop_spine.py",
                                  "build_desktop"),
}


def _path(path):
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def wiring_chain(path):
    """[(path, function)] from the board's own `run_desktop` down through every
    spine it takes, in delegation order -- a P4 board reads as its module,
    then `p4_desktop`, then `desktop_spine`. Takes a str or Path, absolute or
    repo-relative."""
    p = _path(path)
    chain = [(p, "run_desktop")]
    src = p.read_text(encoding="utf-8")
    seen = set()
    while True:
        nxt = None
        for marker, (spine, fn) in _SPINES.items():
            if marker in src and spine not in seen:
                nxt = (spine, fn)
                break
        if nxt is None:
            return chain
        seen.add(nxt[0])
        chain.append(nxt)
        src = nxt[0].read_text(encoding="utf-8")


def runtime_text(path):
    """The text of `path` (a board's moy_runtime.py) plus every shared spine
    it delegates to."""
    out = []
    for i, (p, _fn) in enumerate(wiring_chain(path)):
        if i:
            out.append("\n\n# ---- " + p.name + " (taken by the board above) ----\n")
        out.append(p.read_text(encoding="utf-8"))
    return "".join(out)
