"""The two façade ratchets (#209, docs/console_architecture_2026-08.md 3d).

The carve moves state and verbs off `Workstation` onto collaborator objects,
and leaves fixed-signature forwards behind for the long tail of callers. Two
things have to be pinned, and neither is visible to the goldens:

  1. WHAT IS STILL FORWARDED. A forward is migration debt with a name on it --
     the set may shrink, and a new one must be a deliberate edit here rather
     than something a landing adds without noticing. This also pins the two
     banned shapes: a `*a, **kw` shim (which allocates a tuple per call -- the
     churn class #63/#66 were about) and a `property` forward (measured at
     +5.1us against a plain hop's +0.5us, banned repo-wide).

  2. WHAT `getattr(ws, "X", default)` STILL EXPECTS TO FIND. Eighty-odd sites
     across runtime/, device/ and tools/ read the console this way, and a moved
     attribute does not raise there -- it silently returns the default. That is
     the failure this file exists for: a landing renames `ws.webhost`, the dev
     channel's `state` snapshot quietly reports "no service" forever, and every
     host test stays green.

Both are meant to GROW: `COLLABORATORS` gains a name per landing (prefs,
covers, carts, look, history), and `FORWARDS` gains that landing's set.
"""

import ast
import re
from pathlib import Path

from ws_helpers import build_desktop_ws, build_ws

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "runtime" / "console.py"

# The collaborator attributes on Workstation. Landings C-E add: covers
# (CoverCache), carts (CartManager), look (Appearance), history
# (HistoryRouter).
COLLABORATORS = ("web", "prefs")

# name on Workstation -> method on the collaborator. Every entry is a caller
# that has not moved; the comment says who, so retiring one is a search with an
# answer rather than a guess.
FORWARDS = {
    "web": {
        "web_pin": "pin",                    # moy_webhost's start-time lambda
        "web_console_url": "url",            # dev_channel's `web`
        "park_web_console": "park",          # layers.py's contract comment
        "stop_web_console": "stop",          # the Guition on-glass suite (serial)
        "unpark_web_console": "unpark",      # tests
        "webhost_serving": "serving",        # settings_layer, tools/p4_push_web
        "webhost_label": "label",            # settings_layer, tools/p4_push_web
        "toggle_webhost": "toggle",          # settings_layer, dev_channel, p4_push_web
    },
    # Landing B. The dict itself needed no forward at all -- `ws.system` is a
    # plain alias of `prefs.settings` and SystemStore loads it IN PLACE, so
    # every raw reader and writer (settings_layer, the dev channel, the
    # launcher's favorites, app_context's Prefs role) kept working untouched.
    # Only the WRITE verb has callers left outside the kernel.
    "prefs": {
        "_persist_system": "persist",        # app_context's Prefs, dev_channel's `vol`
    },
}

# `getattr(ws, "X")` names that are deliberately absent from a host console.
# Each must STAY absent -- an entry whose name has appeared is a stale
# exemption, so the test refuses that too.
GETATTR_ABSENT = {
    "ble_keyboard": "the BLE keyboard driver, injected only on a board that has "
                    "one; settings_layer gates its whole panel on the probe",
    "_psave_asleep": "device_boot's idle-blank state, written only on glass",
    "_psave_ms": "the idle-blank timeout the dev channel's `psave` reports",
}

GETATTR_RE = re.compile(
    r'getattr\(\s*(?:self\.ws|self\._ws|ws|workstation)\s*,\s*'
    r'"([A-Za-z_][A-Za-z0-9_]*)"')


def _workstation():
    tree = ast.parse(CONSOLE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Workstation":
            return node
    raise AssertionError("Workstation is not a class in runtime/console.py")


def _statements(fn):
    """The function's body with a leading docstring dropped."""
    body = fn.body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return body


def _collaborator_call(node):
    """(collaborator, method) if `node` is a call on `self.<collaborator>`."""
    if not isinstance(node, ast.Call):
        return None
    fn = node.func
    if not (isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Attribute)
            and isinstance(fn.value.value, ast.Name)
            and fn.value.value.id == "self"
            and fn.value.attr in COLLABORATORS):
        return None
    return fn.value.attr, fn.attr


def _forwards():
    """Every Workstation method whose whole body is one collaborator call."""
    found = {}
    for fn in _workstation().body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = _statements(fn)
        if len(body) != 1:
            continue
        stmt = body[0]
        call = stmt.value if isinstance(stmt, (ast.Return, ast.Expr)) else None
        hit = _collaborator_call(call)
        if hit is not None:
            found[fn.name] = (hit[0], hit[1], fn)
    return found


def test_the_forward_set_is_exactly_what_is_pinned():
    """A new forward or a retired one is a deliberate edit to FORWARDS."""
    found = {n: (c, t) for n, (c, t, _fn) in _forwards().items()}
    pinned = {n: (c, t) for c, m in FORWARDS.items() for n, t in m.items()}
    assert found == pinned


def test_no_forward_is_a_star_args_shim():
    """`*a, **kw` allocates a tuple (and a dict) per call. Forwards are plain
    methods with the signature they forward -- fixed, and cheap."""
    for name, (_c, _t, fn) in _forwards().items():
        assert fn.args.vararg is None, "%s forwards through *args" % name
        assert fn.args.kwarg is None, "%s forwards through **kwargs" % name
        call = _statements(fn)[0].value
        assert not any(isinstance(a, ast.Starred) for a in call.args), name
        assert all(kw.arg is not None for kw in call.keywords), name


def test_no_property_forwards_to_a_collaborator():
    """Measured on this codebase: a plain hop is +0.5us, a property forward
    +5.1us. Live state is read through a method, everywhere."""
    for fn in _workstation().body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated = any(
            (isinstance(d, ast.Name) and d.id == "property")
            or (isinstance(d, ast.Attribute) and d.attr in ("setter", "getter"))
            for d in fn.decorator_list)
        if not decorated:
            continue
        for node in ast.walk(fn):
            hit = (isinstance(node, ast.Attribute)
                   and isinstance(node.value, ast.Name)
                   and node.value.id == "self"
                   and node.attr in COLLABORATORS)
            assert not hit, "%s is a property over self.%s" % (fn.name, node.attr)


def _getattr_sites():
    """name -> the files that probe the console for it."""
    sites = {}
    for top in ("runtime", "device", "tools"):
        for path in sorted((ROOT / top).rglob("*.py")):
            for name in GETATTR_RE.findall(path.read_text(encoding="utf-8")):
                sites.setdefault(name, set()).add(
                    str(path.relative_to(ROOT)))
    return sites


def test_every_getattr_name_resolves_on_a_real_workstation(tmp_path):
    """The silent-default catcher. A `getattr(ws, "X", default)` whose X has
    moved reports the default forever and raises nothing, so the name strings
    are checked against a console that has been built AND has drawn a frame
    (several perf fields are written at the top of `frame`)."""
    sites = _getattr_sites()
    assert len(sites) > 50, "the scan stopped finding the getattr sites"
    ws = build_ws(tmp_path / "fullscreen")
    ws.frame(1 / 30.0)
    win = build_desktop_ws(tmp_path / "windowed")
    win.frame(1 / 30.0)
    missing = sorted(
        "%s (%s)" % (name, ", ".join(sorted(where)))
        for name, where in sites.items()
        if name not in GETATTR_ABSENT
        and not hasattr(ws, name) and not hasattr(win, name))
    assert not missing, (
        "these names are probed on the console and no longer exist on it -- "
        "the probes are silently taking their defaults: " + "; ".join(missing))


def test_the_absent_list_stays_absent_and_stays_used(tmp_path):
    """Both ways an exemption rots: a name that came back (drop it) and one
    nothing probes any more (drop it too)."""
    ws = build_ws(tmp_path / "fullscreen")
    ws.frame(1 / 30.0)
    sites = _getattr_sites()
    for name, why in GETATTR_ABSENT.items():
        assert why, name
        assert not hasattr(ws, name), (
            "%s exists on a host console now -- drop the exemption" % name)
        assert name in sites, (
            "nothing probes %s any more -- drop the exemption" % name)
