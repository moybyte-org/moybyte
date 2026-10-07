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
    # A board's own input module, and the spine's providers (each subsystem's
    # construction, docs/kernel_survival_2026-10.md section 2 item 1): the
    # function named is the one that attaches to the Workstation, or builds.
    "from tdeck_input import": (ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
                                / "modules" / "tdeck_input.py", "build"),
    "from guition_input import": (ROOT / "firmware" / "guition_jc3248w535"
                                  / "modules" / "guition_input.py",
                                  "make_input"),
    "import wire_input": (ROOT / "device" / "wire_input.py", "start_keyboards"),
    "import wire_links": (ROOT / "device" / "wire_links.py", "wire_links"),
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
    seen = {p}
    i = 0
    while i < len(chain):
        src = chain[i][0].read_text(encoding="utf-8")
        for marker, (spine, fn) in _SPINES.items():
            if marker in src and spine not in seen:
                seen.add(spine)
                chain.append((spine, fn))
        i += 1
    return chain


def runtime_text(path):
    """The text of `path` (a board's moy_runtime.py) plus every shared spine
    it delegates to."""
    out = []
    for i, (p, _fn) in enumerate(wiring_chain(path)):
        if i:
            out.append("\n\n# ---- " + p.name + " (taken by the board above) ----\n")
        out.append(p.read_text(encoding="utf-8"))
    return "".join(out)
