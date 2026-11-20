#!/usr/bin/env python3
"""Check a Lua app written in the UI tree API, with nothing drawn.

    .venv/bin/python tools/uitree_lua_check.py [--snapshot] CART_DIR ...

Two halves, the spec's checker for Lua (its section 23):

1. The run. The app's scripts run once, in a host Lua built from the
   console's own (native/moy_lua/lua, LUA_32BITS) with the debug library the
   console leaves out, over a stub engine: every declaration is checked at
   its call, a handler's parameters against its takes (A03), and every
   registered page and every switch and row case is built
   (runtime/run/uitree_check.lua, ui._check).
2. The read. What no run sees is read from the app's source: names nothing
   declares (Q01), a nil in a ui.state table (S13), literal ui.push,
   ui.after and ui.post names and whether a key is handed (P01, A01, A02),
   writes to a derive (S09) or to a value the engine writes (S10), writes
   into a record's field (S03) and reads of a field nothing declares (S04).

Each error is one line, `error[CODE] FILE:LINE: ...`; the exit status is 1
when there is one. The host Lua is built once into .build/uitree_lua/.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LUA_SRC = os.path.join(ROOT, "native", "moy_lua", "lua")
LAYER = os.path.join(ROOT, "runtime", "run", "uitree.lua")
DRIVER = os.path.join(ROOT, "runtime", "run", "uitree_check.lua")
BUILD = os.path.join(ROOT, ".build", "uitree_lua")

MAIN_C = r"""
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"
#include <stdio.h>
int main(int argc, char **argv) {
  lua_State *L = luaL_newstate();
  luaL_openlibs(L);
  lua_newtable(L);
  for (int i = 2; i < argc; i++) { lua_pushstring(L, argv[i]); lua_rawseti(L, -2, i - 1); }
  lua_setglobal(L, "arg");
  if (argc < 2) return 2;
  if (luaL_dofile(L, argv[1]) != LUA_OK) { fprintf(stderr, "%s\n", lua_tostring(L, -1)); return 1; }
  return 0;
}
"""

LUA_STD = set("""assert collectgarbage error getmetatable ipairs load next pairs pcall print rawequal rawget
rawlen rawset require select setmetatable tonumber tostring type xpcall _G _ENV _VERSION math string table
utf8 coroutine os io debug""".split())
APP_GLOBALS = set("""ui moybyte prefs _draw _init _update _layout UI_LAUNCH UI_MAIN W H self""".split())
KEYWORDS = set("""and break do else elseif end false for function goto if in local nil not or repeat return
then true until while""".split())


def hostlua():
    """The host Lua's path, built when its sources changed."""
    srcs = [os.path.join(LUA_SRC, n) for n in sorted(os.listdir(LUA_SRC))
            if n.endswith(".c") and n not in ("lua.c", "luac.c")]
    h = hashlib.sha1(MAIN_C.encode())
    for p in srcs + [os.path.join(LUA_SRC, n) for n in sorted(os.listdir(LUA_SRC)) if n.endswith(".h")]:
        with open(p, "rb") as f:
            h.update(f.read())
    out = os.path.join(BUILD, "lua-" + h.hexdigest()[:12])
    if os.path.exists(out):
        return out
    os.makedirs(BUILD, exist_ok=True)
    main = os.path.join(BUILD, "main.c")
    with open(main, "w") as f:
        f.write(MAIN_C)
    subprocess.check_call(["cc", "-O1", "-w", "-I" + LUA_SRC, "-o", out + ".tmp", main] + srcs + ["-lm"])
    os.replace(out + ".tmp", out)
    return out


def scripts_of(cart):
    with open(os.path.join(cart, "manifest.json")) as f:
        man = json.load(f)
    return man.get("sources") or [man.get("main", "main.lua")]


def run(cart, after=()):
    """The run's lines (half 1); `after` are scripts run once the pages are
    built (a test's ui._fire calls)."""
    names = scripts_of(cart) + (["--"] + list(after) if after else [])
    r = subprocess.run([hostlua(), DRIVER, LAYER, cart] + names, capture_output=True, text=True, timeout=120)
    lines = r.stdout.splitlines()
    if r.returncode != 0:
        lines.append("ERR check: " + (r.stderr.strip() or "the host Lua failed"))
    return lines


# -- half 2: the source read --------------------------------------------------------

def _blank(src):
    """`src` with comments and string contents blanked, newlines kept; and
    the strings' texts by their start offset."""
    out, strings, i, n = [], {}, 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("--", i):
            m = re.match(r"--\[(=*)\[", src[i:])
            end = src.find("]" + m.group(1) + "]", i) + len(m.group(1)) + 2 if m else src.find("\n", i)
            end = n if end < i or end == -1 else end
            out.append(re.sub(r"[^\n]", " ", src[i:end]))
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            strings[i] = src[i + 1:j]
            out.append(c + re.sub(r"[^\n]", " ", src[i + 1:j]) + c)
            i = j + 1
        elif c == "[" and re.match(r"\[=*\[", src[i:]):
            m = re.match(r"\[(=*)\[", src[i:])
            end = src.find("]" + m.group(1) + "]", i)
            end = n if end == -1 else end + len(m.group(1)) + 2
            strings[i] = src[i + len(m.group(0)):end - len(m.group(1)) - 2]
            out.append(re.sub(r"[^\n]", " ", src[i:end]))
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out), strings


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def _err(code, where, what, fix, expected, found):
    return "error[%s] %s: %s; fix: %s (expected %s, found %s)" % (code, where, what, fix, expected, found)


def _declared(blanked):
    names = set()
    for m in re.finditer(r"\blocal\s+function\s+([A-Za-z_]\w*)", blanked):
        names.add(m.group(1))
    for m in re.finditer(r"\blocal\s+([A-Za-z_][\w\s,]*?)(?:=|\n|$)", blanked):
        names.update(x.strip() for x in m.group(1).split(",") if x.strip() and x.strip() not in KEYWORDS)
    for m in re.finditer(r"\bfunction\s+([A-Za-z_]\w*)", blanked):
        names.add(m.group(1))
    for m in re.finditer(r"\bfunction\s*[\w.:]*\s*\(([^)]*)\)", blanked):
        names.update(x.strip() for x in m.group(1).split(",") if re.match(r"^[A-Za-z_]\w*$", x.strip()))
    for m in re.finditer(r"\bfor\s+([\w\s,]+?)\s+(?:in|=)", blanked):
        names.update(x.strip() for x in m.group(1).split(","))
    for m in re.finditer(r"(?:^|\n|;|\bthen|\bdo|\belse|\bend)\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*=(?!=)",
                         blanked):
        names.update(x.strip() for x in m.group(1).split(",") if re.match(r"^[A-Za-z_]\w*$", x.strip()))
    return names


def _free_names(fname, blanked, known):
    out, seen = [], set()
    for m in re.finditer(r"[A-Za-z_]\w*", blanked):
        w, a = m.group(0), m.start()
        if w in KEYWORDS or w in known or w in seen:
            continue
        before = blanked[:a].rstrip()
        if before.endswith(".") and not before.endswith("..") or before.endswith(":") and not before.endswith("::"):
            continue
        after = blanked[m.end():].lstrip()
        if after.startswith("=") and not after.startswith("==") and (before.endswith("{") or before.endswith(",")):
            continue
        if a > 0 and blanked[a - 1].isdigit():
            continue
        seen.add(w)
        out.append(_err("Q01", "%s:%d" % (fname, _line(blanked, a)), "%r is read and nothing declares it" % w,
                        "local it, or declare it", "a declared name", repr(w)))
    return out


def read_source(cart, facts):
    """Half 2 over the app's scripts, against the run's facts."""
    errors = []
    names = scripts_of(cart)
    texts = {}
    for n in names:
        with open(os.path.join(cart, n)) as f:
            texts[n] = f.read()
    blanked = {n: _blank(t) for n, t in texts.items()}
    import importlib.util
    spec = importlib.util.spec_from_file_location("_cart_verbs", os.path.join(ROOT, "runtime", "run", "cart_verbs.py"))
    cv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cv)
    known = LUA_STD | APP_GLOBALS | set(cv.CART_VERBS)
    for n in names:
        known |= _declared(blanked[n][0])
    pages, state, fields, actions, engine = facts["pages"], facts["state"], facts["fields"], facts["actions"], facts["engine"]
    for n in names:
        b, strings = blanked[n]
        errors += _free_names(n, b, known)
        svars = set(re.findall(r"\blocal\s+([A-Za-z_]\w*)\s*=\s*ui\.state\b", b)) or {"s"}
        # S13: a nil in a ui.state table literal (Lua drops the key before the API sees it)
        for m in re.finditer(r"\bui\.state\s*\{", b):
            depth, j = 0, m.end() - 1
            while j < len(b):
                depth += {"{": 1, "}": -1}.get(b[j], 0)
                if depth == 0:
                    break
                j += 1
            for k in re.finditer(r"([A-Za-z_]\w*)\s*=\s*nil\b", b[m.end():j]):
                errors.append(_err("S13", "%s:%d" % (n, _line(b, m.end() + k.start())),
                                   "state %r is declared nil, which Lua drops" % k.group(1),
                                   "ui.opt(0) for a value that may be absent", "a typed initial value", "nil"))
        # P01: a literal ui.push names no page
        for m in re.finditer(r"\bui\.push\s*\(\s*([\"'])", b):
            name = strings.get(m.end() - 1, "")
            if name not in pages:
                errors.append(_err("P01", "%s:%d" % (n, _line(b, m.start())), "ui.push(%r) names no page" % name,
                                   "ui.page(%r, build)" % name, "one of " + ", ".join(sorted(pages)), repr(name)))
        # A01, A02: literal ui.after and ui.post names, and whether a key is handed
        for verb, at_name in (("after", 1), ("post", 0)):
            for m in re.finditer(r"\bui\.%s\s*\(" % verb, b):
                depth, j, commas = 1, m.end(), []
                while j < len(b) and depth:
                    c = b[j]
                    depth += {"(": 1, "{": 1, ")": -1, "}": -1}.get(c, 0)
                    if c == "," and depth == 1:
                        commas.append(j)
                    j += 1
                bounds = [m.end()] + [c + 1 for c in commas] + [j]
                args = [b[bounds[i]:bounds[i + 1] - 1].strip() for i in range(len(bounds) - 1)]
                if len(args) <= at_name or not args[at_name].startswith(("\"", "'")):
                    continue
                apos = bounds[at_name] + (len(b[bounds[at_name]:]) - len(b[bounds[at_name]:].lstrip()))
                name = strings.get(apos, "")
                where = "%s:%d" % (n, _line(b, m.start()))
                a = actions.get(name)
                if a is None or (verb == "post" and a[1] != "hook"):
                    errors.append(_err("A01", where, "ui.%s names %r, which is no %s" % (verb, name, "hook" if verb == "post" else "action or hook"),
                                       "ui.hook(%r, handler, { takes = \"arg\" })" % name,
                                       "a declared " + ("hook" if verb == "post" else "action or hook"), repr(name)))
                    continue
                handed = len(args) > at_name + 1
                if handed != (a[0] == "arg"):
                    errors.append(_err("A02", where, "ui.%s %s %r, which takes %r" % (verb, "hands a key to" if handed else "hands nothing to", name, a[0]),
                                       "{ takes = \"arg\" } on %s, or no key" % name if handed else "hand it its key",
                                       "a key" if a[0] == "arg" else "nothing", "a key" if handed else "nothing"))
        # S09, S10, S03, S04: what a handler writes and reads through the state
        for sv in svars:
            for m in re.finditer(r"\b%s\.([A-Za-z_]\w*)((?:\.[A-Za-z_]\w*)?)(\s*=(?!=))?" % re.escape(sv), b):
                name, sub, write = m.group(1), m.group(2)[1:], bool(m.group(3))
                where = "%s:%d" % (n, _line(b, m.start()))
                ty = state.get(name)
                if ty is None:
                    continue
                if write and not sub and ty[0] == "derive":
                    errors.append(_err("S09", where, "a write to the derive %r" % name, "write the names it reads",
                                       "a read", "a write"))
                elif write and not sub and name in engine:
                    errors.append(_err("S10", where, "a write to %r, which the engine writes" % name, "read it; the engine writes it",
                                       "a read", "a write"))
                elif sub and ty[0] in ("record", "rowof"):
                    if write:
                        errors.append(_err("S03", where, "a write into %s.%s" % (name, sub),
                                           "%s.%s = { every field, with %s = ... }" % (sv, name, sub), "the whole record replaced",
                                           "a write into a field"))
                    elif sub not in fields.get(name, ()):
                        errors.append(_err("S04", where, "%s.%s reads a field %r has not" % (sv, name + "." + sub, name),
                                           "a declared field", "one of " + ", ".join(sorted(fields.get(name, ()))), repr(sub)))
    return errors


def check(cart, snapshot=False, after=()):
    """Every error of `cart` (a .moy folder), as lines; with `snapshot` the
    built pages' snapshot lines follow them."""
    lines = run(cart, after)
    facts = {"pages": [], "state": {}, "fields": {}, "actions": {}, "engine": set()}
    errors, snap = [], []
    for l in lines:
        kind, _, rest = l.partition(" ")
        if kind == "ERR":
            errors.append(rest)
        elif kind == "PAGE":
            facts["pages"].append(rest)
        elif kind == "STATE":
            p = rest.split()
            facts["state"][p[0]] = p[1:]
        elif kind == "FIELD":
            a, b = rest.split()
            facts["fields"].setdefault(a, set()).add(b)
        elif kind == "ACTION":
            a, takes, what = rest.split()
            facts["actions"][a] = (takes, what)
        elif kind == "ENGINE":
            facts["engine"].add(rest)
        elif kind == "SNAP":
            snap.append(rest)
    if not any(e.startswith("check:") for e in errors) and (facts["state"] or facts["actions"]):
        errors += read_source(cart, facts)
    return errors + (snap if snapshot else [])


def main():
    args = sys.argv[1:]
    snapshot = "--snapshot" in args
    carts = [a for a in args if not a.startswith("--")]
    bad = 0
    for cart in carts:
        out = check(cart, snapshot)
        errs = [l for l in out if l.startswith(("error[", "check:")) or not snapshot]
        print("== %s: %s" % (cart, "%d error%s" % (len(errs), "" if len(errs) == 1 else "s") if errs else "clean"))
        for l in out:
            print("  " + l)
        bad += bool(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
