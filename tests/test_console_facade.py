"""The console's façade, as final rules (docs/refactor_2026-11.md, phase 9).

`Workstation` is a compositor with services: each `console_*` module holds
one object over the console that its `__init__` builds (`ws.router`,
`ws.home`, `ws.spine`, ...; tests/ws_services.py reads them), beside the six
collaborators of the 2026-08 carve. Four rules hold it there, none of them a
count:

  1. NO PRIVATE NAME IS READ FROM OUTSIDE ITS OWNER. A `ws._x` (or
     `ws.<service>._x`), `wm._x`, `bar_layer._x` or `player._x` read, or a
     `getattr` of one by literal, in runtime/, device/, firmware/ or tools/
     outside the module that owns it is a private that became an API without
     anyone deciding so. A name the owner never defines (a tool's own stash
     on the console over the serial link) is not the owner's private.

  2. THE PROJECTIONS ARE THE SIX BIG ONES. `cart`, `sheet`, `ns`,
     `cart_error`, `menu_view` and `screen` stay properties on the façade for
     the outside callers and tests; nothing else is a property forward, no
     verb is a one-call forward to a collaborator or a service, and no
     service's module reads one of the six -- a service names the owning
     object (`ws.run.project.cart`, `ws.wm.top_kind()`).

  3. EACH SERVICE'S MODULE HOLDS ONLY ITS OWN VERBS: one class, its service,
     and no verb that only hands the call to another service.

  4. WHAT `getattr(ws, "X", default)` EXPECTS TO FIND IS THERE. A moved
     attribute does not raise behind a defaulted probe -- it silently returns
     the default -- so every probed name is checked against a console that has
     built and drawn a frame.
"""

import ast
import re
import subprocess
from pathlib import Path

from ws_helpers import build_desktop_ws, build_ws
from ws_services import classdef, services
from tools.board_config import runtime_file

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = runtime_file("console.py")

# The collaborators of the 2026-08 carve (docs/history/console_architecture_2026-08.md).
COLLABORATORS = ("web", "prefs", "covers", "carts", "look", "history")

# The services Workstation builds over itself, by attribute: (class, module).
SERVICES = {
    "router": ("InputRouter", "console_input"),
    "home": ("Home", "console_home"),
    "text": ("Text", "console_text"),
    "binding": ("CanvasBinding", "console_canvas"),
    "perf": ("PerfMeters", "console_perf"),
    "toggles": ("SettingsToggles", "console_settings"),
    "saves": ("Saves", "console_saves"),
    "notices": ("Notices", "console_notices"),
    "intents": ("Intents", "console_intents"),
    "spine": ("Spine", "console_spine"),
}

# The projections the façade keeps, over the objects that own the state.
PROJECTIONS = {
    "project": {"cart", "sheet"},
    "player": {"cart_error", "ns"},
    "editor_app": {"menu_view"},
    "wm": {"screen"},
}
SIX = set().union(*PROJECTIONS.values())


def _workstation():
    return classdef(CONSOLE, "Workstation")


def _classes():
    """The Workstation and every service class."""
    return [_workstation()] + [cls for _a, _n, _m, cls in services()]


def _methods():
    for cls in _classes():
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield fn


def _statements(fn):
    """The function's body with a leading docstring dropped."""
    body = fn.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return body


def _is_property(fn):
    return any((isinstance(d, ast.Name) and d.id == "property")
               or (isinstance(d, ast.Attribute) and d.attr in ("setter", "getter"))
               for d in fn.decorator_list)


def _tracked(*tops):
    out = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "*.py"],
                                  text=True).split()
    return [p for p in out if p.split("/", 1)[0] in tops]


# -- the services -------------------------------------------------------------

def test_the_walk_sees_every_service():
    """The console has no base class, and builds exactly these services, each
    from the module SERVICES pins."""
    assert not _workstation().bases
    assert {a: (n, m) for a, n, m, _c in services()} == SERVICES


def test_each_service_module_holds_only_its_service():
    """Rule 3: one class per service module, and no verb on it whose whole
    body hands the call to another service or a collaborator."""
    others = set(SERVICES) | set(COLLABORATORS)
    for attr, name, mod, cls in services():
        tree = ast.parse(runtime_file(mod + ".py").read_text(encoding="utf-8"))
        classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
        assert classes == [name], "%s.py holds %s" % (mod, classes)
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            body = [s for s in _statements(fn)
                    if not (isinstance(s, ast.Assign) and ast.unparse(s) == "ws = self.ws")]
            if len(body) != 1 or not isinstance(body[0], (ast.Return, ast.Expr)):
                continue
            call = body[0].value
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            owner = call.func.value
            hop = (isinstance(owner, ast.Attribute) and owner.attr in others
                   and ast.unparse(owner.value) in ("ws", "self.ws"))
            assert not hop, "%s.%s only forwards to %s" % (
                name, fn.name, ast.unparse(call.func))


# -- the projections ------------------------------------------------------------

def test_no_verb_is_a_one_call_forward():
    """Rule 2: a method of the console whose whole body is one call on a
    collaborator or a service is a name kept for callers that never moved.
    The callers move instead."""
    hops = set(COLLABORATORS) | set(SERVICES)
    for fn in _workstation().body:
        if not isinstance(fn, ast.FunctionDef) or _is_property(fn):
            continue
        body = _statements(fn)
        if len(body) != 1 or not isinstance(body[0], (ast.Return, ast.Expr)):
            continue
        call = body[0].value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            continue
        owner = call.func.value
        assert not (isinstance(owner, ast.Attribute)
                    and isinstance(owner.value, ast.Name) and owner.value.id == "self"
                    and owner.attr in hops), (
            "Workstation.%s forwards to self.%s.%s"
            % (fn.name, owner.attr, call.func.attr))


def test_the_projections_are_exactly_the_six():
    """Rule 2: the property forwards are the six big ones, over the objects
    that own their state, and nothing else is a property over a collaborator
    or a service (a property hop measured +5.1 us against a plain one's
    +0.5)."""
    found = {owner: set() for owner in PROJECTIONS}
    banned = set(COLLABORATORS) | set(SERVICES)
    for fn in _methods():
        if not _is_property(fn):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Attribute):
                continue
            base = node.value
            if (isinstance(base, ast.Name) and base.id == "self"
                    and node.attr in banned):
                raise AssertionError("%s is a property over self.%s"
                                     % (fn.name, node.attr))
            if node.attr in found:
                if (isinstance(base, ast.Attribute) and base.attr == "run"
                        and node.attr in ("project", "player")):
                    base = base.value
                if isinstance(base, ast.Name) and base.id == "self":
                    found[node.attr].add(fn.name)
    assert found == PROJECTIONS


def test_no_service_reads_a_projection():
    """Rule 2: a service names the object that owns the state, never the
    façade's projection of it."""
    rx = re.compile(r"(?<![\w.])(?:ws|self\.ws)\.(%s)\b" % "|".join(sorted(SIX)))
    bad = []
    for _a, _n, mod, _c in services():
        for i, line in enumerate(runtime_file(mod + ".py").read_text(
                encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            for m in rx.finditer(code):
                bad.append("%s.py:%d ws.%s" % (mod, i, m.group(1)))
    assert not bad, "a service reads the façade's projection:\n" + "\n".join(bad)


# -- the privates ---------------------------------------------------------------

def _defined(paths, recv=("self",)):
    """Every private name the given modules define on their objects: a def, a
    class attribute, or a store on `self` (or on the console, `ws`, inside a
    service)."""
    out = set()
    for p in paths:
        tree = ast.parse(Path(p).read_text(encoding="utf-8"))
        for x in ast.walk(tree):
            if isinstance(x, (ast.FunctionDef, ast.ClassDef)):
                out.add(x.name)
            if isinstance(x, ast.ClassDef):
                for st in x.body:
                    if isinstance(st, ast.Assign):
                        out.update(t.id for t in st.targets if isinstance(t, ast.Name))
            if (isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)
                    and x.value.id in recv and isinstance(x.ctx, ast.Store)):
                out.add(x.attr)
    return {n for n in out if n.startswith("_") and not n.startswith("__")}


def _owners():
    shell = sorted(str(p) for p in Path(ROOT / "runtime/shell").glob("console*.py"))
    svc = "|".join(SERVICES)
    return {
        "ws": (_defined(shell, ("self", "ws")),
               lambda f: f.startswith("runtime/shell/console"),
               r"(?:ws|_ws|__ws|ws2|nws|workstation)(?:\.(?:%s))?" % svc),
        "wm": (_defined(sorted(Path(ROOT / "runtime/wm").glob("*.py"))),
               lambda f: f.startswith("runtime/wm/"), r"(?:wm|_wm)"),
        "bar_layer": (_defined([ROOT / "runtime/shell/bar_layer.py"]),
                      lambda f: f == "runtime/shell/bar_layer.py", r"bar_layer"),
        "player": (_defined([ROOT / "runtime/run/player.py"]),
                   lambda f: f == "runtime/run/player.py", r"(?:player|pl)"),
    }


def test_no_private_is_read_from_outside_its_owner():
    """Rule 1, over the code that ships and the tools that drive it."""
    owners = _owners()
    bad = []
    for rel in _tracked("runtime", "device", "firmware", "tools"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for key, (names, own, recv) in owners.items():
            if own(rel):
                continue
            pats = (r"(?<![\w])%s\.(_[a-z]\w*)" % recv,
                    r"(?:get|has|set)attr\(\s*(?:[\w.]*\.)?%s\s*,\s*[\"'](_[a-z]\w*)[\"']"
                    % recv)
            for pat in pats:
                for m in re.finditer(pat, src):
                    if m.group(1) in names:
                        line = src.count("\n", 0, m.start()) + 1
                        bad.append("%s:%d %s.%s" % (rel, line, key, m.group(1)))
    assert not bad, "a private read from outside its owner:\n" + "\n".join(bad)


# -- the probes -------------------------------------------------------------------

# `getattr(ws, "X")` names that are deliberately absent from a host console.
# Each must STAY absent -- an entry whose name has appeared is a stale
# exemption, so the test refuses that too.
GETATTR_ABSENT = {
    "idle_ladder": "the kernel idle ladder's Settings rows (idle_ladder.py), "
                   "set only where the kernel's loop drives the frames; "
                   "settings_layer gates the rows on the probe",
    "ble_keyboard": "the BLE keyboard driver, injected only on a board that has "
                    "one; settings_layer gates its whole panel on the probe",
    # The PERF line's wm columns (#206 item 2). ABSENCE IS THE ANSWER here, not
    # a defaulted probe: wm_windowed stamps these under perf_capture, so a board
    # that does not stage it never has them and a board with the deep meters off
    # has not measured them -- either way the line prints `-`, which is a
    # different reading from a 0.
    "pf_wm_restore": "windowed-WM drag restore ms; only wm_windowed under "
                     "perf_capture writes it, and `-` is the honest reading",
    "pf_wm_windows": "windowed-WM window-stack pass ms, same condition",
    "pf_wm_stamp": "windowed-WM content stamp ms, same condition",
}

GETATTR_RE = re.compile(
    r'getattr\(\s*(?:self\.ws|self\._ws|ws|workstation)'
    r'(?:\.(%s))?\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"' % "|".join(SERVICES))

# A helper that probes BY LITERAL is still a probe: `PerfSampler._take` reads
# a console attribute and CLEARS it, so its names are checked like a getattr's.
TAKE_RE = re.compile(r'self\._take\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')


def _getattr_sites():
    """(service or "", name) -> the files that probe the console for it."""
    sites = {}
    for rel in _tracked("runtime", "device", "tools"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        found = GETATTR_RE.findall(src) + [("", n) for n in TAKE_RE.findall(src)]
        for svc, name in found:
            sites.setdefault((svc, name), set()).add(rel)
    return sites


def test_every_getattr_name_resolves_on_a_real_workstation(tmp_path):
    """Rule 4. The names are checked against a console that has been built
    AND has drawn a frame (several perf fields are written at its top)."""
    sites = _getattr_sites()
    assert len(sites) > 50, "the scan stopped finding the getattr sites"
    ws = build_ws(tmp_path / "fullscreen")
    ws.frame(1 / 30.0)
    win = build_desktop_ws(tmp_path / "windowed")
    win.frame(1 / 30.0)

    def has(console, svc, name):
        return hasattr(getattr(console, svc) if svc else console, name)

    missing = sorted(
        "%s%s (%s)" % (svc + "." if svc else "", name, ", ".join(sorted(where)))
        for (svc, name), where in sites.items()
        if not (not svc and name in GETATTR_ABSENT)
        and not has(ws, svc, name) and not has(win, svc, name))
    assert not missing, (
        "these names are probed on the console and no longer exist on it -- "
        "the probes are silently taking their defaults: " + "; ".join(missing))


def test_the_absent_list_stays_absent_and_stays_used(tmp_path):
    """Both ways an exemption rots: a name that came back (drop it) and one
    nothing probes any more (drop it too)."""
    ws = build_ws(tmp_path / "fullscreen")
    ws.frame(1 / 30.0)
    probed = {name for svc, name in _getattr_sites() if not svc}
    for name, why in GETATTR_ABSENT.items():
        assert why, name
        assert not hasattr(ws, name), (
            "%s exists on a host console now -- drop the exemption" % name)
        assert name in probed, (
            "nothing probes %s any more -- drop the exemption" % name)
