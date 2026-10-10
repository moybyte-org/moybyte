"""The app ABI's role table (`native/moy_app/roles.json`, #224 sprint 5) and
the nets held to it.

The table is one row per verb an app reaches through its `AppContext`: the
role, the verb, where it is served, the permission that grants it to a user
app, and its wasm import type. It is to the app ABI what moy-spec's
`wasm-imports.json` is to the cart verbs, and these tests keep everything that
restates it equal to it:

- `app_context.ROLES` is the table's roles, in its order;
- every role object `AppContext` builds answers exactly the table's verbs, and
  every verb an app calls on the two shared objects (the ArtworkService and the
  clipboard) is a row;
- the grant column is `system_api`'s permission map, and a role
  `NEVER_GRANTED` names is never granted;
- `docs/app_api_v1.md`'s role table lists exactly the rows;
- each shipped app's drawn frame calls each verb no more than its budget.

The roles' semantic trace (`tests/test_semantic_traces.py`) drives every row
over a real store on CPython and on the T-Deck's own desktop; its "uncalled"
line is the coverage ratchet.
"""

import json
import re
from pathlib import Path

import pytest

from ws_helpers import build_ws

from runtime import app_context as _ac
from runtime import system_api as _api

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "native" / "moy_app" / "roles.json"
SERVERS = ("python", "shell", "c")


def _rows():
    return json.loads(TABLE.read_text())["rows"]


def _verbs():
    out = {}
    for r in _rows():
        out.setdefault(r["role"], []).append(r["verb"])
    return out


def test_the_table_is_well_formed():
    seen = set()
    for r in _rows():
        assert set(r) == {"role", "verb", "server", "grant", "wasm"}, r
        key = (r["role"], r["verb"])
        assert key not in seen, "row twice: %s.%s" % key
        seen.add(key)
        assert r["server"] in SERVERS, r
        assert r["wasm"] is None or isinstance(r["wasm"], str), r


def test_roles_is_the_tables_roles_in_order():
    assert tuple(_verbs()) == _ac.ROLES


# What AppContext builds for each role, where the role is an object of its own.
_CLASSES = {"damage": _ac.Damage, "surface": _ac.Surface, "theme": _ac.Theme,
            "files": _ac.Files, "carts": _ac.Carts, "nav": _ac.Nav,
            "prefs": _ac.Prefs, "notify": _ac.Notify,
            "wallpaper": _ac.WallpaperRole, "install": _ac.Installer}

# Attributes a role object holds that are not verbs: the storage roles' raw
# in-session view, which `batch(fn)` hands its `fn`; and the wallpaper role's
# inherited storage machinery, which its own copy verbs use and its class
# keeps out of its surface (no app reads its readiness or opens its session).
_NOT_VERBS = {"raw"}
_INHERITED = {"wallpaper": {"batch", "readable", "ready"}}


def _public(role, cls):
    skip = _NOT_VERBS | _INHERITED.get(role, set())
    return {n for n in dir(cls) if not n.startswith("_") and n not in skip
            and callable(getattr(cls, n))}


@pytest.mark.parametrize("role", sorted(_CLASSES))
def test_each_role_object_answers_exactly_its_rows(role):
    verbs = set(_verbs()[role])
    have = _public(role, _CLASSES[role])
    assert have - verbs == set(), "%s verbs with no row: %s" % (role, sorted(have - verbs))
    assert verbs - have == set(), "%s rows with no verb: %s" % (role, sorted(verbs - have))


def _shared_object_calls(handle_names, modules):
    """Every `<handle>.<verb>(` an app module makes on a shared role object."""
    out = set()
    for mod in modules:
        text = (ROOT / "runtime" / (mod + ".py")).read_text()
        for h in handle_names:
            for m in re.finditer(r"(?<![\w.])" + re.escape(h) + r"\.([a-z_]+)\(", text):
                out.add(m.group(1))
    return out


def test_the_shared_objects_rows_are_what_apps_call():
    """`ctx.artwork` and `ctx.clipboard` are objects, not role classes, so
    their verbs are the methods apps call on them: each row must be a method,
    and each method an app calls through the handle must be a row."""
    from runtime.artwork import ArtworkService
    from runtime.widgets import Clipboard
    verbs = _verbs()
    for role, cls in (("artwork", ArtworkService), ("clipboard", Clipboard)):
        for v in verbs[role]:
            assert callable(getattr(cls, v, None)), "%s.%s is not a method" % (role, v)
    art = _shared_object_calls(
        ("self._art", "art"),
        ("artwork", "appearance_app", "storybook_app", "files_app"))
    art |= _shared_object_calls(("ws.artwork", "self.artwork"), ("console",))
    assert art - set(verbs["artwork"]) == set(), \
        "artwork verbs called with no row: %s" % sorted(art - set(verbs["artwork"]))
    clip = _shared_object_calls(("self._clip", "self.clip"),
                                ("storybook_app", "editors_code"))
    assert clip - set(verbs["clipboard"]) == set(), \
        "clipboard verbs called with no row: %s" % sorted(clip - set(verbs["clipboard"]))


def test_the_grant_column_is_the_permission_map():
    perm_for = {}
    for perm, role in _api._ROLE_FOR.items():
        perm_for[role] = perm
    for r in _rows():
        want = perm_for.get(r["role"], "never")
        assert r["grant"] == want, "%s.%s grant %r, the map says %r" % (
            r["role"], r["verb"], r["grant"], want)
        if r["role"] in _api.NEVER_GRANTED:
            assert r["grant"] == "never", r


def _doc_table():
    text = (ROOT / "docs" / "app_api_v1.md").read_text()
    out = {}
    for line in text.splitlines():
        m = re.match(r"\| `ctx\.([a-z]+)` \|.*\| (.*) \|$", line)
        if m:
            out[m.group(1)] = [v.rstrip("()") for v in re.findall(r"`([a-z_]+(?:\(\))?)`", m.group(2))]
    return out


def test_the_doc_table_lists_exactly_the_rows():
    doc = _doc_table()
    verbs = _verbs()
    assert list(doc) == list(verbs), "the doc's roles are not the table's"
    for role, vs in verbs.items():
        if role == "shell":
            continue
        assert doc[role] == vs, "docs/app_api_v1.md's %s verbs drift: %s vs %s" % (
            role, doc[role], vs)


# -- per-frame budgets ---------------------------------------------------------
#
# Each shipped app's drawn frame, as `ws.frame` paints it with the app on top,
# and the most calls of each row it makes per frame. The caps are the MEASURED
# counts with no slack, so a role read added inside a per-widget helper fails
# here instead of costing a call per widget on glass. A row absent from an
# app's budget is never called by its frame. `surface.canvas` is read at least
# once per frame by every app: the lower bound that keeps the counter honest.

BUDGETS = {
    "artwork": {"surface.canvas": 1, "theme.colors": 25, "surface.glyph": 13},
    "appearance": {"surface.canvas": 1, "theme.colors": 9,
                   "wallpaper.preview": 1, "wallpaper.carts": 1},
    "storybook": {"surface.canvas": 1, "theme.colors": 2, "carts.all": 1},
    "files": {"surface.canvas": 1, "theme.colors": 6},
    "calc": {"surface.canvas": 1, "theme.colors": 1, "theme.light": 1},
    "getcarts": {"surface.canvas": 1, "theme.colors": 1},
}


def _count_rows(ws, app):
    """Wrap every row's verb on the role objects the app's context holds (the
    shared artwork and clipboard objects included), on the instances."""
    counts = {}
    ctx = app.ctx
    for r in _rows():
        obj = getattr(ctx, r["role"], None)
        if obj is None or r["role"] == "shell":
            continue
        real = getattr(obj, r["verb"])
        key = r["role"] + "." + r["verb"]

        def counted(*a, _real=real, _key=key, **kw):
            counts[_key] = counts.get(_key, 0) + 1
            return _real(*a, **kw)

        setattr(obj, r["verb"], counted)
    return counts


@pytest.mark.parametrize("kind", sorted(BUDGETS))
def test_each_apps_drawn_frame_keeps_its_role_budget(tmp_path, kind):
    ws = build_ws(tmp_path)
    app = ws._apps_by_id[kind]
    assert ws.open_app(app), kind + " has no identity cart"
    for _ in range(3):                       # the open's own settling frames
        ws._dirty = True
        ws.frame(1 / 30.0)
    counts = _count_rows(ws, app)
    frames = 4
    for _ in range(frames):
        ws._dirty = True
        ws.frame(1 / 30.0)
    assert counts.get("surface.canvas", 0) >= frames, \
        "no canvas() read counted in %d frames: is the counter on the app?" % frames
    per = {k: -(-v // frames) for k, v in counts.items()}
    over = {k: (v, BUDGETS[kind].get(k, 0)) for k, v in per.items()
            if v > BUDGETS[kind].get(k, 0)}
    assert not over, "%s's frame exceeds its role budget (per frame, cap): %s" % (
        kind, over)
