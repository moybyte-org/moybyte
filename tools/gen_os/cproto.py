"""A row's C prototype, parsed, and the C text every emitter shares."""

import re

from . import TableError, header


_PROTO = re.compile(r"^(?P<ret>.*?[\s*])(?P<name>[A-Za-z_]\w*)\((?P<args>.*)\)$")


def _split(args):
    """The parameters of a parameter list, split at its top-level commas."""
    out, depth, cur = [], 0, ""
    for ch in args:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def param_name(p):
    m = re.search(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)", p)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z_]\w*)\s*(\[[^\]]*\])?$", p)
    if not m:
        raise TableError("a parameter with no name: %r" % p)
    return m.group(1)


def parse(c):
    """(return type, name, [parameter], variadic) of prototype `c`."""
    m = _PROTO.match(c.strip())
    if not m:
        raise TableError("not a prototype: %r" % c)
    ret = m.group("ret").strip()
    ps = _split(m.group("args"))
    variadic = bool(ps) and ps[-1] == "..."
    if variadic:
        ps = ps[:-1]
    if ps == ["void"]:
        ps = []
    for p in ps:
        param_name(p)
    return ret, m.group("name"), ps, variadic


HEAD = header("native/os_calls.json", "//") + "\n"


def wrap(items, indent="    ", width=96):
    """Comma-separated `items` filled to `width` columns."""
    out, cur = [], indent
    for it in items:
        piece = it + ","
        if len(cur) + len(piece) + 1 > width and cur.strip():
            out.append(cur.rstrip())
            cur = indent
        cur += piece + " "
    if cur.strip():
        out.append(cur.rstrip())
    return out


def c_decl(head, ps, tail, width=99):
    """`head(p, p, ...)tail`, its parameters wrapped under the first."""
    line = head + "("
    pad = " " * len(line)
    out = []
    for i, p in enumerate(ps):
        piece = p + ("," if i < len(ps) - 1 else ")" + tail)
        if len(line) + len(piece) + 1 > width and line.strip() and not line.endswith("("):
            out.append(line.rstrip())
            line = pad
        line += piece + ("" if i == len(ps) - 1 else " ")
    if not ps:
        line += ")" + tail
    out.append(line)
    return out
