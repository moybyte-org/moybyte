#!/usr/bin/env python3
"""The `mp_task` call-list guard (docs/kernel_spine_2026-10.md §8).

    python3 tools/mp_task_calls.py check  <micropython checkout> <record>
    python3 tools/mp_task_calls.py record <micropython checkout> <record>
    python3 tools/mp_task_calls.py extract <main.c> [function ...]

The kernel owns `app_main` on a board that takes native/moy_kernel, and its VM
service is the esp32 port's `mp_task` COPIED: the one-time prelude, the
per-start init, the boot scripts and the `soft_reset_exit` teardown, in the
port's order. An init or deinit upstream adds, removes or reorders compiles
fine against the copy and is simply missing from it. This notices.

`check` reads the port's `ports/esp32/main.c` as committed at the checkout's
HEAD (the pinned tag -- never the working file, which the build patches),
extracts the ordered call list of every function the record names (`app_main`,
spelled `MICROPY_ESP_IDF_ENTRY` there, `mp_task` and `platform_mbedtls_time`),
and compares it with the record, `native/moy_kernel/mp_task_calls.txt`. Any
difference fails the build with a diff and the exit status 1. `record` writes
the record from the checkout, for after the difference was reviewed and the
copy brought up to date; `tests/test_mp_task_calls.py` holds the copy in
`native/moy_kernel/moy_kernel.c` to the record.

The list is, per function, in source order: every call (`call name`, nested
calls in the order they open), every preprocessor conditional around them
(`#if ...`, `#else`, `#endif`, normalized), every label and every `goto`.
Comments, string literals, whitespace and argument text are not in it, so a
reformatted call or an edited comment is not a change; a call added, removed,
moved, or put under a new conditional is.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys

MAIN_C = "ports/esp32/main.c"
DEFAULT_FUNCTIONS = ("MICROPY_ESP_IDF_ENTRY", "mp_task", "platform_mbedtls_time")

_NOT_CALLS = {"if", "for", "while", "switch", "return", "sizeof", "defined",
              "else", "do", "case", "__attribute__", "__asm__", "asm"}
_TOKEN = re.compile(
    r"(?P<pp>^[ \t]*#[ \t]*(?P<dir>if|ifdef|ifndef|elif|else|endif)\b(?P<cond>[^\n]*))"
    r"|(?P<label>^[ \t]*(?P<lname>[A-Za-z_]\w*)[ \t]*:(?!:))"
    r"|(?P<goto>\bgoto[ \t]+(?P<gname>[A-Za-z_]\w*))"
    r"|(?P<call>\b(?P<cname>[A-Za-z_]\w*)[ \t\n]*\()",
    re.M)


def strip_c(src: str) -> str:
    """`src` with comments and string/char literals blanked, newlines kept
    (so a preprocessor line stays a line)."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * src.count("\n", i, j) or " ")
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(c + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def function_body(src: str, name: str) -> str:
    """The text between the braces of `name`'s definition in stripped `src`."""
    m = re.search(r"^[A-Za-z_][^;{}()\n]*\b%s[ \t]*\([^;{}]*\)[ \t\n]*\{" % re.escape(name),
                  src, re.M)
    if m is None:
        raise LookupError("no definition of %s()" % name)
    depth, i = 1, m.end()
    while depth:
        if i >= len(src):
            raise LookupError("unbalanced braces in %s()" % name)
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i - 1]


def call_list(source: str, functions=DEFAULT_FUNCTIONS) -> list:
    """The ordered call list of each of `functions` in a main.c's text."""
    src = strip_c(source)
    entries = []
    for fn in functions:
        entries.append("[%s]" % fn)
        for m in _TOKEN.finditer(function_body(src, fn)):
            if m.group("pp"):
                cond = " ".join(m.group("cond").split())
                entries.append(("#%s %s" % (m.group("dir"), cond)).rstrip())
            elif m.group("label"):
                if m.group("lname") != "default":
                    entries.append("label %s" % m.group("lname"))
            elif m.group("goto"):
                entries.append("goto %s" % m.group("gname"))
            elif m.group("cname") not in _NOT_CALLS:
                entries.append("call %s" % m.group("cname"))
    return entries


def committed_main_c(mpy_dir: str) -> str:
    """The port's main.c as committed at the checkout's HEAD."""
    return subprocess.run(["git", "-C", mpy_dir, "show", "HEAD:" + MAIN_C],
                          check=True, capture_output=True, text=True).stdout


def describe(mpy_dir: str) -> str:
    r = subprocess.run(["git", "-C", mpy_dir, "describe", "--tags", "--always"],
                       capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def read_record(path: str):
    """(the tag line, the function names, the entries) of a record file."""
    tag, entries = "", []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("# recorded at "):
                tag = line[len("# recorded at "):]
            elif line and not line.startswith("# "):
                entries.append(line)
    functions = [e[1:-1] for e in entries if e.startswith("[") and e.endswith("]")]
    return tag, functions, entries


def render_record(tag: str, entries) -> str:
    return ("# The esp32 port's mp_task call list (tools/mp_task_calls.py), what the\n"
            "# kernel's VM service was last reviewed against. Regenerate only after\n"
            "# that review: tools/mp_task_calls.py record <checkout> <this file>.\n"
            "# recorded at %s\n" % tag) + "\n".join(entries) + "\n"


def compare(recorded, current, recorded_tag: str, current_tag: str) -> str:
    """'' when equal, else the report a failing check prints."""
    if list(recorded) == list(current):
        return ""
    diff = difflib.unified_diff(list(recorded), list(current),
                                "recorded at " + recorded_tag,
                                MAIN_C + " at " + current_tag, lineterm="", n=2)
    return ("!! mp_task changed shape: the call list of the port's main.c at %s is not\n"
            "!! the one the kernel's VM service was reviewed against (%s). Bring the\n"
            "!! copy in native/moy_kernel/moy_kernel.c up to date, then re-record.\n"
            % (current_tag, recorded_tag)) + "\n".join(diff)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if len(argv) >= 2 and argv[0] == "extract":
        with open(argv[1]) as f:
            print("\n".join(call_list(f.read(), argv[2:] or DEFAULT_FUNCTIONS)))
        return 0
    if len(argv) == 3 and argv[0] in ("check", "record"):
        mpy_dir, record = argv[1], argv[2]
        source, tag = committed_main_c(mpy_dir), describe(mpy_dir)
        if argv[0] == "record":
            entries = call_list(source)
            with open(record, "w") as f:
                f.write(render_record(tag, entries))
            print("recorded %d entries of %s at %s -> %s"
                  % (len(entries), MAIN_C, tag, record))
            return 0
        rec_tag, functions, recorded = read_record(record)
        report = compare(recorded, call_list(source, functions or DEFAULT_FUNCTIONS),
                         rec_tag, tag)
        if report:
            print(report, file=sys.stderr)
            return 1
        print("== mp_task call list at %s matches the record (%s)" % (tag, rec_tag))
        return 0
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
