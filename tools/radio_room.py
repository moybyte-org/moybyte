"""The bench's one radio room: a machine-wide lock a run of a multiplayer
seed cart holds for its length.

Two consoles on the same multiplayer cart within radio range form a match by
design (docs/netplay_v1.md: being in the same room is the pairing ceremony):
the guest's re-run is no HOME launch (stop `place`), a lockstep run stalls on
the peer's inputs, and neither is the solo run a check or a measurement
reads. `tools/board.py pass` runs every board's suite and perf stage at
once, so every run of a multiplayer cart there -- a suite's check
(tests/on_glass.py) or the perf stage's (tools/perf_pass.py) -- takes this
flock under ~/.cache/moybyte; the link stops when the run ends, and the next
board's run finds the room quiet. A console driven by hand outside them is
not in the lock: one left on a multiplayer cart is a peer every run of it
meets.
"""

import contextlib
import fcntl
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def multiplayer_titles(root=ROOT):
    """The seed carts whose manifest asks for `multiplayer`: a run of one
    arms the radio link."""
    titles = set()
    for m in (root / "system_carts").glob("*.moy/manifest.json"):
        man = json.loads(m.read_text(encoding="utf-8"))
        if "multiplayer" in ((man.get("moybyte") or {}).get("permissions") or ()):
            titles.add(man.get("title"))
    return titles


@contextlib.contextmanager
def radio_room(title):
    """Hold the room for a run of `title` when it is a multiplayer cart."""
    if title not in multiplayer_titles():
        yield
        return
    cache = Path.home() / ".cache" / "moybyte"
    cache.mkdir(parents=True, exist_ok=True)
    with open(cache / "radio-room.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
