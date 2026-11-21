"""Both UI tree layers against one table: `tests/uitree_contract/contract.json`
(every error code, node kind and prop, `takes=` value, role, retired name, app
field and verb of the UI API spec, revision 4).

Each layer's facts (the codes its source raises, the kinds and props it
accepts, its `takes=` values and roles) are compared with the table, and every
difference is a line in `tests/uitree_contract/known.json`. The test fails
when a difference appears that the list lacks (a code the table has not, a
kind or prop the table has not, a new gap) and when a listed one is gone, so
the list only shrinks, by hand, with the fix that closes it.

Python: runtime/uitree/ (SCHEMA, TAKES, ROLES, the verbs' signatures).
Lua: runtime/run/uitree.lua (ui._contract(), read through a host Lua).
"""
import inspect
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTRACT = os.path.join(HERE, "uitree_contract")
for p in (ROOT, os.path.join(ROOT, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

from board_config import runtime_file  # noqa: E402

with open(os.path.join(CONTRACT, "contract.json"), encoding="utf-8") as _f:
    TABLE = json.load(_f)

PY_FILES = [str(runtime_file(f)) for f in
            ("uitree.py", "uitree_app.py", "uitree_c.py", "uitree_editor.py", "uitree_doc.py", "uitree_pics.py")] + \
           [os.path.join(ROOT, "tools", "uitree_check.py")]
LUA_FILES = [str(runtime_file("lua_ext.py").parent / f) for f in ("uitree.lua", "uitree_check.lua")] + \
            [os.path.join(ROOT, "tools", "uitree_lua_check.py")]
CODE = re.compile(r'"([A-Z][0-9]{2})"')

# Python stores a kind's head under a prop of SCHEMA; the table's head is positional
PY_HEAD = {"group": "caption", "label": "text", "dialog": "title", "info": "label", "empty": "title",
           "picture": "image", "progress": "value", "status": "text", "col": "text", "menu": "caption",
           "page": "title"}


def _codes(paths):
    out = set()
    for p in paths:
        with open(p, encoding="utf-8") as f:
            out |= set(CODE.findall(f.read()))
    return out


def python_facts():
    import runtime  # noqa: F401  -- the flat module namespace
    import uitree as U
    kinds = {}
    for k, props in U.SCHEMA.items():
        ps = set(props) - {PY_HEAD.get(k)}
        if k == "page":                     # the page's title is a prop in the table, a head in Python
            ps = {"title"}
        kinds[k] = ps
    action = set(inspect.signature(U.action).parameters) - {"name", "label", "fn"}
    hook = set(inspect.signature(U.hook).parameters) - {"name", "fn"}
    retired_live = set()
    for v in TABLE["retired"]["verbs"]:
        fn = getattr(U, v, None)
        if callable(fn) and "N04" not in inspect.getsource(fn):
            retired_live.add(v)
    verbs = {n for n in dir(U) if callable(getattr(U, n)) and not n.startswith("_")}
    if hasattr(U, "sys"):
        verbs.add("sys")
    return {"codes": _codes(PY_FILES), "kinds": kinds, "common": {"shown"}, "action": action, "hook": hook,
            "page": kinds["page"], "takes": set(U.TAKES), "roles": set(U.ROLES), "shell_roles": set(),
            "verbs": verbs, "retired_live": retired_live, "app": set(U.APP_FIELDS)}


def lua_facts():
    import uitree_lua_check as checker
    r = subprocess.run([checker.hostlua(), os.path.join(CONTRACT, "lua_tables.lua"), checker.LAYER],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    f = {"kinds": {}, "common": set(), "action": set(), "hook": set(), "page": set(), "app": set(),
         "takes": set(), "roles": set(), "shell_roles": set(), "verbs": set()}
    for line in r.stdout.splitlines():
        tag, *rest = line.split()
        if tag == "KIND":
            f["kinds"][rest[0]] = set()
        elif tag == "PROP":
            f["kinds"][rest[0]].add(rest[1])
        elif tag in ("COMMON", "ACTION", "HOOK", "PAGE", "APP", "TAKES", "VERB"):
            f[{"COMMON": "common", "ACTION": "action", "HOOK": "hook", "PAGE": "page", "APP": "app",
               "TAKES": "takes", "VERB": "verbs"}[tag]].add(rest[0])
        elif tag == "ROLE":
            f["roles"].add(rest[0])
        elif tag == "SHELLROLE":
            f["shell_roles"].add(rest[0])
    with open(checker.LAYER, encoding="utf-8") as fh:
        src = fh.read()
    stubs = set(re.findall(r"^T\.(\w+) = (?:retired|not_here)\(", src, re.M))
    f["verbs"] -= stubs
    f["kinds"]["page"] = f["page"]
    f["codes"] = _codes(LUA_FILES)
    f["retired_live"] = {v for v in TABLE["retired"]["verbs"] if v in f["verbs"]}
    return f


def differences(f, lang):
    """Every way the layer's facts differ from the table, one line each."""
    out = []
    codes = TABLE["codes"]
    mine = {c for c, d in codes.items() if d["fix"][lang] != "—"}
    for c in sorted(f["codes"] - set(codes)):
        out.append("code reserved, raised: %s" % c if c in TABLE["reserved_codes"] else "code not in the table: %s" % c)
    for c in sorted(mine - f["codes"]):
        out.append("code never raised: %s" % c)
    kinds = TABLE["kinds"]
    common = set(TABLE["common_props"])
    for k in sorted(set(f["kinds"]) - set(kinds)):
        why = " (retired: %s)" % TABLE["retired"]["kinds"][k] if k in TABLE["retired"]["kinds"] else ""
        out.append("kind not in the table: %s%s" % (k, why))
    for k in sorted(set(kinds) - set(f["kinds"])):
        out.append("kind missing: L%s %s" % (kinds[k]["level"], k))
    child = set(TABLE["child_props"])
    for p in sorted(f["common"] - common):
        out.append("prop on every kind, not in the table: %s" % p)
    for k in sorted(set(kinds) & set(f["kinds"])):
        want = set(kinds[k]["props"])
        have = f["kinds"][k] | f["common"]
        for p in sorted(have - want - f["common"] - child):
            out.append("prop not in the table: %s.%s" % (k, p))
        for p in sorted((want | (common if k != "page" else set())) - have):
            lvl = kinds[k]["props"][p]["level"] if p in kinds[k]["props"] else 1
            out.append("prop missing: L%s %s.%s" % (lvl, k, p))
    for name, table in (("action", "action_props"), ("hook", "hook_props")):
        want = set(TABLE[table])
        for p in sorted(f[name] - want):
            out.append("%s prop not in the table: %s" % (name, p))
        for p in sorted(want - f[name]):
            out.append("%s prop missing: %s" % (name, p))
    if f["app"] is not None:
        for p in sorted(f["app"] ^ set(TABLE["app_fields"])):
            out.append("app field %s: %s" % ("not in the table" if p in f["app"] else "missing", p))
    takes = TABLE["takes"]
    for t in sorted(f["takes"] - set(takes)):
        out.append("takes not in the table: %s" % t)
    for t in sorted(set(takes) - f["takes"]):
        out.append("takes missing: L%s %s" % (takes[t]["level"], t))
    roles = TABLE["roles"]
    app_roles = {r for r, d in roles.items() if d["owner"] == "app"}
    for r in sorted(f["roles"] - app_roles):
        out.append("role not in the table: %s" % r)
    for r in sorted(app_roles - f["roles"]):
        out.append("role missing: L%s %s" % (roles[r]["level"], r))
    verbs = dict(TABLE["verbs"], **{k: d["level"] for k, d in kinds.items() if "made_by" not in d})
    for v in sorted(set(verbs) - f["verbs"]):
        out.append("verb missing: L%s %s" % (verbs[v], v))
    for v in sorted(f["retired_live"]):
        out.append("retired verb still runs: %s (%s)" % (v, TABLE["retired"]["verbs"][v]))
    return out


def _known(lang):
    with open(os.path.join(CONTRACT, "known.json"), encoding="utf-8") as f:
        return json.load(f)[lang]


def _held(lang, facts):
    got = differences(facts, lang)
    known = _known(lang)
    new = sorted(set(got) - set(known))
    gone = sorted(set(known) - set(got))
    assert not new and not gone, (
        "the %s layer moved against the contract table.\nNew (fix the layer, or list it in "
        "tests/uitree_contract/known.json with the spec's ruling):\n  %s\nGone (drop the line from known.json):\n  %s"
        % (lang, "\n  ".join(new) or "(none)", "\n  ".join(gone) or "(none)"))


def test_the_table_is_whole():
    codes = TABLE["codes"]
    assert len(codes) == 89
    for c, d in codes.items():
        assert re.fullmatch(r"[A-Z][0-9]{2}", c)
        assert d["meaning"] and d["stage"] and d["fix"]["python"] and d["fix"]["lua"], c
    assert not set(codes) & set(TABLE["reserved_codes"])
    for k, d in TABLE["kinds"].items():
        assert d["level"] in (1, 2), k
        for p, pd in d["props"].items():
            assert pd["type"] and pd["level"] in (1, 2), (k, p)
            assert p not in TABLE["retired"]["props"], (k, p)
    for k in TABLE["retired"]["kinds"]:
        assert k not in TABLE["kinds"]


def not_built_marks(f):
    """Every entry whose `designed` mark disagrees with the Python reference:
    `designed: true` is exactly what the reference does not build."""
    out = []

    def mark(what, d, built):
        if bool(d.get("designed")) == built:
            out.append("%s is %s, marked %s" % (what, "built" if built else "not built",
                                                 "designed" if built else "built"))
    for k, d in TABLE["kinds"].items():
        built = k in f["kinds"]
        mark("kind " + k, d, built)
        for p, pd in d["props"].items():
            mark("prop %s.%s" % (k, p), pd, built and (p in f["kinds"][k] or p in f["common"]))
    for name, table in (("action", "action_props"), ("hook", "hook_props")):
        for p, pd in TABLE[table].items():
            mark("%s prop %s" % (name, p), pd, p in f[name])
    for t, d in TABLE["takes"].items():
        mark("takes " + t, d, t in f["takes"])
    for r, d in TABLE["roles"].items():
        if d["owner"] == "app":
            mark("role " + r, d, r in f["roles"])
    return out


def test_the_not_built_marks_are_the_python_layer_s():
    wrong = not_built_marks(python_facts())
    assert not wrong, "set or drop `designed` in tests/uitree_contract/contract.json:\n  " + "\n  ".join(wrong)


def test_the_python_layer_holds_to_the_table():
    _held("python", python_facts())


def test_the_lua_layer_holds_to_the_table():
    _held("lua", lua_facts())


def test_the_python_layer_answers_the_queries():
    import runtime  # noqa: F401
    import uitree as U
    for q in TABLE["queries"]:
        if q != "about":
            assert callable(getattr(U, q, None)), q


def test_the_lists_name_no_line_twice():
    for lang in ("python", "lua"):
        known = _known(lang)
        assert len(known) == len(set(known)), lang


if __name__ == "__main__":       # the lists as they stand, to compare by hand
    for lang, fn in (("python", python_facts), ("lua", lua_facts)):
        print("== " + lang)
        print("\n".join(differences(fn(), lang)))
